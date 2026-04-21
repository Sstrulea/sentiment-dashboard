"""List all CFTC Legacy Combined contracts active in the last 4 weeks.

Run this before the first `main.py` to validate the codes in
`data/contracts.yaml`. Any symbol whose `cftc_code` is missing from the output
must be corrected (CFTC occasionally relists contracts under new codes).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ENDPOINT = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_FILE = ROOT / "data" / "contracts.yaml"


def fetch_recent_contracts(weeks: int = 4) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    params = {
        "$select": "cftc_contract_market_code,market_and_exchange_names,max(report_date_as_yyyy_mm_dd) as latest",
        "$where": f"report_date_as_yyyy_mm_dd >= '{cutoff}T00:00:00.000'",
        "$group": "cftc_contract_market_code,market_and_exchange_names",
        "$limit": 50000,
    }
    r = requests.get(ENDPOINT, params=params, timeout=60)
    r.raise_for_status()
    rows = r.json()
    rows.sort(key=lambda x: x["market_and_exchange_names"])
    return rows


def load_yaml_codes() -> dict[str, dict]:
    with open(CONTRACTS_FILE) as f:
        cfg = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for cat_key, cat in cfg["categories"].items():
        for inst in cat["instruments"]:
            out[inst["cftc_code"]] = {
                "symbol": inst["symbol"],
                "name": inst["name"],
                "category": cat_key,
            }
    return out


def main() -> int:
    rows = fetch_recent_contracts()
    available_codes = {r["cftc_contract_market_code"] for r in rows}

    print(f"=== {len(rows)} contracts active in the last 4 weeks ===\n")
    for r in rows:
        print(f"{r['cftc_contract_market_code']:>8}  {r['market_and_exchange_names']}")

    yaml_codes = load_yaml_codes()
    missing = [(c, meta) for c, meta in yaml_codes.items() if c not in available_codes]

    print("\n=== Validation against contracts.yaml ===")
    print(f"YAML entries: {len(yaml_codes)}  |  missing from API: {len(missing)}")
    if missing:
        print("\nMISSING codes (fix these in contracts.yaml):")
        for code, meta in missing:
            print(f"  {code}  {meta['symbol']:>12}  {meta['name']}  ({meta['category']})")
        return 1
    print("All YAML codes present in the last 4 weeks of data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
