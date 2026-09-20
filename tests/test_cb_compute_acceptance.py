"""1B-2 acceptance on REAL data frozen at 2026-09-18 (tests/fixtures/cb_engine, made by scripts/cb_freeze_engine_fixture.py).

Where the method is the same as in the 0A / 0B spike, the RAW rate (before the spread) must reproduce the spike's value
within 0.5 bp; where the method changed on purpose (GBP interval average, EUR basis, AUD / CAD tail rule, BoC effective
date +1, spread median) the new value, the 0A value and the difference are pinned here so a later change is visible."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from src import cb_probe
from src.cb_compute import analysis as A
from src.cb_compute import engine as E
from src.cb_compute.__main__ import main as cli
from src.cb_loader import load_context, load_pairs

FIX = Path(__file__).parent / "fixtures" / "cb_engine"
ROOT = Path(__file__).resolve().parents[1]
D = date
ASOF = D(2026, 9, 18)
TOL = 0.005                                     # 0.5 bp in percentage points


@pytest.fixture(scope="module")
def ctx():
    return load_context(FIX)


@pytest.fixture(scope="module")
def reports(ctx):
    return {c: A.bank_report(ctx, c, ASOF) for c in ctx.banks}


def point(tr, meeting):
    return tr.point_for(meeting)


def raw(tr, meeting):
    """Value before the spread: implied benchmark-space rate."""
    p = point(tr, meeting)
    return p.rate + (tr.spread.value if tr.spread and tr.spread.value is not None else 0.0)


# --- raw values against the 0A / 0B spike (same method) ----------------------------------------------------------------

def test_usd_mpt_windows_reproduce_0a_after_the_errata(reports):
    tr = reports["USD"].trajectory
    p26, p27 = point(tr, D(2026, 12, 9)), point(tr, D(2027, 12, 8))
    assert p26.window == (D(2026, 12, 16), D(2027, 3, 17)) and p27.window == (D(2027, 12, 15), D(2028, 3, 15))
    assert raw(tr, D(2026, 12, 9)) == pytest.approx(4.310, abs=TOL)               # 0B E1: MPT 12-16 window, mean
    assert raw(tr, D(2027, 12, 8)) == pytest.approx(4.663 - 0.025, abs=TOL)       # 0B E1: 4.663 = 4.638 + 2.5 bp SOFR spread
    assert (p26.flag, p26.extra["interior"]) == ("UPPER_BOUND", 1) and p27.extra["interior"] == 0


def test_jpy_tona_windows_reproduce_0a(reports):
    tr = reports["JPY"].trajectory
    assert tr.base.rate == 1.25 and tr.base.pending and tr.base.eff == D(2026, 9, 24)      # decided 18 Sep, in force 24 Sep
    p = point(tr, D(2026, 12, 18))
    assert p.eff == D(2026, 12, 21) and p.window == (D(2026, 12, 16), D(2027, 3, 17))       # BoJ 18 Dec, effective 21 Dec
    assert p.extra["pre_days"] == 5 and p.extra["gap_days"] == -5
    assert raw(tr, D(2026, 12, 18)) == pytest.approx(1.475, abs=TOL)                        # 0B E2: contract 202612
    assert raw(tr, D(2027, 12, 17)) == pytest.approx(2.107, abs=TOL)                        # 0A raw dec-27


def test_cad_coa_december_and_cra_windows_reproduce_0a(ctx, reports):
    tr = reports["CAD"].trajectory
    dec = point(tr, D(2026, 12, 9))
    assert dec.flag == "EXACT" and dec.rate == pytest.approx(2.600, abs=TOL)                # 0A: 9 Dec +35bp (2.600)
    cra = ctx.market.latest("mx_corra", ASOF)
    w = next(r for r in cra.rows if r["instrument"] == "corra_3m_futures" and r["ref_start"] == D(2026, 12, 16))
    assert E._raw_rate(w) == pytest.approx(2.775, abs=TOL)                                   # CRAZ26: 12-16 .. 03-17
    assert raw(tr, D(2027, 12, 8)) == pytest.approx(3.595, abs=TOL)                          # CRA 2027-12-15
    assert point(tr, D(2027, 12, 8)).flag == "UPPER_BOUND"                                   # COA horizon ends in Dec 2026


def test_aud_ib_chain_reproduces_0a_where_the_method_is_unchanged(reports):
    tr = reports["AUD"].trajectory
    assert point(tr, D(2027, 12, 14)).rate == pytest.approx(4.885, abs=TOL)                  # 0A: 14 Dec 2027 (+54bp)
    assert point(tr, D(2027, 12, 14)).cum_bp == pytest.approx(53.5, abs=0.6)


# --- the 0A chain function reproduces the spike; the new chain differs only by the tail rule ---------------------------

def legacy_chain(ctx, cur, source, inst, effs):
    snap = ctx.market.latest(source, ASOF)
    months = {(r["ref_start"].year, r["ref_start"].month): E._raw_rate(r) for r in snap.rows if r["instrument"] == inst}
    sp = E.trajectory(ctx, cur, ASOF).spread.value
    r0 = E.rate_in_force(ctx.decisions[cur], D(2026, 9, 1)) + sp
    return {eff: post - sp for eff, post, _ in cb_probe.implied_1m_chain(months, effs, r0) if post is not None}


def test_legacy_0a_chain_reproduces_the_0a_numbers(ctx):
    cad = legacy_chain(ctx, "CAD", "mx_corra", "corra_1m_futures", [D(2026, 10, 28), D(2026, 12, 9)])          # BoC effective +0 in 0A
    assert (cad[D(2026, 10, 28)] - 2.25) * 100 == pytest.approx(12.0, abs=0.5)                                # 0A: 28 Oct +12bp
    assert cad[D(2026, 12, 9)] == pytest.approx(2.600, abs=TOL)                                                # 0A: 9 Dec +35bp
    aud = legacy_chain(ctx, "AUD", "asx_ib", "ib_30d_interbank_futures",
                       [m.eff for m in ctx.meetings["AUD"] if m.decision > ASOF and m.eff <= D(2028, 1, 31)])
    assert aud[D(2026, 12, 9)] == pytest.approx(4.726, abs=TOL)                                                # 0A: 8 Dec 4.726 (+38bp)


def test_where_the_method_changed_new_vs_0a_is_pinned(reports):
    """(new, 0A, difference in bp): the reasons are in ARCHITECTURE.md section 12."""
    cad, aud = reports["CAD"].trajectory, reports["AUD"].trajectory
    # BoC effective date +1 (was +0) and the tail rule (3 days left in October -> November's average, not the x27/4 formula)
    assert (point(cad, D(2026, 10, 28)).cum_bp, point(cad, D(2026, 10, 28)).extra["how"]) == (pytest.approx(14.0, abs=0.1), "next_month")
    assert point(cad, D(2026, 10, 28)).cum_bp - 12.0 == pytest.approx(2.0, abs=0.5)
    # RBA 29 Sep: one day left in September -> October's average; the chain no longer carries the x30/1 noise into November / December
    p = point(aud, D(2026, 12, 8))
    assert p.rate == pytest.approx(4.7343, abs=0.0002) and (p.rate - 4.726) * 100 == pytest.approx(0.8, abs=0.1)
    assert point(aud, D(2026, 9, 29)).extra["how"] == "next_month" and point(aud, D(2026, 9, 29)).cum_bp == pytest.approx(21.5, abs=0.1)


def test_gbp_interval_average_and_eur_level_against_0a(reports):
    gbp, eur = reports["GBP"].trajectory, reports["EUR"].trajectory
    g26, g27 = point(gbp, D(2026, 12, 17)), point(gbp, D(2027, 12, 16))
    assert g26.rate == pytest.approx(4.160, abs=0.001) and (g26.rate - 4.051) * 100 == pytest.approx(10.9, abs=0.3)   # interval average over a rising curve vs the point forward
    assert g27.rate == pytest.approx(4.854, abs=0.001) and abs(g27.rate - 4.855) < TOL      # 2027: the curve is flat there: same as 0A
    # EUR: the ECB AAA curve starts at 3M, after the first effective date -> no basis, so no policy-equivalent value: only the raw level
    e26, e27 = point(eur, D(2026, 12, 17)), point(eur, D(2027, 12, 16))
    assert e26.flag == "PROXY" and e26.rate is None and e26.cum_bp is None and e26.level_kind == "sovereign_proxy"
    assert e26.level == pytest.approx(2.872, abs=0.001) and (e26.level - 2.821) * 100 == pytest.approx(5.1, abs=0.3)       # 0A raw @3.0m: interval average vs the point
    assert e27.level == pytest.approx(3.339, abs=0.001) and (e27.level - 3.397) * 100 == pytest.approx(-5.8, abs=0.3)
    assert eur.extra["proxy_basis"] is None and any("PROXY basis n/a" in n for n in eur.notes)


def test_new_spread_against_the_single_day_spread_of_0a(reports):
    """0A: the spread of the last day (USD SOFR -2.5 bp, on the day of the effective date); now the median of 20 business days."""
    sp = {c: r.trajectory.spread for c, r in reports.items() if r.trajectory.spread and r.trajectory.spread.value is not None}
    assert sp["USD"].bp == pytest.approx(2.0, abs=0.05) and sp["USD"].n == 16 and len(sp["USD"].excluded) == 4
    assert sp["JPY"].bp == pytest.approx(-2.3, abs=0.05) and sp["GBP"].bp == pytest.approx(-1.97, abs=0.05)
    assert sp["CAD"].bp == pytest.approx(4.0, abs=0.05) and sp["AUD"].bp == pytest.approx(0.0, abs=0.05)
    usd = reports["USD"].trajectory
    assert point(usd, D(2026, 12, 9)).rate == pytest.approx(4.290, abs=0.0005)              # 0B: 4.335 with -2.5 bp -> 4.290 with +2.0 bp
    assert (point(usd, D(2026, 12, 9)).rate - 4.335) * 100 == pytest.approx(-4.5, abs=0.1)
    assert {x for x, why in sp["USD"].excluded if "effective date" in why} == {D(2026, 9, 15), D(2026, 9, 16), D(2026, 9, 17)}


# --- outputs ----------------------------------------------------------------------------------------------------------

def test_next_meeting_step_and_probabilities(reports):
    aud, cad, gbp = reports["AUD"].next, reports["CAD"].next, reports["GBP"].next
    assert (aud.decision, aud.flag) == (D(2026, 9, 29), "EXACT") and aud.step_bp == pytest.approx(21.5, abs=0.1)
    assert aud.probabilities["moves"] == pytest.approx({0: 0.14, 1: 0.86}, abs=0.005) and aud.probabilities["direction"] == "hike"
    assert cad.step_bp == pytest.approx(14.0, abs=0.1) and cad.probabilities["moves"] == pytest.approx({0: 0.44, 1: 0.56}, abs=0.005)
    assert (gbp.flag, gbp.decision) == ("CURVE", D(2026, 11, 5)) and gbp.step_bp == pytest.approx(20.4, abs=0.1)
    for c in ("USD", "JPY", "NZD"):
        assert reports[c].next.probabilities is None and reports[c].next.prob_reason
    assert reports["CHF"].next.step_reason == "no market path for this currency"


def test_proxy_without_short_end_keeps_only_the_levels(reports):
    """EUR: no bp, no step, no probability, no surprise vs the market - only the raw levels, labelled, and the delta metrics."""
    eur = reports["EUR"]
    why = "proxy without short end"
    assert eur.next.step_bp is None and eur.next.probabilities is None and eur.next.step_reason.startswith(why) and eur.next.prob_reason.startswith(why)
    assert all(p.rate is None and p.cum_bp is None and p.step_bp is None and p.level is not None and p.level_kind == "sovereign_proxy"
               for p in eur.trajectory.points)
    assert all(s.vs_market_bp is None and s.vs_market_reason.startswith(why) for s in eur.surprises if s.decided)
    assert all(s.reaction_next_bp is not None for s in eur.surprises if s.decided)                      # the reaction needs no basis
    assert eur.trajectory.points[1].step_bp is None and eur.trajectory.points[2].step_bp is None


def test_year_end_cumulative_bp(reports):
    exp = {"USD": (41.5, 74.3, "UPPER_BOUND"), "GBP": (41.0, 110.4, "CURVE"), "JPY": (24.8, 88.1, "UPPER_BOUND"), "AUD": (38.4, 53.5, "EXACT")}
    for c, (y26, y27, flag) in exp.items():
        ye = reports[c].year_ends
        assert ye[2026].cum_bp == pytest.approx(y26, abs=0.1) and ye[2027].cum_bp == pytest.approx(y27, abs=0.1), c
        assert ye[2027].flag == flag or c == "AUD", c
    cad = reports["CAD"].year_ends
    assert (cad[2026].flag, cad[2027].flag) == ("EXACT", "UPPER_BOUND") and cad[2027].cum_bp == pytest.approx(130.5, abs=0.1)
    eur = reports["EUR"].year_ends                                                                     # a sovereign level, no bp
    assert all(eur[y].cum_bp is None and eur[y].rate is None and eur[y].level_kind == "sovereign_proxy" for y in (2026, 2027))
    assert eur[2026].level == pytest.approx(2.872, abs=0.001) and eur[2027].level == pytest.approx(3.339, abs=0.001)
    nzd = reports["NZD"]                                                                               # a BKBM level, no bp
    assert all(nzd.year_ends[y].cum_bp is None and nzd.year_ends[y].rate is None and nzd.year_ends[y].level_kind == "bkbm" for y in (2026, 2027))
    assert nzd.year_ends[2026].level == pytest.approx(3.450) and nzd.year_ends[2027].level == pytest.approx(3.800)
    assert nzd.trajectory.point_for(D(2026, 12, 9)).level == pytest.approx(3.450) and nzd.trajectory.point_for(D(2027, 2, 17)).level == pytest.approx(3.800)
    assert reports["CHF"].year_ends[2026].reason == "no market path for this currency" and reports["CHF"].year_ends[2026].level is None


def test_stale_is_only_flagged_beyond_two_business_days(ctx, reports):
    assert not any(p.stale for r in reports.values() for p in r.trajectory.points)
    for asof, lag, stale in ((D(2026, 9, 19), 1, False), (D(2026, 9, 21), 2, False), (D(2026, 9, 22), 3, True), (D(2026, 9, 24), 5, True)):
        p = E.trajectory(ctx, "GBP", asof).points[0]                                            # the BoE OIS snapshot is from Thu 09-17
        assert (p.lag_bd, p.stale) == (lag, stale), asof


def test_fed_gap_against_the_dots(reports):
    g = reports["USD"].gap
    assert g.kind == "dots" and g.sep == D(2026, 9, 16)
    got = {y.year: y for y in g.years}
    assert [(got[y].bank_median, got[y].n_dots) for y in (2026, 2027, 2028)] == [(4.125, 18), (4.125, 18), (3.875, 17)]
    assert [round(got[y].gap_bp, 1) for y in (2026, 2027, 2028)] == [16.5, 49.3, 60.0]
    assert got[2026].dots == [(4.375, 4), (4.125, 12), (3.875, 2)] and sum(n for _, n in got[2027].dots) == 18
    assert got[2026].market_flag == "UPPER_BOUND" and "2nd Wednesday" in got[2028].note
    assert got[2026].gap_bp == pytest.approx((got[2026].market_rate - 4.125) * 100)


def test_gap_is_na_for_banks_without_a_published_path_and_for_rbnz_without_a_90d_projection(reports):
    for c in ("EUR", "GBP", "JPY", "CAD", "AUD", "CHF"):
        assert reports[c].gap.kind == "n/a" and "does not publish" in reports[c].gap.reason
        assert reports[c].bank_path.kind == "n/a"
    nzd = reports["NZD"]
    assert nzd.gap.kind == "n/a" and "carry no 90-day bank bill projection" in nzd.gap.reason and not nzd.gap.years     # the same-basis rule
    assert nzd.bank_path.kind == "ocr_track" and nzd.bank_path.source == D(2026, 9, 2) and nzd.bank_path.finalised == D(2026, 8, 26)
    assert nzd.bank_path.points[:3] == [("2026Q3", 2.6), ("2026Q4", 2.8), ("2027Q1", 3.0)] and len(nzd.bank_path.points) == 13   # shown separately
    usd = reports["USD"].bank_path
    assert usd.kind == "dots" and usd.source == D(2026, 9, 16) and usd.points == [("2026", 4.125), ("2027", 4.125), ("2028", 3.875)]


def test_rbnz_gap_uses_the_same_basis_when_the_mps_is_filled(ctx):
    rbnz = {"mps": [{"meeting": D(2026, 9, 2), "status": "filled", "bank_bill_90d": [{"period": "2026Q4", "value": 3.30}, {"period": "2027Q1", "value": 3.60}]}]}
    ctx2 = load_context(FIX)
    ctx2.rbnz = rbnz
    g = A.rbnz_gap(ctx2, ASOF)
    assert g.kind == "bank_bill" and [round(y.gap_bp, 1) for y in g.years] == [15.0, 20.0]      # the BB whose 90-day period starts in the quarter: 3.45 (Dec-26) / 3.80 (Mar-27) minus the projection
    assert g.years[0].market_window == (D(2026, 12, 16), D(2027, 3, 17))


def test_repricing_main_value_is_the_change_of_the_level(reports):
    for c in ("USD", "EUR", "GBP"):
        for name in ("1s", "1l"):
            rp = reports[c].repricing[name]
            assert set(rp.level) == {2026, 2027} and not rp.reasons.get("all"), (c, name)
    for c in ("JPY", "CAD", "AUD", "NZD"):
        for name in ("1s", "1l"):
            assert "history starts 2026-09-18" in reports[c].repricing[name].reasons["all"]
    usd = reports["USD"].repricing["1s"]                          # a 25 bp hike fell in between: the level moved +7.5 bp, the cumulative bp -17.5 bp
    assert usd.level[2026] == pytest.approx(7.5, abs=0.1) and usd.level[2027] == pytest.approx(4.1, abs=0.1)
    assert usd.cum[2026] == pytest.approx(-17.5, abs=0.1) and usd.base_change_bp == 25.0
    assert reports["USD"].repricing["1l"].level[2026] == pytest.approx(37.5, abs=0.1)
    eur = reports["EUR"].repricing                                # raw sovereign levels: no basis needed for a difference
    assert eur["1s"].level[2026] == pytest.approx(-0.7, abs=0.1) and eur["1l"].level[2027] == pytest.approx(42.3, abs=0.1)
    assert eur["1s"].level_flag[2026] == "PROXY" and not eur["1s"].cum and eur["1s"].reasons["step"].startswith("proxy without short end")
    gbp = reports["GBP"].repricing                                # the SAME next meeting (5 Nov) at both dates, although on 11 Sep the next one was 17 Sep
    assert gbp["1s"].step_meeting == D(2026, 11, 5) and gbp["1s"].step_bp == pytest.approx(2.0, abs=0.1) and gbp["1l"].step_bp == pytest.approx(9.8, abs=0.1)
    assert gbp["1l"].level[2026] == pytest.approx(14.2, abs=0.1) and gbp["1l"].cum[2026] == pytest.approx(14.2, abs=0.1)


def test_surprises_and_reaction(reports):
    gbp = [s for s in reports["GBP"].surprises if s.decided]
    assert [s.meeting for s in gbp] == [D(2026, 4, 30), D(2026, 6, 18), D(2026, 7, 30), D(2026, 9, 17)]
    last = gbp[-1]
    assert last.delta_bp == 0 and last.vs_market_flag == "CURVE" and last.vs_market_bp == pytest.approx(-5.0, abs=0.1)
    assert last.reaction_next_target == D(2026, 11, 5) and last.reaction_year_target == D(2026, 12, 17)
    usd = [s for s in reports["USD"].surprises if s.decided]
    assert all(s.vs_market_bp is None and "only EXACT / CURVE / PROXY" in s.vs_market_reason for s in usd)
    assert usd[-1].delta_bp == 25.0 and usd[-1].reaction_next_bp == pytest.approx(4.5, abs=0.1) and usd[-1].reaction_next_flag == "UPPER_BOUND"
    assert all("no market history before 2026-09-18" in s.vs_market_reason for s in reports["JPY"].surprises if s.decided)
    eur = [s for s in reports["EUR"].surprises if s.decided]
    assert all(s.vs_market_bp is None and s.vs_market_reason.startswith("proxy without short end") for s in eur)
    assert eur[-1].reaction_next_bp == pytest.approx(9.7, abs=0.1) and eur[-1].reaction_year_bp == pytest.approx(11.1, abs=0.1)
    assert eur[-1].reaction_next_flag == "PROXY" and eur[-1].reaction_next_target == D(2026, 10, 29)
    assert [s.meeting for s in reports["AUD"].surprises if not s.decided] == [D(2026, 9, 29), D(2026, 11, 3)]


def test_cross_checks_are_reported_and_close(reports):
    usd = reports["USD"].crosschecks                                   # basis from the 1M bill (the only tenor before the first effective date, 29 Oct)
    assert len(usd) == 4 and all(abs(c.diff_bp) < 12 for c in usd) and "basis +9.5 bp" in usd[0].note
    cad = reports["CAD"].crosschecks
    assert len(cad) == 1 and cad[0].period == "2026-09-16..2026-12-16" and abs(cad[0].diff_bp) < 2                  # COA path vs CRA, same period
    (aud,) = reports["AUD"].crosschecks                                # the shortest RBA bill (1M) ends after the first effective date (30 Sep)
    assert aud.diff_bp is None and aud.na_reason.startswith("proxy without short end")
    assert reports["GBP"].crosschecks == [] and reports["EUR"].crosschecks == []


# --- pairs ------------------------------------------------------------------------------------------------------------

def test_the_28_pairs_come_from_the_economic_instruments():
    pairs = load_pairs()
    assert len(pairs) == 28 and ("EURUSD", "EUR", "USD") in pairs and ("USDJPY", "USD", "JPY") in pairs
    doc = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
    assert {n for n, i in doc["instruments"].items() if i["type"] == "fx"} == {n for n, _, _ in pairs}


def test_pairs_table_sign_and_flag(ctx, reports):
    rows = {p.pair: p for p in (A.pair_row(n, reports[b], reports[q]) for n, b, q in load_pairs())}
    assert rows["AUDUSD"].current_bp == pytest.approx((4.35 - 3.875) * 100)
    assert rows["AUDUSD"].cum_bp[2026] == pytest.approx(38.4 - 41.5, abs=0.1) and rows["AUDUSD"].flag[2026] == "UPPER_BOUND"
    assert rows["AUDCAD"].flag[2026] == "EXACT" and rows["AUDCAD"].flag[2027] == "UPPER_BOUND"
    assert rows["GBPAUD"].flag[2026] == "CURVE"
    assert rows["USDJPY"].current_bp == pytest.approx((3.875 - 1.25) * 100)
    for n, b, q in load_pairs():                                                                   # antisymmetry: reversing the pair flips the sign
        if rows[n].implied:
            rev = A.pair_row(q + b, reports[q], reports[b])
            assert rev.current_bp == pytest.approx(-rows[n].current_bp)
            assert rev.cum_bp[2026] == pytest.approx(-rows[n].cum_bp[2026]) and rev.flag == rows[n].flag
    assert rows["EURGBP"].reprice[("1l", 2026)] == pytest.approx(6.4, abs=0.2)                     # repricing of the differential


def test_pairs_are_na_metric_by_metric_not_pair_by_pair(reports):
    rows = {p.pair: p for p in (A.pair_row(n, reports[b], reports[q]) for n, b, q in load_pairs())}
    assert len(rows) == 28 and all(p.current_bp is not None for p in rows.values())                # both base rates exist, CHF and NZD included
    policy = {"USD", "GBP", "JPY", "CAD", "AUD"}                                                   # policy-equivalent implied levels
    assert {n for n, p in rows.items() if p.implied} == {n for n, p in rows.items() if p.base in policy and p.quote in policy}
    assert {n for n, p in rows.items() if p.reprice} == {"EURUSD", "GBPUSD", "EURGBP"}              # both legs have a change of level (history)
    eur = rows["EURUSD"]                                                                           # EUR proxy: no level differential, but a repricing
    assert not eur.implied and not eur.cum_bp and eur.reasons[("implied", 2026)][0][0] == "EUR"
    assert eur.reasons[("implied", 2026)][0][1].startswith("proxy without short end")
    assert eur.reprice[("1s", 2026)] == pytest.approx(-0.7 - 7.5, abs=0.2) and eur.reprice_flag[("1s", 2026)] == "PROXY"
    nzd = rows["NZDUSD"]                                                                           # NZD on BKBM: no level differential; a repricing once it has history
    assert not nzd.implied and "BKBM" in nzd.reasons[("implied", 2026)][0][1] and ("1s", 2026) not in nzd.reprice
    assert nzd.reasons[("reprice", "1s", 2026)] == [("NZD", "history starts 2026-09-18 (needs 5 business days back to 2026-09-11)")]
    chf = rows["USDCHF"]
    assert chf.current_bp == pytest.approx(387.5) and not chf.implied and not chf.reprice
    assert chf.reasons[("implied", 2026)] == [("CHF", "no market path for this currency")]
    assert rows["NZDCHF"].reasons[("implied", 2026)][0][0] == "NZD" and rows["NZDCHF"].reasons[("implied", 2026)][1][0] == "CHF"       # both legs named


# --- CLI / report -----------------------------------------------------------------------------------------------------

def test_cli_report_prints_every_bank_and_the_pairs(capsys):
    assert cli(["--report", "--asof", "2026-09-18", "--data-dir", str(FIX), "--points", "3"]) == 0
    out = capsys.readouterr().out
    for c in ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"):
        assert f"=== {c} - as of 2026-09-18" in out
    for token in ("GAP = market - bank", "repricing 1s", "repricing 1l", "surprises and reaction", "=== Pairs", "UPPER_BOUND", "n/a ("):
        assert token in out
    assert out.count("\n  ") > 100 and "AUDUSD" in out and "CADCHF" in out


def test_cli_is_deterministic_and_requires_report(capsys):
    args = ["--report", "--asof", "2026-09-18", "--data-dir", str(FIX), "--currency", "GBP"]
    cli(args)
    a = capsys.readouterr().out
    cli(args)
    assert capsys.readouterr().out == a and "=== USD" not in a
    assert cli([]) == 2


def test_report_at_an_earlier_asof_shows_history_and_na_for_missing_sources(ctx):
    r = A.bank_report(ctx, "AUD", D(2026, 8, 10), with_history=False)
    assert r.trajectory.na_reason and "no quotes on or before 2026-08-10" in r.trajectory.na_reason
    usd = A.bank_report(ctx, "USD", D(2026, 8, 10), with_history=False)
    assert usd.trajectory.base.rate == 3.625 and usd.next.probabilities is None


# --- configuration ----------------------------------------------------------------------------------------------------

def test_config_roles_methods_spreads_and_stale_threshold(ctx):
    src = ctx.sources
    methods = {"EXACT", "CURVE", "WINDOW", "PROXY_CURVE", "PROXY_TENOR"}
    for sid, c in src.items():
        assert c["role"] in ("primary", "crosscheck"), sid
        assert all(i["method"] in methods for i in c["instruments"].values()), sid
    primary = {cur: [(s, {i["method"] for i in c["instruments"].values()}) for s, c in src.items() if c["currency"] == cur and c["role"] == "primary"]
               for cur in ctx.banks}
    assert primary["USD"] == [("atlantafed_mpt", {"WINDOW"})] and primary["EUR"] == [("ecb_aaa_fwd", {"PROXY_CURVE"})]
    assert primary["GBP"] == [("boe_ois", {"CURVE"})] and primary["JPY"] == [("jpx_tona", {"WINDOW"})]
    assert primary["CAD"] == [("mx_corra", {"EXACT", "WINDOW"})] and primary["AUD"] == [("asx_ib", {"EXACT"})]
    assert primary["NZD"] == [("asx_bb", {"WINDOW"})] and primary["CHF"] == []
    xc = {s for s, c in src.items() if c["role"] == "crosscheck"}
    assert xc == {"ust_bills", "boc_tbills", "rba_bank_bills"}
    assert ctx.stale_after_bd == 2
    official = yaml.safe_load((ROOT / "config" / "cb_official.yaml").read_text())["series"]
    for cur, bank in ctx.banks.items():
        sp = bank.get("spread")
        if sp:
            assert sp["benchmark"] in official and all(p in official for p in sp["policy"]), cur
    assert {c for c, b in ctx.banks.items() if not b.get("spread")} == {"EUR", "NZD", "CHF"}


def test_engine_modules_are_pure():
    """src/cb_compute/ reads no file and no URL: all I/O lives in src/cb_loader.py and the CLI entry point."""
    for f in ("methods", "spread", "engine", "analysis", "report"):
        text = (ROOT / "src" / "cb_compute" / f"{f}.py").read_text()
        for bad in ("open(", "read_text", "requests", "urllib", "pq.read", "pd.read", "yaml.", "import os", "subprocess"):
            assert bad not in text, (f, bad)
