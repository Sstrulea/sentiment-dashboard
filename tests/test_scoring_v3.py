"""Scoring v3 "swing" (2026-10-07): block math and weight rescaling, the pair and
US Dollar rows, the indices' asymmetric rule, labels from RMS, policy_rate_history,
and the v2 shadow = the v2 board on a fixed fixture."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.crossasset_compute import (V3_INDEX_WEIGHTS, compute_crossasset_scores_v3,
                                    v3_index_combine, v3_macro_raw, v3_yield_change)
from src.economic_compute import (V3_FX_WEIGHTS, apply_v3_fx, build_payload, v3_combine,
                                  v3_currency_blocks, v3_label, v3_macro_mean)
from src.ff_scoring import build_matcher, to_scoring_frame
from src.policy_rate import (load_decisions, policy_rate_history, policy_rates_with_fallback,
                             rate_at)

ROOT = Path(__file__).resolve().parents[1]
FX_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
X_CFG = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
IND_CFG = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"
K = {"sigma_macro": 0.5, "sigma_carry": 2.0, "sigma_cot": 2.0,
     "sigma_ccy": {c: {"growth": 0.5, "inflation": 0.5, "labour": 1.0} for c in ("USD", "EUR", "JPY")}}


def _cats(g=None, i=None, l=None):
    out = {}
    for name, v in (("growth", g), ("inflation", i), ("labour", l)):
        if v is not None:
            out[name] = {"score_precise": v, "coverage": 2}
    return out


# --- blocks and rescaling ---------------------------------------------------------

def test_macro_mean_over_present_categories_only():
    sig = {"growth": 0.5, "inflation": 0.5, "labour": 1.0}
    assert v3_macro_mean(_cats(1.0, -0.5, 2.0), sig) == pytest.approx((2.0 - 1.0 + 2.0) / 3)
    assert v3_macro_mean(_cats(1.0, None, 2.0), sig) == pytest.approx((2.0 + 2.0) / 2)
    assert v3_macro_mean({"growth": {"score_precise": 3.0, "coverage": 0}}, sig) is None


def test_blocks_and_weights_rescaled_over_present_blocks():
    b = v3_currency_blocks(1.0, -2.0, 3.0, K)
    assert b == {"macro": 2.0, "carry": -1.0, "cot": 1.5}
    s, c = v3_combine(b, V3_FX_WEIGHTS)
    assert s == pytest.approx(0.51 * 2.0 - 0.34 * 1.0 + 0.15 * 1.5)
    assert sum(c.values()) == pytest.approx(s)
    # carry missing → 0.51/0.66 and 0.15/0.66, never a zero-filled carry
    s2, c2 = v3_combine(dict(b, carry=None), V3_FX_WEIGHTS)
    assert s2 == pytest.approx((0.51 * 2.0 + 0.15 * 1.5) / 0.66)
    assert set(c2) == {"macro", "cot"}
    assert v3_combine({"macro": None, "carry": None, "cot": None}, V3_FX_WEIGHTS) == (None, {})


# --- pair and US Dollar -------------------------------------------------------------

def _card(ccy, g, i, l):
    cats, bd = {}, {}
    for cat, v in (("growth", g), ("inflation", i), ("labour", l)):
        cats[cat] = {"score_precise": float(v), "coverage": 1, "weight": 1.0, "score_cell": round(v)}
        bd[f"{cat}_x"] = {"score": int(v), "category": cat}
    return {"currency": ccy, "categories": cats, "breakdown": bd}


def _fx_payload():
    cards = {"EUR": _card("EUR", 1, -1, 2), "USD": _card("USD", -1, 1, 0), "JPY": _card("JPY", 0, 1, -1)}
    insts = [{"symbol": "EURUSD", "type": "fx", "score": 0.4, "bias": "Neutral",
              "breakdown": {"base": {"currency": "EUR"}, "quote": {"currency": "USD"}}},
             {"symbol": "US-DOLLAR", "type": "single", "score": -0.2, "bias": "Neutral",
              "breakdown": {"base": {"currency": "USD"}}}]
    return {"currencies": cards, "instruments": insts}


def _cfg():
    return {"v3": dict(K, rms={"EURUSD": 2.0, "US-DOLLAR": 1.0}, thresholds={"mild": 0.7, "very": 1.5})}


IND = {"indicators": {f"{c}_x": {"weight": 1.0} for c in ("growth", "inflation", "labour")}}


def test_pair_is_base_minus_quote_times_2_5_and_usd_row_uses_dxy():
    p = _fx_payload()
    apply_v3_fx(p, _cfg(), IND, {"EUR": 2, "DXY": -3}, {"EUR": 2.0, "USD": 4.0, "JPY": 0.0})
    cur = p["currencies"]
    eur, usd = p["instruments"]
    assert eur["score"] == pytest.approx((cur["EUR"]["score_v3"] - cur["USD"]["score_v3"]) * 2.5)
    # USD as a pair leg: COT = 0; the US Dollar row: the DXY cell
    assert cur["USD"]["v3"]["blocks"]["cot"] == 0.0
    assert usd["v3"]["blocks"]["cot"] == pytest.approx(-3 / 2.0)
    mean8 = 2.0
    blocks = v3_currency_blocks(v3_macro_mean(cur["USD"]["categories"], K["sigma_ccy"]["USD"]),
                                4.0 - mean8, -3, K)
    assert usd["score"] == pytest.approx(v3_combine(blocks, V3_FX_WEIGHTS)[0] * 2.5)
    for inst in p["instruments"]:
        assert abs(inst["contrib_residual"]) < 1e-12, inst["symbol"]
    assert eur["score_v2"] == 0.4 and eur["bias_v2"] == "Neutral"            # the v2 shadow
    assert eur["v3"]["carry"] == pytest.approx((2.0 - 4.0) / 2.0)
    assert eur["fund_score"] == pytest.approx(
        2.5 * (cur["EUR"]["v3"]["blocks"]["macro"] - cur["USD"]["v3"]["blocks"]["macro"]))
    assert cur["EUR"]["score_v2_ccy"] == pytest.approx(0.4) and cur["USD"]["score_v2_ccy"] == pytest.approx(-0.4)


def test_label_from_rms():
    th = {"mild": 0.7, "very": 1.5}
    assert v3_label(1.3, 2.0, th) == "Neutral"            # z 0.65
    assert v3_label(-1.6, 2.0, th) == "Bearish"           # z −0.8
    assert v3_label(3.0, 2.0, th) == "Very Bullish"       # z 1.5
    assert v3_label(5.0, None, th) == "Neutral"


# --- indices: the asymmetric rule ----------------------------------------------------

def test_index_rule_rates_up_or_flat_add():
    s, parts = v3_index_combine(1.0, 0.5)
    assert s == pytest.approx(0.67 + 0.165) and sum(parts.values()) == pytest.approx(s)


def test_index_rule_rising_rates_only_erase_a_positive_macro():
    s, parts = v3_index_combine(1.0, -3.0)                # m 0.67, r −0.99 → floored at 0
    assert s == 0.0 and sum(parts.values()) == pytest.approx(0.0)
    s, _ = v3_index_combine(1.0, -1.0)                    # m 0.67, r −0.33 → 0.34
    assert s == pytest.approx(0.34)


def test_index_rule_negative_macro_ignores_rising_rates():
    s, parts = v3_index_combine(-1.0, -2.0)
    assert s == pytest.approx(-0.67) and parts["rates"] == pytest.approx(0.0)


def test_index_with_macro_missing_rates_alone_floored_at_zero():
    s, parts = v3_index_combine(None, -1.0)
    assert s == 0.0 and parts == {"macro": 0.0, "rates": 0.0}
    s, parts = v3_index_combine(None, 1.2)                # full weight, not × 0.33
    assert s == pytest.approx(1.2) and sum(parts.values()) == pytest.approx(1.2)


def test_index_with_rates_missing_macro_alone_unchanged():
    assert v3_index_combine(1.0, None) == (1.0, {"macro": 1.0, "rates": 0.0})
    assert v3_index_combine(-1.5, None)[0] == -1.5       # macro alone may stay negative


def test_crossasset_contributions_sum_and_rates_signal():
    cats = {"USD": _cats(0.5, -0.4, 0.3), "JPY": _cats(0.1, 0.2, -0.3)}
    for c in cats.values():
        for v in c.values():
            v["coverage"] = 1
    sig = {"USD": {"growth": 0.3, "inflation": 0.5, "labour": 0.3}, "JPY": {"growth": 0.4, "inflation": 0.4, "labour": 0.7}}
    ys = {"USD": {"signal": -1.0}, "JPY": {"signal": 0.5}, "REAL10": {"signal": -0.4}}
    out = compute_crossasset_scores_v3(cats, X_CFG, sig, ys, metal_cot={"GOLD": 2})
    for sym, r in out.items():
        assert sum(f["contribution"] or 0.0 for f in r["factors"] if f["present"]) == pytest.approx(r["score_precise"], abs=1e-12)
    gold = out["GOLD"]
    rates = next(f for f in gold["factors"] if f["name"] == "rates")
    assert [c["name"] for c in rates["components"]] == ["rate_exp_2y"]          # metals: US 2Y only
    assert rates["value"] == pytest.approx(-1.0 / X_CFG["v3"]["sigma_rates"]["GOLD"])
    nik = next(f for f in out["NIKKEI"]["factors"] if f["name"] == "rates")
    assert nik["raw"] == pytest.approx((0.5 - 0.4) / 2)                          # home 2Y + US real 10Y
    m = v3_macro_raw(X_CFG["instruments"]["DJIA"], cats["USD"], sig["USD"])
    assert out["DJIA"]["v3"]["macro"] == pytest.approx(m / X_CFG["v3"]["sigma_macro"]["DJIA"])


def test_yield_change_uses_averaged_ends():
    dates = list(pd.date_range("2026-01-01", periods=80, freq="D").date)
    ys = [float(i) for i in range(80)]
    d, latest = v3_yield_change(dates, ys, dates[-1])
    assert d == pytest.approx(63.0) and latest == dates[-1]
    assert v3_yield_change(dates[:60], ys[:60], dates[59])[0] is None


# --- policy_rate_history ----------------------------------------------------------------

def _ff(rows):
    return pd.DataFrame([{"canonical_id": f"{c.lower()}_x_interest_rate_decision", "currency": c,
                          "datetime_utc": pd.Timestamp(t), "actual": a, "previous": p} for c, t, a, p in rows])


def test_fed_is_the_midpoint_and_a_missing_actual_takes_the_next_previous():
    ff = _ff([("USD", "2024-07-31 18:00", 5.50, 5.50), ("USD", "2024-09-18 18:00", None, 5.50),
              ("USD", "2024-11-07 19:00", 4.75, 5.00), ("CHF", "2024-09-26 07:30", 1.00, 1.25)])
    h = policy_rate_history(ff)
    assert rate_at(h, pd.Timestamp("2024-08-01"))["USD"] == pytest.approx(5.375)
    assert rate_at(h, pd.Timestamp("2024-10-01"))["USD"] == pytest.approx(5.00 - 0.125)   # next row's previous
    assert rate_at(h, pd.Timestamp("2024-10-01"))["CHF"] == 1.00
    assert "CHF" not in rate_at(h, pd.Timestamp("2024-09-01"))


def test_ecb_switch_on_2024_09_12():
    ff = _ff([("EUR", "2024-06-06 12:15", 4.25, 4.50), ("EUR", "2024-09-12 12:15", 3.65, 4.25)])
    h = policy_rate_history(ff)
    assert rate_at(h, pd.Timestamp("2024-07-01"))["EUR"] == pytest.approx(3.75)   # MRO − 0.50
    assert rate_at(h, pd.Timestamp("2024-09-13"))["EUR"] == pytest.approx(3.50)   # MRO − 0.15 (DFR)


def test_decisions_parquet_overrides_from_its_first_decision(tmp_path):
    ff = _ff([("USD", "2026-03-18 18:00", 4.00, 4.00), ("USD", "2026-04-29 18:00", 9.99, 4.00)])
    dec = pd.DataFrame([{"bank": "fed", "currency": "USD", "meeting_date": pd.Timestamp("2026-04-29").date(),
                         "decision_time_utc": pd.Timestamp("2026-04-29 18:00", tz="UTC"), "rate_before": 3.875,
                         "rate_after": 3.875, "lower": 3.75, "upper": 4.0, "delta_bp": 0.0,
                         "effective_date": pd.Timestamp("2026-04-30").date(), "consensus": None,
                         "surprise_consensus_bp": None, "rate_source": "fred", "status": "official", "notes": ""}])
    p = tmp_path / "d.parquet"
    dec.to_parquet(p, index=False)
    h = policy_rate_history(ff, load_decisions(p))
    assert rate_at(h, pd.Timestamp("2026-04-01"))["USD"] == pytest.approx(3.875)      # FF, converted
    assert rate_at(h, pd.Timestamp("2026-05-01"))["USD"] == pytest.approx(3.875)      # decisions, not FF 9.99
    assert set(h.loc[h["release_dt"] >= pd.Timestamp("2026-04-29"), "source"]) == {"cb_decisions"}


def test_policy_rates_fallback_is_per_currency_and_keeps_zero():
    out = policy_rates_with_fallback(None, pd.Timestamp("2026-10-07"),
                                     {"CHF": {"rate_pct": 0.0}, "USD": {"rate_pct": None}})
    assert out == {"CHF": 0.0}


# --- the v2 shadow is the v2 board ------------------------------------------------------

def test_v2_shadow_equals_the_v2_board_on_the_frozen_fixture():
    cal = to_scoring_frame(pd.read_parquet(FROZEN_FF), build_matcher())
    as_of = pd.Timestamp("2026-09-23T07:06:11")
    cal = cal[pd.to_datetime(cal["release_dt"]) <= as_of]
    v2_cfg = {k: v for k, v in FX_CFG.items() if k != "v3"}
    sent = {"EUR": 2, "GBP": -1, "JPY": 1, "CHF": 0, "CAD": -2, "AUD": 1, "NZD": 3, "DXY": -1}
    v2 = build_payload(cal, IND_CFG, v2_cfg, as_of=as_of, sentiment_cells=sent)
    v3 = build_payload(cal, IND_CFG, FX_CFG, as_of=as_of, sentiment_cells=sent,
                       policy_rates={"USD": 3.875, "EUR": 2.5, "GBP": 3.75, "JPY": 1.25,
                                     "AUD": 4.35, "NZD": 2.75, "CAD": 2.25, "CHF": 0.0})
    a = {i["symbol"]: (i["score"], i["bias"]) for i in v2["instruments"]}
    b = {i["symbol"]: (i["score_v2"], i["bias_v2"]) for i in v3["instruments"]}
    assert a == b
    assert all(abs(i["contrib_residual"]) < 1e-9 for i in v3["instruments"])
