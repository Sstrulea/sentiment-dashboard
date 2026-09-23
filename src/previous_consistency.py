"""Previous-consistency integrity check (audit 2026-09-23, point 1.2).

Pure and side-effect-free: reads nothing, writes nothing, never changes a score.
It catches the class of defect behind the AUD import prices override (a JBlanked
0.0 placeholder that reached scoring, or was confirmed by hand): the NEXT print of
the same series publishes a `previous` that disagrees with the 0.0 we scored.

Inputs
  ff        FF canonical parquet frame (canonical_id, currency, name_raw,
            name_canonical, datetime_utc, actual, previous), full history.
  upcoming  optional canonical frame parsed from the latest weekly FF feed
            (data/ff_raw/ff_weekly_*.json): its `previous` covers the next print of
            a series whose next event is not in the parquet yet.
  scoring   the scoring calendar exactly as build_payload receives it
            (ff_scoring.to_scoring_frame + FRED quarantine + manual overrides).
            The actual checked is the effective one: a manual override counts
            with the value it gives, else the scored actual; a 0.0 quarantined
            to NaN at scoring (and never overridden) is checked too, reported as
            scored_source="quarantined" — not scored, but the real print is lost
            and the next `previous` tells what it was.

Release = one row per (canonical_id, UTC calendar date), the latest listed time
wins (same convention as manual_actuals rule (b)): FF re-lists a release at a
revised time, and both copies carry the same `previous` for the next release.

Revision tolerance per series (canonical_id), from its own history:
    pairs  = consecutive releases (i, i+1) where actual_i is present and != 0.0
             and previous_{i+1} is present (0.0 actuals are excluded: they are
             the suspects);
    a_i    = |previous_{i+1} - actual_i|;
    tol    = median(a) + MAD_K * 1.4826 * MAD(a),  MAD(a) = median(|a - median(a)|);
             0 if n_pairs == 0.
Robust on purpose: the archive holds ghost / out-of-order duplicate releases
(data_integrity class 1) whose pairing yields spurious "revisions". A quantile is
dragged by them — AUD import prices: q95 = 0.87 while every real revision of the
series was 0.0 — the median/MAD estimate is not (tol 0.0 there).
Flag: scored actual_i == 0.0, previous_{i+1} != 0 and
      |previous_{i+1} - 0.0| > tol + EPS.
A series whose revisions are routinely large (e.g. payrolls) gets a large tol and
is not flagged for an ordinary revision; a series never revised (tol = 0) is
flagged at the first disagreement.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .economic_fetch import CompiledMatcher
from .ff_scoring import CCY2COUNTRY

MAD_K = 3.0
EPS = 1e-9

FINDING_FIELDS = [
    "canonical_id", "currency", "indicator_key", "name_raw", "release_dt",
    "scored_actual", "scored_source", "next_release_dt", "next_previous",
    "next_from", "diff", "tolerance", "n_revision_pairs",
]


def _releases(ff: pd.DataFrame, upcoming: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["canonical_id", "currency", "name_raw", "name_canonical",
            "datetime_utc", "actual", "previous"]
    base = ff[cols].copy()
    base["_from"] = "parquet"
    frames = [base]
    if upcoming is not None and len(upcoming):
        up = upcoming[cols].copy()
        up["_from"] = "ff_weekly"
        frames.append(up)
    df = pd.concat(frames, ignore_index=True)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df["previous"] = pd.to_numeric(df["previous"], errors="coerce")
    df["_date"] = df["datetime_utc"].dt.date
    # the parquet wins over the feed for the same row; then the latest listed time
    # of the day wins.
    df["_rank"] = (df["_from"] == "parquet").astype(int)
    df = df.sort_values(["canonical_id", "_date", "datetime_utc", "_rank"])
    df = df.drop_duplicates(["canonical_id", "datetime_utc"], keep="last")
    df = df.drop_duplicates(["canonical_id", "_date"], keep="last")
    return df.sort_values(["canonical_id", "datetime_utc"]).reset_index(drop=True)


def _scored_lookup(scoring: pd.DataFrame) -> tuple[dict, dict]:
    """(ff rows by (currency, name_raw, release_dt), manual rows by
    (currency, indicator_key, release_dt)) -> scored actual."""
    by_name: dict = {}
    by_key: dict = {}
    if scoring is None or scoring.empty:
        return by_name, by_key
    for r in scoring.itertuples(index=False):
        dt = pd.Timestamp(r.release_dt)
        a = r.actual
        if getattr(r, "source", "ff") == "manual":
            by_key[(r.currency, r.indicator_key, dt)] = a
        else:
            by_name[(r.currency, r.name_raw, dt)] = a
    return by_name, by_key


def revision_tolerance(actuals: np.ndarray, next_previous: np.ndarray) -> tuple[float, int]:
    """tol and n_pairs per the module docstring. Inputs are aligned arrays of
    (actual_i, previous_{i+1})."""
    m = ~np.isnan(actuals) & ~np.isnan(next_previous) & (actuals != 0.0)
    r = np.abs(next_previous[m] - actuals[m])
    n = int(r.size)
    if n == 0:
        return 0.0, 0
    med = float(np.median(r))
    mad = float(np.median(np.abs(r - med)))
    return med + MAD_K * 1.4826 * mad, n


def check_previous_consistency(ff: pd.DataFrame, scoring: pd.DataFrame,
                               matcher: CompiledMatcher,
                               upcoming: Optional[pd.DataFrame] = None) -> dict:
    """Returns {"findings": [...], "n_series", "n_scored_zero", "formula"}."""
    rel = _releases(ff, upcoming)
    by_name, by_key = _scored_lookup(scoring)
    findings: list[dict] = []
    n_series = 0
    n_scored_zero = 0
    for cid, g in rel.groupby("canonical_id", sort=True):
        g = g.reset_index(drop=True)
        r0 = g.iloc[0]
        key = matcher.match(CCY2COUNTRY.get(r0.currency, ""), r0.name_canonical)
        if key is None:
            continue   # not modeled -> never scored
        n_series += 1
        raw_actual = g["actual"].to_numpy(dtype=float)
        nxt_prev = np.append(g["previous"].to_numpy(dtype=float)[1:], np.nan)
        tol, n_pairs = revision_tolerance(raw_actual, nxt_prev)
        for i in range(len(g) - 1):
            row = g.iloc[i]
            dt = pd.Timestamp(row.datetime_utc)
            if (row.currency, key, dt) in by_key:
                scored, src = by_key[(row.currency, key, dt)], "manual"
            elif (row.currency, row.name_raw, dt) in by_name:
                scored, src = by_name[(row.currency, row.name_raw, dt)], "ff"
                if pd.isna(scored) and row.actual == 0.0:
                    # 0.0 quarantined to NaN at scoring and never overridden: not
                    # scored, but the real print is lost -> still reported.
                    scored, src = 0.0, "quarantined"
            else:
                continue
            if pd.isna(scored) or float(scored) != 0.0:
                continue
            n_scored_zero += 1
            nprev = nxt_prev[i]
            if np.isnan(nprev) or nprev == 0.0:
                continue
            diff = float(nprev) - 0.0
            if abs(diff) > tol + EPS:
                nrow = g.iloc[i + 1]
                findings.append({
                    "canonical_id": cid, "currency": row.currency,
                    "indicator_key": key, "name_raw": row.name_raw,
                    "release_dt": dt.isoformat(), "scored_actual": 0.0,
                    "scored_source": src,
                    "next_release_dt": pd.Timestamp(nrow.datetime_utc).isoformat(),
                    "next_previous": float(nprev), "next_from": nrow._from,
                    "diff": round(diff, 6), "tolerance": round(tol, 6),
                    "n_revision_pairs": n_pairs,
                })
    findings.sort(key=lambda f: (f["release_dt"], f["canonical_id"]))
    by_src: dict[str, int] = {}
    for f in findings:
        by_src[f["scored_source"]] = by_src.get(f["scored_source"], 0) + 1
    return {
        "findings": findings,
        "findings_by_source": by_src,
        "n_series": n_series,
        "n_scored_zero": n_scored_zero,
        "formula": ("a = |previous[i+1] - actual[i]| over the series' pairs with "
                    "actual[i] != 0; tol = median(a) + 3*1.4826*MAD(a) (0 if no pairs); "
                    "flag effective actual[i] == 0 with previous[i+1] != 0 and "
                    "|previous[i+1]| > tol"),
    }
