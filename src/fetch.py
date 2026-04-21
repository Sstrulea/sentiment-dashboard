"""Fetch CFTC Legacy Combined COT data and maintain a local parquet cache."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import yaml

log = logging.getLogger(__name__)

ENDPOINT = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_FILE = ROOT / "data" / "contracts.yaml"
HISTORY_FILE = ROOT / "data" / "history.parquet"

# Columns we actually care about (downloaded as strings, coerced to numeric).
NUMERIC_COLS = [
    "noncomm_positions_long_all",
    "noncomm_positions_short_all",
    "comm_positions_long_all",
    "comm_positions_short_all",
    "open_interest_all",
]

SELECT_COLS = [
    "report_date_as_yyyy_mm_dd",
    "cftc_contract_market_code",
    "market_and_exchange_names",
    *NUMERIC_COLS,
]


def load_contracts() -> list[dict]:
    with open(CONTRACTS_FILE) as f:
        cfg = yaml.safe_load(f)
    out: list[dict] = []
    for cat_key, cat in cfg["categories"].items():
        for inst in cat["instruments"]:
            out.append(
                {
                    "category": cat_key,
                    "category_label": cat["label"],
                    "symbol": inst["symbol"],
                    "name": inst["name"],
                    "cftc_code": inst["cftc_code"],
                }
            )
    return out


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _fetch_batch(codes: list[str], since_iso: str) -> pd.DataFrame:
    code_list = ",".join(f"'{c}'" for c in codes)
    where = (
        f"cftc_contract_market_code in ({code_list}) and "
        f"report_date_as_yyyy_mm_dd >= '{since_iso}'"
    )
    params = {
        "$select": ",".join(SELECT_COLS),
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 50000,
    }
    r = requests.get(ENDPOINT, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame(columns=SELECT_COLS)
    df = pd.DataFrame(data)
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["report_date_as_yyyy_mm_dd"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None)
    return df


def fetch_all(codes: list[str], since: datetime) -> pd.DataFrame:
    since_iso = since.strftime("%Y-%m-%dT00:00:00.000")
    frames: list[pd.DataFrame] = []
    for batch in _chunks(codes, 20):
        log.info("Fetching batch of %d codes since %s", len(batch), since_iso)
        frames.append(_fetch_batch(batch, since_iso))
    if not frames:
        return pd.DataFrame(columns=SELECT_COLS)
    return pd.concat(frames, ignore_index=True)


def update_history() -> pd.DataFrame:
    """Refresh `data/history.parquet` and return the full DataFrame.

    First run pulls ~3 years. Subsequent runs pull only data newer than
    the latest row already stored.
    """
    contracts = load_contracts()
    codes = [c["cftc_code"] for c in contracts]
    now = datetime.now(timezone.utc)

    existing: pd.DataFrame | None = None
    if HISTORY_FILE.exists():
        existing = pd.read_parquet(HISTORY_FILE)
        latest = existing["report_date_as_yyyy_mm_dd"].max()
        # Re-fetch the latest week too in case of restatements.
        since = (latest - pd.Timedelta(days=7)).to_pydatetime()
        log.info("Incremental update from %s", since.date())
    else:
        since = now - timedelta(days=365 * 3 + 30)
        log.info("Full history fetch from %s", since.date())

    fresh = fetch_all(codes, since)
    log.info("Fetched %d rows", len(fresh))

    # Warn about codes returning no data.
    returned = set(fresh["cftc_contract_market_code"].unique()) if not fresh.empty else set()
    for c in contracts:
        if c["cftc_code"] not in returned and (existing is None or c["cftc_code"] not in set(existing["cftc_contract_market_code"].unique())):
            log.warning("No data returned for %s (%s) cftc_code=%s", c["symbol"], c["name"], c["cftc_code"])

    combined = (
        pd.concat([existing, fresh], ignore_index=True) if existing is not None else fresh
    )
    combined = combined.drop_duplicates(
        subset=["cftc_contract_market_code", "report_date_as_yyyy_mm_dd"],
        keep="last",
    ).sort_values(["cftc_contract_market_code", "report_date_as_yyyy_mm_dd"])

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(HISTORY_FILE, index=False)
    log.info("Saved %d rows to %s", len(combined), HISTORY_FILE)
    return combined
