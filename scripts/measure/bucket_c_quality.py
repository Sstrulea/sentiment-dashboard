"""eval/bucket-c-candidates — FAZA 1: series quality (gaps, zero-prints, outliers)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import normalize_ff_value, jblanked_to_utc  # noqa: E402

ARCHIVE = ROOT / "data" / "archive" / "ff_calendar_range.json"

CANDIDATES = [
    ("growth", "AUD", "Private Capital Expenditure q/q", "quarterly"), ("growth", "AUD", "Company Operating Profits q/q", "quarterly"),
    ("growth", "CAD", "Trade Balance", "monthly"), ("growth", "CHF", "Trade Balance", "monthly"),
    ("growth", "GBP", "Industrial Production m/m", "monthly"), ("growth", "JPY", "Trade Balance", "monthly"),
    ("growth", "JPY", "Prelim Industrial Production m/m", "monthly"), ("growth", "JPY", "Core Machinery Orders m/m", "monthly"),
    ("growth", "JPY", "Capital Spending q/y", "quarterly"), ("growth", "NZD", "Trade Balance", "monthly"),
    ("growth", "USD", "Trade Balance", "monthly"), ("growth", "USD", "Durable Goods Orders m/m", "monthly"),
    ("growth", "USD", "Personal Spending m/m", "monthly"), ("growth", "USD", "Personal Income m/m", "monthly"),
    ("growth", "USD", "Industrial Production m/m", "monthly"),
    ("inflation", "AUD", "Import Prices q/q", "quarterly"), ("inflation", "GBP", "RPI y/y", "monthly"),
    ("inflation", "JPY", "Tokyo Core CPI y/y", "monthly"), ("inflation", "JPY", "SPPI y/y", "monthly"),
    ("inflation", "JPY", "Prelim GDP Price Index y/y", "quarterly"), ("inflation", "USD", "Prelim UoM Inflation Expectations", "monthly"),
    ("inflation", "USD", "Import Prices m/m", "monthly"), ("inflation", "USD", "Advance GDP Price Index q/q", "quarterly"),
    ("labour", "CAD", "Labor Productivity q/q", "quarterly"), ("labour", "USD", "Prelim Nonfarm Productivity q/q", "quarterly"),
    ("labour", "USD", "Prelim Unit Labor Costs q/q", "quarterly"),
    ("monetary", "AUD", "Private Sector Credit m/m", "monthly"), ("monetary", "GBP", "M4 Money Supply m/m", "monthly"),
    ("monetary", "GBP", "Net Lending to Individuals m/m", "monthly"), ("monetary", "GBP", "MPC Official Bank Rate Votes", "quarterly"),
    ("monetary", "JPY", "Monetary Base y/y", "monthly"), ("monetary", "JPY", "M2 Money Stock y/y", "monthly"),
    ("monetary", "JPY", "Bank Lending y/y", "monthly"),
]

EXPECTED_GAP = {"weekly": 7, "monthly": 30, "quarterly": 91}


def main():
    data = json.load(open(ARCHIVE))
    rows = []
    for cat, ccy, name, cadence in CANDIDATES:
        recs = [e for e in data if e.get("Currency") == ccy and e.get("Name") == name]
        parsed = []
        for e in recs:
            dt = jblanked_to_utc(e.get("Date", ""))
            if dt is None:
                continue
            try:
                a = normalize_ff_value(e.get("Actual"), name=name)
                f = normalize_ff_value(e.get("Forecast"), name=name)
            except ValueError:
                continue
            parsed.append({"dt": dt, "actual": a, "forecast": f})
        df = pd.DataFrame(parsed).sort_values("dt")
        n = len(df)
        gaps = df["dt"].diff().dt.days.dropna()
        expected = EXPECTED_GAP[cadence]
        big_gaps = int((gaps > expected * 1.8).sum())
        max_gap = int(gaps.max()) if len(gaps) else 0
        zero_actual = int((df["actual"] == 0.0).sum())
        zero_forecast = int((df["forecast"] == 0.0).sum())
        # outliers: |z| of actual relative to its own series (robust: median/MAD)
        med = df["actual"].median()
        mad = (df["actual"] - med).abs().median()
        outliers = int(((df["actual"] - med).abs() > 6 * mad).sum()) if mad and mad > 0 else 0
        rows.append({
            "category": cat, "currency": ccy, "name_raw": name, "cadence": cadence, "n": n,
            "expected_gap_days": expected, "max_gap_days": max_gap, "big_gaps_count": big_gaps,
            "zero_actual_count": zero_actual, "zero_forecast_count": zero_forecast,
            "outlier_count_6mad": outliers,
        })
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "docs" / "bucket-c-quality.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
