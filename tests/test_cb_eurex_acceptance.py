"""Eurex OIS acceptance (pre-registered values, task spec section 10) on REAL data frozen at 2026-10-08
(tests/fixtures/cb_engine_1008, scripts/cb_freeze_engine_fixture.py --asof 2026-10-08): the Eurex settlement file of
2026-10-08 (seeded with scripts/cb_seed_eurex.py) and the meetings of data/cb/meetings.yaml.

Checked: the implied overnight rate after each meeting (r_k, BEFORE the spread), +-1.0 bp, and the largest pillar residual.
External reference, not a test - RateProbability 2026-10-09 07:00, Fed cumulative vs EFFR: 28 Oct +7.5, 9 Dec +28.3, 27 Jan +36.2,
17 Mar +51.2, 28 Apr +59.8; here, vs r_0: +5.4, +26.2, +34.5, +54.4, +63.5."""
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
TOL = 0.010                                       # 1.0 bp in percentage points

EXPECTED = {                                      # pre (r_0), then the rate after each meeting, overnight benchmark space
    "USD": (3.8783, {D(2026, 10, 28): 3.9322, D(2026, 12, 9): 4.1407, D(2027, 1, 27): 4.2232, D(2027, 3, 17): 4.4224,
                     D(2027, 4, 28): 4.5135, D(2027, 6, 9): 4.6013}),
    "EUR": (2.4395, {D(2026, 10, 29): 2.4737, D(2026, 12, 17): 2.6724, D(2027, 2, 4): 2.7603, D(2027, 3, 18): 2.9338,
                     D(2027, 4, 29): 2.9856, D(2027, 6, 10): 3.0966}),
    "GBP": (3.7355, {D(2026, 11, 5): 3.9522, D(2026, 12, 17): 4.0934, D(2027, 2, 4): 4.3020, D(2027, 3, 18): 4.4432,
                     D(2027, 4, 29): 4.5799, D(2027, 6, 17): 4.6725}),
    "JPY": (1.2273, {D(2026, 10, 30): 1.2481, D(2026, 12, 18): 1.4777, D(2027, 1, 22): 1.5145, D(2027, 3, 18): 1.6938,
                     D(2027, 4, 28): 1.7409, D(2027, 6, 11): 1.8734}),
    "CHF": (-0.0528, {D(2026, 12, 10): 0.0222, D(2027, 3, 18): 0.1485, D(2027, 6, 24): 0.3528, D(2027, 9, 23): 0.4915,
                      D(2027, 12, 16): 0.5604}),
}
SOFR = (3.8880, {D(2026, 10, 28): 3.9507, D(2026, 12, 9): 4.1717})
MAX_RESIDUAL_BP = {"USD": 0.6, "EUR": 0.6, "GBP": 0.6, "JPY": 0.6, "CHF": 1.6}
PRIMARY = {"USD": "eurex_ois_usd", "EUR": "eurex_ois_eur", "GBP": "eurex_ois_gbp", "JPY": "eurex_ois_jpy", "CHF": "eurex_ois_chf"}


@pytest.fixture(scope="module")
def ctx():
    return load_context(FIX)


@pytest.fixture(scope="module")
def reports(ctx):
    return {c: A.bank_report(ctx, c, ASOF) for c in ctx.banks}


@pytest.mark.parametrize("cur", sorted(EXPECTED))
def test_rate_after_each_meeting_matches_the_preregistered_values(reports, cur):
    tr = reports[cur].trajectory
    pre, after = EXPECTED[cur]
    assert tr.extra["ois_source"] == PRIMARY[cur]
    assert tr.extra["ois_r0"] == pytest.approx(pre, abs=TOL)
    for meeting, r in after.items():
        p = tr.point_for(meeting)
        assert p is not None and p.extra["how"] == "ois_step" and p.flag == "CURVE", (cur, meeting)
        assert p.extra["r_raw"] == pytest.approx(r, abs=TOL), (cur, meeting)
        assert p.rate == pytest.approx(p.extra["r_raw"] - tr.spread.value, abs=1e-12) and p.level == p.rate, (cur, meeting)   # policy-equivalent = r_k - spread


@pytest.mark.parametrize("cur", sorted(EXPECTED))
def test_largest_pillar_residual(reports, cur):
    tr = reports[cur].trajectory
    res = [abs(c[2]) for c in tr.consistency if c[0] == f"OIS {PRIMARY[cur]}"]
    assert len(res) >= 18 and max(res) < MAX_RESIDUAL_BP[cur]                          # 1W-3W + the monthly pillars to the last meeting + 56 days
    assert tr.extra["ois_max_residual_bp"] == pytest.approx(max(res))


def test_sofr_crosscheck_curve(ctx, reports):
    tr = reports["USD"].trajectory
    snap = ctx.market.latest("eurex_ois_usd_sofr", ASOF)
    unknown = [m for m in ctx.meetings["USD"] if m.decision > ASOF]
    (fit, inside), why = E.ois_fit(ctx, tr, unknown, "eurex_ois_usd_sofr", snap, [r for r in snap.rows if r["instrument"] == "usd_sofr_ois_df"])
    assert why == "" and fit.rates[0] == pytest.approx(SOFR[0], abs=TOL)
    eff = {m.decision: m.eff for m in unknown}
    for meeting, r in SOFR[1].items():
        assert fit.rate_from(eff[meeting]) == pytest.approx(r, abs=TOL), meeting
    xc = [c for c in reports["USD"].crosschecks if c.name == "USD Eurex fed funds vs SOFR OIS"]
    assert len(xc) == 6 and all(abs(c.diff_bp) < 5 for c in xc)                              # policy space: the two spreads removed
    xs = E.spread_for(ctx, "USD", ASOF, "crosscheck_spread")
    assert xs.benchmark == "fred:SOFR" and tr.spread.benchmark == "fred:EFFR"                # both +0.5 bp on 2026-10-08
    first = unknown[0]
    assert xc[0].b == pytest.approx(fit.rate_from(first.eff) - xs.value, abs=1e-12) and xc[0].a == tr.point_for(first.decision).rate


def test_every_eurex_bank_has_a_per_meeting_step_and_probability(reports):
    for cur in EXPECTED:
        nm = reports[cur].next
        assert nm.flag == "CURVE" and nm.step_bp is not None and nm.probabilities, cur
        assert nm.point.cum_bp == pytest.approx(nm.step_bp + (reports[cur].trajectory.extra["ois_r0"] - reports[cur].trajectory.spread.value
                                                              - reports[cur].trajectory.base.rate) * 100, abs=1e-6), cur
    usd = reports["USD"].trajectory
    cum_vs_r0 = [round((usd.point_for(m).extra["r_raw"] - usd.extra["ois_r0"]) * 100, 1) for m in list(EXPECTED["USD"][1])[:5]]
    assert cum_vs_r0 == pytest.approx([5.4, 26.2, 34.5, 54.4, 63.5], abs=0.15)                # the figures quoted next to RateProbability


def test_spreads_are_the_new_benchmark_pairs(reports):
    sp = {c: reports[c].trajectory.spread for c in EXPECTED}
    assert (sp["USD"].benchmark, sp["EUR"].benchmark, sp["CHF"].benchmark) == ("fred:EFFR", "ecb:ESTR", "snb:SARON")
    assert sp["USD"].policy == "fred:DFEDTARL+fred:DFEDTARU" and sp["EUR"].policy == "ecb:DFR" and sp["CHF"].policy == "snb:LZ"
    assert all(s.value is not None and s.n >= 10 for s in sp.values())
    for c in EXPECTED:                                                                           # r_0 vs base + spread, in the notes
        assert any(n.startswith(f"OIS {PRIMARY[c]}: r_0") for n in reports[c].trajectory.notes)


def test_crosschecks_against_the_former_primaries(reports):
    names = {c: {x.name for x in reports[c].crosschecks} for c in EXPECTED}
    assert {"USD Eurex fed funds vs SOFR OIS", "USD Eurex fed funds OIS vs MPT"} <= names["USD"]
    mpt = next(x for x in reports["USD"].crosschecks if x.name == "USD Eurex fed funds OIS vs MPT")
    assert mpt.na_reason.startswith("MPT is stale: as-of 2026-09-18")                            # MPT has no new as-of since 18 Sep
    gbp = [x for x in reports["GBP"].crosschecks if x.name == "GBP Eurex SONIA vs BoE OIS"]
    assert [x.period[:8] for x in gbp] == ["end-2026", "end-2027"] and all(abs(x.diff_bp) < 10 for x in gbp)
    jpy = [x for x in reports["JPY"].crosschecks if x.name == "JPY Eurex TONA vs JPX TONA-3M"]
    assert len(jpy) == 4 and all(abs(x.diff_bp) < 5 for x in jpy)
    eur = [x for x in reports["EUR"].crosschecks if x.name.startswith("EUR Eurex EUR STR vs ECB AAA")]
    assert len(eur) == 2 and "informative" in eur[0].name and eur[0].na_reason                  # end-2026 starts before the AAA 3M tenor
    assert names["CHF"] == set()


def test_cad_aud_nzd_are_unchanged(reports):
    assert reports["CAD"].trajectory.points[0].flag == "EXACT" and reports["AUD"].trajectory.points[0].flag == "EXACT"
    assert reports["NZD"].trajectory.points[0].flag == "UPPER_BOUND" and reports["NZD"].trajectory.points[0].level_kind == "bkbm"
