"""measure/coverage-asymmetry — historical reconstruction of category
score_precise/coverage from data/economic_calendar_ff.parquet.

Investigation tooling only (not wired into production). Mirrors
src/ff_scoring.py::score_calendar exactly (Phase 2 precedent), with one
addition required for HISTORICAL (not "now") as_of values: manual
truncation of the calendar frame to release_dt <= as_of.
compute_indicator_score has only a LOWER bound on recency (max_age_days
cutoff); it has no upper bound against `as_of`, because in production the
live parquet never contains rows released after "now". Reconstructing a
PAST as_of from TODAY's full parquet breaks that implicit assumption
unless done explicitly here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402
from src.economic_compute import compute_currency_scorecard  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
QUARANTINE_PARQUET = ROOT / "data" / "ff_quarantine.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"

OUR_CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
CALC_CATEGORIES = ["growth", "inflation", "labour"]  # monetary excluded — not calendar-driven


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def build_full_scoring_frame(ff_parquet: Path = FF_PARQUET,
                              quarantine_parquet: Path = QUARANTINE_PARQUET) -> pd.DataFrame:
    """The FULL (all dates) canonical scoring frame — mirrors
    economic_render._load_calendar_frame's FF branch exactly, including the
    FRED-quarantine anti-join."""
    ffdf = pd.read_parquet(ff_parquet)
    ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
    cal = to_scoring_frame(ffdf, build_matcher())
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    if quarantine_parquet.exists() and len(cal):
        q = pd.read_parquet(quarantine_parquet)
        if len(q):
            q["datetime_utc"] = pd.to_datetime(q["datetime_utc"])
            key = ["currency", "indicator_key", "release_dt"]
            qkey = q.rename(columns={"datetime_utc": "release_dt"})[key]
            cal = cal.merge(qkey.assign(_q=1), on=key, how="left")
            cal = cal[cal["_q"].isna()].drop(columns="_q")
    return cal


def scorecards_at(full_cal: pd.DataFrame, as_of: pd.Timestamp,
                   indicators_cfg: dict, instruments_cfg: dict) -> dict:
    """Category score_precise/coverage per currency, AS THEY WOULD HAVE BEEN
    at `as_of` — truncates to release_dt <= as_of before calling the real
    compute_currency_scorecard (rate_entry=None: monetary is out of scope,
    see module docstring; sentiment/trend are instrument-layer, not needed
    for per-currency category numbers)."""
    as_of = pd.Timestamp(as_of)
    trunc = full_cal[full_cal["release_dt"] <= as_of]
    out = {}
    for ccy in OUR_CCYS:
        card = compute_currency_scorecard(trunc, ccy, indicators_cfg, instruments_cfg, as_of,
                                          rate_entry=None)
        out[ccy] = {cat: card["categories"].get(cat, {"score_precise": 0.0, "coverage": 0})
                    for cat in CALC_CATEGORIES}
    return out


if __name__ == "__main__":
    ind = load_yaml(INDICATORS_YAML)
    inst = load_yaml(INSTRUMENTS_YAML)
    full_cal = build_full_scoring_frame()
    print(f"Full scoring frame: {len(full_cal)} rows, "
          f"{full_cal['release_dt'].min()} .. {full_cal['release_dt'].max()}")
