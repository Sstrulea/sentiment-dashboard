"""Implied-rate methods (phase 1B-2, pure numerics): the month-average chain (EXACT), the curve average, the window rule and
the probability split. Each expected value is derived from a known rate path, independently of the code under test."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import cb_probe
from src.cb_compute import methods as M

from .cb_synth import D, month_avg, rate_on

MONTHS = [(2026, 10), (2026, 11), (2026, 12), (2027, 1)]


def chain(path, unknown, r_start, months=MONTHS, known=None, spread=0.0):
    contracts = {ym: month_avg(path, *ym) + spread for ym in months}
    return M.exact_chain({k: v - spread for k, v in contracts.items()}, unknown, r_start, known)


# --- EXACT ------------------------------------------------------------------------------------------------------------

def test_exact_single_hike_mid_month_recovers_the_step():
    path = [(D(2026, 1, 1), 2.00), (D(2026, 10, 15), 2.25)]
    res = chain(path, [(D(2026, 10, 14), D(2026, 10, 15))], 2.00)
    p = res.points[0]
    assert p.how == "formula" and p.r_prev == 2.00
    assert p.r_post == pytest.approx(2.25, abs=1e-9)
    assert p.days_after == 17                                               # 15..31 October
    assert [c.month for c in res.consistency] == [(2026, 11), (2026, 12), (2027, 1)]
    assert all(abs(c.dev_bp) < 1e-9 for c in res.consistency)               # the months without a meeting agree with the path


def test_exact_chain_of_hikes_and_a_cut_recovers_every_step():
    path = [(D(2026, 1, 1), 2.00), (D(2026, 10, 8), 2.25), (D(2026, 11, 12), 2.75), (D(2026, 12, 10), 2.50)]
    unk = [(D(2026, 10, 7), D(2026, 10, 8)), (D(2026, 11, 11), D(2026, 11, 12)), (D(2026, 12, 9), D(2026, 12, 10))]
    res = chain(path, unk, 2.00)
    assert [round(p.r_post, 9) for p in res.points] == [2.25, 2.75, 2.50]
    assert [round((p.r_post - p.r_prev) * 100, 6) for p in res.points] == [25.0, 50.0, -25.0]


def test_exact_meeting_in_the_last_five_days_of_the_month_uses_the_next_months_average():
    """Effective 27 Nov: 4 days left in November (< 5) -> the noisy formula (x30/4) is replaced by December's average."""
    path = [(D(2026, 1, 1), 2.00), (D(2026, 10, 15), 2.25), (D(2026, 11, 27), 2.50)]
    unk = [(D(2026, 10, 14), D(2026, 10, 15)), (D(2026, 11, 26), D(2026, 11, 27))]
    res = chain(path, unk, 2.00)
    oct_, nov = res.points
    assert oct_.how == "formula" and oct_.r_post == pytest.approx(2.25)
    assert nov.how == "next_month" and nov.days_after == 4
    assert nov.r_post == pytest.approx(2.50)                                # = December average (no meeting in December)
    assert "next month's average" in nov.reason


def test_exact_late_meeting_followed_by_a_meeting_month_is_not_identified():
    path = [(D(2026, 1, 1), 2.00), (D(2026, 10, 15), 2.25), (D(2026, 11, 27), 2.50), (D(2026, 12, 3), 2.75)]
    unk = [(D(2026, 10, 14), D(2026, 10, 15)), (D(2026, 11, 26), D(2026, 11, 27)), (D(2026, 12, 2), D(2026, 12, 3))]
    res = chain(path, unk, 2.00)
    nov = res.points[1]
    assert nov.r_post is None and nov.how == "unresolved" and "next month has a meeting" in nov.reason


def test_exact_five_days_left_still_uses_the_formula():
    """Boundary: N - d_pre = 5 -> formula (the rule is `< 5`)."""
    path = [(D(2026, 1, 1), 2.00), (D(2026, 11, 26), 2.30)]                # 26 Nov: d_pre = 25, N = 30 -> 5 days after
    res = chain(path, [(D(2026, 11, 25), D(2026, 11, 26))], 2.00, months=[(2026, 11), (2026, 12)])
    assert res.points[0].how == "formula" and res.points[0].days_after == 5
    assert res.points[0].r_post == pytest.approx(2.30)


def test_exact_flat_path_gives_zero_everywhere():
    path = [(D(2026, 1, 1), 3.10)]
    unk = [(D(2026, 10, 7), D(2026, 10, 8)), (D(2026, 11, 11), D(2026, 11, 12))]
    res = chain(path, unk, 3.10)
    assert [p.r_post for p in res.points] == [pytest.approx(3.10)] * 2
    assert all(abs(c.dev_bp) < 1e-9 for c in res.consistency)


def test_exact_month_without_a_meeting_reports_the_deviation():
    """The contract for a month with no meeting disagrees with the chain's rate: reported in bp, not absorbed."""
    months = {(2026, 10): 2.00, (2026, 11): 2.06}                            # November should still be 2.00 (no meeting)
    res = M.exact_chain(months, [], 2.00)
    assert [(c.month, round(c.dev_bp, 6)) for c in res.consistency] == [((2026, 10), 0.0), ((2026, 11), 6.0)]


def test_exact_decided_pending_change_inside_the_horizon_is_known_not_solved():
    """BoJ-like: a decided rise takes effect on 24 Sep; the next meeting's post rate must not absorb it."""
    path = [(D(2026, 1, 1), 1.00), (D(2026, 9, 24), 1.25), (D(2026, 10, 30), 1.50)]
    months = {ym: month_avg(path, *ym) for ym in [(2026, 9), (2026, 10), (2026, 11)]}
    res = M.exact_chain(months, [(D(2026, 10, 29), D(2026, 10, 30))], 1.00, known=[(D(2026, 9, 24), 1.25)])
    assert res.points[0].r_prev == 1.25 and res.points[0].r_post == pytest.approx(1.50)
    assert res.consistency and abs(res.consistency[0].dev_bp) < 1e-9        # September: 23 days at 1.00 + 7 at 1.25


def test_exact_two_meetings_in_one_month_are_not_identifiable():
    path = [(D(2026, 1, 1), 2.00), (D(2026, 10, 5), 2.25), (D(2026, 10, 25), 2.50)]
    unk = [(D(2026, 10, 4), D(2026, 10, 5)), (D(2026, 10, 24), D(2026, 10, 25))]
    res = chain(path, unk, 2.00)
    assert [p.how for p in res.points] == ["unresolved", "unresolved"]


# --- CURVE / PROXY building blocks ------------------------------------------------------------------------------------

def test_curve_average_of_a_ramp_is_the_midpoint():
    c = M.Curve([(t, 3.0 + 0.1 * t) for t in range(1, 25)])
    assert c.average(4.0, 8.0) == pytest.approx(3.0 + 0.1 * 6.0)
    assert c.average(4.5, 5.5) == pytest.approx(3.0 + 0.1 * 5.0)             # between knots
    assert c.value(0.2) == c.value(1.0) and c.value(99) == c.value(24)         # flat outside the grid


def test_curve_average_over_a_kink_weights_each_segment():
    c = M.Curve([(0, 2.0), (2, 2.0), (4, 3.0)])                             # flat, then a ramp
    assert c.average(0, 4) == pytest.approx((2 * 2.0 + 2 * 2.5) / 4)


def test_interval_ends_last_meeting_runs_the_tail():
    effs = [D(2026, 11, 5), D(2026, 12, 17)]
    assert M.interval_ends(effs) == [D(2026, 12, 17), D(2026, 12, 17) + timedelta(days=M.TAIL_DAYS)]


def test_tenor_curve_is_none_beyond_its_longest_tenor():
    tc = M.TenorCurve([(1, 2.0), (3, 2.2), (6, 2.5)])
    assert tc.value(4.5) == pytest.approx(2.35) and tc.value(6.5) is None and tc.value(0.5) == 2.0


# --- WINDOW -----------------------------------------------------------------------------------------------------------

WINS = [M.Window(D(2026, 9, 16), D(2026, 12, 16), 1.225), M.Window(D(2026, 12, 16), D(2027, 3, 17), 1.475),
        M.Window(D(2027, 3, 17), D(2027, 6, 16), 1.680)]


def test_pick_window_boj_18_dec_effective_21_dec_takes_the_window_from_16_dec():
    w = M.pick_window(WINS, D(2026, 12, 21))
    assert w.start == D(2026, 12, 16) and (w.start - D(2026, 12, 21)).days == -5
    assert M.pre_days(w, D(2026, 12, 21)) == 5                              # 16..20 Dec still carry the old rate


def test_pick_window_start_up_to_seven_days_before_is_allowed_eight_is_not():
    eff = D(2026, 12, 23)
    ok = M.pick_window([M.Window(eff - timedelta(days=7), D(2027, 3, 1), 1.0), M.Window(D(2027, 3, 17), D(2027, 6, 1), 2.0)], eff)
    assert ok.rate == 1.0                                                   # -7 days is the boundary, inclusive
    no = M.pick_window([M.Window(eff - timedelta(days=8), D(2027, 3, 1), 1.0), M.Window(D(2027, 3, 17), D(2027, 6, 1), 2.0)], eff)
    assert no.rate == 2.0                                                   # -8 days is out: the next window


def test_pick_window_prefers_the_closest_start_not_the_first():
    """Both 6 days before and 40 days after qualify: the closest wins. (The 0A rule was 'earliest'.)"""
    eff = D(2026, 12, 10)
    wins = [M.Window(D(2026, 12, 4), D(2027, 3, 4), 1.0), M.Window(D(2027, 1, 19), D(2027, 4, 19), 2.0)]
    assert M.pick_window(wins, eff).rate == 1.0
    wins2 = [M.Window(D(2026, 12, 3), D(2027, 3, 4), 1.0), M.Window(D(2026, 12, 11), D(2027, 4, 19), 2.0)]      # -7 vs +1
    assert M.pick_window(wins2, eff).rate == 2.0
    tie = [M.Window(D(2026, 12, 7), D(2027, 3, 4), 1.0), M.Window(D(2026, 12, 13), D(2027, 4, 19), 2.0)]        # -3 vs +3
    assert M.pick_window(tie, eff).rate == 1.0                                                                  # ties: earlier


def test_pick_window_none_beyond_the_horizon():
    assert M.pick_window(WINS, D(2027, 12, 1)) is None


def test_interior_meetings_counts_other_meetings_inside_the_window_only():
    w = WINS[1]                                                              # 12-16 .. 03-17
    effs = [D(2026, 12, 10), D(2027, 1, 28), D(2027, 3, 4), D(2027, 3, 19)]
    assert M.interior_meetings(w, D(2026, 12, 10), effs) == 2               # 28 Jan and 4 Mar; 19 Mar is after the window
    assert M.interior_meetings(WINS[2], D(2027, 3, 19), effs) == 0


def test_probe_pick_window_uses_the_same_rule():
    ws = [{"start": s.start, "end": s.end} for s in WINS]
    w, gap = cb_probe.pick_window(ws, D(2026, 12, 21))
    assert w["start"] == D(2026, 12, 16) and gap == -5
    w, gap = cb_probe.pick_window([{"start": D(2026, 12, 4)}, {"start": D(2027, 1, 19)}], D(2026, 12, 10))
    assert (w["start"], gap) == (D(2026, 12, 4), -6)


# --- probabilities ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("step,moves,direction", [
    (0.0, {0: 1.0}, "hold"),
    (12.5, {0: 0.5, 1: 0.5}, "hike"),
    (25.0, {1: 1.0}, "hike"),
    (37.5, {1: 0.5, 2: 0.5}, "hike"),
    (-12.5, {0: 0.5, 1: 0.5}, "cut"),
    (-37.5, {1: 0.5, 2: 0.5}, "cut"),
    (6.25, {0: 0.75, 1: 0.25}, "hike"),
])
def test_step_probabilities(step, moves, direction):
    got = M.step_probabilities(step)
    assert got["direction"] == direction and got["moves"] == pytest.approx(moves)
    assert sum(got["moves"].values()) == pytest.approx(1.0)


def test_step_probabilities_other_unit():
    assert M.step_probabilities(20.0, unit_bp=10.0)["moves"] == pytest.approx({2: 1.0})


# --- path average -----------------------------------------------------------------------------------------------------

def test_path_average_weights_days_and_extends_the_first_rate_backwards():
    path = [(D(2026, 10, 10), 2.00), (D(2026, 10, 20), 3.00)]
    assert M.path_average(path, D(2026, 10, 10), D(2026, 10, 30)) == pytest.approx((10 * 2.0 + 10 * 3.0) / 20)
    assert M.path_average(path, D(2026, 10, 5), D(2026, 10, 15)) == pytest.approx(2.00)
    assert M.path_average(path, D(2026, 10, 5), D(2026, 10, 25)) == pytest.approx((15 * 2.0 + 5 * 3.0) / 20)
    assert rate_on(path, D(2026, 10, 25)) == 3.0
