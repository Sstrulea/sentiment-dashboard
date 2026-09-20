"""Implied policy-rate methods (phase 1B-2, pure numerics). Rates are percent, dates are `datetime.date`.

  EXACT   1M futures on the month average: r_post = (N * X - d_pre * r_prev) / (N - d_pre) in calendar days, the rate
          changes only on the effective date; a meeting with fewer than 5 days left in its month takes the next month's
          average when that month has no meeting; months without a meeting are consistency checks.
  CURVE   OIS instantaneous-forward curve on a monthly grid, linear between grid points: the rate after meeting m is the
          average of the curve on [eff_m, eff_m+1).
  WINDOW  3M contracts (MPT, JPX TONA, MX CRA, ASX BB): one value per reference window; the window of a meeting is the one
          starting in [eff - 7 days, inf) closest to the effective date; flag UPPER_BOUND with the number of interior
          meetings and the days of the window before the effective date.
  PROXY   government curves / bills: basis = curve average on [as-of, first effective date) - current rate; implied rate =
          average - basis (monthly grid: per meeting; bills: per tenor).
"""
from __future__ import annotations

import bisect
import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

MONTH_DAYS = 365.25 / 12
BACK_DAYS = 7                     # pick_window: a window may start up to 7 calendar days before the effective date
MIN_DAYS_AFTER = 5                # EXACT: fewer days left in the month -> use the next month's average
TAIL_DAYS = 56                    # CURVE / PROXY_CURVE: interval after the last known meeting (~8 weeks)


def months_between(asof: date, d: date) -> float:
    return (d - asof).days / MONTH_DAYS


def month_start(y: int, m: int) -> date:
    return date(y, m, 1)


def next_month(y: int, m: int) -> tuple:
    return (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------

class Curve:
    """Piecewise-linear f(t), t in months from the as-of date; flat outside the grid."""

    def __init__(self, grid: list) -> None:
        pts = sorted((float(t), float(v)) for t, v in grid)
        if not pts:
            raise ValueError("empty curve")
        self.t = [p[0] for p in pts]
        self.v = [p[1] for p in pts]

    def value(self, t: float) -> float:
        if t <= self.t[0]:
            return self.v[0]
        if t >= self.t[-1]:
            return self.v[-1]
        i = bisect.bisect_right(self.t, t)
        t0, t1, v0, v1 = self.t[i - 1], self.t[i], self.v[i - 1], self.v[i]
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def average(self, t0: float, t1: float) -> float:
        """Exact mean of the piecewise-linear function on [t0, t1] (the point value when the interval is empty)."""
        if t1 <= t0:
            return self.value(t0)
        knots = [t0] + [t for t in self.t if t0 < t < t1] + [t1]
        area = sum((knots[i + 1] - knots[i]) * (self.value(knots[i]) + self.value(knots[i + 1])) / 2 for i in range(len(knots) - 1))
        return area / (t1 - t0)

    @property
    def horizon(self) -> float:
        return self.t[-1]


class TenorCurve:
    """Yield by tenor (months): linear between tenors, flat below the shortest one, None beyond the longest."""

    def __init__(self, pts: list) -> None:
        self.pts = sorted((float(t), float(v)) for t, v in pts)
        self._c = Curve(self.pts)

    def value(self, t: float) -> Optional[float]:
        return None if t > self.pts[-1][0] + 1e-9 else self._c.value(t)

    @property
    def max_tenor(self) -> float:
        return self.pts[-1][0]


def proxy_basis(pts: list, t_start: float, t_eff: float, base: float) -> tuple:
    """PROXY basis = average, on [t_start, t_eff), of the curve built ONLY from the tenors observed on or before the first
    effective date, minus the current rate. Returns (basis, reason); without such a tenor the basis is None ("proxy without
    short end": a curve that starts after the first meeting says nothing about the rate that is in force now)."""
    obs = sorted((float(t), float(v)) for t, v in pts if t <= t_eff + 1e-9)
    if not obs:
        first = min(t for t, _ in pts) if pts else float("nan")
        return None, (f"proxy without short end: the shortest observed tenor ({first:g}M) is after the first effective date "
                      f"({t_eff:.1f} months out), so no basis can be measured")
    c = Curve(obs)
    avg = c.average(t_start, t_eff) if t_eff > t_start else c.value(t_start)
    return avg - base, ""


def curve_interval_averages(curve: Curve, asof: date, starts: list, ends: list) -> list:
    return [curve.average(months_between(asof, a), months_between(asof, b)) for a, b in zip(starts, ends)]


def interval_ends(effs: list, tail_days: int = TAIL_DAYS) -> list:
    """End of each meeting's interval: the next effective date; the last one runs `tail_days`."""
    return [effs[i + 1] if i + 1 < len(effs) else effs[i] + timedelta(days=tail_days) for i in range(len(effs))]


# ---------------------------------------------------------------------------
# EXACT: month-average chain
# ---------------------------------------------------------------------------

@dataclass
class ChainPoint:
    decision: date
    eff: date
    r_prev: float                    # rate in force just before the effective date
    r_post: Optional[float]          # None: not identified (see reason)
    days_after: int                  # days of the month from the effective date (to the next known change or month end)
    how: str                         # formula | next_month | unresolved
    reason: str = ""


@dataclass
class Consistency:
    month: tuple
    expected: float
    actual: float

    @property
    def dev_bp(self) -> float:
        return (self.actual - self.expected) * 100


@dataclass
class ChainResult:
    points: list = field(default_factory=list)
    consistency: list = field(default_factory=list)
    last_month: Optional[tuple] = None         # last contract month (the chain's horizon)


def exact_chain(months: dict, unknown: list, r_start: float, known: Optional[list] = None,
                min_days: int = MIN_DAYS_AFTER) -> ChainResult:
    """Bootstrap the post-meeting rates from monthly averages.

    months   {(y, m): X} monthly average of the POLICY rate (benchmark average minus the spread)
    unknown  [(decision, eff)] meetings not decided yet, ascending; the rate changes on `eff`
    r_start  policy rate in force on the first day of the first contract month
    known    [(eff, rate)] decided changes not in force yet (fixed steps inside the horizon)
    """
    res = ChainResult()
    keys = sorted(months)
    if not keys:
        return res
    res.last_month = keys[-1]
    known = sorted(known or [])
    by_month: dict = {}
    for dec, eff in sorted(unknown, key=lambda x: x[1]):
        by_month.setdefault((eff.year, eff.month), []).append((dec, eff))
    r = r_start
    for ym in keys:
        y, m = ym
        S = month_start(y, m)
        N = calendar.monthrange(y, m)[1]
        E = month_start(*next_month(y, m))
        steps = sorted((e, rate) for e, rate in known if S <= e < E)
        unk = by_month.get(ym, [])
        if len(unk) > 1:
            for dec, eff in unk:
                res.points.append(ChainPoint(dec, eff, r, None, 0, "unresolved", "several meetings in one month: not identifiable from a monthly average"))
            r = months[ym]                                             # best available guess for the next month's start
            continue
        if not unk:
            segs, cur, prev = [], r, S
            for e, rate in steps:
                segs.append((cur, (e - prev).days))
                cur, prev = rate, e
            segs.append((cur, (E - prev).days))
            res.consistency.append(Consistency(ym, sum(rt * dd for rt, dd in segs) / N, months[ym]))
            r = cur
            continue
        dec, eff = unk[0]
        # days at the old rate before the meeting (through the known steps that precede it), days at r_post after it
        cur, prev, known_sum = r, S, 0.0
        later = []
        for e, rate in steps:
            if e <= eff:
                known_sum += cur * (e - prev).days
                cur, prev = rate, e
            else:
                later.append((e, rate))
        r_prev = cur
        known_sum += cur * (eff - prev).days
        span_end = later[0][0] if later else E
        days_u = (span_end - eff).days
        tail_sum = 0.0
        for i, (e, rate) in enumerate(later):
            nxt = later[i + 1][0] if i + 1 < len(later) else E
            tail_sum += rate * (nxt - e).days
        if days_u >= min_days:
            r_post = (N * months[ym] - known_sum - tail_sum) / days_u
            res.points.append(ChainPoint(dec, eff, r_prev, r_post, days_u, "formula"))
        else:
            ny = next_month(y, m)
            nxt_has_meeting = ny in by_month or any(month_start(*ny) <= e < month_start(*next_month(*ny)) for e, _ in known)
            if ny in months and not nxt_has_meeting:
                r_post = months[ny]
                res.points.append(ChainPoint(dec, eff, r_prev, r_post, days_u, "next_month",
                                             f"{days_u} day(s) after the effective date: the next month's average is used"))
            else:
                res.points.append(ChainPoint(dec, eff, r_prev, None, days_u, "unresolved",
                                             f"{days_u} day(s) after the effective date and " +
                                             ("the next month has a meeting" if nxt_has_meeting else "no next-month contract")))
                r_post = None
        if r_post is None:
            r = r_prev                                                     # chain continues on the last identified rate
        else:
            r = later[-1][1] if later else r_post
    return res


def chain_path(chain: ChainResult, r_start: float, start: date, known: Optional[list] = None) -> list:
    """[(date, rate)] change points of the identified path - decided changes included - for averaging over a window."""
    path = [(start, r_start)] + list(known or [])
    path += [(p.eff, p.r_post) for p in chain.points if p.r_post is not None]
    return sorted(path)


def path_average(path: list, start: date, end: date) -> float:
    """Average of a step function [(from_date, rate)] over [start, end)."""
    total, days = 0.0, (end - start).days
    if days <= 0:
        return path[0][1]
    steps = sorted(path)
    for i, (d0, r) in enumerate(steps):
        d1 = steps[i + 1][0] if i + 1 < len(steps) else end
        a, b = max(d0, start), min(d1, end)
        if b > a:
            total += r * (b - a).days
    # before the first change point the first rate holds
    if steps[0][0] > start:
        total += steps[0][1] * (min(steps[0][0], end) - start).days
    return total / days


# ---------------------------------------------------------------------------
# WINDOW
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    start: date
    end: date                        # exclusive
    rate: float                      # policy space (spread removed) unless the instrument has none (BKBM)
    label: str = ""


def pick_window(windows: list, eff: date, back_days: int = BACK_DAYS) -> Optional[Window]:
    """The window starting in [eff - back_days, inf) that is closest to the effective date (ties: the earlier one)."""
    cands = [w for w in windows if w.start >= eff - timedelta(days=back_days)]
    return min(cands, key=lambda w: (abs((w.start - eff).days), w.start)) if cands else None


def pre_days(w: Window, eff: date) -> int:
    """Days of the window that lie before the effective date (the window average still contains the old rate)."""
    return max(0, min((eff - w.start).days, (w.end - w.start).days))


def interior_meetings(w: Window, eff: date, all_effs: list) -> int:
    """Other meetings whose effective date falls inside the window."""
    return sum(1 for e in all_effs if e != eff and w.start <= e < w.end)


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------

def step_probabilities(step_bp: float, unit_bp: float = 25.0) -> dict:
    """Step of |x| bp = n whole moves plus a fraction p of one more: P(n + 1 moves) = p, P(n) = 1 - p, in the direction of
    the step. Returns {"direction": ..., "moves": {k: probability}}."""
    a = abs(step_bp)
    n = int(math.floor(a / unit_bp + 1e-9))
    p = (a - unit_bp * n) / unit_bp
    if p < 1e-9:
        p = 0.0
    moves = {n: 1.0 - p}
    if p > 0:
        moves[n + 1] = p
    direction = "hike" if step_bp > 1e-9 else "cut" if step_bp < -1e-9 else "hold"
    return {"direction": direction, "moves": {k: round(v, 6) for k, v in sorted(moves.items())}}
