"""Retail sentiment data acquisition: snapshot, validate, append parquet.

This module is the orchestrator. It instantiates a `RetailSentimentProvider`,
fetches a snapshot, filters to configured symbols, and appends to two parquet
files:

    data/retail_history.parquet  — per-symbol snapshot rows
    data/retail_general.parquet  — provider-level meta rows
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from .retail_providers import (
    FetchResult,
    GeneralStats,
    MyfxbookProvider,
    RetailSentimentProvider,
    RetailSnapshot,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RETAIL_HISTORY_FILE = ROOT / "data" / "retail_history.parquet"
RETAIL_GENERAL_FILE = ROOT / "data" / "retail_general.parquet"
SYMBOLS_YAML = ROOT / "data" / "retail_symbols.yaml"

HISTORY_COLUMNS = [
    "date",
    "fetched_at",
    "provider",
    "symbol",
    "long_pct",
    "short_pct",
    "long_volume",
    "short_volume",
    "long_positions",
    "short_positions",
    "total_positions",
    "avg_long_price",
    "avg_short_price",
]

GENERAL_COLUMNS = [
    "fetched_at",
    "provider",
    "real_account_pct",
    "demo_account_pct",
    "profitable_pct",
    "nonprofitable_pct",
    "total_funds_usd",
    "average_deposit_usd",
]


def load_symbols_config() -> dict:
    """Read and parse data/retail_symbols.yaml."""
    if not SYMBOLS_YAML.exists():
        raise FileNotFoundError(f"{SYMBOLS_YAML} not found")
    with open(SYMBOLS_YAML) as f:
        return yaml.safe_load(f) or {}


def snapshot_retail_sentiment(
    provider: RetailSentimentProvider | None = None,
) -> int:
    """Fetch a fresh snapshot, validate, append parquet. Returns # of symbols written.

    If `provider` is None, a `MyfxbookProvider` is constructed from environment
    variables. Idempotent on (fetched_at_minute_truncated, provider, symbol):
    last write wins.
    """
    cfg = load_symbols_config()
    configured = set(cfg.get("symbols", {}).keys())
    if not configured:
        raise RuntimeError("No symbols configured in retail_symbols.yaml")

    if provider is None:
        provider = MyfxbookProvider()

    log.info("Fetching retail sentiment from %s", provider.name)
    result = provider.fetch_all()

    # Filter to configured symbols. Provider should already normalize, but
    # double-check to keep the parquet schema clean.
    snapshots = [s for s in result.snapshots if s.symbol in configured]
    skipped = len(result.snapshots) - len(snapshots)
    if skipped:
        log.info("Filtered out %d snapshot(s) not in retail_symbols.yaml", skipped)

    _append_history(snapshots, result.fetched_at, result.provider)
    _append_general(result.general, result.fetched_at, result.provider)

    log.info(
        "Snapshot complete: provider=%s symbols=%d fetched_at=%s",
        result.provider, len(snapshots), result.fetched_at.isoformat(),
    )
    return len(snapshots)


def _append_history(
    snapshots: list[RetailSnapshot],
    fetched_at: datetime,
    provider_name: str,
) -> None:
    if not snapshots:
        log.warning("No snapshots to append; skipping retail_history.parquet write")
        return

    rows: list[dict] = []
    fetched_minute = pd.Timestamp(fetched_at).floor("min")
    fetched_date = pd.Timestamp(fetched_at).normalize()

    for s in snapshots:
        rows.append({
            "date": fetched_date,
            "fetched_at": pd.Timestamp(fetched_at),
            "provider": provider_name,
            "symbol": s.symbol,
            "long_pct": float(s.long_pct),
            "short_pct": float(s.short_pct),
            "long_volume": s.long_volume,
            "short_volume": s.short_volume,
            "long_positions": s.long_positions,
            "short_positions": s.short_positions,
            "total_positions": s.total_positions,
            "avg_long_price": s.avg_long_price,
            "avg_short_price": s.avg_short_price,
        })

    new_df = pd.DataFrame(rows, columns=HISTORY_COLUMNS)
    new_df["fetched_at_minute"] = pd.Timestamp(fetched_minute)

    if RETAIL_HISTORY_FILE.exists():
        existing = pd.read_parquet(RETAIL_HISTORY_FILE)
        existing["fetched_at_minute"] = pd.to_datetime(existing["fetched_at"]).dt.floor("min")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = (
        combined.drop_duplicates(
            subset=["fetched_at_minute", "provider", "symbol"], keep="last"
        )
        .sort_values(["fetched_at", "symbol"])
        .reset_index(drop=True)
    )
    combined = combined.drop(columns=["fetched_at_minute"])

    RETAIL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(RETAIL_HISTORY_FILE, index=False)
    log.info("retail_history.parquet now has %d rows", len(combined))


def _append_general(
    general: GeneralStats,
    fetched_at: datetime,
    provider_name: str,
) -> None:
    fetched_minute = pd.Timestamp(fetched_at).floor("min")
    row = {
        "fetched_at": pd.Timestamp(fetched_at),
        "provider": provider_name,
        "real_account_pct": general.real_account_pct,
        "demo_account_pct": general.demo_account_pct,
        "profitable_pct": general.profitable_pct,
        "nonprofitable_pct": general.nonprofitable_pct,
        "total_funds_usd": general.total_funds_usd,
        "average_deposit_usd": general.average_deposit_usd,
    }

    new_df = pd.DataFrame([row], columns=GENERAL_COLUMNS)
    new_df["fetched_at_minute"] = pd.Timestamp(fetched_minute)

    if RETAIL_GENERAL_FILE.exists():
        existing = pd.read_parquet(RETAIL_GENERAL_FILE)
        existing["fetched_at_minute"] = pd.to_datetime(existing["fetched_at"]).dt.floor("min")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = (
        combined.drop_duplicates(
            subset=["fetched_at_minute", "provider"], keep="last"
        )
        .sort_values("fetched_at")
        .reset_index(drop=True)
    )
    combined = combined.drop(columns=["fetched_at_minute"])

    RETAIL_GENERAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(RETAIL_GENERAL_FILE, index=False)
    log.info("retail_general.parquet now has %d rows", len(combined))
