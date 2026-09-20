"""Engine (phase 1B-2) on a made-up currency with a known rate path: base rate, spread, the four methods end to end, the
per-meeting outputs (step, probability, year end), repricing, surprises / reaction and the currency pairs."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.cb_calendar import weekends_only
from src.cb_compute import analysis as A
from src.cb_compute import engine as E
from src.cb_compute import spread as S

from .cb_synth import (BENCH, CUR, D, POL, bdays, curve_quotes, dec, futures_month, make_ctx, month_avg, quote, src, window_avg)

ASOF = D(2026, 9, 30)
BASE_DEC = dec(D(2026, 9, 16), D(2026, 9, 17), 1.75, 2.00)          # the last decided change; benchmark spread contamination stays away
BASE_PATH = [(D(2026, 1, 1), 1.75), (D(2026, 9, 17), 2.00)]


def ctx_exact(path, meetings, *, spread=0.10, asof=ASOF, months=((2026, 10), (2026, 11), (2026, 12), (2027, 1)), decisions=None, **kw):
    quotes = futures_month("fut", "inst", path, spread, asof, list(months))
    return make_ctx(decisions=decisions or [BASE_DEC], meetings=[(D(2026, 9, 16), D(2026, 9, 17))] + meetings, quotes=quotes,
                    policy_path=BASE_PATH, spread=spread, sources=src("fut", "EXACT"), **kw)


# --- base rate --------------------------------------------------------------------------------------------------------

BOJ = [dec(D(2026, 7, 31), D(2026, 8, 3), 0.75, 1.00), dec(D(2026, 9, 18), D(2026, 9, 24), 1.00, 1.25)]


def test_base_is_the_last_decided_rate_including_a_decision_not_yet_in_force():
    assert E.base_rate(BOJ, D(2026, 9, 17)).rate == 1.00
    for day in (D(2026, 9, 18), D(2026, 9, 21), D(2026, 9, 23)):
        b = E.base_rate(BOJ, day)
        assert b.rate == 1.25 and b.pending and b.eff == D(2026, 9, 24)      # BoJ between 18 and 24 Sep
    b = E.base_rate(BOJ, D(2026, 9, 24))
    assert b.rate == 1.25 and not b.pending


def test_rate_in_force_switches_on_the_effective_date_not_the_decision():
    assert E.rate_in_force(BOJ, D(2026, 9, 23)) == 1.00 and E.rate_in_force(BOJ, D(2026, 9, 24)) == 1.25
    assert E.pending_steps(BOJ, D(2026, 9, 20), D(2026, 9, 1)) == [(D(2026, 9, 24), 1.25)]
    assert E.pending_steps(BOJ, D(2026, 9, 17), D(2026, 9, 1)) == []           # not decided yet at that as-of


def test_base_before_any_decision_falls_back_to_rate_before_of_the_next_one():
    b = E.base_rate(BOJ, D(2026, 6, 1))
    assert b.rate == 0.75 and b.meeting is None and "rate_before" in b.note


# --- spread -----------------------------------------------------------------------------------------------------------

def spread_ctx(daily: dict, policy_path, asof):
    return make_ctx(decisions=[], meetings=[], quotes=[], policy_path=policy_path, spread=0.05, spread_by_day=daily,
                    official_start=D(2026, 7, 1), official_end=asof)


def test_spread_median_of_20_business_days_excludes_effective_date_window_and_month_end():
    """Effective 17 Sep +-2 business days (15..21 Sep) and the last business day of August are excluded: the median is the
    median of the 14 clean days, not of all 20."""
    path = [(D(2026, 1, 1), 2.00), (D(2026, 9, 17), 2.25)]
    days = bdays(D(2026, 8, 24), D(2026, 9, 28))
    clean = [d for d in days if not (D(2026, 9, 15) <= d <= D(2026, 9, 21)) and d != D(2026, 8, 31)]
    daily = {d: 0.02 + 0.001 * i for i, d in enumerate(clean)}                # distinct clean values
    daily |= {d: -0.50 for d in days if D(2026, 9, 15) <= d <= D(2026, 9, 21)}    # contaminated by the change
    daily[D(2026, 8, 31)] = 0.90                                               # month-end pressure
    ctx = spread_ctx(daily, path, D(2026, 9, 28))
    sp = S.compute_spread(ctx.view, weekends_only(), BENCH, [POL], D(2026, 9, 28), [D(2026, 9, 17)])
    window = bdays(D(2026, 8, 31), D(2026, 9, 28))[-20:]
    assert (sp.start, sp.end) == (window[0], window[-1]) and len(window) == 20
    kept = [daily[d] for d in window if d not in {x for x, _ in sp.excluded}]
    excluded = {d for d, _ in sp.excluded}
    assert excluded == {d for d in window if D(2026, 9, 15) <= d <= D(2026, 9, 21)} | ({D(2026, 8, 31)} & set(window))
    assert sp.n == len(kept) == 20 - len(excluded)
    assert sp.value == pytest.approx(sorted(kept)[len(kept) // 2 - 1] / 2 + sorted(kept)[len(kept) // 2] / 2 if len(kept) % 2 == 0 else sorted(kept)[len(kept) // 2])
    assert sp.value > 0.01                                                    # the -50 bp days did not leak in


def test_spread_last_business_day_of_month_is_excluded_even_when_it_is_a_friday():
    path = [(D(2026, 1, 1), 2.00)]
    days = bdays(D(2026, 8, 20), D(2026, 9, 18))
    daily = {d: 0.03 for d in days} | {D(2026, 8, 31): 0.80}
    for d in days[:9]:
        daily[d] = 0.80                                                        # pushes the median up if month-end were kept
    ctx = spread_ctx(daily, path, D(2026, 9, 18))
    sp = S.compute_spread(ctx.view, weekends_only(), BENCH, [POL], D(2026, 9, 18), [])
    assert (D(2026, 8, 31), "last business day of the month") in sp.excluded


def test_spread_ends_at_the_last_observation_and_reports_missing_series():
    path = [(D(2026, 1, 1), 2.00)]
    ctx = spread_ctx({}, path, D(2026, 9, 10))
    sp = S.compute_spread(ctx.view, weekends_only(), BENCH, [POL], D(2026, 9, 30), [])
    assert sp.end == D(2026, 9, 10) and sp.value == pytest.approx(0.05)
    assert S.compute_spread(ctx.view, weekends_only(), "x:none", [POL], D(2026, 9, 30), []).reason == "series missing"
    assert S.compute_spread(ctx.view, weekends_only(), None, None, D(2026, 9, 30), []).reason == "no spread pair for this bank"


def test_spread_of_a_target_range_uses_the_midpoint():
    from src.cb_compute.decisions import SeriesView
    v = SeriesView([{"series_id": "lo", "date": D(2026, 1, 1), "value": 3.75}, {"series_id": "hi", "date": D(2026, 1, 1), "value": 4.00}])
    assert S.policy_level(v, ["lo", "hi"], D(2026, 9, 1)) == 3.875


# --- EXACT end to end -------------------------------------------------------------------------------------------------

def test_exact_single_hike_gives_the_step_probability_and_year_end():
    path = BASE_PATH + [(D(2026, 10, 15), 2.25)]
    ctx = ctx_exact(path, [(D(2026, 10, 14), D(2026, 10, 15)), (D(2026, 12, 9), D(2026, 12, 10))])
    tr = E.trajectory(ctx, CUR, ASOF)
    assert tr.base.rate == 2.00 and tr.spread.value == pytest.approx(0.10)
    p1, p2 = tr.points
    assert (p1.flag, p1.method) == ("EXACT", "EXACT")
    assert p1.rate == pytest.approx(2.25) and p1.cum_bp == pytest.approx(25.0) and p1.step_bp == pytest.approx(25.0)
    assert p2.rate == pytest.approx(2.25) and p2.cum_bp == pytest.approx(25.0) and p2.step_bp == pytest.approx(0.0, abs=1e-6)
    nm = E.next_meeting(tr)
    assert nm.decision == D(2026, 10, 14) and nm.probabilities == {"direction": "hike", "moves": {1: 1.0}}
    ye = E.year_end(ctx, tr, 2026)
    assert ye.meeting == D(2026, 12, 9) and ye.cum_bp == pytest.approx(25.0) and ye.flag == "EXACT" and not ye.stale


def test_exact_flat_trajectory_is_zero_bp_everywhere():
    ctx = ctx_exact(BASE_PATH, [(D(2026, 10, 14), D(2026, 10, 15)), (D(2026, 11, 25), D(2026, 11, 26)), (D(2026, 12, 9), D(2026, 12, 10))])
    tr = E.trajectory(ctx, CUR, ASOF)
    assert [round(p.cum_bp, 6) for p in tr.points] == [0.0, 0.0, 0.0]
    assert [round(p.step_bp, 6) for p in tr.points] == [0.0, 0.0, 0.0]
    assert all(abs(c[2]) < 1e-6 for c in tr.consistency)
    nm = E.next_meeting(tr)
    assert nm.probabilities == {"direction": "hold", "moves": {0: 1.0}}


def test_exact_meeting_in_the_last_five_days_of_the_month_end_to_end():
    path = BASE_PATH + [(D(2026, 10, 15), 2.25), (D(2026, 11, 27), 2.50)]
    ctx = ctx_exact(path, [(D(2026, 10, 14), D(2026, 10, 15)), (D(2026, 11, 26), D(2026, 11, 27))])
    p1, p2 = E.trajectory(ctx, CUR, ASOF).points
    assert p2.rate == pytest.approx(2.50) and p2.step_bp == pytest.approx(25.0) and p2.extra["how"] == "next_month"
    assert p2.notes and "next month's average" in p2.notes[0]


def test_exact_spread_is_removed_before_the_chain():
    """Benchmark = policy + 10 bp: with no spread removal the implied policy rate would be 10 bp too high."""
    path = BASE_PATH + [(D(2026, 10, 15), 2.25)]
    ctx = ctx_exact(path, [(D(2026, 10, 14), D(2026, 10, 15))], spread=0.10)
    assert E.trajectory(ctx, CUR, ASOF).points[0].rate == pytest.approx(2.25)


def test_exact_pending_decision_is_part_of_the_base_and_of_the_chain():
    """A decided-not-effective change (BoJ style): base = the new rate; the chain treats the change as known."""
    decs = [dec(D(2026, 7, 31), D(2026, 8, 3), 1.75, 1.75, None), dec(D(2026, 9, 28), D(2026, 10, 6), 1.75, 2.00)]
    path = [(D(2026, 1, 1), 1.75), (D(2026, 10, 6), 2.00), (D(2026, 11, 12), 2.25)]
    quotes = futures_month("fut", "inst", path, 0.10, D(2026, 9, 29), [(2026, 10), (2026, 11), (2026, 12)])
    ctx = make_ctx(decisions=decs, meetings=[(D(2026, 11, 11), D(2026, 11, 12))], quotes=quotes, policy_path=[(D(2026, 1, 1), 1.75), (D(2026, 10, 6), 2.00)],
                   spread=0.10, sources=src("fut", "EXACT"), official_end=D(2026, 9, 29))
    tr = E.trajectory(ctx, CUR, D(2026, 9, 29))
    assert tr.base.rate == 2.00 and tr.base.pending
    (p,) = tr.points
    assert p.rate == pytest.approx(2.25) and p.cum_bp == pytest.approx(25.0)


def test_stale_when_the_source_lags_more_than_the_threshold():
    path = BASE_PATH
    meetings = [(D(2026, 10, 14), D(2026, 10, 15))]
    ctx = ctx_exact(path, meetings, asof=D(2026, 9, 25))                         # Friday snapshot
    fresh = E.trajectory(ctx, CUR, D(2026, 9, 28))                              # Monday: lag 1 bd
    late = E.trajectory(ctx, CUR, D(2026, 9, 30))                               # Wednesday: lag 3 bd
    assert (fresh.points[0].lag_bd, fresh.points[0].stale) == (1, False)
    assert (late.points[0].lag_bd, late.points[0].stale) == (3, True)
    edge = E.trajectory(ctx, CUR, D(2026, 9, 29))                               # Tuesday: lag 2 = threshold, not stale
    assert (edge.points[0].lag_bd, edge.points[0].stale) == (2, False)
    assert E.year_end(ctx, late, 2026).stale is True and E.next_meeting(late).stale is True    # the year-end value carries its point's flag
    ctx.sources["fut"]["stale_after_bd"] = 5
    assert E.trajectory(ctx, CUR, D(2026, 9, 30)).points[0].stale is False


# --- CURVE ------------------------------------------------------------------------------------------------------------

def ctx_curve(fn, meetings, *, spread=0.02, asof=ASOF, tenors=range(1, 37), decisions=None, method="CURVE", **kw):
    quotes = curve_quotes("crv", "inst", asof, fn, tenors)
    return make_ctx(decisions=decisions or [BASE_DEC], meetings=[(D(2026, 9, 16), D(2026, 9, 17))] + meetings, quotes=quotes,
                    policy_path=BASE_PATH, spread=spread, sources=src("crv", method), **kw)


def months(a, b):
    return (b - a).days / (365.25 / 12)


def test_curve_flat_at_base_plus_spread_is_zero_bp():
    ctx = ctx_curve(lambda t: 2.02, [(D(2026, 11, 4), D(2026, 11, 5)), (D(2026, 12, 16), D(2026, 12, 17))])
    tr = E.trajectory(ctx, CUR, ASOF)
    assert all(p.flag == "CURVE" and abs(p.cum_bp) < 1e-9 and abs(p.step_bp) < 1e-9 for p in tr.points)
    assert abs(tr.consistency[0][2]) < 1e-9                                    # curve average before the first meeting = base + spread


def test_curve_ramp_is_averaged_over_the_interval_between_effective_dates():
    e1, e2, e3 = D(2026, 11, 5), D(2026, 12, 17), D(2027, 2, 4)
    ctx = ctx_curve(lambda t: 3.0 + 0.1 * t, [(e1 - timedelta(1), e1), (e2 - timedelta(1), e2), (e3 - timedelta(1), e3)])
    tr = E.trajectory(ctx, CUR, ASOF)
    t = lambda d: months(ASOF, d)                                              # noqa: E731
    exp1 = 3.0 + 0.1 * (t(e1) + t(e2)) / 2 - 0.02
    exp2 = 3.0 + 0.1 * (t(e2) + t(e3)) / 2 - 0.02
    assert tr.points[0].rate == pytest.approx(exp1) and tr.points[1].rate == pytest.approx(exp2)
    assert tr.points[0].cum_bp == pytest.approx((exp1 - 2.00) * 100)
    assert tr.points[1].step_bp == pytest.approx((exp2 - exp1) * 100)
    last = tr.points[2]                                                         # the last meeting runs the tail (56 days)
    assert last.rate == pytest.approx(3.0 + 0.1 * (t(e3) + t(e3 + timedelta(days=56))) / 2 - 0.02)


def test_curve_single_hike_gives_the_step_and_probabilities():
    step = lambda t: 2.02 if t <= 1.0 else 2.02 + 0.25 * min(1.0, t - 1.0)      # +25 bp ramped over month 1..2
    e1 = D(2026, 12, 9)                                                         # ~2.3 months out: interval starts after the ramp
    ctx = ctx_curve(step, [(e1 - timedelta(1), e1)])
    (p,) = E.trajectory(ctx, CUR, ASOF).points
    assert p.rate == pytest.approx(2.25) and p.step_bp == pytest.approx(25.0) and p.cum_bp == pytest.approx(25.0)
    assert E.next_meeting(E.trajectory(ctx, CUR, ASOF)).probabilities["moves"] == {1: 1.0}


def test_curve_fractional_step_probabilities():
    ctx = ctx_curve(lambda t: 2.02 + 0.125 * min(1.0, max(0.0, t - 1.0)), [(D(2026, 12, 8), D(2026, 12, 9))])
    nm = E.next_meeting(E.trajectory(ctx, CUR, ASOF))
    assert nm.step_bp == pytest.approx(12.5) and nm.probabilities["moves"] == pytest.approx({0: 0.5, 1: 0.5})


def test_curve_beyond_the_horizon_is_na_with_a_reason():
    ctx = ctx_curve(lambda t: 2.02, [(D(2026, 11, 4), D(2026, 11, 5)), (D(2029, 6, 1), D(2029, 6, 2))], tenors=range(1, 13))
    p = E.trajectory(ctx, CUR, ASOF).points[1]
    assert p.rate is None and "beyond the curve horizon" in p.reason


# --- PROXY ------------------------------------------------------------------------------------------------------------

def test_proxy_basis_is_the_curve_average_before_the_first_meeting_minus_the_current_rate():
    """A government curve sitting 20 bp above the policy rate: the basis (+20 bp) is removed, the 25 bp hike is not."""
    curve = lambda t: 2.20 if t <= 3 else 2.45 if t >= 4 else 2.20 + 0.25 * (t - 3)         # +25 bp between the 3M and 4M knots
    ctx = ctx_curve(curve, [(D(2026, 11, 4), D(2026, 11, 5)), (D(2027, 2, 3), D(2027, 2, 4))], method="PROXY_CURVE", has_spread=False)
    tr = E.trajectory(ctx, CUR, ASOF)
    assert any("PROXY basis +20.0 bp" in n for n in tr.notes)
    p1, p2 = tr.points
    assert p1.flag == p2.flag == "PROXY"
    assert p2.rate == pytest.approx(2.25) and p2.cum_bp == pytest.approx(25.0)      # 2.45 curve - 0.20 basis
    assert 0 < p1.cum_bp < 10                                                       # its interval averages the ramp; a raw curve would say +25 or more
    assert p2.step_bp == pytest.approx(p2.cum_bp - p1.cum_bp)


def test_proxy_gives_a_step_but_no_probability():
    """Government curves carry term premia: the monthly PROXY step is shown (and flagged PROXY), the 25 bp probability is not."""
    ctx = ctx_curve(lambda t: 2.20 + 0.06 * t, [(D(2026, 11, 4), D(2026, 11, 5)), (D(2026, 12, 16), D(2026, 12, 17))],
                    method="PROXY_CURVE", has_spread=False)
    nm = E.next_meeting(E.trajectory(ctx, CUR, ASOF))
    assert nm.flag == "PROXY" and nm.step_bp is not None and nm.step_bp > 0
    assert nm.probabilities is None and "only EXACT / CURVE" in nm.prob_reason and "PROXY_CURVE" in nm.prob_reason


def test_proxy_flat_curve_above_the_rate_is_zero_bp_after_the_basis():
    ctx = ctx_curve(lambda t: 2.33, [(D(2026, 11, 4), D(2026, 11, 5)), (D(2026, 12, 16), D(2026, 12, 17))], method="PROXY_CURVE", has_spread=False)
    assert [round(p.cum_bp, 6) for p in E.trajectory(ctx, CUR, ASOF).points] == [0.0, 0.0]


def test_proxy_first_meeting_far_below_the_first_tenor_is_na_not_a_flat_extrapolation():
    ctx = ctx_curve(lambda t: 2.30, [(D(2026, 10, 15), D(2026, 10, 16)), (D(2027, 1, 13), D(2027, 1, 14))], method="PROXY_CURVE",
                    has_spread=False, tenors=range(3, 37))                       # grid starts at 3M (the ECB AAA case)
    p1, p2 = E.trajectory(ctx, CUR, ASOF).points
    assert p1.rate is None and "carries no information" in p1.reason
    assert p2.rate is not None and p2.step_bp is None and "previous meeting is not identified" in p2.extra["step_reason"]
    assert E.next_meeting(E.trajectory(ctx, CUR, ASOF)).probabilities is None


# --- WINDOW -----------------------------------------------------------------------------------------------------------

def win_quotes(asof, windows):
    return [quote("win", "inst", 100 - r, asof, ref_start=s, ref_end=e, contract=f"{s:%Y%m}") for s, e, r in windows]


def ctx_window(meetings, windows, *, spread=0.10, asof=ASOF, decisions=None, has_spread=True, **kw):
    return make_ctx(decisions=decisions or [BASE_DEC], meetings=[(D(2026, 9, 16), D(2026, 9, 17))] + meetings, quotes=win_quotes(asof, windows),
                    policy_path=BASE_PATH, spread=spread, sources=src("win", "WINDOW"), has_spread=has_spread, **kw)


WINDOWS = [(D(2026, 9, 16), D(2026, 12, 16), 2.30), (D(2026, 12, 16), D(2027, 3, 17), 2.55), (D(2027, 3, 17), D(2027, 6, 16), 2.90)]


def test_window_picks_the_window_after_the_effective_date_and_flags_upper_bound_with_interior_meetings():
    ctx = ctx_window([(D(2026, 12, 9), D(2026, 12, 10)), (D(2027, 1, 27), D(2027, 1, 28))], WINDOWS)
    p1, p2 = E.trajectory(ctx, CUR, ASOF).points
    assert p1.flag == "UPPER_BOUND" and p1.window == (D(2026, 12, 16), D(2027, 3, 17))
    assert p1.rate == pytest.approx(2.55 - 0.10) and p1.cum_bp == pytest.approx((2.45 - 2.00) * 100)
    assert p1.extra["interior"] == 1 and p1.extra["pre_days"] == 0 and p1.extra["gap_days"] == 6
    assert p1.step_bp is None and "UPPER_BOUND" in E.step_reason(p1)
    assert p2.window == (D(2027, 3, 17), D(2027, 6, 16)) and p2.extra["gap_days"] == 48         # 16 Dec is 43 days before 28 Jan: out


def test_window_boj_18_dec_effective_21_dec_window_from_16_dec_with_5_pre_days():
    decs = [dec(D(2026, 9, 18), D(2026, 9, 24), 1.00, 1.25)]
    ctx = ctx_window([(D(2026, 12, 18), D(2026, 12, 21))], WINDOWS, decisions=decs, spread=-0.02)
    (p,) = E.trajectory(ctx, CUR, D(2026, 9, 30)).points
    assert p.window == (D(2026, 12, 16), D(2027, 3, 17)) and p.extra["gap_days"] == -5 and p.extra["pre_days"] == 5
    assert p.flag == "UPPER_BOUND" and p.rate == pytest.approx(2.55 + 0.02)


def test_window_is_chosen_from_the_effective_date_not_the_decision_date():
    """Friday decision, Monday effective: windows starting 16 Dec (-5 from the effective date, -2 from the decision) and 23 Dec
    (+2 / +5). Closest to the effective date is 23 Dec; measured from the decision it would have been 16 Dec."""
    wins = [(D(2026, 12, 16), D(2027, 3, 17), 2.55), (D(2026, 12, 23), D(2027, 3, 24), 2.70)]
    ctx = ctx_window([(D(2026, 12, 18), D(2026, 12, 21))], wins)
    (p,) = E.trajectory(ctx, CUR, ASOF).points
    assert p.window == (D(2026, 12, 23), D(2027, 3, 24)) and p.extra["gap_days"] == 2 and p.extra["pre_days"] == 0
    old = [(D(2026, 12, 11), D(2027, 3, 10), 2.40), (D(2027, 3, 17), D(2027, 6, 16), 2.90)]      # 10 days before the effective date: out
    (q,) = E.trajectory(ctx_window([(D(2026, 12, 18), D(2026, 12, 21))], old), CUR, ASOF).points
    assert q.window == (D(2027, 3, 17), D(2027, 6, 16))


def test_window_upper_bound_has_no_probability_and_says_why():
    ctx = ctx_window([(D(2026, 12, 9), D(2026, 12, 10))], WINDOWS)
    nm = E.next_meeting(E.trajectory(ctx, CUR, ASOF))
    assert nm.step_bp is None and nm.probabilities is None
    assert "UPPER_BOUND" in nm.step_reason and nm.prob_reason


def test_window_without_an_ocr_spread_reports_the_level_but_no_bp():
    """NZD: the ASX bank bill is BKBM, not the OCR - no spread pair exists, so the level is shown and cum bp is n/a."""
    ctx = ctx_window([(D(2026, 12, 9), D(2026, 12, 10))], WINDOWS, has_spread=False)
    (p,) = E.trajectory(ctx, CUR, ASOF).points
    assert p.rate == pytest.approx(2.55) and p.cum_bp is None and "BKBM" in p.reason
    assert p.notes and "BKBM" in p.notes[0]


def test_window_meeting_beyond_the_last_contract_is_na():
    ctx = ctx_window([(D(2028, 12, 13), D(2028, 12, 14))], WINDOWS)
    (p,) = E.trajectory(ctx, CUR, ASOF).points
    assert p.rate is None and "no window starts" in p.reason


def test_windows_only_fill_the_meetings_the_exact_method_does_not_cover():
    """CAD: 1M chain first (EXACT), 3M windows beyond the chain's horizon (UPPER_BOUND)."""
    path = BASE_PATH + [(D(2026, 10, 15), 2.25)]
    quotes = (futures_month("fut", "inst", path, 0.10, ASOF, [(2026, 10), (2026, 11), (2026, 12)]) +
              [quote("fut", "wins", 100 - r, ASOF, ref_start=s, ref_end=e) for s, e, r in WINDOWS])
    sources = {"fut": {"currency": CUR, "role": "primary", "instruments": {"inst": {"method": "EXACT"}, "wins": {"method": "WINDOW"}}}}
    ctx = make_ctx(decisions=[BASE_DEC], meetings=[(D(2026, 10, 14), D(2026, 10, 15)), (D(2027, 1, 27), D(2027, 1, 28))], quotes=quotes,
                   policy_path=BASE_PATH, spread=0.10, sources=sources)
    p1, p2 = E.trajectory(ctx, CUR, ASOF).points
    assert p1.flag == "EXACT" and p1.rate == pytest.approx(2.25)
    assert p2.flag == "UPPER_BOUND" and p2.window == (D(2027, 3, 17), D(2027, 6, 16))


# --- n/a currencies ---------------------------------------------------------------------------------------------------

def test_currency_without_a_market_path_is_na_with_a_reason_everywhere():
    ctx = make_ctx(decisions=[BASE_DEC], meetings=[(D(2026, 12, 9), D(2026, 12, 10))], quotes=[], policy_path=BASE_PATH, sources={})
    tr = E.trajectory(ctx, CUR, ASOF)
    assert tr.na_reason == "no market path for this currency" and tr.points == []
    nm = E.next_meeting(tr)
    assert nm.step_bp is None and nm.probabilities is None and nm.step_reason == tr.na_reason
    ye = E.year_end(ctx, tr, 2026)
    assert ye.cum_bp is None and ye.reason == tr.na_reason


def test_year_end_already_decided_is_part_of_the_base_and_a_missing_year_says_so():
    decs = [BASE_DEC, dec(D(2026, 12, 9), D(2026, 12, 10), 2.00, 2.00)]
    ctx = ctx_exact(BASE_PATH, [(D(2026, 12, 9), D(2026, 12, 10)), (D(2027, 2, 3), D(2027, 2, 4))], asof=D(2026, 12, 15),
                    months=((2027, 1), (2027, 2), (2027, 3)), decisions=decs, official_end=D(2026, 12, 15))
    tr = E.trajectory(ctx, CUR, D(2026, 12, 15))
    ye = E.year_end(ctx, tr, 2026)
    assert ye.meeting == D(2026, 12, 9) and ye.flag == "DECIDED" and ye.cum_bp == 0.0 and "already decided" in ye.reason
    assert E.year_end(ctx, tr, 2028).reason == "no 2028 meeting on record"


# --- flags ------------------------------------------------------------------------------------------------------------

def test_weakest_flag_order():
    assert E.weakest("EXACT", "CURVE") == "CURVE"
    assert E.weakest("CURVE", "UPPER_BOUND", "EXACT") == "UPPER_BOUND"
    assert E.weakest("UPPER_BOUND", "PROXY") == "PROXY"
    assert E.weakest(None, "EXACT") == "EXACT" and E.weakest(None, None) is None


# --- repricing --------------------------------------------------------------------------------------------------------

def ctx_reprice(shift_recent: float):
    """Curve snapshots on three dates; the newest curve is `shift_recent` percentage points higher than the older ones."""
    cal = weekends_only()
    asof = D(2026, 9, 30)
    d5, d21 = cal.add_business_days(asof, -5), cal.add_business_days(asof, -21)
    quotes = []
    for d, lvl in ((d21, 2.02), (d5, 2.02), (asof, 2.02 + shift_recent)):
        quotes += curve_quotes("crv", "inst", d, lambda t, lvl=lvl: lvl, range(1, 37))
    meetings = [(D(2026, 11, 4), D(2026, 11, 5)), (D(2026, 12, 16), D(2026, 12, 17)), (D(2027, 12, 15), D(2027, 12, 16))]
    return make_ctx(decisions=[dec(D(2026, 7, 29), D(2026, 7, 30), 1.75, 2.00)], meetings=[(D(2026, 7, 29), D(2026, 7, 30))] + meetings, quotes=quotes,
                    policy_path=[(D(2026, 1, 1), 2.00)], spread=0.02, sources=src("crv", "CURVE"), official_start=D(2026, 6, 1)), asof, d5, d21


def test_repricing_is_the_change_of_the_cumulative_bp_over_5_and_21_business_days():
    ctx, asof, d5, d21 = ctx_reprice(0.10)
    r = A.bank_report(ctx, CUR, asof)
    for name, prev in (("1s", d5), ("1l", d21)):
        rp = r.repricing[name]
        assert rp.prev == prev and rp.cum[2026] == pytest.approx(10.0) and rp.cum[2027] == pytest.approx(10.0)
        assert rp.step_bp == pytest.approx(10.0) and rp.step_flag == "CURVE" and rp.level[2026] == pytest.approx(10.0)
        assert not rp.reasons


def test_repricing_flat_market_is_zero():
    ctx, asof, *_ = ctx_reprice(0.0)
    rp = A.bank_report(ctx, CUR, asof).repricing["1s"]
    assert rp.cum[2026] == pytest.approx(0.0) and rp.step_bp == pytest.approx(0.0)


def test_repricing_is_na_with_a_reason_when_the_history_is_too_short():
    ctx, asof, d5, d21 = ctx_reprice(0.10)
    only_today = [q for q in ctx.market._by["crv"][asof]]
    ctx.market = E.MarketIndex(only_today)
    r = A.bank_report(ctx, CUR, asof)
    assert "history starts 2026-09-30" in r.repricing["1s"].reasons["all"] and not r.repricing["1s"].cum
    assert "needs 21 business days" in r.repricing["1l"].reasons["all"]


def test_repricing_a_decision_in_between_is_flagged_and_the_level_change_is_kept():
    """The base moved +25 bp between the two dates: the change of the cumulative bp is -25 + level move; the level move is shown."""
    cal = weekends_only()
    asof = D(2026, 9, 30)
    d5 = cal.add_business_days(asof, -5)
    older = dec(D(2026, 7, 29), D(2026, 7, 30), 1.75, 1.75)
    newer = dec(D(2026, 9, 28), D(2026, 9, 29), 1.75, 2.00)
    quotes = curve_quotes("crv", "inst", d5, lambda t: 1.77, range(1, 37)) + curve_quotes("crv", "inst", asof, lambda t: 2.02, range(1, 37))
    quotes += curve_quotes("crv", "inst", cal.add_business_days(asof, -21), lambda t: 1.77, range(1, 37))
    ctx = make_ctx(decisions=[older, newer], meetings=[(D(2026, 12, 16), D(2026, 12, 17)), (D(2027, 12, 15), D(2027, 12, 16))], quotes=quotes,
                   policy_path=[(D(2026, 1, 1), 1.75), (D(2026, 9, 29), 2.00)], spread=0.02, sources=src("crv", "CURVE"), official_start=D(2026, 6, 1))
    rp = A.bank_report(ctx, CUR, asof).repricing["1s"]
    assert rp.base_change_bp == pytest.approx(25.0)
    assert rp.cum[2026] == pytest.approx(0.0, abs=1e-6) and rp.level[2026] == pytest.approx(25.0)      # the market moved 25 bp, all of it the decision


# --- surprises and reaction -------------------------------------------------------------------------------------------

def test_surprise_vs_market_t_minus_1_and_the_reaction_of_the_next_and_year_end_meetings():
    cal = weekends_only()
    T = D(2026, 9, 16)
    t1 = cal.prev_business_day(T)
    decided = dec(T, D(2026, 9, 17), 2.00, 2.25, consensus=5.0)
    quotes = curve_quotes("crv", "inst", t1, lambda t: 2.15, range(1, 37)) + curve_quotes("crv", "inst", T, lambda t: 2.30, range(1, 37))
    ctx = make_ctx(decisions=[dec(D(2026, 7, 29), D(2026, 7, 30), 2.00, 2.00), decided],
                   meetings=[(T, D(2026, 9, 17)), (D(2026, 10, 28), D(2026, 10, 29)), (D(2026, 12, 9), D(2026, 12, 10))], quotes=quotes,
                   policy_path=[(D(2026, 1, 1), 2.00), (D(2026, 9, 17), 2.25)], spread=0.0, sources=src("crv", "CURVE"),
                   official_start=D(2026, 6, 1), official_end=D(2026, 9, 16))
    rows = A.surprises(ctx, CUR, T, E.trajectory(ctx, CUR, T))
    row = next(r for r in rows if r.meeting == T)
    assert row.vs_consensus_bp == 5.0 and row.delta_bp == pytest.approx(25.0)
    assert row.implied_step_bp == pytest.approx(15.0) and row.vs_market_bp == pytest.approx(10.0) and row.vs_market_flag == "CURVE"
    assert row.reaction_next_bp == pytest.approx(15.0) and row.reaction_next_target == D(2026, 10, 28)
    assert row.reaction_year_bp == pytest.approx(15.0) and row.reaction_year_target == D(2026, 12, 9)
    upcoming = [r for r in rows if not r.decided]
    assert upcoming and upcoming[0].meeting == D(2026, 10, 28)


def test_surprise_vs_market_is_na_for_a_window_method_and_says_why():
    cal = weekends_only()
    T = D(2026, 9, 16)
    t1 = cal.prev_business_day(T)
    quotes = win_quotes(t1, WINDOWS) + win_quotes(T, WINDOWS)
    ctx = make_ctx(decisions=[dec(T, D(2026, 9, 17), 2.00, 2.25)], meetings=[(T, D(2026, 9, 17)), (D(2026, 12, 9), D(2026, 12, 10))], quotes=quotes,
                   policy_path=[(D(2026, 1, 1), 2.00), (D(2026, 9, 17), 2.25)], spread=0.0, sources=src("win", "WINDOW"),
                   official_start=D(2026, 6, 1), official_end=D(2026, 9, 16))
    row = next(r for r in A.surprises(ctx, CUR, T, E.trajectory(ctx, CUR, T)) if r.meeting == T)
    assert row.vs_market_bp is None and "only EXACT / CURVE / PROXY" in row.vs_market_reason
    assert row.reaction_next_bp == pytest.approx(0.0)                             # the reaction of the window level itself is still reported


def test_surprise_needs_market_history_before_the_meeting():
    T = D(2026, 9, 16)
    ctx = make_ctx(decisions=[dec(T, D(2026, 9, 17), 2.00, 2.25)], meetings=[(T, D(2026, 9, 17))],
                   quotes=curve_quotes("crv", "inst", D(2026, 9, 16), lambda t: 2.3, range(1, 37)),
                   policy_path=[(D(2026, 1, 1), 2.00)], spread=0.0, sources=src("crv", "CURVE"), official_end=T)
    row = A.surprises(ctx, CUR, T, E.trajectory(ctx, CUR, T))[0]
    assert "no market history before 2026-09-16" in row.vs_market_reason and "no market history" in row.reaction_reason["next"]


# --- pairs ------------------------------------------------------------------------------------------------------------

def leg(cur, base, y26, y27, flags=("EXACT", "EXACT"), lvl=None):
    ye = {}
    for y, cum, fl in zip((2026, 2027), (y26, y27), flags):
        ye[y] = E.YearEnd(y, D(y, 12, 9), cum_bp=cum, rate=None if cum is None else base + cum / 100, flag=fl,
                          reason="" if cum is not None else "no path")
    rep = {n: A.Repricing(n, 5 if n == "1s" else 21, cum={2026: (lvl or 0)}, level={2026: (lvl or 0)}, cum_flag={2026: flags[0]}) for n in ("1s", "1l")}
    tr = SimpleNamespace(base=E.BaseRate(base, D(2026, 9, 16), D(2026, 9, 17), False))
    return SimpleNamespace(currency=cur, trajectory=tr, year_ends=ye, repricing=rep)


def test_pair_is_base_minus_quote_everywhere_with_the_weakest_flag():
    aud = leg("AUD", 4.35, 38.4, 53.5, flags=("EXACT", "EXACT"), lvl=6.0)
    usd = leg("USD", 3.875, 41.5, 74.3, flags=("UPPER_BOUND", "UPPER_BOUND"), lvl=2.0)
    p = A.pair_row("AUDUSD", aud, usd)
    assert p.current_bp == pytest.approx((4.35 - 3.875) * 100)
    assert p.cum_bp[2026] == pytest.approx(38.4 - 41.5) and p.cum_bp[2027] == pytest.approx(53.5 - 74.3)
    assert p.implied[2026] == pytest.approx((4.35 + 0.384) - (3.875 + 0.415))
    assert p.flag == {2026: "UPPER_BOUND", 2027: "UPPER_BOUND"}
    assert p.reprice[("1s", 2026)] == pytest.approx(4.0)                          # repricing of the differential = base leg - quote leg
    rev = A.pair_row("USDAUD", usd, aud)
    assert rev.current_bp == pytest.approx(-p.current_bp) and rev.cum_bp[2026] == pytest.approx(-p.cum_bp[2026])


def test_pair_is_na_when_one_leg_is_missing_and_says_which():
    chf = leg("CHF", 0.0, None, None)
    usd = leg("USD", 3.875, 41.5, 74.3)
    p = A.pair_row("USDCHF", usd, chf)
    assert p.current_bp == pytest.approx(387.5) and not p.implied and not p.cum_bp
    assert "CHF: no path" in p.reasons[2026] and "USD" not in p.reasons[2026]


def test_pair_repricing_is_na_when_a_leg_has_no_history():
    a, b = leg("AUD", 4.35, 38.4, 53.5), leg("USD", 3.875, 41.5, 74.3)
    b.repricing["1s"] = A.Repricing("1s", 5, reasons={"all": "history starts 2026-09-18"})
    p = A.pair_row("AUDUSD", a, b)
    assert ("1s", 2026) not in p.reprice and "history starts" in p.reasons[("1s", 2026)] and ("1l", 2026) in p.reprice
