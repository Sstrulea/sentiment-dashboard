"""Faza B — TREND kill switch (config/pipeline.yaml `trend_enabled`, default
false; see docs/accepted-degradations.md, MT5 dependency).

Written BEFORE the implementation and confirmed to fail against the
pre-flag `src/economic_render.py` (TREND was unconditional there — no flag,
no gate, "trend"/"trend_detail" always present, `_freshness()` always
watched "price"). Deliberately integration-style, over the REAL repo data
(data/economic_calendar_ff.parquet, data/rates.parquet, ... — same files
`build_economic_payload()` always reads, same technique as
scripts/diag/trend_off_diff.py), because the flag's whole contract is about
what the real, full payload does and does not contain — a synthetic
fixture would only prove the plumbing, not the actual DOM/JSON contract.

The score tests below are invariants (audit 4D): disabled == trendless,
enabled-without-cells == disabled bit for bit, and a trend cell folds in its
own direction — they hold on any data, unlike the former comparison with the
Faza A CSV snapshot, which drifted with every refresh.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import economic_render

ROOT = Path(__file__).resolve().parents[1]
def _all_instruments(payload: dict) -> list[dict]:
    return list(payload.get("instruments", [])) + \
        list((payload.get("crossasset", {}) or {}).get("instruments", []))


# --- _trend_enabled() itself -------------------------------------------------

def test_trend_enabled_defaults_false_when_key_absent():
    assert economic_render._trend_enabled({}) is False


def test_trend_enabled_reads_explicit_true_and_false():
    assert economic_render._trend_enabled({"trend_enabled": True}) is True
    assert economic_render._trend_enabled({"trend_enabled": False}) is False


def test_live_config_default_is_false():
    """config/pipeline.yaml ships with trend_enabled: false — the new default,
    mirroring calendar_source's ff/mt5 pattern (true = rollback)."""
    assert economic_render._trend_enabled() is False


# --- trend_score_all() must never be called while disabled -------------------

def test_trend_score_all_not_called_when_disabled(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("trend_score_all() was called with trend_enabled=false")
    monkeypatch.setattr(economic_render, "trend_score_all", _boom)
    payload = economic_render.build_economic_payload()   # live default: false
    assert payload["meta"]["trend_enabled"] is False


# --- no trend/trend_detail key anywhere when disabled -------------------------

def test_trend_disabled_no_instrument_carries_trend_keys():
    payload = economic_render.build_economic_payload()
    assert payload["meta"]["trend_enabled"] is False

    fx = payload["instruments"]
    ca = payload["crossasset"]["instruments"]
    assert fx, "expected FX instruments in the live payload"
    assert ca, "expected cross-asset instruments in the live payload"

    for inst in fx + ca:
        assert "trend" not in inst, f"{inst['symbol']}: trend key present while disabled"
        assert "trend_detail" not in inst, f"{inst['symbol']}: trend_detail key present while disabled"


# --- trend off / rollback: invariants over the same in-process data --------
# (audit 4D: these used to compare with the frozen docs/trend-off-before-after
# .csv from Faza A, which pinned values on the LIVE data/ files and broke with
# every hourly refresh. The contract is data-independent, so it is tested as
# invariants: whatever the data, disabled == trendless and the enabled path is
# a pure fold of the trend cell on top of it.)

_AS_OF = __import__("pandas").Timestamp("2026-09-23T07:06:11")


def _scores(payload: dict) -> dict:
    return {i["symbol"]: (i["score"], i.get("bias", i.get("bias_label")))
            for i in _all_instruments(payload)}


def test_trend_disabled_scores_are_the_trendless_scores(monkeypatch):
    monkeypatch.setattr(economic_render, "_trend_enabled", lambda cfg=None: False)
    payload = economic_render.build_economic_payload(as_of=_AS_OF)
    from src.economic_compute import bias_label
    th = payload["meta"]["bias_thresholds"]
    for inst in payload["instruments"]:
        keys = {c["key"] for c in inst["contributions"]}
        assert "trend" not in keys, inst["symbol"]
        assert inst["contrib_sum"] == pytest.approx(inst["score"], abs=1e-9), inst["symbol"]
        assert inst["bias"] == bias_label(inst["score"], th), inst["symbol"]


def test_trend_enabled_without_cells_is_bit_identical_to_disabled(monkeypatch):
    """Rollback proof, data-independent: flipping the flag on changes nothing
    by itself — only a real trend cell can move a score."""
    monkeypatch.setattr(economic_render, "_trend_enabled", lambda cfg=None: False)
    off = economic_render.build_economic_payload(as_of=_AS_OF)
    monkeypatch.setattr(economic_render, "_trend_enabled", lambda cfg=None: True)
    monkeypatch.setattr(economic_render, "trend_score_all", lambda: {})
    on = economic_render.build_economic_payload(as_of=_AS_OF)
    assert on["meta"]["trend_enabled"] is True
    assert _scores(on) == _scores(off)


def test_trend_enabled_folds_the_trend_cell_in_its_direction(monkeypatch):
    monkeypatch.setattr(economic_render, "_trend_enabled", lambda cfg=None: False)
    off = _scores(economic_render.build_economic_payload(as_of=_AS_OF))
    monkeypatch.setattr(economic_render, "_trend_enabled", lambda cfg=None: True)
    fx = [i["symbol"] for i in economic_render.build_economic_payload(as_of=_AS_OF)["instruments"]]
    cells = {sym: {"trend_cell": 3} for sym in fx}
    monkeypatch.setattr(economic_render, "trend_score_all", lambda: cells)
    on = economic_render.build_economic_payload(as_of=_AS_OF)
    for inst in on["instruments"]:
        if inst["symbol"] not in cells or inst["type"] != "fx":
            continue
        assert "trend" in inst and "trend_detail" in inst, inst["symbol"]
        assert inst["score"] >= off[inst["symbol"]][0] - 1e-12, inst["symbol"]
        trend = [c for c in inst["contributions"] if c["key"] == "trend"]
        assert trend and trend[0]["contribution"] == pytest.approx(
            inst["score"] - off[inst["symbol"]][0], abs=1e-9), inst["symbol"]


# --- _freshness()'s "price" entry ---------------------------------------------

def test_freshness_omits_price_when_trend_disabled():
    f = economic_render._freshness(trend_enabled=False)
    assert "price" not in f


def test_freshness_includes_price_when_trend_enabled():
    # This repo's data/price_history.parquet exists (see docs/trend-off-before-after.csv,
    # generated against it) — the "price" entry is only skipped for the flag,
    # never silently dropped for another reason.
    assert economic_render.PRICE_HISTORY_PARQUET.exists()
    f = economic_render._freshness(trend_enabled=True)
    assert "price" in f


def test_build_economic_payload_freshness_matches_the_live_flag():
    payload = economic_render.build_economic_payload()
    assert payload["meta"]["trend_enabled"] is False
    assert "price" not in payload["freshness"]
