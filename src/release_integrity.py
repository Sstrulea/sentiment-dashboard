"""One publication = one row (audit 4B). Pure; no I/O.

The FF canonical parquet can hold several rows for one release on the same UTC
day (a re-listed time, a DST copy, a mis-dated row from the archive). Until now
the latest listed time won. Here the SERIES CHAIN decides:

  back     the row's `previous` must match the actual of the previous VALID
           release of the series, when that release is the previous period;
  forward  the row's actual (when real) must match the `previous` the next
           VALID release publishes, when that release is the next period.

A row contradicted beyond the series' threshold — max(tol, 2 * resolution): the
previous-consistency tolerance (median + 3*1.4826*MAD of its revisions) with the
value-class floor — leaves scoring and is reported (release_conflict). Only
same-day groups whose rows disagree on consensus or previous are examined; two
rows the chain cannot tell apart are left as they are.

"Valid" release = the day's representative row (latest listed time among the
rows not excluded) with a usable actual: not NaN and not a zero placeholder
candidate (a 0.0 with jb_status "Data Not Loaded", a 0/0/0 row, or a 0.0 on a
zero_possible=false series). "Next/previous period" = the day gap is between 0.5 and
1.5 x the series cadence (ff_scoring.detect_cadence); beyond that it is a GAP:
nothing is compared across it, and find_series_gaps reports it (missing_release).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .econ_calendar_ff import JB_NOT_LOADED, ensure_provenance_columns

CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365}
PERIOD_SLACK = 1.5
MIN_PERIOD = 0.5
EPS = 1e-9


def _cadence_days(dates) -> Optional[float]:
    from .ff_scoring import detect_cadence
    return CADENCE_DAYS.get(detect_cadence(dates))


def _is_usable(row, zp: bool) -> bool:
    a = row.actual
    if a is None or pd.isna(a):
        return False
    if float(a) != 0.0:
        return True
    if not zp or row.jb_status == JB_NOT_LOADED:
        return False
    f, p = row.forecast, row.previous
    return not ((pd.notna(f) and float(f) == 0.0) and (pd.notna(p) and float(p) == 0.0))


def _previous_usable(row) -> bool:
    p = row.previous
    if p is None or pd.isna(p):
        return False
    a, f = row.actual, row.forecast
    zero_row = float(p) == 0.0 and (pd.isna(a) or float(a) == 0.0) and (pd.isna(f) or float(f) == 0.0)
    return not zero_row


def _threshold(actuals: np.ndarray, next_prev: np.ndarray, values: np.ndarray) -> float:
    from .previous_consistency import revision_tolerance, series_resolution
    tol, _n = revision_tolerance(actuals, next_prev)
    return max(tol, 2 * series_resolution(values))


def _prep(ff: pd.DataFrame) -> pd.DataFrame:
    df = ensure_provenance_columns(ff).copy()
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    for c in ("actual", "forecast", "previous"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["day"] = df["datetime_utc"].dt.date
    return df


def _same(a, b) -> bool:
    return (pd.isna(a) and pd.isna(b)) or (pd.notna(a) and pd.notna(b) and abs(a - b) < EPS)


class SeriesChain:
    """Per series: day representatives, cadence, threshold, excluded rows."""

    def __init__(self, g: pd.DataFrame, zp: bool):
        self.g = g.sort_values("datetime_utc")
        self.zp = zp
        self.cad = _cadence_days(self.g["day"].unique())
        vals = np.concatenate([self.g["actual"].to_numpy(float), self.g["previous"].to_numpy(float)])
        rep = self.g.drop_duplicates("day", keep="last")
        nxt = np.append(rep["previous"].to_numpy(float)[1:], np.nan)
        self.thr = _threshold(rep["actual"].to_numpy(float), nxt, vals)
        self.excluded: dict = {}

    def _period_ok(self, d0, d1) -> bool:
        """d1 is the period right after d0: between 0.5 and 1.5 cadences apart
        (a row one day earlier is the same release re-listed, not the previous
        period; beyond 1.5 is a gap)."""
        if self.cad is None:
            return False
        gap = (pd.Timestamp(d1) - pd.Timestamp(d0)).days
        return MIN_PERIOD * self.cad <= gap <= PERIOD_SLACK * self.cad

    def representatives(self) -> pd.DataFrame:
        keep = self.g[~self.g["datetime_utc"].isin([k[1] for k in self.excluded])]
        return keep.drop_duplicates("day", keep="last")

    def valid(self) -> pd.DataFrame:
        key = frozenset(self.excluded)
        if getattr(self, "_valid_key", None) != key:
            rep = self.representatives()
            self._valid = rep[[_is_usable(r, self.zp) for r in rep.itertuples(index=False)]]
            self._valid_key = key
        return self._valid

    def _far_enough(self, d0, d1) -> bool:
        return self.cad is not None and \
            (pd.Timestamp(d1) - pd.Timestamp(d0)).days >= MIN_PERIOD * self.cad

    def prev_valid(self, day):
        """The previous period's valid release: releases closer than half a
        cadence are the SAME publication re-listed (skipped); the first one
        beyond is compared only if it is within 1.5 cadences (else a gap)."""
        v = self.valid()
        v = v[v["day"] < day]
        v = v[[self._far_enough(d, day) for d in v["day"]]]
        if v.empty:
            return None
        r = v.iloc[-1]
        return r if self._period_ok(r["day"], day) else None

    def _next_index(self):
        """Cached (days, rows) of the representatives whose previous is usable."""
        key = frozenset(self.excluded)
        if getattr(self, "_next_key", None) != key:
            rep = self.representatives()
            rep = rep[[_previous_usable(r) for r in rep.itertuples(index=False)]]
            self._next_rows = rep.reset_index(drop=True)
            self._next_days = [pd.Timestamp(d) for d in self._next_rows["day"]]
            self._next_key = key
        return self._next_days, self._next_rows

    def next_valid(self, day):
        """The next period's release whose `previous` is usable — what the
        forward comparisons read. A placeholder ACTUAL does not disqualify it
        (FF still publishes a real previous next to a "Data Not Loaded"
        actual); a 0/0/0 row or a missing previous does. Re-listings of the
        same publication (< half a cadence away) are skipped; beyond 1.5
        cadences it is a gap (None)."""
        if self.cad is None:
            return None
        import bisect
        days, rows = self._next_index()
        start = pd.Timestamp(day) + pd.Timedelta(days=MIN_PERIOD * self.cad)
        i = bisect.bisect_left(days, start)
        if i >= len(days):
            return None
        r = rows.iloc[i]
        return r if self._period_ok(day, r["day"]) else None


def resolve_conflicts(ff: pd.DataFrame, zero_possible: dict) -> tuple[dict, list[dict]]:
    """({(canonical_id, datetime_utc): reason} excluded from scoring, findings)."""
    if ff is None or len(ff) == 0:
        return {}, []
    df = _prep(ff)
    excluded: dict = {}
    findings: list[dict] = []
    for cid, g in df.groupby("canonical_id", sort=False):
        if not g.duplicated("day").any():
            continue
        chain = SeriesChain(g, bool(zero_possible.get(cid, True)))
        for day, grp in g.groupby("day"):
            if len(grp) < 2:
                continue
            fp = {(None if pd.isna(r.forecast) else round(float(r.forecast), 9),
                   None if pd.isna(r.previous) else round(float(r.previous), 9))
                  for r in grp.itertuples(index=False)}
            if len(fp) < 2:
                continue
            prev_r, next_r = chain.prev_valid(day), chain.next_valid(day)
            for r in grp.itertuples(index=False):
                why = []
                if prev_r is not None and pd.notna(r.previous) and \
                        abs(float(r.previous) - float(prev_r["actual"])) > chain.thr + EPS:
                    why.append(f"previous {r.previous} vs prior actual {prev_r['actual']} "
                               f"({prev_r['day']})")
                if next_r is not None and pd.notna(r.actual) and float(r.actual) != 0.0 \
                        and pd.notna(next_r["previous"]) and \
                        abs(float(next_r["previous"]) - float(r.actual)) > chain.thr + EPS:
                    why.append(f"actual {r.actual} vs next previous {next_r['previous']} "
                               f"({next_r['day']})")
                if why:
                    excluded[(cid, pd.Timestamp(r.datetime_utc))] = "; ".join(why)
                    findings.append({
                        "check": "release_conflict", "canonical_id": cid, "currency": r.currency,
                        "name_raw": r.name_raw, "release_dt": pd.Timestamp(r.datetime_utc).isoformat(),
                        "actual": None if pd.isna(r.actual) else float(r.actual),
                        "forecast": None if pd.isna(r.forecast) else float(r.forecast),
                        "previous": None if pd.isna(r.previous) else float(r.previous),
                        "threshold": round(chain.thr, 6), "reason": "; ".join(why)})
    return excluded, findings


def chains(ff: pd.DataFrame, zero_possible: dict, excluded: Optional[dict] = None) -> dict:
    """{canonical_id: SeriesChain} with the conflict exclusions applied."""
    df = _prep(ff)
    out = {}
    for cid, g in df.groupby("canonical_id", sort=False):
        ch = SeriesChain(g, bool(zero_possible.get(cid, True)))
        ch.excluded = {k: v for k, v in (excluded or {}).items() if k[0] == cid}
        out[cid] = ch
    return out


def find_series_gaps(ff: pd.DataFrame, as_of: pd.Timestamp, zero_possible: dict,
                     min_releases: int = 6) -> list[dict]:
    """missing_release (series gap): two consecutive release days further apart
    than 1.5 x the series cadence — a period with no row at all (e.g. USD NFP
    2026-04-03, Good Friday: the JBlanked archive capture has no events at all
    for 2026-04-03..04-06). Only series with >= min_releases and a regular
    cadence; weekly series are skipped (holiday weeks move them) and so are
    policy-rate decisions (a meeting calendar; freshness.policy_rates checks
    those against data/cb/meetings.yaml)."""
    df = _prep(ff)
    df = df[df["datetime_utc"] <= pd.Timestamp(as_of)]
    out = []
    for cid, g in df.groupby("canonical_id", sort=False):
        if cid.endswith("_interest_rate_decision"):
            continue      # meeting calendar, not a cadence: freshness.policy_rates covers it
        days = sorted(g["day"].unique())
        if len(days) < min_releases:
            continue
        cad = _cadence_days(days)
        if cad is None or cad == 7:
            continue
        for d0, d1 in zip(days, days[1:]):
            gap = (pd.Timestamp(d1) - pd.Timestamp(d0)).days
            if gap > PERIOD_SLACK * cad:
                out.append({"check": "missing_release", "kind": "series_gap", "canonical_id": cid,
                            "currency": g.iloc[0]["currency"], "after": str(d0), "before": str(d1),
                            "gap_days": int(gap), "cadence_days": cad})
    return out


def load_known_gaps(path=None) -> list[dict]:
    """config/known_gaps.yaml -> [{id, start, end, currencies|None, reason}]."""
    import yaml
    from pathlib import Path
    p = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "known_gaps.yaml"
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return [{"id": str(e["id"]), "start": pd.Timestamp(e["start"]), "end": pd.Timestamp(e["end"]),
             "currencies": set(e["currencies"]) if e.get("currencies") else None,
             "reason": str(e["reason"])} for e in (raw.get("known_gaps") or [])]


def explain_gap(finding: dict, known: list[dict]) -> Optional[dict]:
    """The known-gap entry that explains a series_gap finding, or None: every
    release expected inside the gap (after + k x cadence) must fall in ONE
    known window of that currency, give or take min(7, cadence/4) days."""
    if finding.get("kind") != "series_gap" or not known:
        return None
    after, before = pd.Timestamp(finding["after"]), pd.Timestamp(finding["before"])
    cad = float(finding["cadence_days"])
    tol = pd.Timedelta(days=min(7.0, cad / 4))
    expected = []
    k = 1
    while after + pd.Timedelta(days=k * cad) < before - pd.Timedelta(days=MIN_PERIOD * cad):
        expected.append(after + pd.Timedelta(days=k * cad))
        k += 1
    if not expected:
        return None
    for e in known:
        if e["currencies"] is not None and finding.get("currency") not in e["currencies"]:
            continue
        if all(e["start"] - tol <= d <= e["end"] + tol for d in expected):
            return e
    return None


def find_unfed_scheduled(ff: pd.DataFrame, weekly: list[tuple[str, list]], as_of: pd.Timestamp,
                         matcher=None) -> list[dict]:
    """missing_release (not ingested): an event listed in an archived FF weekly
    feed (data/ff_raw), mapped to a modeled indicator and scheduled before
    as_of, with no parquet row for its series on that UTC day."""
    from .econ_calendar_ff import parse_ff_weekly
    from .ff_scoring import CCY2COUNTRY, build_matcher
    matcher = matcher or build_matcher()
    df = _prep(ff)
    have = set(zip(df["canonical_id"], df["day"]))
    # the archived snapshots overlap heavily: parse each distinct event once
    uniq: dict = {}
    for _tag, events in weekly:
        for e in events or []:
            uniq[(e.get("title"), e.get("country"), e.get("date"))] = e
    if not uniq:
        return []
    wk = parse_ff_weekly(list(uniq.values()), now_utc=pd.Timestamp(as_of))
    matched: dict = {}
    seen: dict = {}
    for r in wk.itertuples(index=False):
        dt = pd.Timestamp(r.datetime_utc)
        if dt > pd.Timestamp(as_of):
            continue
        k = (r.currency, r.name_canonical)
        if k not in matched:
            matched[k] = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical) is not None
        if matched[k]:
            seen[(r.canonical_id, dt.date())] = (r.currency, r.name_raw, dt)
    return [{"check": "missing_release", "kind": "not_ingested", "canonical_id": cid,
             "currency": c, "name_raw": n, "release_dt": dt.isoformat()}
            for (cid, day), (c, n, dt) in sorted(seen.items()) if (cid, day) not in have]


def next_valid_previous(chain: SeriesChain, day) -> float:
    """The `previous` published by the next VALID release when it is the next
    period, else NaN (a gap: nothing to compare)."""
    r = chain.next_valid(day)
    return float("nan") if r is None or pd.isna(r["previous"]) else float(r["previous"])


def chain_tolerance(chain: SeriesChain) -> tuple[float, int]:
    """Revision tolerance over (valid release, its next valid release) pairs —
    the previous-consistency formula on the same chain definition."""
    from .previous_consistency import revision_tolerance
    v = chain.valid()
    a, n = [], []
    for r in v.itertuples(index=False):
        np_ = next_valid_previous(chain, r.day)
        a.append(r.actual); n.append(np_)
    return revision_tolerance(np.array(a, float), np.array(n, float))
