"""Deterministic signature of the TREND OUTPUT — the cron render trigger for price.

Hashing the raw price parquet is wrong for triggering a render: the MT5 EA
rewrites the FORMING (current server-day) bar hourly, so the parquet md5 changes
every hour even though `trend_score` EXCLUDES that bar and the published
`trend_cell` values change only ~once/day at bar-close rollover.

This prints an md5 over the actual dashboard-facing values — {board_key:
trend_cell}, sorted keys, None preserved — so `econ_refresh.sh` re-renders on the
price path ONLY when the trend genuinely changes (not on every intraday tick).

    python -m src.trend_signature   # prints a 32-hex digest
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.trend_score import PARQUET, SYMBOLS_YAML, score_all


def trend_signature(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML,
                    server_today=None) -> str:
    """md5 of {board_key: trend_cell} (deterministic: sorted keys, None kept).

    Reads the same inputs as the dashboard render; the forming-bar exclusion lives
    in `score_all`, so two runs that differ only in the current day's OHLC produce
    the SAME signature.
    """
    res = score_all(parquet_path=parquet_path, yaml_path=yaml_path, server_today=server_today)
    cells = {k: v["trend_cell"] for k, v in res.items()}
    blob = json.dumps(cells, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    print(trend_signature())
