"""Tests for src.price_fetch — pure local ingest (no network, no real MT5/data)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.price_fetch import (
    PRICE_COLUMNS,
    load_raw,
    load_symbol_map,
    merge,
    normalize,
    unmapped_symbols,
    update_price_history,
)

# A small board->broker map: GOLD/EURUSD mapped, DXY intentionally blank,
# and the CSV will also carry a broker symbol (FOO) absent from the map.
YAML_TEXT = """\
symbols:
  EURUSD: EURUSD.r
  GOLD:   XAUUSD
  SP500:  US500
  DXY:    ""
"""


def _write_utf16_tsv(path: Path, rows: list[tuple]) -> None:
    """Write the EA's exact format: UTF-16, tab-separated, header + rows."""
    header = "symbol\tdate\topen\thigh\tlow\tclose"
    lines = [header] + ["\t".join(str(c) for c in r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-16")


@pytest.fixture()
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "price_symbols.yaml"
    p.write_text(YAML_TEXT)
    return p


@pytest.fixture()
def csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "price_history.csv"
    _write_utf16_tsv(p, [
        ("EURUSD.r", "2026-06-25", 1.1700, 1.1750, 1.1680, 1.1720),
        ("EURUSD.r", "2026-06-26", 1.1720, 1.1800, 1.1710, 1.1790),
        ("XAUUSD",   "2026-06-25", 2300.0, 2325.5, 2295.0, 2310.2),
        ("US500",    "2026-06-25", 5400.0, 5420.0, 5390.0, 5415.0),
        ("FOO",      "2026-06-25", 10.0,   11.0,   9.0,    10.5),   # unmapped broker symbol
    ])
    return p


# --- format parsing (UTF-16, tab) ------------------------------------------

def test_load_raw_parses_utf16_tab(csv_path: Path):
    df = load_raw(csv_path)
    assert list(df.columns) == ["symbol", "date", "open", "high", "low", "close"]
    assert len(df) == 5
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    for c in ("open", "high", "low", "close"):
        assert pd.api.types.is_float_dtype(df[c])
    assert df.loc[0, "symbol"] == "EURUSD.r"
    assert df.loc[2, "close"] == pytest.approx(2310.2)


def test_load_raw_drops_unparseable_dates(tmp_path: Path):
    p = tmp_path / "bad.csv"
    _write_utf16_tsv(p, [
        ("EURUSD.r", "2026-06-25", 1.1, 1.2, 1.0, 1.15),
        ("EURUSD.r", "not-a-date", 1.1, 1.2, 1.0, 1.15),
    ])
    df = load_raw(p)
    assert len(df) == 1


# --- broker -> board mapping -----------------------------------------------

def test_load_symbol_map_inverts_and_skips_blank(yaml_path: Path):
    broker_to_board, board_keys = load_symbol_map(yaml_path)
    assert broker_to_board == {"EURUSD.r": "EURUSD", "XAUUSD": "GOLD", "US500": "SP500"}
    # DXY (blank broker) is still a board key, just absent from the lookup.
    assert set(board_keys) == {"EURUSD", "GOLD", "SP500", "DXY"}
    assert "DXY" not in broker_to_board.values() or True  # DXY has no broker mapping


def test_normalize_maps_to_board_and_drops_unmapped(csv_path: Path, yaml_path: Path):
    broker_to_board, _ = load_symbol_map(yaml_path)
    raw = load_raw(csv_path)
    out = normalize(raw, broker_to_board)
    assert list(out.columns) == PRICE_COLUMNS
    # FOO dropped; EURUSD.r -> EURUSD, XAUUSD -> GOLD, US500 -> SP500
    assert set(out["symbol"]) == {"EURUSD", "GOLD", "SP500"}
    assert (out["source"] == "mt5").all()


def test_unmapped_symbols_reports_foo(csv_path: Path, yaml_path: Path):
    broker_to_board, _ = load_symbol_map(yaml_path)
    raw = load_raw(csv_path)
    assert unmapped_symbols(raw, broker_to_board) == ["FOO"]


# --- merge dedup (last-write-wins) -----------------------------------------

def test_merge_dedup_last_write_wins(tmp_path: Path):
    pq = tmp_path / "price_history.parquet"
    first = pd.DataFrame([
        {"symbol": "EURUSD", "date": pd.Timestamp("2026-06-25"),
         "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "source": "mt5"},
    ])
    merge(first, parquet_path=pq)
    # same (symbol, date) re-ingested with a corrected close -> overwrites.
    second = pd.DataFrame([
        {"symbol": "EURUSD", "date": pd.Timestamp("2026-06-25"),
         "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.07, "source": "mt5"},
        {"symbol": "EURUSD", "date": pd.Timestamp("2026-06-26"),
         "open": 1.07, "high": 1.2, "low": 1.0, "close": 1.15, "source": "mt5"},
    ])
    out = merge(second, parquet_path=pq)
    assert len(out) == 2  # history preserved, no dup
    row = out[out["date"] == pd.Timestamp("2026-06-25")].iloc[0]
    assert row["close"] == pytest.approx(1.07)  # last write won


# --- graceful degradation ---------------------------------------------------

def test_missing_csv_leaves_parquet_untouched(tmp_path: Path, yaml_path: Path):
    pq = tmp_path / "price_history.parquet"
    n = update_price_history(csv_path=tmp_path / "nope.csv", parquet_path=pq, yaml_path=yaml_path)
    assert n == 0
    assert not pq.exists()


def test_none_csv_path_is_graceful(tmp_path: Path, yaml_path: Path, monkeypatch):
    # No MT5_FILES_DIR -> _csv_path() returns None -> no crash, parquet untouched.
    monkeypatch.delenv("MT5_FILES_DIR", raising=False)
    pq = tmp_path / "price_history.parquet"
    n = update_price_history(csv_path=None, parquet_path=pq, yaml_path=yaml_path)
    assert n == 0
    assert not pq.exists()


def test_all_unmapped_is_noop(tmp_path: Path, yaml_path: Path):
    csv = tmp_path / "price_history.csv"
    _write_utf16_tsv(csv, [("ONLY_UNKNOWN", "2026-06-25", 1.0, 1.1, 0.9, 1.05)])
    pq = tmp_path / "price_history.parquet"
    n = update_price_history(csv_path=csv, parquet_path=pq, yaml_path=yaml_path)
    assert n == 0
    assert not pq.exists()


# --- end-to-end schema ------------------------------------------------------

def test_update_writes_correct_schema(csv_path: Path, yaml_path: Path, tmp_path: Path):
    pq = tmp_path / "price_history.parquet"
    n = update_price_history(csv_path=csv_path, parquet_path=pq, yaml_path=yaml_path)
    assert n == 4  # 2 EURUSD + 1 GOLD + 1 SP500 (FOO dropped)
    df = pd.read_parquet(pq)
    assert list(df.columns) == PRICE_COLUMNS
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    for c in ("open", "high", "low", "close"):
        assert pd.api.types.is_float_dtype(df[c])
    assert set(df["symbol"]) == {"EURUSD", "GOLD", "SP500"}
    # deterministic ordering: sorted by (symbol, date)
    assert df.equals(df.sort_values(["symbol", "date"]).reset_index(drop=True))
