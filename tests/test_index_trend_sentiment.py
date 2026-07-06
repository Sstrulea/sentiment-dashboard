"""Cross-asset trend + P/C sentiment proxy for DAX/NIKKEI/FTSE100 (config-driven,
no scoring-engine change). Tests: symbol-discovery ingest mapping, config→instrument
keys, the P/C proxy (present+marked on the 3, native on US, absent elsewhere), and
that the trend factor stays excluded until a price series exists."""
from __future__ import annotations

import pandas as pd
import yaml

from src.crossasset_compute import compute_crossasset_scores
from src.economic_render import _us_index_pc
from src.price_fetch import load_symbol_map, normalize


# --- symbol discovery: the ingest maps every candidate → the same board key ---

def test_price_symbols_multi_alias_maps_all_candidates(tmp_path):
    p = tmp_path / "price_symbols.yaml"
    p.write_text("symbols:\n  DAX: [DE40, GER40, DE30]\n  SP500: US500\n")
    broker_to_board, board_keys = load_symbol_map(p)
    assert broker_to_board["DE40"] == "DAX"      # whichever the EA discovers…
    assert broker_to_board["GER40"] == "DAX"     # …maps to the same board key
    assert broker_to_board["DE30"] == "DAX"
    assert broker_to_board["US500"] == "SP500"   # plain string still works
    assert set(board_keys) == {"DAX", "SP500"}


def test_normalize_resolves_discovered_symbol(tmp_path):
    p = tmp_path / "price_symbols.yaml"
    p.write_text("symbols:\n  DAX: [DE40, GER40]\n")
    b2b, _ = load_symbol_map(p)
    # EA exported under DE40 (the resolved candidate) → normalizes to board key DAX
    raw = pd.DataFrame([{"symbol": "DE40", "date": pd.Timestamp("2026-06-01"),
                         "open": 18000.0, "high": 18100.0, "low": 17900.0, "close": 18050.0}])
    out = normalize(raw, b2b)
    assert list(out["symbol"].unique()) == ["DAX"]


# --- config → instrument keys + factors -------------------------------------

def test_foreign_indices_have_trend_sentiment_and_proxy():
    cfg = yaml.safe_load(open("data/crossasset_instruments.yaml"))["instruments"]
    price = yaml.safe_load(open("data/price_symbols.yaml"))["symbols"]
    for sym in ["DAX", "NIKKEI", "FTSE100"]:
        f = cfg[sym]["factors"]
        assert f.get("trend") == {"sign": 1, "weight": 0.5}, sym
        assert f.get("sentiment") == {"sign": 1, "weight": 0.5}, sym
        assert cfg[sym].get("sentiment_proxy") == "us_equity_pc", sym
        assert sym in price, f"{sym} board key missing from price_symbols.yaml"


def test_nasdaq_has_native_trend_not_proxy():
    # NASDAQ (USTEC resolved, EA v1.3) gains a trend factor but keeps NATIVE sentiment
    # (home_ccy USD) — no proxy flag; its board key lists USTEC as a candidate.
    cfg = yaml.safe_load(open("data/crossasset_instruments.yaml"))["instruments"]["NASDAQ"]
    assert cfg["factors"].get("trend") == {"sign": 1, "weight": 0.5}
    assert cfg.get("sentiment_proxy") is None
    price = yaml.safe_load(open("data/price_symbols.yaml"))["symbols"]["NASDAQ"]
    assert "USTEC" in price


# --- P/C proxy: native vs proxy vs none -------------------------------------

def test_us_index_pc_native_and_proxy_sets():
    cfg = yaml.safe_load(open("data/crossasset_instruments.yaml"))
    _cell, _detail, native, proxy = _us_index_pc(cfg)
    assert native == {"DJIA", "SP500", "NASDAQ"}          # home_ccy == USD
    assert proxy == {"DAX", "NIKKEI", "FTSE100"}          # sentiment_proxy flag
    assert native.isdisjoint(proxy)


def test_proxy_folds_identically_and_absent_without_flag():
    # DAX (proxy) vs a hypothetical index with NO sentiment factor: the P/C cell folds
    # into DAX identically to a US index; an instrument without the factor is untouched.
    cfg = {"scale": 5, "bias_thresholds": {"mild": 1.9, "very": 4.3}, "instruments": {
        "DAX": {"type": "index", "home_ccy": "EUR", "sentiment_proxy": "us_equity_pc", "factors": {
            "growth": {"sign": 1, "weight": 1.0}, "sentiment": {"sign": 1, "weight": 0.5}}},
        "NOSENT": {"type": "index", "home_ccy": "EUR", "factors": {"growth": {"sign": 1, "weight": 1.0}}},
    }}
    cats = {"EUR": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    base = compute_crossasset_scores(cats, 0, cfg)["DAX"]                       # no sentiment supplied
    withpc = compute_crossasset_scores(cats, 0, cfg, sentiment_by_symbol={"DAX": -1})["DAX"]
    assert withpc["score_precise"] < base["score_precise"]                     # P/C -1 pulls it down
    sf = next(f for f in withpc["factors"] if f["name"] == "sentiment")
    assert sf["present"] and sf["weight"] == 0.5 and sf["value"] == -1.0       # same fold as US
    # instrument without the sentiment factor is unaffected by a supplied cell
    nos = compute_crossasset_scores(cats, 0, cfg, sentiment_by_symbol={"NOSENT": -1})["NOSENT"]
    nob = compute_crossasset_scores(cats, 0, cfg)["NOSENT"]
    assert nos["score_precise"] == nob["score_precise"]


def test_trend_factor_excluded_until_series_exists():
    # DAX has a trend factor but no price series yet (trend cell None) → excluded,
    # score == no-trend baseline (the pre-EA-run state).
    cfg = {"scale": 5, "bias_thresholds": {"mild": 1.9, "very": 4.3}, "instruments": {
        "DAX": {"type": "index", "home_ccy": "EUR", "factors": {
            "growth": {"sign": 1, "weight": 1.0}, "trend": {"sign": 1, "weight": 0.5}}}}}
    cats = {"EUR": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    no_trend = compute_crossasset_scores(cats, 0, cfg)["DAX"]                   # trend_by_symbol empty
    with_trend = compute_crossasset_scores(cats, 0, cfg, trend_by_symbol={"DAX": 3})["DAX"]
    assert no_trend["score_precise"] != with_trend["score_precise"]            # trend enters when present
    tf = next(f for f in no_trend["factors"] if f["name"] == "trend")
    assert tf["present"] is False                                             # absent without a cell
