"""Section G — rate_expectations data quality per currency (current day).

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import CCYS, DOCS_DIR, load_today  # noqa: E402

RATES_PATH = ROOT / "data" / "rates.parquet"


def run() -> dict:
    today = load_today()
    snap = today["snapshot"]
    as_of = today["as_of"]

    rates = pd.read_parquet(RATES_PATH)
    rates["date"] = pd.to_datetime(rates["date"])
    latest_source = {}
    latest_date = {}
    for ccy, g in rates.sort_values("date").groupby("currency"):
        latest_source[ccy] = g["source"].iloc[-1]
        latest_date[ccy] = g["date"].iloc[-1]

    rows = []
    for ccy in CCYS:
        entry = (snap["currencies"].get(ccy, {}) or {}).get("breakdown", {}).get("rate_expectations")
        n_rows_available = int((rates["currency"] == ccy).sum())
        if entry is None:
            rows.append({
                "ccy": ccy, "status": "ABSENT (no rows in rates.parquet)" if n_rows_available == 0
                                       else "ABSENT (compute_rate_scores returned nothing)",
                "method": None, "as_of": None, "stale": None,
                "latest_yield": None, "delta_w": None, "z": None,
                "source_latest": latest_source.get(ccy), "source_latest_date": latest_date.get(ccy),
                "n_rows_in_rates_parquet": n_rows_available,
            })
            continue
        rows.append({
            "ccy": ccy, "status": "present (STALE — excluded from index)" if entry.get("stale") else "present (live, in index)",
            "method": entry.get("method"), "as_of": entry.get("as_of"), "stale": entry.get("stale"),
            "latest_yield": entry.get("latest_yield"), "delta_w": entry.get("delta_w"), "z": entry.get("z"),
            "source_latest": entry.get("source") or latest_source.get(ccy),
            "source_latest_date": latest_date.get(ccy),
            "n_rows_in_rates_parquet": n_rows_available,
        })

    df = pd.DataFrame(rows)
    df["days_since_last_obs"] = (pd.Timestamp(as_of).normalize() - pd.to_datetime(df["source_latest_date"])).dt.days
    df.to_csv(DOCS_DIR / "diag-G-rate-expectations-quality.csv", index=False)

    focus = {}
    for ccy in ["NZD", "AUD", "CHF"]:
        row = df[df["ccy"] == ccy].iloc[0].to_dict()
        focus[ccy] = row

    return {"df": df, "focus": focus, "as_of": as_of}


if __name__ == "__main__":
    res = run()
    print(f"as_of = {res['as_of']}\n")
    print(res["df"].to_string(index=False))
    print("\n--- NZD / AUD / CHF focus ---")
    for ccy, row in res["focus"].items():
        print(f"\n{ccy}: {row}")
