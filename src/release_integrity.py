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

import math
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
    # 7D: `day` is the PUBLICATION (publication_keys), not the UTC calendar day
    df["day"] = publication_keys(df)
    return df


def _same(a, b) -> bool:
    return (pd.isna(a) and pd.isna(b)) or (pd.notna(a) and pd.notna(b) and abs(a - b) < EPS)


# ---------------------------------------------------------------------------
# Publication identity (audit 7D) — THE definition of "the same publication",
# used by 4B (conflicts, chain), the zero rule, previous_consistency, the
# scoring frame and the Manual Actuals panel.
# ---------------------------------------------------------------------------

PUBLICATION_WINDOW = pd.Timedelta(hours=26)     # "at most ±1 day", DST slack


def publication_relation(a, b) -> Optional[str]:
    """Two rows of ONE series, `a` listed no later than `b` (attributes
    datetime_utc, actual, forecast, previous):
      None           more than PUBLICATION_WINDOW apart -> different publications
      "same"         same forecast and previous -> one publication, re-listed
      "next_period"  b.previous == a.actual (a real, non-zero value): two
                     consecutive periods published together (NFP Oct/Nov
                     2025-12-16) -> different publications, both kept
      "conflict"     otherwise -> one publication whose rows disagree: the
                     series chain decides (resolve_conflicts)."""
    if pd.Timestamp(b.datetime_utc) - pd.Timestamp(a.datetime_utc) >= PUBLICATION_WINDOW:
        return None
    if _same(a.forecast, b.forecast) and _same(a.previous, b.previous):
        return "same"
    if pd.notna(b.previous) and pd.notna(a.actual) and float(a.actual) != 0.0 \
            and abs(float(b.previous) - float(a.actual)) < EPS:
        return "next_period"
    return "conflict"


def publication_keys(df: pd.DataFrame) -> pd.Series:
    """Per row (index-aligned): the publication it belongs to, as the listed time
    of that publication's first row (a Timestamp; unique per series). Rows of a
    series join the current publication while they are within the window of
    its first row and not the next period of any of its rows."""
    out = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    if df.empty:
        return out
    dt = pd.to_datetime(df["datetime_utc"]).to_numpy()
    cid = df["canonical_id"].to_numpy()
    act = pd.to_numeric(df["actual"], errors="coerce").to_numpy(float)
    fc = pd.to_numeric(df["forecast"], errors="coerce").to_numpy(float)
    pv = pd.to_numeric(df["previous"], errors="coerce").to_numpy(float)
    order = np.lexsort((dt, cid))                      # by series, then time (stable)
    keys = np.empty(len(df), dtype="datetime64[ns]")
    members: list = []
    anchor = None
    cur = object()
    for i in order:
        row = _Pub(dt[i], act[i], fc[i], pv[i])
        if cid[i] == cur and members:
            rel0 = publication_relation(members[0], row)
            if rel0 is not None and all(publication_relation(m, row) != "next_period" for m in members):
                members.append(row)
                keys[i] = anchor
                continue
        cur, members, anchor = cid[i], [row], dt[i]
        keys[i] = anchor
    return pd.Series(keys, index=df.index)


class _Pub:
    __slots__ = ("datetime_utc", "actual", "forecast", "previous")

    def __init__(self, t, a, f, p):
        self.datetime_utc, self.actual, self.forecast, self.previous = t, _num(a), _num(f), _num(p)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


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
        day = pd.Timestamp(day)
        import bisect
        days, rows = self._prev_index()
        if self.cad is None:
            return None
        # the latest valid release at least half a cadence before `day`
        limit = day - pd.Timedelta(days=math.ceil(MIN_PERIOD * self.cad))   # = _far_enough (whole days)
        i = bisect.bisect_right(days, limit) - 1
        if i < 0:
            return None
        r = rows.iloc[i]
        return r if self._period_ok(r["day"], day) else None

    def _prev_index(self):
        """Cached (days, rows) of the valid representatives (prev_valid)."""
        key = frozenset(self.excluded)
        if getattr(self, "_prev_key", None) != key:
            v = self.valid().reset_index(drop=True)
            self._prev_rows = v
            self._prev_days = [pd.Timestamp(d) for d in v["day"]]
            self._prev_key = key
        return self._prev_days, self._prev_rows

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
        day = pd.Timestamp(day)
        import bisect
        days, rows = self._next_index()
        start = pd.Timestamp(day) + pd.Timedelta(days=MIN_PERIOD * self.cad)
        i = bisect.bisect_left(days, start)
        if i >= len(days):
            return None
        r = rows.iloc[i]
        return r if self._period_ok(day, r["day"]) else None


_CONFLICT_CACHE: dict = {}
_CONFLICT_COLS = ["canonical_id", "currency", "name_raw", "datetime_utc", "actual",
                  "forecast", "previous", "jb_status"]


def resolve_conflicts(ff: pd.DataFrame, zero_possible: dict) -> tuple[dict, list[dict]]:
    """({(canonical_id, datetime_utc): reason} excluded from scoring, findings).
    Memoized on the CONTENT of the columns it reads (+ zero_possible): one build
    calls it several times on the same calendar."""
    if ff is None or len(ff) == 0:
        return {}, []
    try:
        cols = [c for c in _CONFLICT_COLS if c in ff.columns]
        key = (int(pd.util.hash_pandas_object(ff[cols], index=False).sum()), len(ff),
               type(zero_possible).__name__,
               tuple(sorted((k, bool(v)) for k, v in (zero_possible or {}).items())))
    except TypeError:
        key = None
    if key is not None and key in _CONFLICT_CACHE:
        ex, fi = _CONFLICT_CACHE[key]
        return dict(ex), [dict(f) for f in fi]
    ex, fi = _resolve_conflicts(ff, zero_possible)
    if key is not None:
        if len(_CONFLICT_CACHE) > 8:
            _CONFLICT_CACHE.clear()
        _CONFLICT_CACHE[key] = (dict(ex), [dict(f) for f in fi])
    return ex, fi


def _resolve_conflicts(ff: pd.DataFrame, zero_possible: dict) -> tuple[dict, list[dict]]:
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
            has_real = bool(((grp["actual"].notna()) & (grp["actual"] != 0.0)).any())
            for r in grp.itertuples(index=False):
                why = []
                # 7D: a 0/0/0 listing never survives beside a real print of the
                # same publication (it is that publication's placeholder)
                if has_real and all(pd.notna(v) and float(v) == 0.0
                                    for v in (r.actual, r.forecast, r.previous)):
                    why.append("0/0/0 listing of a publication that has a real print")
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
                            "currency": g.iloc[0]["currency"],
                            "after": str(pd.Timestamp(d0).date()), "before": str(pd.Timestamp(d1).date()),
                            "gap_days": int(gap), "cadence_days": cad})
    return out


def _load_known_gaps_raw(path=None) -> dict:
    import yaml
    from pathlib import Path
    p = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "known_gaps.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def load_known_gaps(path=None) -> list[dict]:
    """config/known_gaps.yaml -> [{id, start, end, currencies|None, reason}] (capture
    windows) + [{id, canonical_ids, after, before, publications, reason}]
    (per-publication entries, audit 6A)."""
    raw = _load_known_gaps_raw(path)
    out = [{"id": str(e["id"]), "start": pd.Timestamp(e["start"]), "end": pd.Timestamp(e["end"]),
            "currencies": set(e["currencies"]) if e.get("currencies") else None,
            "reason": str(e["reason"])} for e in (raw.get("known_gaps") or [])]
    for e in raw.get("missing_publications") or []:
        pubs = e.get("publications") or []
        if not pubs or any(not p.get("evidence") for p in pubs):
            raise ValueError(f"known_gaps: {e.get('id')} needs publications with evidence")
        out.append({"id": str(e["id"]), "canonical_ids": set(e["canonical_ids"]),
                    "after": pd.Timestamp(e["gap"]["after"]), "before": pd.Timestamp(e["gap"]["before"]),
                    "publications": pubs, "reason": str(e["reason"])})
    return out


def explain_gap(finding: dict, known: list[dict]) -> Optional[dict]:
    """The known-gap entry that explains a series_gap finding, or None.
      per-publication entry: same canonical_id and exactly the same (after,
        before) — the entry names each missing publication with its evidence;
      capture window: every release expected inside the gap (after + k x
        cadence) falls in ONE window of that currency, give or take
        min(7, cadence/4) days (only for holes that hit every series)."""
    if finding.get("kind") != "series_gap" or not known:
        return None
    after, before = pd.Timestamp(finding["after"]), pd.Timestamp(finding["before"])
    for e in known:
        if "canonical_ids" in e and finding.get("canonical_id") in e["canonical_ids"] \
                and e["after"] == after and e["before"] == before:
            return e
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
        if "start" not in e:
            continue
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
    listed: dict = {}
    for cid, t in zip(df["canonical_id"], df["datetime_utc"]):
        listed.setdefault(cid, []).append(pd.Timestamp(t))
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
            for (cid, day), (c, n, dt) in sorted(seen.items())
            if not any(abs(dt - t) < PUBLICATION_WINDOW for t in listed.get(cid, ()))]


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
