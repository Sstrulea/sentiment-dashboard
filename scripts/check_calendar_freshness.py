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
from src.alert_exceptions import (  # noqa: E402
    load_exceptions,
    classify_stale_rows,
    find_orphaned_exceptions,
)

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
JB_RAW_DIR = ROOT / "data" / "jb_raw"
ALERT_EXCEPTIONS_YAML = ROOT / "config" / "alert_exceptions.yaml"

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

    exceptions = load_exceptions(ALERT_EXCEPTIONS_YAML)
    stale_row_list = stale_rows.to_dict("records")
    classified = classify_stale_rows(stale_row_list, exceptions, as_of, scope="calendar")
    classified_by_key = {(c["currency"], c["indicator_key"]): c for c in classified}

    any_gate_alert = False
    for ccy in report["stale_currencies"]:
        for ind in report["by_currency"][ccy]["stale_indicators"]:
            key = (ccy, ind["indicator_key"])
            extra = jb_check.get(key, {"status": "not_checked (no jb_raw payloads)"})
            c = classified_by_key[key]
            print(f"  {ccy} {ind['indicator_key']}: last={ind['last_date']} "
                 f"age={ind['age_days']}d (threshold {ind['threshold_days']}d) -- {extra}")
            if c["exception_note"]:
                print(f"    {c['exception_note']}")
            if c["gate"] == "alert":
                any_gate_alert = True

    orphans = find_orphaned_exceptions(stale_row_list, exceptions, scope="calendar")
    if orphans:
        print()
        print(f"NOTE: {len(orphans)} exception(s) in {ALERT_EXCEPTIONS_YAML.name} no longer "
             f"apply (series not currently stale) — REMOVABLE, review before it masks a recurrence:")
        for exc in orphans:
            print(f"  {exc['currency']} {exc['indicator']} (review_by was {exc['review_by']})")

    if not any_gate_alert:
        print()
        print("OK — all stale series are covered by an active exception.")
        return 0

    print("FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
