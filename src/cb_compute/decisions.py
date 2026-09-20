"""Rate decisions (phase 1B-1): one row per (bank, meeting) built from the official series, BIS, validated Forex Factory
rows and (last resort) a manual entry. Pure functions - the caller supplies the meetings, the series views and the FF
rows; nothing here reads a file or a URL.

Precedence for `rate_after`:  official series (level on the effective date)  >  the rate in the bank's own statement (phase 2a)  >
BIS WS_CBPOL  >  validated FF  >  manual.  A row whose winner is FF is `ff_pending` (waiting for the official / BIS series to reach
the effective date); if a lower-precedence source disagrees with the winner the row is `conflict` (the winner's value stays).

FF validation:
  * matched on (currency, name) - the caller passes only the rows of the bank's FF name - and on the OFFICIAL meeting
    (first day - 1 ... decision + 1, by UTC date);
  * several rows in the window -> the one closest to the decision time;
  * actual NaN -> rejected; actual 0.0 with previous > 0 -> rejected unless the official / BIS level confirms 0.0
    (BoJ zero placeholders); SNB has no numeric FF rows at all (config `ff_row_maps_to: none`).
Consensus = the FF forecast mapped to the bank's convention (Fed: upper limit -> midpoint; ECB: MRO -> DFR with the
MRO - DFR spread of the ECB series on the effective date - 0.50 until 2024-09-17, 0.15 since). The spread is read on the
day the decision takes effect: the 12 Sep 2024 cut took effect on the 18th, when the spread had already moved to 0.15.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..cb_calendar import Calendar, effective_date

TOL = 0.001                               # percentage points: source disagreement threshold (0.1 bp)
RANGE_WIDTH = 0.25                        # Fed target range width (percentage points)
ONE = timedelta(days=1)
STATUSES = ("official", "statement", "bis", "ff_pending", "manual", "conflict")


def _r(x: Optional[float], n: int = 6) -> Optional[float]:
    return None if x is None else round(float(x), n)


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# Data views
# ---------------------------------------------------------------------------

class SeriesView:
    """Official series as {series_id: sorted [(date, value)]}: level on / before a date, coverage."""

    def __init__(self, rows: list) -> None:
        by: dict = {}
        for r in rows:
            by.setdefault(r["series_id"], []).append((r["date"], r["value"]))
        self._d = {k: sorted(v) for k, v in by.items()}
        self._k = {k: [d for d, _ in v] for k, v in self._d.items()}

    def last_date(self, sid: str) -> Optional[date]:
        return self._d[sid][-1][0] if self._d.get(sid) else None

    def first_date(self, sid: str) -> Optional[date]:
        return self._d[sid][0][0] if self._d.get(sid) else None

    def level(self, sid: str, on: date) -> Optional[float]:
        """Last observation on or before `on` (policy rates are step functions); None if the series starts later."""
        ks = self._k.get(sid)
        if not ks:
            return None
        i = bisect.bisect_right(ks, on)
        return self._d[sid][i - 1][1] if i else None

    def covers(self, sid: str, on: date) -> bool:
        first, last = self.first_date(sid), self.last_date(sid)
        return first is not None and first <= on <= last


@dataclass(frozen=True)
class FfRow:
    at: datetime                # timezone-aware UTC
    actual: Optional[float]
    forecast: Optional[float]
    previous: Optional[float]

    @property
    def day(self) -> date:
        return self.at.date()


def ff_rows_from_frame(df) -> list:
    """FF parquet rows (columns datetime_utc, actual, forecast, previous) -> FfRow (UTC-aware, NaN -> None)."""
    out = []
    for r in df.itertuples():
        at = r.datetime_utc.to_pydatetime() if hasattr(r.datetime_utc, "to_pydatetime") else r.datetime_utc
        at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
        f = lambda x: None if _isnan(x) else float(x)          # noqa: E731
        out.append(FfRow(at, f(r.actual), f(r.forecast), f(r.previous)))
    return sorted(out, key=lambda x: x.at)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    after: float
    before: Optional[float]
    source: str
    status: str                                  # official | statement | bis | ff_pending | manual
    lower: Optional[float] = None                # Fed range after the decision
    upper: Optional[float] = None
    notes: list = field(default_factory=list)


def spread_on(view: SeriesView, on: date, mro: str = "ecb:MRO", dfr: str = "ecb:DFR") -> Optional[float]:
    a, b = view.level(mro, on), view.level(dfr, on)
    return None if a is None or b is None else round(a - b, 4)


def official_candidate(cfg: dict, view: SeriesView, eff: date) -> Optional[Candidate]:
    """Official series level on the effective date (Fed: the two range limits; JPY / NZD: BIS WS_CBPOL, which is dated
    by the effective date exactly like the official series - checked for every change since 2025-09)."""
    pr = cfg["policy_rate"]
    off = pr.get("official") or {}
    if "lower" in off and "upper" in off:
        lo, up = off["lower"], off["upper"]
        if not (view.covers(lo, eff) and view.covers(up, eff)):
            return None
        before = eff - ONE
        u_b, l_b = view.level(up, before), view.level(lo, before)
        return Candidate(_r((view.level(up, eff) + view.level(lo, eff)) / 2),
                         None if u_b is None or l_b is None else _r((u_b + l_b) / 2),
                         f"{lo}+{up}", "official", _r(view.level(lo, eff)), _r(view.level(up, eff)))
    if "level" in off:
        sid = off["level"]
        if not view.covers(sid, eff):
            return None
        return Candidate(_r(view.level(sid, eff)), _r(view.level(sid, eff - ONE)), sid, "official")
    if "bis" in off:
        sid = off["bis"]
        if not view.covers(sid, eff):
            return None
        return Candidate(_r(view.level(sid, eff)), _r(view.level(sid, eff - ONE)), sid, "bis")
    return None


def _expected_utc(cfg: dict, decision: date) -> datetime:
    dt = cfg["decision_time"]
    tz = ZoneInfo(cfg["tz"])
    if dt.get("variable"):
        a, b = dt["window_local"]
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        mid = (ha * 60 + ma + hb * 60 + mb) // 2
        t = time(mid // 60, mid % 60)
    else:
        h, m = (int(x) for x in dt["local"].split(":"))
        t = time(h, m)
    return datetime.combine(decision, t, tz).astimezone(timezone.utc)


def decision_time_utc(cfg: dict, decision: date) -> Optional[datetime]:
    """Fixed wall-clock time of the decision on the meeting day in UTC; None where the bank only has a window (BoJ)."""
    return None if cfg["decision_time"].get("variable") else _expected_utc(cfg, decision)


def _to_convention(cfg: dict, ff_value: float, on: date, view: SeriesView) -> Optional[tuple]:
    """FF level -> (level in the bank's compute convention, lower, upper, note)."""
    m = cfg["policy_rate"].get("ff_row_maps_to")
    if m == "upper":                                             # Fed: FF shows the upper limit of the range
        return ff_value - RANGE_WIDTH / 2, ff_value - RANGE_WIDTH, ff_value, f"range width assumed {RANGE_WIDTH * 100:.0f} bp (FF shows the upper limit)"
    if m == "mro":                                               # ECB: FF shows the MRO
        s = spread_on(view, on)
        return (None if s is None else ff_value - s), None, None, f"DFR = MRO - {s} (ECB series spread on {on})"
    return ff_value, None, None, ""


def ff_candidate(cfg: dict, view: SeriesView, rows: list, meeting: dict, eff: date, expected: datetime,
                 official_after: Optional[float]) -> tuple:
    """Validated FF actual -> Candidate (or None) + validation notes; and the FF forecast (consensus) in the bank's
    convention."""
    notes: list = []
    if cfg["policy_rate"].get("ff_row_maps_to") in (None, "none"):
        return None, None, ["no numeric FF rows for this bank"]
    decision = meeting["date"]
    lo = (meeting.get("first_day") or decision) - ONE
    hi = decision + ONE
    win = [r for r in rows if lo <= r.day <= hi]
    if not win:
        return None, None, ["no FF row in the meeting window"]
    valid = []
    for r in win:
        if r.actual is None:
            notes.append(f"FF actual missing (NaN) at {r.at:%Y-%m-%d %H:%M}Z rejected")
        elif r.actual == 0.0 and r.previous is not None and r.previous > 0 and not (official_after is not None and abs(official_after) < TOL):
            notes.append(f"FF actual 0.0 with previous {r.previous:g} at {r.at:%Y-%m-%d %H:%M}Z rejected (placeholder; the official level does not confirm 0.0)")
        else:
            valid.append(r)
    if len(win) > 1:
        notes.append(f"{len(win)} FF rows in the window; closest to the decision time used")
    near = lambda r: abs((r.at - expected).total_seconds())      # noqa: E731
    cand = None
    if valid:
        r = min(valid, key=near)
        conv = _to_convention(cfg, r.actual, eff, view)
        prev = _to_convention(cfg, r.previous, eff - ONE, view) if r.previous is not None else None
        if conv and conv[0] is not None:
            cand = Candidate(_r(conv[0]), None if not prev or prev[0] is None else _r(prev[0]), f"ff:{cfg['policy_rate']['ff_name']}",
                             "ff_pending", _r(conv[1]), _r(conv[2]))
            if conv[3]:
                cand.notes.append(conv[3])
        else:
            notes.append("FF level could not be mapped to the bank's convention")
    fc = [r for r in win if r.forecast is not None]
    consensus = None
    if fc:
        r = min(fc, key=near)
        conv = _to_convention(cfg, r.forecast, eff, view)
        consensus = None if conv is None or conv[0] is None else _r(conv[0])
    return cand, consensus, notes


# ---------------------------------------------------------------------------
# One decision
# ---------------------------------------------------------------------------

def compute_decision(bank: str, cfg: dict, meeting: dict, calendars: dict, view: SeriesView, ff_rows: list,
                     manual: Optional[dict] = None, statement: Optional[dict] = None) -> Optional[dict]:
    """The decision row of one meeting, or None when no source has a rate for it yet. `statement` = {"after", "lower", "upper",
    "doc_id"}: the rate parsed (and validated) from the bank's own statement."""
    decision = meeting["date"]
    cal: Calendar = calendars[cfg["calendar_id"]]
    eff = effective_date(cfg["effective_rule"], decision, cal)
    notes: list = []
    if not cal.covers(eff):
        notes.append(f"calendar {cal.id} does not cover {eff}: weekends only")
    expected = _expected_utc(cfg, decision)

    official = official_candidate(cfg, view, eff)                        # official series or BIS (status says which)
    series = official if official is not None and official.status == "official" else None
    bis = official if official is not None and official.status == "bis" else None
    stmt = None
    if statement and statement.get("after") is not None:
        stmt = Candidate(_r(statement["after"]), None, f"statement:{statement['doc_id']}", "statement", _r(statement.get("lower")), _r(statement.get("upper")))
    ff, consensus, ff_notes = ff_candidate(cfg, view, ff_rows, meeting, eff, expected, official.after if official else None)
    notes += ff_notes
    man = None
    if manual:
        man = Candidate(_r(manual["rate_after"]), _r(manual.get("rate_before")), "manual", "manual", notes=[manual.get("note", "manual entry")])

    win = series or stmt or bis or ff or man
    if win is None:
        return None
    status, source = win.status, win.source
    notes += win.notes
    disagree = [c for c in (series, stmt, bis, ff) if c is not None and c is not win and abs(c.after - win.after) > TOL]
    if disagree:
        status = "conflict"
        for c in disagree:
            notes.append(f"CONFLICT: {win.source} says {win.after:g} but {'FF' if c is ff else c.source} says {c.after:g}; {win.source} wins")
    elif win is ff and man is not None and abs(man.after - ff.after) > TOL:
        notes.append(f"manual entry says {man.after:g}; FF wins by precedence")
    if win is ff and official is None:
        notes.append("waiting for the official / BIS series to reach the effective date")
    before = win.before
    if before is None:
        before = next((c.before for c in (official, ff, man, stmt) if c is not None and c.before is not None), None)
        if before is not None:
            notes.append("rate_before taken from another source")
    delta = None if before is None else _r((win.after - before) * 100, 4)
    return {
        "bank": cfg["id"], "currency": bank, "meeting_date": decision,
        "decision_time_utc": decision_time_utc(cfg, decision),
        "rate_before": before, "rate_after": win.after,
        "lower": win.lower, "upper": win.upper, "delta_bp": delta, "effective_date": eff,
        "consensus": consensus,
        "surprise_consensus_bp": None if consensus is None else _r((win.after - consensus) * 100, 4),
        "rate_source": source, "status": status, "notes": "; ".join(dict.fromkeys(n for n in notes if n)),
    }


def select_meetings(meetings: list, today: date, n: int = 4) -> list:
    """The last `n` meetings whose decision day is on or before today."""
    past = sorted((m for m in meetings if m["date"] <= today), key=lambda m: m["date"])
    return past[-n:]


def compute_all(cfgs: dict, meetings: dict, calendars: dict, view: SeriesView, ff_by_bank: dict, today: date,
                manual: Optional[dict] = None, n: int = 4, statements: Optional[dict] = None) -> tuple:
    """Rows for the last `n` meetings of every bank; also the list of (bank, date) that no source could resolve."""
    rows, unresolved = [], []
    for bank, cfg in cfgs.items():
        for m in select_meetings(meetings.get(bank, []), today, n):
            man = (manual or {}).get((bank, m["date"]))
            row = compute_decision(bank, cfg, m, calendars, view, ff_by_bank.get(bank, []), man, (statements or {}).get((bank, m["date"])))
            if row is None:
                unresolved.append((bank, m["date"]))
            else:
                rows.append(row)
    return rows, unresolved
