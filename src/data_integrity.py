"""Data-integrity checks over the FF canonical calendar — ghost (wrong-year
duplicate) rows and implausible zeros on LEVEL series (FAZA 1A, docs/).

Pure, side-effect-free: no I/O, no mutation. NOT wired into any production
path in this commit — `to_scoring_frame`/`compute_indicator_score` are
untouched. This module only produces a PROPOSED quarantine list for manual
review; adopting it (importing `build_quarantine_proposal` into the scoring
path) is a separate, later change.

Evidence base (measure/history-catalog-audit branch, FAZA 0.6): 93 wrong-year
duplicate pairs found on the raw canonical parquet, 79/93 confirmed present
in the raw JBlanked archive itself (upstream bug, not introduced by our own
merge/dedup); a handful of corrupted zero-placeholders on LEVEL series (e.g.
JPY BOJ Policy Rate, EUR ECB Main Refinancing Rate) that survive
`flagged_bad`'s gate because JBlanked's own Quality/Strength label didn't
catch them.

Reuses `ff_scoring.detect_cadence` for empirical cadence — cadence detection
is not re-derived here.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .ff_scoring import detect_cadence

CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365}

# Class 1 — ghost (wrong-year duplicate) rows.
GHOST_YEAR_TARGETS = (365, 366)   # leap-year aware
GHOST_YEAR_TOL_DAYS = 3

# Class 2 — implausible zeros on LEVEL series (a zero on a VARIATION series —
# m/m, q/q, y/y — is a legitimate frequent value; NOT covered here).
LEVEL_KEYS = frozenset({"interest_rate_decision", "unemployment_rate"})

PROPOSAL_COLUMNS = ["currency", "indicator_key", "name_raw", "release_dt",
                    "reason", "paired_with_dt", "confidence", "detail"]


def _close(a, b, eps: float = 1e-9) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) < eps


def _has_close_neighbor(g: pd.DataFrame, at_dt: pd.Timestamp, exclude_dts: set,
                        window_days: float) -> bool:
    nb = g[(~g["release_dt"].isin(exclude_dts)) & (g["release_dt"] != at_dt)
          & g["actual"].notna()]
    if nb.empty:
        return False
    gaps = (nb["release_dt"] - at_dt).abs().dt.total_seconds() / 86400
    return bool((gaps <= window_days).any())


def detect_ghost_rows(df: pd.DataFrame,
                      cadence_map: Optional[dict] = None) -> pd.DataFrame:
    """`df` columns required: currency, indicator_key, canonical_id, name_raw,
    release_dt, actual, forecast, previous. One row per calendar print; grouped
    by `canonical_id` (the precise structural series identity — see module
    docstring on why NOT indicator_key: two canonical_ids can share one
    indicator_key, e.g. AUD retail_sales's "Retail Sales m/m" ->
    "Household Spending m/m" continuation, and must never be cross-compared).

    A row is a ghost iff BOTH hold (intersection, not union):
      (a) triple-identical (actual, forecast, previous) with another print of
          the SAME canonical_id at 365 or 366 days (+/- GHOST_YEAR_TOL_DAYS)
      (b) that other print is not the row's only close neighbor: the OLDER
          row of the pair has some OTHER real print of the same canonical_id
          within half the empirical cadence window — a genuine cadence
          violation, evidence something extra was inserted near it.
    (a) alone false-positives on a genuinely stable series (a rate/PMI that
    legitimately repeats its exact triple a year later). (b) alone catches
    unrelated DST/placeholder duplicates. Only the intersection isolates the
    wrong-year bug. The GHOST is the OLDER row of a qualifying pair.

    `cadence_map`, if given, is {canonical_id: cadence_str} pre-computed
    overrides; otherwise cadence is detected per group via
    `ff_scoring.detect_cadence` on that group's own printed dates.
    """
    records = []
    for cid, g in df.groupby("canonical_id"):
        g = g.sort_values("release_dt").reset_index(drop=True)
        printed = g[g["actual"].notna()]
        if cadence_map and cid in cadence_map:
            cadence = cadence_map[cid]
        else:
            cadence = detect_cadence(printed["release_dt"]) if len(printed) >= 2 else "unknown"
        cad_days = CADENCE_DAYS.get(cadence)
        half_window = (cad_days / 2.0) if cad_days else None

        n = len(g)
        for i in range(n):
            ri = g.loc[i]
            if pd.isna(ri["actual"]):
                continue
            for j in range(i + 1, n):
                rj = g.loc[j]
                gap = (rj["release_dt"] - ri["release_dt"]).days
                if gap > max(GHOST_YEAR_TARGETS) + GHOST_YEAR_TOL_DAYS:
                    break
                cond_a = (any(abs(gap - t) <= GHOST_YEAR_TOL_DAYS for t in GHOST_YEAR_TARGETS)
                         and _close(ri["actual"], rj["actual"])
                         and _close(ri["forecast"], rj["forecast"])
                         and _close(ri["previous"], rj["previous"]))
                if not cond_a:
                    continue
                cond_b = half_window is not None and _has_close_neighbor(
                    g, ri["release_dt"], {ri["release_dt"], rj["release_dt"]}, half_window)
                if not cond_b:
                    continue
                records.append({
                    "currency": ri["currency"], "indicator_key": ri["indicator_key"],
                    "name_raw": ri["name_raw"], "release_dt": ri["release_dt"],
                    "reason": "ghost_wrong_year", "paired_with_dt": rj["release_dt"],
                    "confidence": "high",
                    "detail": f"canonical_id={cid}, gap={gap}d, real print at {rj['release_dt']}",
                })
    return pd.DataFrame(records, columns=PROPOSAL_COLUMNS)


def detect_implausible_zeros(df: pd.DataFrame,
                             level_keys: frozenset = LEVEL_KEYS) -> pd.DataFrame:
    """`df` columns required: currency, indicator_key, name_raw, release_dt,
    actual, forecast, previous. Restricted to `level_keys` (LEVEL series —
    a policy rate, an unemployment rate — where 0.0 is near-certainly a
    corrupted "Data Not Loaded" placeholder, unlike a VARIATION series m/m|
    q/q|y/y where 0.0 is a routine legitimate reading).

    actual==0.0 is flagged iff the NEAREST non-zero print of the SAME key
    both BEFORE and AFTER it (skipping over any other zero rows in between,
    so a run of several consecutive corrupted zeros is caught as a whole, not
    just an isolated one) are both non-zero and differ from zero by more
    than the series' own typical step (median abs diff between consecutive
    non-zero actuals, floor 0.1).

    A genuine move-to-zero-and-hold (SNB cutting to 0.00% in 2025-06 with no
    later print yet) has NO non-zero print after it at all -> `next_idx`
    empty -> not flagged, by construction, not a special case.
    """
    records = []
    for (ccy, key), g in df.groupby(["currency", "indicator_key"]):
        if key not in level_keys:
            continue
        printed = g[g["actual"].notna()].sort_values("release_dt").reset_index(drop=True)
        if len(printed) < 3:
            continue
        nonzero = printed[printed["actual"] != 0.0]
        diffs = nonzero["actual"].diff().abs().dropna()
        step_floor = max(float(diffs.median()) if len(diffs) else 0.0, 0.1)

        for i in range(len(printed)):
            if printed.loc[i, "actual"] != 0.0:
                continue
            prev_idx = next((k for k in range(i - 1, -1, -1)
                            if printed.loc[k, "actual"] != 0.0), None)
            next_idx = next((k for k in range(i + 1, len(printed))
                            if printed.loc[k, "actual"] != 0.0), None)
            if prev_idx is None or next_idx is None:
                continue
            prev_a = printed.loc[prev_idx, "actual"]
            next_a = printed.loc[next_idx, "actual"]
            if abs(prev_a) > step_floor and abs(next_a) > step_floor:
                records.append({
                    "currency": ccy, "indicator_key": key,
                    "name_raw": printed.loc[i, "name_raw"],
                    "release_dt": printed.loc[i, "release_dt"],
                    "reason": "implausible_zero_level", "paired_with_dt": None,
                    "confidence": "high",
                    "detail": f"neighbors: prev={prev_a} ({printed.loc[prev_idx,'release_dt']}), "
                             f"next={next_a} ({printed.loc[next_idx,'release_dt']}), "
                             f"typical_step={round(step_floor, 4)}",
                })
    return pd.DataFrame(records, columns=PROPOSAL_COLUMNS)


def build_quarantine_proposal(ghost_df: pd.DataFrame,
                              zero_df: pd.DataFrame) -> pd.DataFrame:
    """Union (not concatenation with duplicates) of the two detectors, one row
    per (currency, indicator_key, name_raw, release_dt) — a row flagged by
    both keeps the ghost-row entry (checked first in the concat order)."""
    if ghost_df.empty and zero_df.empty:
        return pd.DataFrame(columns=PROPOSAL_COLUMNS)
    combined = pd.concat([df for df in (ghost_df, zero_df) if not df.empty], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["currency", "indicator_key", "name_raw", "release_dt"], keep="first")
    return combined.sort_values(["currency", "indicator_key", "release_dt"]).reset_index(drop=True)
