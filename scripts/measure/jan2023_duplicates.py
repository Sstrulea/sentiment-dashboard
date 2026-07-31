"""MEASUREMENT INSTRUMENT — not production code. Read-only.

FAZA 2 — identify + measure the January 2023 duplicate-of-January-2024 rows
in data/archive/ff_calendar_range.json (read-only, never written).

Strict purge criterion (as specified):
  same currency, same name_raw, same actual, same forecast,
  same day-of-month, exactly one year later (Jan/Feb 2023 -> Jan/Feb 2024).
No partial matches.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import parse_jblanked_range  # noqa: E402

ARCHIVE = ROOT / "data" / "archive" / "ff_calendar_range.json"


def find_strict_duplicates(arch: pd.DataFrame) -> pd.DataFrame:
    """Rows in Jan/Feb 2023 whose (currency, name_raw, actual, forecast, day)
    match a Jan/Feb 2024 row exactly one year later. Returns the 2023 rows,
    each annotated with its matching 2024 row's datetime for audit."""
    early = arch[(arch["datetime_utc"].dt.year == 2023) & (arch["datetime_utc"].dt.month.isin([1, 2]))].copy()
    late = arch[(arch["datetime_utc"].dt.year == 2024) & (arch["datetime_utc"].dt.month.isin([1, 2]))].copy()

    early["_key"] = list(zip(
        early["currency"], early["name_raw"], early["actual"], early["forecast"],
        early["datetime_utc"].dt.month, early["datetime_utc"].dt.day,
    ))
    late["_key"] = list(zip(
        late["currency"], late["name_raw"], late["actual"], late["forecast"],
        late["datetime_utc"].dt.month, late["datetime_utc"].dt.day,
    ))
    late_by_key = {}
    for _, r in late.iterrows():
        late_by_key.setdefault(r["_key"], []).append(r["datetime_utc"])

    matches = []
    for _, r in early.iterrows():
        cands = late_by_key.get(r["_key"])
        if not cands:
            continue
        matched_2024 = min(cands, key=lambda dt: abs((dt.replace(year=2023) - r["datetime_utc"]).total_seconds()))
        matches.append({
            "currency": r["currency"], "canonical_id": r["canonical_id"], "name_raw": r["name_raw"],
            "datetime_2023": r["datetime_utc"], "datetime_2024_match": matched_2024,
            "actual": r["actual"], "forecast": r["forecast"],
        })
    return pd.DataFrame(matches)


def main() -> None:
    arch = parse_jblanked_range(str(ARCHIVE))
    dups = find_strict_duplicates(arch)

    pd.set_option("display.max_rows", None, "display.width", 160)
    print(f"=== Strict duplicate matches found: {len(dups)} ===")
    print(dups.sort_values("datetime_2023")[
        ["currency", "name_raw", "datetime_2023", "datetime_2024_match", "actual", "forecast"]
    ].to_string(index=False))

    print()
    print("=== Distinct 2023 calendar days involved ===")
    days = sorted(dups["datetime_2023"].dt.strftime("%Y-%m-%d").unique())
    print(days)
    print(f"n distinct days: {len(days)}")

    print()
    print("=== Per (currency, indicator/name_raw) count ===")
    print(dups.groupby(["currency", "name_raw"]).size().sort_values(ascending=False).to_string())

    print()
    print("=== Per currency totals ===")
    print(dups.groupby("currency").size().to_string())

    dups.to_csv(ROOT / "docs" / "jan2023-duplicate-matches.csv", index=False)
    print(f"\nSaved {len(dups)} matches to docs/jan2023-duplicate-matches.csv")


if __name__ == "__main__":
    main()
