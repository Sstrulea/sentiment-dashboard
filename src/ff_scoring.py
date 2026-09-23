"""PHASE 2 (isolated) — score the FF calendar through the EXISTING scoring code.

Reuses `economic_compute.build_payload` + the `economic_fetch` matcher by IMPORT
ONLY — no production file is modified, nothing is wired into the live pipeline
(that is Phase 3). Provides the FF-canonical → MT5-calendar-schema bridge so the
identical scoring runs on the FF source, and a thin z-score baseline that is
rebuilt exclusively from FF (build_payload computes the trailing-K sigma live from
whatever calendar frame it is given — here, only FF rows).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .econ_calendar_ff import JB_NOT_LOADED, ensure_provenance_columns
from .economic_compute import build_payload
from .economic_fetch import CompiledMatcher, _load_indicators_cfg

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"


def load_can_be_zero(indicators_cfg: Optional[dict] = None) -> set[str]:
    """Set of indicator_keys where 0.0 is a LEGITIMATE reading BY CONFIG
    (can_be_zero: true) — net-change/level indicators (employment_change,
    interest_rate_decision) with no period-transform suffix to derive it from.

    This is only ONE of two orthogonal reasons `to_scoring_frame` may treat a
    0.0 actual as legitimate, not the whole decision (fix/can-be-zero-transform,
    2026-08): the OTHER is the row's own REAL transform (extract_period_suffix
    on name_raw) being m/m or q/q, gated by a Quality/Strength guard — see
    `to_scoring_frame`'s docstring. `cpi_yoy` is correctly absent from this SET
    for every currency (0.0 on a y/y print is still suspect by config), even
    though CHF/CAD/... cpi_yoy zeros ARE now recoverable via the suffix path."""
    cfg = indicators_cfg or _load_indicators_cfg()
    return {k for k, v in (cfg.get("indicators", {}) or {}).items()
            if (v or {}).get("can_be_zero") is True}

# matcher is country-keyed; map our currency -> the matcher's country label.
CCY2COUNTRY = {
    "USD": "United States", "EUR": "European Union", "GBP": "United Kingdom",
    "JPY": "Japan", "AUD": "Australia", "NZD": "New Zealand",
    "CAD": "Canada", "CHF": "Switzerland",
}

SCORING_COLUMNS = ["currency", "indicator_key", "release_dt", "actual",
                   "consensus", "previous", "source", "name_raw"]


def build_matcher() -> CompiledMatcher:
    return CompiledMatcher(_load_indicators_cfg().get("matcher", {}) or {})


def effective_consensus(forecast, origin) -> float:
    """Audit 2.2 — the ONE consensus rule, from the provenance recorded at ingest:
      ff        valid, 0.0 included (FF printed "0.0%": a real consensus)
      manual    valid
      ff_blank  NaN (FF printed "": no consensus)
      jb / None a JBlanked 0.0 is its "no forecast" placeholder -> NaN; any other
                value is kept (history before the FF archive)."""
    if forecast is None or pd.isna(forecast):
        return float("nan")
    if origin in ("ff", "manual"):
        return float(forecast)
    if origin == "ff_blank":
        return float("nan")
    return float("nan") if float(forecast) == 0.0 else float(forecast)


def actual_is_placeholder(actual, jb_status) -> bool:
    """Audit 2.3 — the ONE placeholder rule: an actual of 0.0 is JBlanked's
    unreleased placeholder iff the newest JB payload carrying the event said
    "Data Not Loaded" (persisted per row as jb_status). JB's Good/Bad Data label
    is a direction tag, not a quality flag (C1), and is ignored."""
    return actual is not None and not pd.isna(actual) and float(actual) == 0.0 \
        and jb_status == JB_NOT_LOADED


def to_scoring_frame(ff_df: pd.DataFrame, matcher: Optional[CompiledMatcher] = None,
                     can_be_zero: Optional[set[str]] = None) -> pd.DataFrame:
    """FF canonical DataFrame → MT5-schema calendar frame that build_payload accepts.

    indicator_key = matcher.match(country(currency), name_canonical). Rows whose
    name_canonical is not modeled for that country are DROPPED (parity with MT5).
    Every output row carries source='ff' — a series baseline can never mix MT5 rows.

    Zeros and consensus (audit 2026-09-23, phase 2): provenance is recorded at
    ingest, never guessed here.
      actual     0.0 -> NaN iff actual_is_placeholder(actual, jb_status);
      consensus  effective_consensus(forecast, forecast_origin).
    The earlier heuristics (can_be_zero config route, m/m|q/q suffix route,
    Quality/Strength flagged_bad OR across payloads) are gone: one mechanism
    per decision.

    NEW-DUPLICATE GUARD (kept — a duplicate-handling decision, not a zero one): a
    (canonical_id, calendar date) group that had <2 valid actuals under the OLD
    config-only contract (0.0 valid only for can_be_zero keys) and has >=2 now,
    solely because a 0.0 became valid, is corrected: identical actuals collapse
    to one row, divergent ones are all excluded (fail-safe, no tiebreak). A group
    already >=2 valid under the old contract is left untouched.
    """
    matcher = matcher or build_matcher()
    cbz = load_can_be_zero() if can_be_zero is None else can_be_zero
    ff_df = ensure_provenance_columns(ff_df)
    nan = float("nan")
    recs: list[dict] = []
    meta: list[tuple[str, "date", bool]] = []  # (canonical_id, date, valid under OLD contract)
    q_actual = q_cons = 0
    for r in ff_df.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        release_date = pd.Timestamp(r.datetime_utc).date()
        old_valid = pd.notna(r.actual) and not (key not in cbz and r.actual == 0.0)
        actual = r.actual
        if actual_is_placeholder(actual, r.jb_status):
            actual = nan
            q_actual += 1
        consensus = effective_consensus(r.forecast, r.forecast_origin)
        if pd.notna(r.forecast) and pd.isna(consensus):
            q_cons += 1
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
            "name_raw": r.name_raw,
        })
        meta.append((r.canonical_id, release_date, old_valid))

    groups: dict[tuple[str, "date"], list[int]] = defaultdict(list)
    for i, (cid, d, _old_valid) in enumerate(meta):
        groups[(cid, d)].append(i)
    for (cid, d), idxs in groups.items():
        valid_before = sum(1 for i in idxs if meta[i][2])
        valid_after = sum(1 for i in idxs if pd.notna(recs[i]["actual"]))
        if valid_before >= 2 or valid_after < 2:
            continue
        valid_idxs = [i for i in idxs if pd.notna(recs[i]["actual"])]
        actuals = [recs[i]["actual"] for i in valid_idxs]
        currency = recs[idxs[0]]["currency"]
        if len(set(actuals)) == 1:
            valid_idxs.sort(key=lambda i: recs[i]["release_dt"])
            for i in valid_idxs[1:]:
                recs[i]["actual"] = nan
            log.info("zero rule: new-duplicate group (canonical_id=%s, %s, %s) actuals=%s "
                     "IDENTICAL → collapsed %d rows to 1.", cid, currency, d, actuals, len(valid_idxs))
        else:
            for i in valid_idxs:
                recs[i]["actual"] = nan
            log.info("zero rule: new-duplicate group (canonical_id=%s, %s, %s) actuals=%s "
                     "DIVERGENT, no tiebreak → excluded all %d rows (fail-safe).",
                     cid, currency, d, actuals, len(valid_idxs))

    if q_actual or q_cons:
        log.info("Provenance rules: %d actual 0.0 placeholder(s) (jb_status Data Not Loaded) "
                 "and %d consensus value(s) (ff_blank / jb 0.0) -> NaN.", q_actual, q_cons)
    return pd.DataFrame(recs, columns=SCORING_COLUMNS)


# z-history sufficiency thresholds by DETECTED cadence (not a uniform monthly rule).
CADENCE_THRESHOLD = {"weekly": 24, "monthly": 24, "quarterly": 8, "annual": 3, "unknown": 24}
# interest_rate_decision is display-only (weight 0, scored via the FRED rate engine,
# not calendar z-surprise) → excluded from the z-history threshold discussion entirely.
NON_ZSCORED_INDICATORS = {"interest_rate_decision"}


def detect_cadence(dates) -> str:
    """Classify a series' cadence from the MEDIAN inter-print interval (days):
    <=10 weekly · <=45 monthly · <=135 quarterly · else annual."""
    d = pd.to_datetime(pd.Series(sorted(set(pd.to_datetime(dates)))))
    if len(d) < 2:
        return "unknown"
    med = float(d.diff().dt.days.dropna().median())
    if med <= 10:
        return "weekly"
    if med <= 45:
        return "monthly"
    if med <= 135:
        return "quarterly"
    return "annual"


def load_configs() -> tuple[dict, dict]:
    with open(INDICATORS_YAML) as f:
        ind = yaml.safe_load(f) or {}
    with open(INSTRUMENTS_YAML) as f:
        inst = yaml.safe_load(f) or {}
    return ind, inst


def score_calendar(calendar_df: pd.DataFrame, as_of: pd.Timestamp,
                   rate_scores: Optional[dict] = None) -> dict:
    """Run the EXISTING build_payload on a calendar frame (MT5 or FF-bridged).
    sentiment_cells=None → category scores are unaffected by COT (they are computed
    pre-sentiment); the before/after table compares only calendar-driven categories."""
    ind, inst = load_configs()
    return build_payload(calendar_df, ind, inst, as_of=as_of,
                         rate_scores=rate_scores or None, sentiment_cells=None)
