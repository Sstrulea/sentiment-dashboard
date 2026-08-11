"""fix/price-watchdog-wiring — regression test written BEFORE the fix.

economic_render._freshness()'s "price" entry used to take p["date"].max()
over ALL board instruments combined (a single blind aggregate): one frozen
instrument (e.g. FTSE100) is invisible as long as any other instrument keeps
updating. This test proves the payload surfaces a per-instrument freeze by
name, not just an aggregate age.

100% synthetic fixture — does not read data/price_history.parquet or
data/price_symbols.yaml. Monkeypatches economic_render's price-history and
symbol-map sources so the check is fully self-contained.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import economic_render

AS_OF = pd.Timestamp("2026-08-11")
FRESH_SYMBOLS = [f"SYM{i:02d}" for i in range(1, 36)]  # 35 healthy instruments
FROZEN_SYMBOL = "FTSE100"
NO_DATA_SYMBOL = "DXY"


def _rows(symbol: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": symbol, "date": pd.to_datetime(list(dates)),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "source": "mt5",
    })


def _write_synthetic_price_history(tmp_path) -> "pd.io.parquet":
    frames = []
    # 35 fresh instruments: normal daily cadence ending right at AS_OF.
    for sym in FRESH_SYMBOLS:
        dates = pd.bdate_range(end=AS_OF, periods=60)
        frames.append(_rows(sym, dates))
    # 1 frozen instrument: normal cadence, but no export in ~90 days.
    frozen_dates = pd.bdate_range(start="2026-01-01", periods=90)
    cutoff = AS_OF - pd.Timedelta(days=75)
    frames.append(_rows(FROZEN_SYMBOL, frozen_dates[frozen_dates < cutoff]))
    # DXY (no_data) deliberately gets zero rows.
    df = pd.concat(frames, ignore_index=True)
    path = tmp_path / "price_history.parquet"
    df.to_parquet(path)
    return path


def _write_synthetic_symbols_yaml(tmp_path):
    board_keys = FRESH_SYMBOLS + [FROZEN_SYMBOL, NO_DATA_SYMBOL]
    lines = ["symbols:"] + [f"  {k}: {k}" for k in board_keys]
    path = tmp_path / "price_symbols.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def synthetic_price_payload(tmp_path, monkeypatch):
    parquet_path = _write_synthetic_price_history(tmp_path)
    yaml_path = _write_synthetic_symbols_yaml(tmp_path)
    monkeypatch.setattr(economic_render, "PRICE_HISTORY_PARQUET", parquet_path)
    # Pre-fix, economic_render has no symbol-map source at all; raising=False
    # lets this test run (and fail on its real assertions) on main too.
    monkeypatch.setattr(economic_render, "PRICE_SYMBOLS_YAML", yaml_path, raising=False)
    return economic_render._freshness(AS_OF)


def test_single_frozen_instrument_is_surfaced_as_stale(synthetic_price_payload):
    price = synthetic_price_payload["price"]
    assert price["stale"] is True


def test_frozen_instrument_is_named_not_just_aggregate_age(synthetic_price_payload):
    price = synthetic_price_payload["price"]
    stale_symbols = {row["symbol"] for row in price.get("stale_instruments", [])}
    assert stale_symbols == {FROZEN_SYMBOL}


def test_no_data_instrument_never_marked_stale(synthetic_price_payload):
    price = synthetic_price_payload["price"]
    stale_symbols = {row["symbol"] for row in price.get("stale_instruments", [])}
    assert NO_DATA_SYMBOL not in stale_symbols
    assert NO_DATA_SYMBOL in price.get("no_data", [])
