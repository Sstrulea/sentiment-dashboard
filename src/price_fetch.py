"""Daily OHLC acquisition from the MT5 price-history TSV.

Source: an MT5 Expert Advisor (mt5/PriceHistoryExport.mq5) writes
`$MT5_FILES_DIR/price_history.csv` periodically — a TSV (tab-separated),
UTF-16 encoded, 6 columns keyed by the BROKER symbol:

    symbol  date  open  high  low  close

`date` is "YYYY-MM-DD" (the daily bar's server date); OHLC are plain floats.
This mirrors the calendar pipeline (`src/economic_fetch.py`): EA writes a
file, Python ingests it. Acquisition + normalization only — NO scoring, NO
column, NO rendering (price first, score later, like COT).

It writes one parquet:

    data/price_history.parquet

with schema: symbol (the BOARD key, not the broker one — mapped at ingest via
data/price_symbols.yaml), date (datetime64, tz-naive), open/high/low/close
(float64), source. Dedup is last-write-wins on (symbol, date); the output is
sorted deterministically so unchanged data produces no git diff.

GRACEFUL (anti-degradation, like FRED/COT/liquidity): a missing MT5_FILES_DIR,
a missing CSV, or an unmapped broker symbol never raises — it logs and leaves
the parquet untouched / skips the symbol. The ingest is purely LOCAL (reads the
EA's file off disk); no network.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd
import yaml

# Load .env once at import so local runs pick up MT5_FILES_DIR (mirror of
# economic_fetch — same MT5 Files directory).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "price_history.parquet"
SYMBOLS_YAML = ROOT / "data" / "price_symbols.yaml"

CSV_ENCODING = "utf-16"
CSV_SEP = "\t"
CSV_NAME = "price_history.csv"

PRICE_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "source"]
_OHLC = ["open", "high", "low", "close"]


def _csv_path() -> Path | None:
    """Locate the EA's CSV via $MT5_FILES_DIR; None (logged) if not configured."""
    files_dir = os.environ.get("MT5_FILES_DIR")
    if not files_dir:
        log.warning("MT5_FILES_DIR not set (env or .env); cannot locate %s.", CSV_NAME)
        return None
    return Path(files_dir) / CSV_NAME


# ---------------------------------------------------------------------------
# Symbol map (board key -> broker symbol)  ->  inverted for ingest lookup.
# ---------------------------------------------------------------------------

def load_symbol_map(yaml_path: Path = SYMBOLS_YAML) -> tuple[dict[str, str], list[str]]:
    """Return (broker_symbol -> board_key, [all board keys]).

    Blank/missing broker values are kept in the board-key list (so they show up
    as "no data") but excluded from the lookup. Broker symbols are matched
    case-sensitively after stripping whitespace.
    """
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f) or {}
    symbols = cfg.get("symbols", {}) or {}

    broker_to_board: dict[str, str] = {}
    board_keys: list[str] = []
    for board_key, broker_sym in symbols.items():
        board_keys.append(board_key)
        broker = (str(broker_sym).strip() if broker_sym is not None else "")
        if broker:
            broker_to_board[broker] = board_key
    return broker_to_board, board_keys


# ---------------------------------------------------------------------------
# Raw load
# ---------------------------------------------------------------------------

def load_raw(csv_path: Path) -> pd.DataFrame:
    """Read the EA's TSV; coerce OHLC to float and `date` to datetime.

    Rows without a parseable date are dropped; `symbol` is stripped. Returns the
    raw broker-keyed frame (mapping to board keys happens in `normalize`).
    """
    df = pd.read_csv(csv_path, sep=CSV_SEP, encoding=CSV_ENCODING, dtype=str)
    df["symbol"] = df["symbol"].fillna("").astype(str).str.strip()
    for c in _OHLC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Normalize (broker symbol -> board key)
# ---------------------------------------------------------------------------

def normalize(df: pd.DataFrame, broker_to_board: dict[str, str]) -> pd.DataFrame:
    """Map broker rows to the board schema, dropping unmapped broker symbols."""
    if df.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    board = df["symbol"].map(broker_to_board)
    keep = board.notna()
    out = df.loc[keep, ["date"] + _OHLC].copy()
    out.insert(0, "symbol", board[keep].to_numpy())
    out["source"] = "mt5"
    out = out[PRICE_COLUMNS]
    out["date"] = pd.to_datetime(out["date"])
    return out.reset_index(drop=True)


def unmapped_symbols(df: pd.DataFrame, broker_to_board: dict[str, str]) -> list[str]:
    """Distinct broker symbols present in the CSV but absent from the map."""
    if df.empty:
        return []
    present = set(df["symbol"].unique())
    return sorted(s for s in present if s and s not in broker_to_board)


# ---------------------------------------------------------------------------
# Merge to parquet
# ---------------------------------------------------------------------------

def merge(new: pd.DataFrame, parquet_path: Path = PARQUET) -> pd.DataFrame:
    """Append `new`, dedup last-write-wins on (symbol, date), write deterministically."""
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new

    combined = (
        combined.sort_values(["symbol", "date"])
        .drop_duplicates(["symbol", "date"], keep="last")
        .reset_index(drop=True)
    )
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(parquet_path, index=False)
    return combined


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_price_history(
    csv_path: Path | None = None,
    parquet_path: Path = PARQUET,
    yaml_path: Path = SYMBOLS_YAML,
) -> int:
    """Read the EA CSV, map broker->board, merge into parquet. Returns # rows ingested.

    Graceful: a missing CSV path/file leaves the parquet untouched and returns 0
    (no exception) — same anti-degradation contract as the FRED/COT fetchers.
    """
    path = csv_path or _csv_path()
    if path is None or not Path(path).exists():
        log.warning("Price CSV not found (%s); parquet unchanged.", path)
        return 0

    broker_to_board, _ = load_symbol_map(yaml_path)
    raw = load_raw(Path(path))

    skipped = unmapped_symbols(raw, broker_to_board)
    if skipped:
        log.warning("Unmapped broker symbols skipped (%d): %s", len(skipped), ", ".join(skipped))

    new = normalize(raw, broker_to_board)
    if new.empty:
        log.info("No mappable rows in %s; parquet unchanged.", path)
        return 0

    total = len(merge(new, parquet_path))
    log.info(
        "Ingested %d OHLC row(s) over %d symbol(s); price_history.parquet now has %d rows.",
        len(new), new["symbol"].nunique(), total,
    )
    return len(new)


# ---------------------------------------------------------------------------
# CLI report
# ---------------------------------------------------------------------------

def _print_report(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML) -> None:
    _, board_keys = load_symbol_map(yaml_path)
    if not parquet_path.exists():
        print("price_history.parquet: (absent)")
        print(f"Board keys without data: {len(board_keys)} / {len(board_keys)}")
        return

    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"])
    present = sorted(df["symbol"].unique())
    missing = [k for k in board_keys if k not in set(present)]

    print(f"price_history.parquet: {len(df)} rows, {len(present)} symbols")
    if len(df):
        print(f"  date range: {df['date'].min().date()} .. {df['date'].max().date()}")
    print("  depth per symbol (bars | first .. last):")
    g = df.groupby("symbol")["date"]
    for sym in present:
        s = g.get_group(sym)
        print(f"    {sym:<9} {len(s):>4}  {s.min().date()} .. {s.max().date()}")
    print(f"  board keys WITHOUT data ({len(missing)}/{len(board_keys)}): "
          f"{', '.join(missing) if missing else '—'}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Ingest the MT5 daily-OHLC CSV -> data/price_history.parquet")
    ap.add_argument("--csv", type=Path, default=None,
                    help="path to the EA CSV (default: $MT5_FILES_DIR/price_history.csv)")
    ap.add_argument("--report-only", action="store_true",
                    help="skip ingest; just print the current parquet report")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    if not args.report_only:
        n = update_price_history(csv_path=args.csv)
        print(f"Ingested {n} OHLC row(s).")
    _print_report()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
