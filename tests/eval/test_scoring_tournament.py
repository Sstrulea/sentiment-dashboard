"""Scoring tournament (eval/scoring-tournament) — Step 5 tests.

1. No look-ahead: for every variant, the score at 3 random Fridays is identical
   with all the data and with the data cut at t (and the state_at record itself is
   identical when every production input is cut at t).
2. A1 reproduced = the v3_scales reconstruction (< 1e-9).
3. Entry/exit dates on an example with a holiday.
4. Balanced accuracy on a small example computed by hand.

1 and 2 need the local caches (~/.cache/macro-tournament: collect.py, prices);
they are skipped when the caches are missing.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOUR = ROOT / "scripts" / "measure" / "tournament"
sys.path.insert(0, str(TOUR))
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import common  # noqa: E402
import metrics as M  # noqa: E402
import prices as P  # noqa: E402

HAVE_CACHE = (common.CACHE / "weeks.pkl").exists() and (common.YAHOO_DIR / "GSPC.csv").exists() \
    and all(p.exists() for p in common.STOOQ.values())
needs_cache = pytest.mark.skipif(not HAVE_CACHE, reason="tournament caches not built")


# ---------------------------------------------------------------------------
# 3. Entry / exit with a holiday
# ---------------------------------------------------------------------------

def _series(days):
    return pd.Series(np.arange(1.0, len(days) + 1.0), index=pd.DatetimeIndex(days))


def test_entry_exit_monday_holiday():
    # 2024-01-15 (MLK day) has no close: entry moves to Tuesday 01-16; exit = first
    # close on or after 01-16 + 7 = 01-23.
    days = pd.bdate_range("2024-01-08", "2024-02-02").drop(pd.Timestamp("2024-01-15"))
    px = _series(days)
    t = pd.Timestamp("2024-01-12 21:00")
    d0, p0, d1, p1 = P.entry_exit(px, t, 1)
    assert d0 == pd.Timestamp("2024-01-16") and d1 == pd.Timestamp("2024-01-23")
    assert P.fwd_log_return(px, t, 1) == pytest.approx(math.log(p1 / p0))


def test_exit_on_holiday_moves_forward():
    # entry Monday 2024-02-12; entry + 7 = 2024-02-19 (Presidents Day, no close) → 02-20
    days = pd.bdate_range("2024-02-05", "2024-03-15").drop(pd.Timestamp("2024-02-19"))
    px = _series(days)
    d0, _, d1, _ = P.entry_exit(px, pd.Timestamp("2024-02-09 21:00"), 1)
    assert d0 == pd.Timestamp("2024-02-12") and d1 == pd.Timestamp("2024-02-20")
    # h = 2: 02-26 exists
    assert P.entry_exit(px, pd.Timestamp("2024-02-09 21:00"), 2)[2] == pd.Timestamp("2024-02-26")


def test_no_exit_returns_none():
    px = _series(pd.bdate_range("2024-02-05", "2024-02-16"))
    assert P.entry_exit(px, pd.Timestamp("2024-02-09 21:00"), 1) is None


def test_past_return_uses_closes_up_to_friday():
    days = pd.bdate_range("2024-01-01", "2024-01-31").drop(pd.Timestamp("2024-01-15"))
    px = _series(days)
    t = pd.Timestamp("2024-01-19 21:00")
    # last close <= 01-19 (pos of 01-19) vs last close <= 01-12
    want = math.log(px[pd.Timestamp("2024-01-19")] / px[pd.Timestamp("2024-01-12")])
    assert P.past_log_return(px, t, 1) == pytest.approx(want)


@needs_cache
def test_entry_exit_real_fred_holiday():
    # FRED H.10 has no fix on 2024-01-15 (MLK): entry on 01-16
    eur = P.fred_usd_values()["EUR"]
    d0, _, d1, _ = P.entry_exit(eur, pd.Timestamp("2024-01-12 21:00"), 1)
    assert pd.Timestamp("2024-01-15") not in eur.index
    assert d0 == pd.Timestamp("2024-01-16") and d1 == pd.Timestamp("2024-01-23")


# ---------------------------------------------------------------------------
# 4. Balanced accuracy by hand
# ---------------------------------------------------------------------------

def test_balanced_accuracy_by_hand():
    # calls (s≠0, r≠0): (1,+.5) ok, (-1,+.2) wrong, (2,-.1) wrong, (-3,-.4) ok, (1,+.1) ok
    # (0,+.3) no call, (1, 0) no call, (nan, .2) no call
    s = np.array([[1, -1, 2, 0, -3, 1, 1, np.nan]], dtype=float)
    r = np.array([[.5, .2, -.1, .3, -.4, 0.0, .1, .2]])
    ba, hit, n = M.ba_from(M.call_counts(s, r).sum(0))
    up, down = 2 / 3, 1 / 2
    assert n == 5
    assert ba == pytest.approx((up + down) / 2) == pytest.approx(7 / 12)
    assert hit == pytest.approx(3 / 5)


def test_balanced_accuracy_resum_equals_pooled():
    rng = np.random.default_rng(1)
    s, r = rng.normal(size=(30, 5)), rng.normal(size=(30, 5))
    c = M.call_counts(s, r)
    a = M.ba_from(c.sum(0))[0]
    m = (s != 0) & (r != 0)
    up, dn = m & (r > 0), m & (r < 0)
    b = 0.5 * ((up & (s > 0)).sum() / up.sum() + (dn & (s < 0)).sum() / dn.sum())
    assert a == pytest.approx(b)


def test_holm():
    p = {"a": 0.01, "b": 0.04, "c": 0.03}
    h = M.holm(p)
    assert h["a"] == pytest.approx(0.03) and h["c"] == pytest.approx(0.06) and h["b"] == pytest.approx(0.06)


# ---------------------------------------------------------------------------
# 1–2. No look-ahead and A1 reproduction (need the caches)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def data():
    import collect
    import variants as VV
    recs = collect.load()["recs"]
    return VV.Data({r["week"]: r for r in recs}), recs


def _weeks(recs, k=3, seed=20261016):
    ws = [r["week"] for r in recs if r["week"] <= M.END]
    rng = np.random.default_rng(seed)
    return [ws[i] for i in sorted(rng.choice(len(ws), size=k, replace=False))]


def _eq(a, b, tol=1e-12):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    return abs(float(a) - float(b)) <= tol


@needs_cache
def test_no_lookahead_every_variant(data):
    import variants as VV
    D, recs = data
    for t in _weeks(recs):
        Dc = D.cut(t)
        for name in VV.VARIANTS:
            full, cut = VV.VARIANTS[name](D, t), VV.VARIANTS[name](Dc, t)
            for part in ("ccy", "pair", "cross"):
                for u in set(full[part]) | set(cut[part]):
                    assert _eq(full[part].get(u), cut[part].get(u)), (name, str(t), part, u)
            assert _eq(full["usd_row"], cut["usd_row"]), (name, str(t), "usd_row")


@needs_cache
def test_state_at_record_identical_with_inputs_cut_at_t(data):
    """The collected record at t = state_at on every production input cut at t
    (COT enriched again from the cut raw rows)."""
    import collect
    import factor_scales as fs
    from src.compute import compute_metrics, load_meta
    from src.cot_score import DATE_COL
    D, recs = data
    inp = collect.inputs()
    raw = pd.read_parquet(ROOT / "data" / "history.parquet")
    meta = load_meta()
    by_w = {r["week"]: r for r in recs}
    for t in _weeks(recs):
        d = pd.Timestamp(t.date())
        inp_c = collect.inputs()
        inp_c.cal = inp.cal[pd.to_datetime(inp.cal["release_dt"]) <= t]
        inp_c.rates = inp.rates[pd.to_datetime(inp.rates["date"]) <= d]
        inp_c.ry = inp.ry[pd.to_datetime(inp.ry["date"]) <= d]
        inp_c.liq = inp.liq[pd.to_datetime(inp.liq["date"]) <= d]
        inp_c.pc = inp.pc[pd.to_datetime(inp.pc["date"]) <= d]
        e = compute_metrics(raw[pd.to_datetime(raw[DATE_COL]) <= t])
        e["symbol"] = e["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("symbol"))
        cat = e["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("category"))
        inp_c.cot_fx = e[cat == "fx"].copy()
        inp_c.cot_metal = e[e["symbol"].isin(("GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER"))].copy()
        rc = collect.record(fs.state_at(inp_c, t), t)
        r0 = by_w[t]
        assert rc["fx_cells"] == r0["fx_cells"] and rc["metal_cells"] == r0["metal_cells"]
        for c in r0["cats"]:
            for k, v in r0["cats"][c].items():
                assert _eq(v["score_precise"], rc["cats"][c][k]["score_precise"]), (str(t), c, k)
        for s, v in r0["v2_fx"].items():
            assert _eq(v[0], rc["v2_fx"][s][0], 1e-9), (str(t), s)
        for s, v in r0["v2_cross"].items():
            assert _eq(v[0], rc["v2_cross"][s][0], 1e-9), (str(t), s)


@needs_cache
def test_a1_reproduces_v3_scales(data):
    """A1 (tournament) = v3_scales.v3_week on v3_scales' own record of state_at at t."""
    import collect
    import factor_scales as fs
    import v3_scales as V
    import variants as VV
    import yaml
    from src import economic_render as er
    from src.policy_rate import load_decisions, policy_rate_history
    D, recs = data
    inp = collect.inputs()
    inst = yaml.safe_load(er.INSTRUMENTS_YAML.read_text())
    xcfg = yaml.safe_load(er.CROSSASSET_YAML.read_text())
    k = V.config_constants(inst, xcfg)
    hist = policy_rate_history(pd.read_parquet(ROOT / "data/economic_calendar_ff.parquet"), load_decisions())
    series = V.yield_series()
    for t in _weeks(recs, seed=7):
        st = fs.state_at(inp, t)
        cards = st["payload"]["currencies"]
        # the record exactly as v3_scales.collect builds it
        rv = {"week": t,
              "cats": {c: {kk: {"score_precise": v["score_precise"], "coverage": v["coverage"]}
                           for kk, v in cards[c]["categories"].items() if kk in V.V3_MACRO_CATS} for c in cards},
              "fx_cells": dict(st["fx_cells"]),
              "metal_cells": {s: v for s, v in st["sent"].items() if s in ("GOLD", "SILVER")}}
        want = V.v3_week(rv, hist, k, inst, xcfg, series)
        got = VV.a1(D, t)
        for s, v in want["scores"].items():
            g = got["usd_row"] if s == VV.USD_ROW else (got["pair"].get(s) if s in got["pair"] else got["cross"].get(s))
            assert (v is None and g is None) or abs(v - g) < 1e-9, (str(t), s, v, g)
        for c, v in want["ccy"].items():
            assert (v is None and got["ccy"][c] is None) or abs(v - got["ccy"][c]) < 1e-9, (str(t), c)
