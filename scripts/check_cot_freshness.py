"""Compare the latest report_date in data/history.parquet against the CFTC API.

Exits non-zero (with an explicit message) if the local parquet is behind the
latest report published by the CFTC, if the parquet is missing/empty, or if
the CFTC API can't be reached. Run standalone: python scripts/check_cot_freshness.py
"""
import sys
from pathlib import Path

import pandas as pd
import requests

CFTC_URL = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
DEFAULT_PARQUET_PATH = Path(__file__).resolve().parent.parent / "data" / "history.parquet"


def local_max_date(parquet_path):
    if not parquet_path.exists():
        sys.exit(f"check_cot_freshness: parquet not found at {parquet_path}")
    df = pd.read_parquet(parquet_path)
    if df.empty:
        sys.exit(f"check_cot_freshness: parquet at {parquet_path} is empty")
    return df["report_date_as_yyyy_mm_dd"].max()


def remote_max_date():
    try:
        resp = requests.get(
            CFTC_URL,
            params={
                "$select": "report_date_as_yyyy_mm_dd",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": 1,
            },
            timeout=60,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as exc:
        sys.exit(f"check_cot_freshness: CFTC API request failed: {exc}")
    if not rows:
        sys.exit("check_cot_freshness: CFTC API returned no rows")
    return pd.Timestamp(rows[0]["report_date_as_yyyy_mm_dd"]).normalize()


def main():
    parquet_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PARQUET_PATH

    local = local_max_date(parquet_path)
    remote = remote_max_date()

    local_str = local.strftime("%Y-%m-%d")
    remote_str = remote.strftime("%Y-%m-%d")
    print(f"local={local_str} remote={remote_str}")

    if local < remote:
        sys.exit(
            f"check_cot_freshness: local data is stale (local={local_str}, remote={remote_str})"
        )


if __name__ == "__main__":
    main()
