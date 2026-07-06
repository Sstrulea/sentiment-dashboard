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
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .economic_compute import build_payload
from .economic_fetch import CompiledMatcher, _load_indicators_cfg

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"


def load_can_be_zero(indicators_cfg: Optional[dict] = None) -> set[str]:
    """Set of indicator_keys where 0.0 is a LEGITIMATE reading (can_be_zero: true).
    All others default to False → 0.0 actual/consensus is a quarantined placeholder."""
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
                   "consensus", "previous", "source"]


def build_matcher() -> CompiledMatcher:
    return CompiledMatcher(_load_indicators_cfg().get("matcher", {}) or {})


def to_scoring_frame(ff_df: pd.DataFrame, matcher: Optional[CompiledMatcher] = None,
                     can_be_zero: Optional[set[str]] = None) -> pd.DataFrame:
    """FF canonical DataFrame → MT5-schema calendar frame that build_payload accepts.

    indicator_key = matcher.match(country(currency), name_canonical). Rows whose
    name_canonical is not modeled for that country (e.g. JPY PPI — the MT5 matcher
    has no JPY PPI pattern either) are DROPPED, giving parity with the MT5 pipeline.
    Every output row carries source='ff' — a series baseline can never mix MT5 rows.

    ZERO-PLACEHOLDER QUARANTINE: for indicators NOT flagged `can_be_zero`, a 0.0
    `actual` or `consensus` is an FF unreleased/missing placeholder (a delayed or
    cancelled release) — set to NaN so it never enters the z-baseline as a fake huge
    surprise (see docs/baseline_variance_audit.csv). Applied to EVERY row, so it
    cleans both the ongoing weekly ingest AND the historical backfill on read.
    """
    matcher = matcher or build_matcher()
    cbz = load_can_be_zero() if can_be_zero is None else can_be_zero
    nan = float("nan")
    recs: list[dict] = []
    q_actual = q_cons = 0
    for r in ff_df.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        actual, consensus = r.actual, r.forecast   # FF forecast → MT5 'consensus'
        if key not in cbz:
            if actual == 0.0:
                actual = nan
                q_actual += 1
            if consensus == 0.0:
                consensus = nan
                q_cons += 1
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
        })
    if q_actual or q_cons:
        log.info("Zero-placeholder quarantine: %d actual==0.0 + %d consensus==0.0 → NaN "
                 "(indicators where can_be_zero is False).", q_actual, q_cons)
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
