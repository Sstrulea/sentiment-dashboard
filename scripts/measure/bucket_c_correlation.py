"""eval/bucket-c-candidates — FAZA 1: informational independence.

For each of the 33 bucket-C candidates, correlates its surprise series
(actual - forecast, from data/archive/ff_calendar_range.json) against
every EXISTING scored indicator in the same (currency, category), over
their common monthly history. Investigation only — no production code
touched.

Alignment: both series are resampled to one value per calendar MONTH
(the print's own surprise that month; if a currency/indicator prints more
than once in a month, the LAST is kept) — different indicators release on
different days, so exact-date alignment is not meaningful; month is the
finest grid both a monthly candidate and a monthly existing indicator
share.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import normalize_ff_value, jblanked_to_utc, OUR_CCYS  # noqa: E402
from scripts.measure.reconstruct import build_full_scoring_frame  # noqa: E402

ARCHIVE = ROOT / "data" / "archive" / "ff_calendar_range.json"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"

CANDIDATES = [
    ("growth", "AUD", "Private Capital Expenditure q/q"), ("growth", "AUD", "Company Operating Profits q/q"),
    ("growth", "CAD", "Trade Balance"), ("growth", "CHF", "Trade Balance"),
    ("growth", "GBP", "Industrial Production m/m"), ("growth", "JPY", "Trade Balance"),
    ("growth", "JPY", "Prelim Industrial Production m/m"), ("growth", "JPY", "Core Machinery Orders m/m"),
    ("growth", "JPY", "Capital Spending q/y"), ("growth", "NZD", "Trade Balance"),
    ("growth", "USD", "Trade Balance"), ("growth", "USD", "Durable Goods Orders m/m"),
    ("growth", "USD", "Personal Spending m/m"), ("growth", "USD", "Personal Income m/m"),
    ("growth", "USD", "Industrial Production m/m"),
    ("inflation", "AUD", "Import Prices q/q"), ("inflation", "GBP", "RPI y/y"),
    ("inflation", "JPY", "Tokyo Core CPI y/y"), ("inflation", "JPY", "SPPI y/y"),
    ("inflation", "JPY", "Prelim GDP Price Index y/y"), ("inflation", "USD", "Prelim UoM Inflation Expectations"),
    ("inflation", "USD", "Import Prices m/m"), ("inflation", "USD", "Advance GDP Price Index q/q"),
    ("labour", "CAD", "Labor Productivity q/q"), ("labour", "USD", "Prelim Nonfarm Productivity q/q"),
    ("labour", "USD", "Prelim Unit Labor Costs q/q"),
    ("monetary", "AUD", "Private Sector Credit m/m"), ("monetary", "GBP", "M4 Money Supply m/m"),
    ("monetary", "GBP", "Net Lending to Individuals m/m"), ("monetary", "GBP", "MPC Official Bank Rate Votes"),
    ("monetary", "JPY", "Monetary Base y/y"), ("monetary", "JPY", "M2 Money Stock y/y"),
    ("monetary", "JPY", "Bank Lending y/y"),
]


def candidate_monthly_surprise(ccy: str, name: str) -> pd.Series:
    data = json.load(open(ARCHIVE))
    rows = [e for e in data if e.get("Currency") == ccy and e.get("Name") == name]
    recs = []
    for e in rows:
        dt = jblanked_to_utc(e.get("Date", ""))
        if dt is None:
            continue
        try:
            a = normalize_ff_value(e.get("Actual"), name=name)
            f = normalize_ff_value(e.get("Forecast"), name=name)
        except ValueError:
            continue
        if pd.isna(a) or pd.isna(f):
            continue
        recs.append({"month": pd.Timestamp(dt).to_period("M"), "surprise": a - f, "dt": dt})
    if not recs:
        return pd.Series(dtype=float)
    df = pd.DataFrame(recs).sort_values("dt")
    return df.groupby("month")["surprise"].last()


def existing_monthly_surprise(full_cal: pd.DataFrame, ccy: str, indicator_key: str) -> pd.Series:
    sub = full_cal[(full_cal["currency"] == ccy) & (full_cal["indicator_key"] == indicator_key)].copy()
    sub = sub[sub["actual"].notna() & sub["consensus"].notna()]
    if sub.empty:
        return pd.Series(dtype=float)
    sub["surprise"] = sub["actual"] - sub["consensus"]
    sub["month"] = pd.to_datetime(sub["release_dt"]).dt.to_period("M")
    sub = sub.sort_values("release_dt")
    return sub.groupby("month")["surprise"].last()


def main():
    ind_cfg = yaml.safe_load(open(INDICATORS_YAML)) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    full_cal = build_full_scoring_frame()

    rows = []
    for cat, ccy, name in CANDIDATES:
        cand_s = candidate_monthly_surprise(ccy, name)
        # existing scored indicators in this (currency, category)
        same_cat_keys = [k for k, cfg in indicators.items() if cfg.get("category") == cat]
        existing_present = full_cal[(full_cal["currency"] == ccy)
                                    & (full_cal["indicator_key"].isin(same_cat_keys))]["indicator_key"].unique()
        if len(existing_present) == 0:
            rows.append({"category": cat, "currency": ccy, "name_raw": name,
                        "existing_indicator": "(none scored)", "n_common_months": 0, "corr": None})
            continue
        for key in sorted(existing_present):
            exist_s = existing_monthly_surprise(full_cal, ccy, key)
            common = cand_s.index.intersection(exist_s.index)
            n = len(common)
            corr = cand_s.loc[common].corr(exist_s.loc[common]) if n >= 6 else None
            rows.append({"category": cat, "currency": ccy, "name_raw": name,
                        "existing_indicator": key, "n_common_months": n,
                        "corr": None if corr is None or pd.isna(corr) else round(float(corr), 3)})

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "docs" / "bucket-c-correlation.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
