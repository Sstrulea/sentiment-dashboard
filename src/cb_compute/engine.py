"""Implied-rate engine (phase 1B-2, pure): trajectory per currency at an as-of date, next meeting, cumulative bp at the
last meeting of 2026 / 2027, GAP, repricing, surprises and reaction, currency pairs.

Nothing here reads a file or a URL: `cb_loader.load_context` builds a `Context` (market quotes, official series, decisions,
meetings, projections, config) and `cb_compute.report` renders the result. Every value carries its method flag:
EXACT | CURVE | UPPER_BOUND (3M windows) | PROXY (government curves / bills); n/a comes with a reason.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable, Optional

from ..cb_calendar import Calendar, weekends_only
from .decisions import SeriesView
from .methods import (Curve, TenorCurve, Window, chain_path, exact_chain, interior_meetings, interval_ends, months_between,
                      path_average, pick_window, pre_days, proxy_basis, step_probabilities)
from .spread import Spread, compute_spread

FLAG_OF = {"EXACT": "EXACT", "CURVE": "CURVE", "WINDOW": "UPPER_BOUND", "PROXY_CURVE": "PROXY", "PROXY_TENOR": "PROXY"}
STRENGTH = {"EXACT": 0, "CURVE": 1, "UPPER_BOUND": 2, "PROXY": 3, "DECIDED": -1}      # higher = weaker
LEVEL_KINDS = ("policy", "bkbm", "sovereign_proxy")
YEAR_ENDS = (2026, 2027)
GAP_YEARS = (2026, 2027, 2028)
REPRICING_BD = {"1s": 5, "1l": 21}


def weakest(*flags: Optional[str]) -> Optional[str]:
    flags = [f for f in flags if f]
    return max(flags, key=lambda f: STRENGTH.get(f, 9)) if flags else None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Meeting:
    decision: date
    eff: date                                   # effective date (bank rule + calendar)
    first_day: Optional[date] = None
    has_projections: bool = False
    has_presser: bool = False
    source: str = ""                            # official | ff | manual (meetings.yaml)
    verified: bool = False                      # the decision DATE was matched to an official page


@dataclass
class Snapshot:
    source: str
    asof: date
    rows: list


class MarketIndex:
    """Market quotes grouped by source and as-of date; `latest(source, on)` = the newest snapshot with asof <= on."""

    def __init__(self, rows: Iterable[dict]) -> None:
        self._by: dict = {}
        for r in rows:
            self._by.setdefault(r["source"], {}).setdefault(r["asof"], []).append(r)
        self._dates = {s: sorted(d) for s, d in self._by.items()}

    def latest(self, source: str, on: date) -> Optional[Snapshot]:
        ds = self._dates.get(source)
        if not ds:
            return None
        i = bisect.bisect_right(ds, on)
        return Snapshot(source, ds[i - 1], self._by[source][ds[i - 1]]) if i else None

    def first_date(self, source: str) -> Optional[date]:
        ds = self._dates.get(source)
        return ds[0] if ds else None

    def sources(self) -> list:
        return sorted(self._by)

    def newest(self) -> Optional[date]:
        """The latest as-of of any source."""
        return max((ds[-1] for ds in self._dates.values()), default=None)


@dataclass
class Context:
    banks: dict                                  # central_banks.yaml banks
    sources: dict                                # cb_sources.yaml sources
    calendars: dict                              # id -> Calendar
    market: MarketIndex
    view: SeriesView                             # official series
    decisions: dict                              # currency -> [decision rows], ascending
    meetings: dict                               # currency -> [Meeting], ascending
    series_calendar: dict = field(default_factory=dict)      # official series id -> calendar id
    projections: list = field(default_factory=list)
    rbnz: dict = field(default_factory=dict)
    documents: list = field(default_factory=list)                # official texts (phase 2a): rows of data/cb/documents
    votes: dict = field(default_factory=dict)                    # (currency, meeting_date) -> votes row
    redlines: dict = field(default_factory=dict)                 # (currency, meeting_date) -> redline row
    doc_warnings: list = field(default_factory=list)             # documents past their usual publication lag
    stale_after_bd: int = 2

    def cal(self, cal_id: Optional[str]) -> Calendar:
        return self.calendars.get(cal_id) or weekends_only()

    def primary(self, currency: str) -> list:
        return [(sid, c) for sid, c in self.sources.items() if c["currency"] == currency and c.get("role") == "primary"]

    def crosscheck(self, currency: str) -> list:
        return [(sid, c) for sid, c in self.sources.items() if c["currency"] == currency and c.get("role") == "crosscheck"]

    def source_cal(self, source: str) -> Calendar:
        return self.cal(self.sources[source].get("calendar_id"))

    def stale_limit(self, source: str) -> int:
        return int(self.sources[source].get("stale_after_bd", self.stale_after_bd))


# ---------------------------------------------------------------------------
# Base rate
# ---------------------------------------------------------------------------

@dataclass
class BaseRate:
    rate: float
    meeting: Optional[date]
    eff: Optional[date]
    pending: bool                                # decided, not in force yet (BoJ 18 Sep -> 24 Sep)
    note: str = ""


def base_rate(decisions: list, asof: date) -> Optional[BaseRate]:
    """The last rate DECIDED on or before `asof` - announced but not yet effective decisions included (Fed: midpoint)."""
    past = [d for d in decisions if d["meeting_date"] <= asof]
    if past:
        d = past[-1]
        return BaseRate(d["rate_after"], d["meeting_date"], d["effective_date"], d["effective_date"] > asof)
    later = [d for d in decisions if d["meeting_date"] > asof]
    if later and later[0]["rate_before"] is not None:
        return BaseRate(later[0]["rate_before"], None, None, False, f"no decision on record before {later[0]['meeting_date']}: rate_before of the next one")
    return None


def rate_in_force(decisions: list, on: date) -> Optional[float]:
    """Policy rate actually in force on `on` (a decided change counts from its effective date)."""
    eff = [d for d in decisions if d["effective_date"] <= on]
    if eff:
        return eff[-1]["rate_after"]
    return decisions[0]["rate_before"] if decisions and decisions[0]["rate_before"] is not None else None


def pending_steps(decisions: list, asof: date, after: date) -> list:
    """Decided changes whose effective date is later than `after`: [(eff, rate)]."""
    return [(d["effective_date"], d["rate_after"]) for d in decisions if d["meeting_date"] <= asof and d["effective_date"] > after]


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------

@dataclass
class Point:
    meeting: Optional[date]                      # decision date of the meeting (None for a tenor point)
    eff: Optional[date]
    kind: str                                    # meeting | window | tenor
    rate: Optional[float]                        # implied POLICY-equivalent rate, percent (None when only a level exists)
    cum_bp: Optional[float]                      # vs the base
    step_bp: Optional[float]                     # implied step at this meeting (per-meeting methods only)
    method: str                                  # EXACT | CURVE | WINDOW | PROXY_CURVE | PROXY_TENOR
    flag: str
    source: str
    source_asof: Optional[date]
    lag_bd: int = 0
    stale: bool = False
    window: Optional[tuple] = None
    reason: str = ""                             # why rate / cum / step are n/a
    notes: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    level: Optional[float] = None                # the value the DELTA metrics (repricing, reaction) use: == rate for policy-equivalent
    level_kind: str = "policy"                   # policy | bkbm (ASX BB, not the OCR) | sovereign_proxy (raw government curve)


@dataclass
class Trajectory:
    currency: str
    asof: date
    base: Optional[BaseRate]
    spread: Optional[Spread]
    points: list = field(default_factory=list)
    consistency: list = field(default_factory=list)      # [(label, month/window, deviation bp, detail)]
    notes: list = field(default_factory=list)
    na_reason: str = ""                                  # the whole currency is n/a (CHF)
    extra: dict = field(default_factory=dict)            # exact_path / exact_start / exact_end for the cross-checks

    def point_for(self, meeting: date) -> Optional[Point]:
        return next((p for p in self.points if p.meeting == meeting), None)


def _q(rows: list, instrument: str) -> list:
    return [r for r in rows if r["instrument"] == instrument]


def _raw_rate(row: dict) -> float:
    """Value as published -> rate in percent: futures 100 - price, MPT bp / 100, curves and yields already percent."""
    u = row["unit"]
    return (100.0 - row["value"]) if u == "index_points" else row["value"] / 100.0 if u == "bp" else row["value"]


def _finish(ctx: Context, pt: Point, asof: date) -> Point:
    pt.lag_bd = ctx.source_cal(pt.source).lag(pt.source_asof, asof) if pt.source_asof else 0
    pt.stale = pt.lag_bd > ctx.stale_limit(pt.source)
    return pt


# ---------------------------------------------------------------------------
# Spread of a currency at an as-of
# ---------------------------------------------------------------------------

def spread_for(ctx: Context, cur: str, asof: date) -> Spread:
    cfg = ctx.banks[cur].get("spread")
    if not cfg:
        return Spread(None, reason="no spread pair for this bank")
    cal = ctx.cal(ctx.series_calendar.get(cfg["benchmark"]))
    changes = [d["effective_date"] for d in ctx.decisions.get(cur, []) if d["meeting_date"] <= asof and d["delta_bp"]]
    return compute_spread(ctx.view, cal, cfg["benchmark"], cfg["policy"], asof, changes)


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------

def trajectory(ctx: Context, cur: str, asof: date) -> Trajectory:
    decs = ctx.decisions.get(cur, [])
    tr = Trajectory(cur, asof, base_rate(decs, asof), None)
    prim = ctx.primary(cur)
    if not prim:
        tr.na_reason = "no market path for this currency"
        return tr
    if tr.base is None:
        tr.na_reason = "no decision on record to anchor the base rate"
        return tr
    tr.spread = spread_for(ctx, cur, asof)
    unknown = [m for m in ctx.meetings.get(cur, []) if m.decision > asof]
    by_method: dict = {}
    for sid, cfg in prim:
        for inst, ic in cfg["instruments"].items():
            by_method.setdefault(ic["method"], []).append((sid, inst))
    pts: dict = {}
    for method in ("EXACT", "CURVE", "PROXY_CURVE", "WINDOW"):                 # exact first: the windows only fill the rest
        for sid, inst in by_method.get(method, []):
            snap = ctx.market.latest(sid, asof)
            if snap is None:
                tr.notes.append(f"{sid}: no quotes on or before {asof}")
                continue
            rows = _q(snap.rows, inst)
            if not rows:
                tr.notes.append(f"{sid}: no {inst} rows in the {snap.asof} snapshot")
                continue
            covered = {m for m, p in pts.items() if p.level is not None}
            for m, p in _METHODS[method](ctx, tr, unknown, sid, snap, rows, covered).items():
                if m not in covered:
                    pts[m] = _finish(ctx, p, asof)
    src = prim[0][0]
    why = tr.notes[0] if tr.notes else "no instrument reaches this meeting"
    tr.points = [pts.get(u.decision) or Point(u.decision, u.eff, "meeting", None, None, None, "n/a", "n/a", src, None, reason=why)
                 for u in unknown]
    if not pts and tr.notes:
        tr.na_reason = tr.notes[0]
    return tr


def _pt(sid: str, snap: Snapshot, u: Meeting, method: str, **kw) -> Point:
    return Point(u.decision, u.eff, kw.pop("kind", "meeting"), kw.pop("rate", None), kw.pop("cum_bp", None), kw.pop("step_bp", None),
                 method, FLAG_OF[method], sid, snap.asof, **kw)


def _cum(rate: float, base: float) -> float:
    return (rate - base) * 100


def step_reason(p: Point) -> str:
    """Why a point has no per-meeting step (the rate itself may still be there)."""
    if p.step_bp is not None:
        return ""
    return p.extra.get("step_reason") or p.reason or (
        "UPPER_BOUND: a 3M window average has no per-meeting step (interior meetings)" if p.method == "WINDOW" else "n/a")


# ---- EXACT ----------------------------------------------------------------

def _exact(ctx, tr, unknown, sid, snap, rows, covered) -> dict:
    sp = tr.spread.value if tr.spread and tr.spread.value is not None else None
    if sp is None:
        return {u.decision: _pt(sid, snap, u, method="EXACT", reason=f"spread unavailable ({tr.spread.reason if tr.spread else 'none'})") for u in unknown}
    months = {(r["ref_start"].year, r["ref_start"].month): _raw_rate(r) - sp for r in rows}
    if not months:
        return {}
    first = min(months)
    S0 = date(first[0], first[1], 1)
    decs = ctx.decisions.get(tr.currency, [])
    r_start = rate_in_force(decs, S0)
    if r_start is None:
        return {}
    known = pending_steps(decs, tr.asof, S0)
    last = max(months)
    inside = [u for u in unknown if (u.eff.year, u.eff.month) <= last and u.decision not in covered]
    chain = exact_chain(months, [(u.decision, u.eff) for u in inside], r_start, known)
    out, gap = {}, False
    for cp in chain.points:
        u = next(x for x in inside if x.decision == cp.decision)
        if cp.r_post is None:
            out[u.decision] = _pt(sid, snap, u, method="EXACT", reason=cp.reason, extra={"days_after": cp.days_after})
            gap = True
        else:
            notes = [cp.reason] if cp.how == "next_month" else []
            extra = {"days_after": cp.days_after, "how": cp.how, "r_prev": cp.r_prev, "months": last}
            if gap:                                                       # r_prev is the rate before an unidentified meeting
                extra["step_reason"] = "the previous meeting is not identified: the difference to the last identified rate spans two meetings"
            out[u.decision] = _pt(sid, snap, u, method="EXACT", rate=cp.r_post, level=cp.r_post, cum_bp=_cum(cp.r_post, tr.base.rate),
                                  step_bp=None if gap else (cp.r_post - cp.r_prev) * 100, notes=notes, extra=extra)
            gap = False
    tr.extra.update(exact_path=chain_path(chain, r_start, S0, known), exact_start=S0,
                    exact_end=date(last[0] + (last[1] == 12), last[1] % 12 + 1, 1))
    tr.consistency += [("EXACT " + sid, f"{c.month[0]}-{c.month[1]:02d}", c.dev_bp, f"contract {c.actual:.3f} vs path {c.expected:.3f}") for c in chain.consistency]
    return out


# ---- CURVE / PROXY_CURVE ---------------------------------------------------

def _curve_of(rows: list) -> Curve:
    return Curve([(r["tenor_months"], r["value"]) for r in rows])


def _base_start(tr: Trajectory) -> date:
    """First day on which the base rate is in force: today, or the effective date of a decided-but-pending change."""
    return max(tr.asof, tr.base.eff) if tr.base and tr.base.pending and tr.base.eff else tr.asof


def _curve_points(ctx, tr, unknown, sid, snap, rows, covered, proxy: bool) -> dict:
    todo = [u for u in unknown if u.decision not in covered]
    if not todo:
        return {}
    curve = _curve_of(rows)
    effs = [u.eff for u in unknown]
    ends = interval_ends(effs)
    t = lambda d: months_between(snap.asof, d)                                   # noqa: E731
    start0 = _base_start(tr)
    method = "PROXY_CURVE" if proxy else "CURVE"
    basis, basis_reason, offset = None, "", 0.0
    if proxy:
        # the basis comes only from tenors observed before the first effective date; without one it is n/a and so is every
        # policy-equivalent metric (cum bp, step, probability, surprise vs the market) - the raw levels remain
        basis, basis_reason = proxy_basis(list(zip(curve.t, curve.v)), t(start0), t(effs[0]), tr.base.rate)
        tr.extra["proxy_basis"] = basis
        offset = basis if basis is not None else 0.0
        tr.notes.append(f"PROXY basis {basis * 100:+.1f} bp = observed-tenor curve average on [{start0}, {effs[0]}) - base {tr.base.rate:g}"
                        if basis is not None else f"PROXY basis n/a - {basis_reason}")
    else:
        avg0 = curve.average(t(start0), t(effs[0])) if effs[0] > start0 else curve.value(t(start0))
        sp = tr.spread.value if tr.spread and tr.spread.value is not None else None
        if sp is None:
            return {u.decision: _pt(sid, snap, u, method=method, reason="spread unavailable") for u in todo}
        offset = sp
        tr.consistency.append(("CURVE " + sid, f"[{start0}, {effs[0]})", (avg0 - offset - tr.base.rate) * 100, f"curve {avg0:.3f} - spread {offset * 100:+.1f} bp vs base {tr.base.rate:g}"))
    label = "basis" if proxy else "spread"
    out, prev = {}, (None if proxy and basis is None else tr.base.rate)
    for u, e0, e1 in zip(unknown, effs, ends):
        t0m, t1m = t(e0), t(e1)
        why = ""
        # a reported level needs its WHOLE interval inside the tenors observed: no flat extrapolation at either end
        if t0m < curve.t[0] - 1e-9:
            why = f"outside curve coverage (<{curve.t[0]:g}M): the interval starts {t0m:.1f} months out, before the curve's first tenor"
        elif t1m > curve.horizon + 1e-9:
            why = f"outside curve coverage (>{curve.horizon:g}M): the interval ends {t1m:.1f} months out, beyond the curve's last tenor"
        if why:
            if u.decision not in covered:
                out[u.decision] = _pt(sid, snap, u, method=method, reason=why,
                                      extra={"step_reason": basis_reason} if proxy and basis is None else {})       # the step needs the basis too
            prev = None
            continue
        level = curve.average(t(e0), t(e1))
        notes = []
        if proxy:
            rate = None if basis is None else level - basis
            kind = "sovereign_proxy"
        else:
            rate, level, kind = level - offset, level - offset, "policy"
        if u.decision not in covered:
            extra = {"interval": (e0, e1), label: basis if proxy else offset}
            if proxy:
                extra["level_raw"] = level
            reason = basis_reason if rate is None else ""
            if rate is not None and prev is None:
                extra["step_reason"] = "the previous meeting is not identified: the difference to the base would span two meetings"
            out[u.decision] = _pt(sid, snap, u, method=method, rate=rate, level=level, level_kind=kind,
                                  cum_bp=None if rate is None else _cum(rate, tr.base.rate),
                                  step_bp=None if rate is None or prev is None else (rate - prev) * 100,
                                  window=(e0, e1), reason=reason, notes=notes, extra=extra)
        prev = rate
    return out


def _curve(ctx, tr, unknown, sid, snap, rows, covered):
    return _curve_points(ctx, tr, unknown, sid, snap, rows, covered, proxy=False)


def _proxy_curve(ctx, tr, unknown, sid, snap, rows, covered):
    return _curve_points(ctx, tr, unknown, sid, snap, rows, covered, proxy=True)


# ---- WINDOW ----------------------------------------------------------------

def _windows(tr: Trajectory, rows: list, sp: Optional[float]) -> list:
    return [Window(r["ref_start"], r["ref_end"], _raw_rate(r) - (sp or 0.0), r["contract"]) for r in rows if r["ref_start"] and r["ref_end"]]


def _window(ctx, tr, unknown, sid, snap, rows, covered) -> dict:
    todo = [u for u in unknown if u.decision not in covered]
    has_spread = bool(ctx.banks[tr.currency].get("spread"))
    sp = tr.spread.value if tr.spread and tr.spread.value is not None else None
    if has_spread and sp is None:
        return {u.decision: _pt(sid, snap, u, method="WINDOW", reason=f"spread unavailable ({tr.spread.reason})") for u in todo}
    wins = _windows(tr, rows, sp)
    effs = [u.eff for u in unknown]
    out = {}
    for u in todo:
        w = pick_window(wins, u.eff)
        if w is None:
            out[u.decision] = _pt(sid, snap, u, method="WINDOW", reason="no window starts on or after the effective date - 7 days (contract horizon)")
            continue
        notes = []
        if not has_spread:
            notes.append("BKBM level, not the OCR: no BKBM-OCR spread exists, so no bp vs the OCR")
        out[u.decision] = _pt(sid, snap, u, method="WINDOW", rate=w.rate if has_spread else None, level=w.rate,
                              level_kind="policy" if has_spread else "bkbm",
                              cum_bp=_cum(w.rate, tr.base.rate) if has_spread else None, window=(w.start, w.end), notes=notes,
                              reason="" if has_spread else "BKBM basis (no OCR spread)",
                              extra={"pre_days": pre_days(w, u.eff), "interior": interior_meetings(w, u.eff, effs),
                                     "gap_days": (w.start - u.eff).days, "contract": w.label,
                                     "step": "n/a: window average (UPPER_BOUND), not a per-meeting value"})
    return out


_METHODS = {"EXACT": _exact, "CURVE": _curve, "PROXY_CURVE": _proxy_curve, "WINDOW": _window}


# ---------------------------------------------------------------------------
# Next meeting, year ends
# ---------------------------------------------------------------------------

@dataclass
class NextMeeting:
    decision: Optional[date]
    eff: Optional[date]
    step_bp: Optional[float] = None
    step_reason: str = ""
    probabilities: Optional[dict] = None
    prob_reason: str = ""
    flag: Optional[str] = None
    stale: bool = False
    point: Optional[Point] = None


def next_meeting(tr: Trajectory) -> NextMeeting:
    if tr.na_reason:
        return NextMeeting(None, None, step_reason=tr.na_reason, prob_reason=tr.na_reason)
    if not tr.points:
        return NextMeeting(None, None, step_reason="no upcoming meeting", prob_reason="no upcoming meeting")
    p = tr.points[0]
    nm = NextMeeting(p.meeting, p.eff, flag=p.flag, stale=p.stale, point=p)
    if p.step_bp is not None:
        nm.step_bp = p.step_bp
    else:
        nm.step_reason = step_reason(p)
    if p.step_bp is not None and p.method in ("EXACT", "CURVE"):
        nm.probabilities = step_probabilities(p.step_bp)
    else:
        nm.prob_reason = step_reason(p) if p.step_bp is None else f"only EXACT / CURVE give a per-meeting probability (this is {p.method})"
    return nm


@dataclass
class YearEnd:
    year: int
    meeting: Optional[date]
    cum_bp: Optional[float] = None
    rate: Optional[float] = None
    flag: Optional[str] = None
    window: Optional[tuple] = None
    stale: bool = False
    reason: str = ""
    extra: dict = field(default_factory=dict)
    level: Optional[float] = None                # what the delta metrics use (see Point.level)
    level_kind: str = "policy"


def last_meeting_of(ctx: Context, cur: str, year: int) -> Optional[Meeting]:
    ms = [m for m in ctx.meetings.get(cur, []) if m.decision.year == year]
    return max(ms, key=lambda m: m.decision) if ms else None


def year_end(ctx: Context, tr: Trajectory, year: int) -> YearEnd:
    m = last_meeting_of(ctx, tr.currency, year)
    if tr.na_reason:
        return YearEnd(year, m.decision if m else None, reason=tr.na_reason)
    if m is None:
        return YearEnd(year, None, reason=f"no {year} meeting on record")
    if m.decision <= tr.asof:
        return YearEnd(year, m.decision, cum_bp=0.0, rate=tr.base.rate if tr.base else None, flag="DECIDED",
                       reason="the last meeting of the year is already decided: it is part of the base",
                       level=tr.base.rate if tr.base else None)
    p = tr.point_for(m.decision)
    if p is None or p.level is None:
        return YearEnd(year, m.decision, reason=(p.reason if p else "meeting not in the trajectory"))
    return YearEnd(year, m.decision, p.cum_bp, p.rate, p.flag, p.window, p.stale, p.reason if p.cum_bp is None else "", p.extra,
                   level=p.level, level_kind=p.level_kind)
