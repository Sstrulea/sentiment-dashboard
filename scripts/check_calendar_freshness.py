"""fix/calendar-freshness-per-ccy — standalone per-currency calendar gate.

NOT wired into any `.github/workflows/*.yml` (out of scope, mirrors
scripts/check_freshness.py's own pattern) — complete, tested, ready for a
future, separate change to add one workflow step.

Unlike `price`, `calendar` IS refreshed by GitHub Actions
(`.github/workflows/econ-refresh.yml`'s `python -m src.ff_refresh` +
`python -m src.jb_actuals` steps) — a stale calendar badge here is
actionable from a cloud job, so this script's exit code is meant to gate a
workflow the same way (see scripts/check_freshness.py's CLOUD_REFRESHED_SOURCES).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.calendar_freshness_guard import (  # noqa: E402
    per_currency_indicator_freshness,
    currency_freshness_report,
    check_pending_actuals_in_jb_raw,
)

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
JB_RAW_DIR = ROOT / "data" / "jb_raw"

CCY2COUNTRY = {"USD": "United States", "EUR": "European Union", "GBP": "United Kingdom",
              "JPY": "Japan", "AUD": "Australia", "NZD": "New Zealand",
              "CAD": "Canada", "CHF": "Switzerland"}


def _load_calendar_with_indicator_key(ind_cfg: dict) -> pd.DataFrame:
    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}))
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    return df.rename(columns={"datetime_utc": "release_dt"})


def main() -> int:
    as_of = pd.Timestamp.utcnow().tz_localize(None)

    with open(INDICATORS_YAML) as f:
        ind_cfg = yaml.safe_load(f)

    cal = _load_calendar_with_indicator_key(ind_cfg)
    per_indicator = per_currency_indicator_freshness(cal, ind_cfg, as_of)
    report = currency_freshness_report(per_indicator)

    print("=== Calendar freshness report (per currency, per-indicator threshold) ===")
    if not report["any_stale"]:
        print("OK — all currencies fresh (or no_data, no scored indicator overdue).")
        return 0

    print(f"STALE currencies: {report['stale_currencies']}")

    # For every stale (currency, indicator), check whether jb_raw already
    # holds a newer actual that never reached the parquet (real ingest gap)
    # vs. genuinely nothing new available in the retained window.
    stale_rows = pd.DataFrame([
        {"currency": ccy, "indicator_key": ind["indicator_key"]}
        for ccy in report["stale_currencies"]
        for ind in report["by_currency"][ccy]["stale_indicators"]
    ])
    parquet_last_dates = {
        (ccy, ind["indicator_key"]): pd.Timestamp(ind["last_date"])
        for ccy in report["stale_currencies"]
        for ind in report["by_currency"][ccy]["stale_indicators"]
    }
    jb_check = {}
    if JB_RAW_DIR.exists() and list(JB_RAW_DIR.glob("jb_range_*.json")):
        from src.econ_calendar_ff import parse_jblanked_range
        from src.ff_scoring import build_matcher
        jb_check = check_pending_actuals_in_jb_raw(
            JB_RAW_DIR, parse_jblanked_range, build_matcher, stale_rows, parquet_last_dates)

    for ccy in report["stale_currencies"]:
        for ind in report["by_currency"][ccy]["stale_indicators"]:
            key = (ccy, ind["indicator_key"])
            extra = jb_check.get(key, {"status": "not_checked (no jb_raw payloads)"})
            print(f"  {ccy} {ind['indicator_key']}: last={ind['last_date']} "
                 f"age={ind['age_days']}d (threshold {ind['threshold_days']}d) -- {extra}")

    print("FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
