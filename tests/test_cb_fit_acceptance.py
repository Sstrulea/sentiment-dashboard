"""FIT acceptance (pre-registered values, stage-2 spec section 6) on the engine fixture frozen at 2026-10-08
(tests/fixtures/cb_engine_1008). Its MX snapshot is the one stored at 12:41Z on 9 Oct (see docs/accepted-degradations.md,
2026-10-09): the test checks the method on fixed data, not the market.

NZD: BKBM levels before the spread, with r_0 = OCR + the estimated BKBM-OCR spread (config/cb_nzd_spread.yaml, 16.0 bp). The
first pre-registration was computed with r_0 = OCR (no spread) - an error in the reference, corrected by Sebastian on 2026-10-09;
the values below are the corrected pre-registration."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.cb_compute import analysis as A
from src.cb_compute import engine as E
from src.cb_loader import load_context

FIX = Path(__file__).parent / "fixtures" / "cb_engine_1008"
D = date
ASOF = D(2026, 10, 8)
TOL = 0.010                                           # 1.0 bp

CAD = {D(2027, 1, 27): 2.5460, D(2027, 3, 3): 2.6944, D(2027, 4, 28): 2.9341, D(2027, 6, 2): 2.9943, D(2027, 7, 21): 3.0407,
       D(2027, 9, 8): 3.1590, D(2027, 10, 27): 3.2260, D(2027, 12, 8): 3.2344}
NZD = {D(2026, 12, 9): 3.3475, D(2027, 2, 10): 3.5942, D(2027, 3, 17): 3.7285, D(2027, 5, 5): 3.8712, D(2027, 6, 16): 4.0185,
            D(2027, 8, 4): 4.1140, D(2027, 9, 15): 4.1657, D(2027, 10, 27): 4.1933, D(2027, 12, 8): 4.1968}    # r_0 = 2.75 + 0.16


@pytest.fixture(scope="module")
def ctx():
    return load_context(FIX)


def fit_res(tr, sid):
    return [abs(c[2]) for c in tr.consistency if c[0] == f"FIT {sid}"]


def test_cad_policy_rate_after_each_estimated_meeting(ctx):
    tr = E.trajectory(ctx, "CAD", ASOF)
    assert tr.base.rate == 2.25 and tr.spread.value == pytest.approx(0.04, abs=1e-9)
    for meeting, r in CAD.items():
        p = tr.point_for(meeting)
        assert (p.method, p.flag, p.level_kind) == ("FIT", "ESTIMATE", "policy"), meeting
        assert p.rate == pytest.approx(r, abs=TOL), meeting
    res = fit_res(tr, "mx_corra")
    assert tr.extra["fit_contracts"] == 9 and len(res) == 9 and max(res) <= 2.0
    worst = max((c for c in tr.consistency if c[0] == "FIT mx_corra"), key=lambda c: abs(c[2]))
    assert worst[1] == "corra_3m_futures 202609" and worst[2] == pytest.approx(1.8, abs=0.1)        # the CRA that covers realised days


def test_cad_exact_meetings_are_unchanged(ctx):
    tr = E.trajectory(ctx, "CAD", ASOF)
    saved = ctx.sources["mx_corra"]
    ctx.sources["mx_corra"] = dict(saved, instruments={**saved["instruments"], "corra_3m_futures": dict(saved["instruments"]["corra_3m_futures"], method="WINDOW")})
    try:
        before = E.trajectory(ctx, "CAD", ASOF)                                                       # the stage-1 methods
    finally:
        ctx.sources["mx_corra"] = saved
    for m in (D(2026, 10, 28), D(2026, 12, 9)):
        a, b = tr.point_for(m), before.point_for(m)
        assert a.flag == b.flag == "EXACT" and a.rate == b.rate and a.step_bp == b.step_bp
    assert tr.point_for(D(2027, 1, 27)).step_bp == pytest.approx((tr.point_for(D(2027, 1, 27)).level - tr.point_for(D(2026, 12, 9)).level) * 100)


def test_nzd_with_the_estimated_spread(ctx):
    r = A.bank_report(ctx, "NZD", ASOF)
    tr = r.trajectory
    sp = ctx.estimated_spreads["NZD"]["value_bp"] / 100
    assert tr.spread.estimated and tr.spread.value == pytest.approx(sp) and tr.extra["fit_r0"] == pytest.approx(2.75 + sp)
    assert tr.point_for(D(2026, 10, 28)).rate is None and tr.point_for(D(2026, 10, 28)).reason == E.NOT_COVERED
    for meeting, lvl in NZD.items():
        p = tr.point_for(meeting)
        assert p.extra["r_raw"] == pytest.approx(lvl, abs=TOL) and p.rate == pytest.approx(p.extra["r_raw"] - sp) and p.flag == "ESTIMATE", meeting
        assert any(n.startswith("BKBM-OCR spread estimated (16 bp, median of 62 months)") for n in p.notes), meeting
    dec = tr.point_for(D(2026, 12, 9))
    assert "includes the 2026-10-28 meeting" in dec.notes and dec.step_bp == pytest.approx((dec.level - 2.75) * 100)
    assert r.next.decision == D(2026, 10, 28) and r.next.step_bp is None and r.next.probabilities is None
    assert tr.extra["fit_contracts"] == 4 and max(fit_res(tr, "asx_bb")) <= 0.1
