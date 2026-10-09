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


def test_tenor_curve_is_none_outside_its_tenors():
    tc = M.TenorCurve([(1, 2.0), (3, 2.2), (6, 2.5)])
    assert tc.value(4.5) == pytest.approx(2.35) and tc.value(6.5) is None and tc.value(0.5) is None      # no extrapolation at either end


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


# --- OIS: piecewise-constant overnight rate fitted to discount factors -----------------------------------------------

from src.cb_sources.base import add_months_date                          # noqa: E402

T0 = D(2026, 10, 8)


def eurex_pillars(t0=T0, months=36):
    return [t0 + timedelta(days=k) for k in (7, 14, 21)] + [add_months_date(t0, n) for n in range(1, months + 1)]


def dfs_of(path, pillars, basis, t0=T0):
    """DF(T) = prod over the days of [t0, T) of 1 / (1 + r_d / basis): daily compounding of a known overnight path (%)."""
    import math
    out = []
    for T in pillars:
        s = sum(math.log(1 + rate_on(path, t0 + timedelta(days=k)) / 100 / basis) for k in range((T - t0).days))
        out.append((T, math.exp(-s)))
    return out


QUARTERLY = [D(2026, 12, 11), D(2027, 3, 19), D(2027, 6, 25), D(2027, 9, 24), D(2027, 12, 17)]      # SNB-like: a pillar inside every segment
Q_PATH = [(T0, -0.05), (QUARTERLY[0], 0.02), (QUARTERLY[1], 0.15), (QUARTERLY[2], 0.35), (QUARTERLY[3], 0.49), (QUARTERLY[4], 0.56)]


@pytest.mark.parametrize("basis", [360, 365])
def test_ois_fit_recovers_a_synthetic_step_path_on_the_eurex_pillars(basis):
    fit = M.ois_step_fit(dfs_of(Q_PATH, eurex_pillars(), basis), T0, QUARTERLY, basis)
    assert fit.bounds == [T0] + QUARTERLY + [QUARTERLY[-1] + timedelta(days=56)]
    assert fit.rates == pytest.approx([r for _, r in Q_PATH], abs=1e-4)                  # +-0.01 bp
    assert fit.max_residual_bp < 0.01
    assert fit.used == sum(1 for T in eurex_pillars() if T <= fit.bounds[-1])           # pillars past the tail are not equations


def test_ois_fit_recovers_close_meetings_when_every_segment_has_a_pillar():
    effs = [D(2026, 10, 29), D(2026, 12, 10), D(2027, 1, 28)]
    path = [(T0, 3.8783), (effs[0], 3.9322), (effs[1], 4.1407), (effs[2], 4.2232)]
    pillars = sorted(set(eurex_pillars(months=6)) | set(effs))                          # a pillar on each effective date
    fit = M.ois_step_fit(dfs_of(path, pillars, 360), T0, effs, 360)
    steps = [(b - a) * 100 for a, b in zip(fit.rates, fit.rates[1:])]
    assert steps == pytest.approx([(b - a) * 100 for (_, a), (_, b) in zip(path, path[1:])], abs=0.01)


def test_ois_regularisation_does_not_move_an_identified_system():
    dfs = dfs_of(Q_PATH, eurex_pillars(), 360)
    a = M.ois_step_fit(dfs, T0, QUARTERLY, 360, lam=0.03)
    b = M.ois_step_fit(dfs, T0, QUARTERLY, 360, lam=0.0)
    assert a.rates == pytest.approx(b.rates, abs=1e-4)


def test_ois_regularisation_makes_an_unidentified_segment_well_posed():
    """Two 10-day segments between the same two pillars (1M = 8 Nov, 2M = 8 Dec): only their sum is identified. The
    lam * (g_k - g_k-1) rows pick the smoothest split: with s0 = 2.00, s3 = 2.25 and s1 + s2 = 4.00 fixed, minimising
    (s1 - s0)^2 + (s2 - s1)^2 + (s3 - s2)^2 gives s1 = 2 - x, s2 = 2 + x with 12x = 0.5."""
    effs = [D(2026, 11, 10), D(2026, 11, 20), D(2026, 11, 30)]
    pillars = eurex_pillars(months=4)
    assert not any(effs[0] < T <= effs[2] for T in pillars)
    fit = M.ois_step_fit(dfs_of([(T0, 2.0), (effs[2], 2.25)], pillars, 360), T0, effs, 360)
    x = 0.5 / 12
    assert fit.rates == pytest.approx([2.0, 2.0 - x, 2.0 + x, 2.25], abs=1e-3)
    assert fit.max_residual_bp < 0.01                                                     # the DF equations are still met


def test_ois_boundaries_before_t0_are_ignored_and_the_tail_is_56_days():
    fit = M.ois_step_fit(dfs_of(Q_PATH, eurex_pillars(), 360), T0, [T0 - timedelta(days=3), T0] + QUARTERLY, 360)
    assert fit.bounds[0] == T0 and fit.bounds[1] == QUARTERLY[0] and (fit.bounds[-1] - fit.bounds[-2]).days == 56
    assert fit.rate_from(QUARTERLY[1]) == pytest.approx(0.15, abs=1e-4) and fit.rate_from(D(2027, 1, 1)) is None
    assert fit.path()[0] == (T0, pytest.approx(-0.05, abs=1e-4))


def test_ois_residual_is_reported_in_bp_of_the_average_rate():
    dfs = dfs_of(Q_PATH, eurex_pillars(), 360)
    T, v = dfs[10]
    bumped = dfs[:10] + [(T, v * (1 - 0.0001 * (T - T0).days / 360))] + dfs[11:]      # +1 bp on the average rate of one pillar
    fit = M.ois_step_fit(bumped, T0, QUARTERLY, 360)
    worst = max(fit.residuals, key=lambda x: abs(x[1]))
    assert worst[0] == T and 0.2 < abs(worst[1]) < 1.0                                 # least squares spreads the bump over its segment


def test_ois_no_pillar_in_the_span_raises():
    with pytest.raises(ValueError):
        M.ois_step_fit([(D(2031, 1, 1), 0.9)], T0, QUARTERLY, 360)
