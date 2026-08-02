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

from .econ_calendar_ff import extract_period_suffix
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
                   "consensus", "previous", "source"]


def build_matcher() -> CompiledMatcher:
    return CompiledMatcher(_load_indicators_cfg().get("matcher", {}) or {})


def to_scoring_frame(ff_df: pd.DataFrame, matcher: Optional[CompiledMatcher] = None,
                     can_be_zero: Optional[set[str]] = None,
                     flagged_bad: Optional[dict[tuple[str, str, "date"], bool]] = None
                     ) -> pd.DataFrame:
    """FF canonical DataFrame → MT5-schema calendar frame that build_payload accepts.

    indicator_key = matcher.match(country(currency), name_canonical). Rows whose
    name_canonical is not modeled for that country (e.g. JPY PPI — the MT5 matcher
    has no JPY PPI pattern either) are DROPPED, giving parity with the MT5 pipeline.
    Every output row carries source='ff' — a series baseline can never mix MT5 rows.

    ZERO-PLACEHOLDER QUARANTINE, actual side (fix/can-be-zero-transform, 2026-08):
    a 0.0 `actual` is legitimate — kept, not nulled — iff EITHER of two orthogonal
    reasons holds (a UNION, not a replacement of the old rule):
      (a) `can_be_zero[indicator_key]` is True by CONFIG — net-change/level
          indicators (employment_change, interest_rate_decision) that have no
          period-transform suffix to derive legitimacy from at all;
      (b) the row's REAL transform — `extract_period_suffix(r.name_raw)` — is
          m/m or q/q. Catches a short-transform series aliased onto a y/y-named
          canonical slot (config/ff_aliases.yaml `# xf`, e.g. CHF cpi_yoy fed by
          "CPI m/m") where 0.0 IS a legitimate flat print, same reasoning as (a),
          just derived from the feed instead of hand-maintained per indicator_key.
    (b) is gated by `flagged_bad`: even a real-transform-shaped zero can be a
    JBlanked-side placeholder or questionable print (Quality/Strength ==
    'Bad Data' or 'Data Not Loaded' — see jb_actuals.build_flagged_bad_lookup,
    measured 2026-08: 73/107 suffix-only candidates were one or the other, only
    34/107 were clean). `flagged_bad.get((currency, name_raw, release_date),
    True)` — a MISSING key defaults to flagged/blocked, never assumed clean
    (measured 0/107 missing today; the default protects any future coverage
    gap). `flagged_bad=None` (the default) disables (b) ENTIRELY — callers that
    don't pass it get EXACTLY today's config-only behavior, unchanged; only
    `extract_period_suffix` — fail-loud on an unrecognized slash-shaped suffix,
    see its docstring — is ever invoked, and only for a 0.0 actual on a
    non-can_be_zero indicator with flagged_bad provided, since that is the only
    place an unrecognized suffix could silently change the outcome.

    Consensus quarantine is UNCHANGED — config-only (a), no suffix widening, no
    flagged_bad guard. Out of scope for this fix.

    NEW-DUPLICATE GUARD (fix/can-be-zero-transform, 2026-08 audit): a
    (canonical_id, calendar date) group that goes from <2 valid `actual` rows
    under the OLD contract to >=2 under the NEW one — SOLELY because widening
    recovered a 0.0 that duplicates (±1h DST artifact, or same-day
    re-publication) a row never deduped by clean_jblanked_actuals (the
    pre-07-12 archive backfill doesn't go through it) — is corrected: identical
    duplicate actuals collapse to one row; DIVERGENT ones are BOTH excluded
    (fail-safe, no tiebreak guess). Scoped NARROWLY to this transition — a
    group already >=2 valid BEFORE widening (measured 2026-08: 143 such groups
    already in production) is left untouched; that is a pre-existing archive
    integrity issue, out of scope here (see docs/). This is event-level
    grouping, NOT a reintroduction of "actual != 0.0" value filtering.
    """
    matcher = matcher or build_matcher()
    cbz = load_can_be_zero() if can_be_zero is None else can_be_zero
    nan = float("nan")
    recs: list[dict] = []
    meta: list[tuple[str, "date", bool]] = []  # (canonical_id, date, valid under OLD contract)
    q_actual = q_cons = q_widened = 0
    for r in ff_df.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        actual, consensus = r.actual, r.forecast   # FF forecast → MT5 'consensus'
        release_date = pd.Timestamp(r.datetime_utc).date()
        old_valid = pd.notna(r.actual) and not (key not in cbz and r.actual == 0.0)
        if key not in cbz:
            if actual == 0.0:
                widened = False
                if flagged_bad is not None and extract_period_suffix(r.name_raw) in ("m/m", "q/q"):
                    is_bad = flagged_bad.get((r.currency, r.name_raw, release_date), True)
                    widened = not is_bad
                if widened:
                    q_widened += 1
                else:
                    actual = nan
                    q_actual += 1
            if consensus == 0.0:
                consensus = nan
                q_cons += 1
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
        })
        meta.append((r.canonical_id, release_date, old_valid))

    if flagged_bad is not None:
        groups: dict[tuple[str, "date"], list[int]] = defaultdict(list)
        for i, (cid, d, _old_valid) in enumerate(meta):
            groups[(cid, d)].append(i)
        for (cid, d), idxs in groups.items():
            valid_before = sum(1 for i in idxs if meta[i][2])
            valid_after = sum(1 for i in idxs if pd.notna(recs[i]["actual"]))
            if valid_before >= 2 or valid_after < 2:
                continue   # pre-existing duplicate (untouched) or no new duplication
            valid_idxs = [i for i in idxs if pd.notna(recs[i]["actual"])]
            actuals = [recs[i]["actual"] for i in valid_idxs]
            currency = recs[idxs[0]]["currency"]
            if len(set(actuals)) == 1:
                valid_idxs.sort(key=lambda i: recs[i]["release_dt"])
                for i in valid_idxs[1:]:
                    recs[i]["actual"] = nan
                log.info("can_be_zero widening: new-duplicate group (canonical_id=%s, %s, %s) "
                        "actuals=%s IDENTICAL → collapsed %d rows to 1.",
                        cid, currency, d, actuals, len(valid_idxs))
            else:
                for i in valid_idxs:
                    recs[i]["actual"] = nan
                log.info("can_be_zero widening: new-duplicate group (canonical_id=%s, %s, %s) "
                        "actuals=%s DIVERGENT, no tiebreak → excluded all %d rows (fail-safe).",
                        cid, currency, d, actuals, len(valid_idxs))

    if q_actual or q_cons or q_widened:
        log.info("Zero-placeholder quarantine: %d actual==0.0 + %d consensus==0.0 → NaN, "
                 "%d actual==0.0 recovered via real-transform suffix (indicators where "
                 "can_be_zero is False by config).", q_actual, q_cons, q_widened)
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
