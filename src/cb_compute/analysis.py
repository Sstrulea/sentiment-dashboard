"""Phase 1B-2, level 2 (pure): cross-checks, repricing 1s / 1l, GAP, surprises and reaction, currency pairs, and the
per-bank report that ties them to the trajectory of `engine.py`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .engine import (GAP_YEARS, REPRICING_BD, STRENGTH, YEAR_ENDS, Context, NextMeeting, Point, Trajectory, YearEnd,
                     last_meeting_of, next_meeting, step_reason, trajectory, weakest, year_end, _raw_rate, _windows)
from .methods import TenorCurve, Window, months_between, path_average, pick_window

# ---------------------------------------------------------------------------
# Cross-checks (the primary instrument against an independent one)
# ---------------------------------------------------------------------------


@dataclass
class CrossCheck:
    name: str
    period: str
    a_label: str
    a: Optional[float]
    b_label: str
    b: Optional[float]
    note: str = ""
    na_reason: str = ""                          # why the second source could not be brought to policy terms

    @property
    def diff_bp(self) -> Optional[float]:
        return None if self.a is None or self.b is None else (self.b - self.a) * 100


def _tenor_curve(ctx: Context, source: str, asof: date, instrument: str) -> Optional[tuple]:
    snap = ctx.market.latest(source, asof)
    if snap is None:
        return None
    pts = [(r["tenor_months"], r["value"]) for r in snap.rows if r["instrument"] == instrument]
    return (TenorCurve(pts), snap) if pts else None


def bills_average(curve: TenorCurve, t_months: float, basis: float) -> Optional[float]:
    """Implied average policy rate over [as-of, as-of + t): the bill yield of that tenor minus the basis."""
    y = curve.value(t_months)
    return None if y is None else y - basis


def bills_window_average(curve: TenorCurve, t0: float, t1: float, basis: float) -> Optional[float]:
    """Average over [t0, t1] from two tenor averages: (t1 * a(t1) - t0 * a(t0)) / (t1 - t0)."""
    a1 = bills_average(curve, t1, basis)
    a0 = bills_average(curve, t0, basis) if t0 > 0 else None
    if a1 is None:
        return None
    if t0 <= 0:
        return a1
    return None if a0 is None else (t1 * a1 - t0 * a0) / (t1 - t0)


def bills_basis(curve: TenorCurve, asof: date, first_eff: date, base: float) -> Optional[float]:
    """Bills basis from the OBSERVED tenors only: the longest tenor that ends on or before the first effective date (its yield
    averages the policy rate over a period with no meeting in it) minus the current rate. None when there is none."""
    t_eff = months_between(asof, first_eff)
    obs = [(t, v) for t, v in curve.pts if t <= t_eff + 1e-9]
    return None if not obs else obs[-1][1] - base


NO_SHORT_END = "proxy without short end: no bill tenor ends before the first effective date, so no basis can be measured"


def crosschecks(ctx: Context, tr: Trajectory) -> list:
    """USD: MPT windows vs Treasury bills; CAD: CRA windows vs the COA chain path; AUD: IB path vs RBA bank bills."""
    out: list = []
    if tr.na_reason or tr.base is None:
        return out
    cur, asof = tr.currency, tr.asof
    unknown = [m for m in ctx.meetings.get(cur, []) if m.decision > asof]
    if not unknown:
        return out
    first_eff = unknown[0].eff
    if cur == "USD":
        got = _tenor_curve(ctx, "ust_bills", asof, "ust_par_curve")
        mpt = ctx.market.latest("atlantafed_mpt", asof)
        if got and mpt and tr.spread and tr.spread.value is not None:
            curve, snap = got
            basis = bills_basis(curve, snap.asof, first_eff, tr.base.rate)
            wins = [w for w in _windows(tr, [r for r in mpt.rows if r["instrument"] == "sofr_3m_ref_quarter_mean"], tr.spread.value)[:4] if w.start > asof]
            if basis is None and wins:
                out.append(CrossCheck("USD MPT vs Treasury bills", "all windows", "MPT (policy)", None, "Treasury", None, na_reason=NO_SHORT_END))
            for w in wins if basis is not None else []:
                b = bills_window_average(curve, months_between(snap.asof, w.start), months_between(snap.asof, w.end), basis)
                if b is not None:
                    out.append(CrossCheck("USD MPT vs Treasury bills", f"{w.start}..{w.end}", "MPT (policy)", w.rate, "Treasury (policy, basis removed)", b,
                                          f"basis {basis * 100:+.1f} bp (longest bill tenor before {first_eff} - base); Treasury {snap.asof}"))
    if cur == "CAD" and tr.extra.get("exact_path"):
        cra = ctx.market.latest("mx_corra", asof)
        if cra and tr.spread and tr.spread.value is not None:
            path, end = tr.extra["exact_path"], tr.extra["exact_end"]
            for w in _windows(tr, [r for r in cra.rows if r["instrument"] == "corra_3m_futures"], tr.spread.value):
                if w.end <= end and w.start >= tr.extra["exact_start"]:
                    out.append(CrossCheck("CAD COA chain vs CRA", f"{w.start}..{w.end}", "COA chain path average (policy)", path_average(path, w.start, w.end),
                                          "CRA (policy)", w.rate, "same period; the CRA contract is inside the COA horizon"))
    if cur == "AUD" and tr.extra.get("exact_path"):
        got = _tenor_curve(ctx, "rba_bank_bills", asof, "bank_bill_eod_yield")
        if got:
            curve, snap = got
            basis = bills_basis(curve, snap.asof, first_eff, tr.base.rate)
            path, end = tr.extra["exact_path"], tr.extra["exact_end"]
            if basis is None:
                out.append(CrossCheck("AUD IB path vs RBA bank bills", "3M / 6M", "IB path average (policy)", None, "RBA bills", None, na_reason=NO_SHORT_END))
            for t in (3.0, 6.0) if basis is not None else ():
                stop = asof + timedelta(days=round(t * 30.4375))
                b = bills_average(curve, t, basis)
                if b is not None and stop <= end:
                    out.append(CrossCheck("AUD IB path vs RBA bank bills", f"{asof}..{stop} ({t:g}M)", "IB path average (policy)", path_average(path, asof, stop),
                                          "RBA bills (policy, basis removed)", b, f"basis {basis * 100:+.1f} bp; bills {snap.asof}"))
    return out


# ---------------------------------------------------------------------------
# Repricing 1s / 1l
# ---------------------------------------------------------------------------

@dataclass
class Repricing:
    """Main value = change of the implied LEVEL at a fixed horizon (the last meeting of 2026 / 2027): it does not depend on a
    decision taken in between. The change of the cumulative bp (secondary) is against the base at each date."""
    name: str                                   # 1s | 1l
    bd: int
    prev: Optional[date] = None
    level: dict = field(default_factory=dict)   # year -> change of the implied level, bp (MAIN)
    level_flag: dict = field(default_factory=dict)
    cum: dict = field(default_factory=dict)     # year -> change of the cumulative bp (secondary)
    cum_flag: dict = field(default_factory=dict)
    step_bp: Optional[float] = None             # the SAME next meeting at both dates
    step_flag: Optional[str] = None
    step_meeting: Optional[date] = None
    base_change_bp: Optional[float] = None      # the base moved between the two dates (a decision): the cum bp are vs another base
    reasons: dict = field(default_factory=dict)  # metric -> why n/a


def primary_history_start(ctx: Context, cur: str) -> Optional[date]:
    ds = [ctx.market.first_date(sid) for sid, _ in ctx.primary(cur)]
    return None if not ds or any(d is None for d in ds) else max(ds)


def primary_calendar(ctx: Context, cur: str):
    prim = ctx.primary(cur)
    return ctx.source_cal(prim[0][0]) if prim else ctx.cal(None)


def repricing(ctx: Context, cur: str, asof: date, tr: Trajectory, ye: dict, nm: NextMeeting) -> dict:
    out = {}
    cal = primary_calendar(ctx, cur)
    for name, n in REPRICING_BD.items():
        rp = Repricing(name, n)
        out[name] = rp
        if tr.na_reason:
            rp.reasons["all"] = tr.na_reason
            continue
        rp.prev = cal.add_business_days(asof, -n)
        start = primary_history_start(ctx, cur)
        if start is None or start > rp.prev:
            rp.reasons["all"] = f"history starts {start} (needs {n} business days back to {rp.prev})"
            continue
        tp = trajectory(ctx, cur, rp.prev)
        ye_prev = {y: year_end(ctx, tp, y) for y in YEAR_ENDS}
        if tr.base is not None and tp.base is not None and abs(tr.base.rate - tp.base.rate) > 1e-9:
            rp.base_change_bp = (tr.base.rate - tp.base.rate) * 100
        for y in YEAR_ENDS:
            a, b = ye[y], ye_prev[y]
            if a.level is None or b.level is None:
                rp.reasons[str(y)] = (a.reason if a.level is None else b.reason) or "n/a"
                continue
            rp.level[y] = (a.level - b.level) * 100
            rp.level_flag[y] = weakest(a.flag, b.flag)
            if a.cum_bp is not None and b.cum_bp is not None:
                rp.cum[y] = a.cum_bp - b.cum_bp
                rp.cum_flag[y] = weakest(a.flag, b.flag)
        if nm.decision is None or nm.step_bp is None:
            rp.reasons["step"] = nm.step_reason or "no per-meeting step"
        else:
            pp = tp.point_for(nm.decision)                                   # the SAME meeting at the earlier date
            if pp is None or pp.step_bp is None:
                rp.reasons["step"] = f"the step of the {nm.decision} meeting was not identified on {rp.prev}: " + (step_reason(pp) if pp else "meeting not in the trajectory")
            else:
                rp.step_bp = nm.step_bp - pp.step_bp
                rp.step_flag = weakest(nm.flag, pp.flag)
                rp.step_meeting = nm.decision
    return out


# ---------------------------------------------------------------------------
# GAP = market - bank
# ---------------------------------------------------------------------------

@dataclass
class GapYear:
    year: int
    market_rate: Optional[float] = None
    market_flag: Optional[str] = None
    market_window: Optional[tuple] = None
    bank_median: Optional[float] = None
    n_dots: Optional[int] = None
    dots: list = field(default_factory=list)          # [(level, count)]
    gap_bp: Optional[float] = None
    note: str = ""


@dataclass
class Gap:
    currency: str
    kind: str                                          # dots | bank_bill | n/a
    reason: str = ""
    sep: Optional[date] = None
    years: list = field(default_factory=list)


def second_wednesday(y: int, m: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(2 - d.weekday()) % 7)
    return d + timedelta(days=7)


def latest_sep(ctx: Context, asof: date) -> Optional[date]:
    ds = sorted({r["meeting_date"] for r in ctx.projections if r["kind"] == "median_from_dots" and r["meeting_date"] <= asof})
    return ds[-1] if ds else None


def fed_gap(ctx: Context, tr: Trajectory, asof: date) -> Gap:
    sep = latest_sep(ctx, asof)
    if sep is None:
        return Gap("USD", "dots", "no SEP on record")
    g = Gap("USD", "dots", sep=sep)
    mpt = ctx.market.latest("atlantafed_mpt", asof)
    sp = tr.spread.value if tr.spread else None
    for y in GAP_YEARS:
        gy = GapYear(y)
        g.years.append(gy)
        med = next((r for r in ctx.projections if r["meeting_date"] == sep and r["kind"] == "median_from_dots" and r["horizon"] == str(y)), None)
        if med is None:
            gy.note = f"the {sep} SEP has no {y} dot"
            continue
        gy.bank_median, gy.n_dots = med["value"], med["count"]
        gy.dots = sorted(((r["level"], r["count"]) for r in ctx.projections if r["meeting_date"] == sep and r["kind"] == "dot" and r["horizon"] == str(y)), reverse=True)
        ye = year_end(ctx, tr, y) if y in YEAR_ENDS else None
        if ye is not None and ye.rate is not None:
            gy.market_rate, gy.market_flag, gy.market_window = ye.rate, ye.flag, ye.window
        elif y not in YEAR_ENDS and mpt is not None and sp is not None and tr.base is not None:
            # no meeting calendar that far: assume the Fed pattern (second Wednesday of December), decision + 1 day
            eff = second_wednesday(y, 12) + timedelta(days=1)
            w = pick_window(_windows(tr, [r for r in mpt.rows if r["instrument"] == "sofr_3m_ref_quarter_mean"], sp), eff)
            if w is not None:
                gy.market_rate, gy.market_flag, gy.market_window = w.rate, "UPPER_BOUND", (w.start, w.end)
                gy.note = f"last meeting of {y} assumed on {second_wednesday(y, 12)} (2nd Wednesday of December, Fed pattern)"
        if gy.market_rate is None:
            gy.note = gy.note or (ye.reason if ye else "no market value")
        else:
            gy.gap_bp = (gy.market_rate - gy.bank_median) * 100
    return g


def quarter_bounds(period: str) -> Optional[tuple]:
    try:
        y, q = int(period[:4]), int(period[-1])
        s = date(y, 3 * (q - 1) + 1, 1)
        e = date(y + (1 if q == 4 else 0), 1 if q == 4 else 3 * q + 1, 1)
        return s, e
    except (ValueError, IndexError):
        return None


def latest_mps(ctx: Context, asof: date) -> Optional[dict]:
    doc = ctx.rbnz or {}
    filled = sorted((e for e in doc.get("mps", []) if e.get("status") == "filled" and e["meeting"] <= asof), key=lambda e: e["meeting"])
    return filled[-1] if filled else None


def rbnz_gap(ctx: Context, asof: date) -> Gap:
    """MPS 90-day bank bill projection vs the ASX BB level - the same BKBM basis, or n/a: the OCR track is a different
    basis and is shown separately (`bank_path`). The BB contract of a quarter is the one whose 90-day period starts in it."""
    mps = latest_mps(ctx, asof)
    if mps is None:
        return Gap("NZD", "n/a", "no filled MPS on record (data/cb/manual/rbnz.yaml)")
    if not mps.get("bank_bill_90d"):
        return Gap("NZD", "n/a", f"the {mps['meeting']} MPS tables carry no 90-day bank bill projection: only the OCR track (a different basis "
                                 "from the ASX BB) - shown separately, not compared", sep=mps["meeting"])
    g = Gap("NZD", "bank_bill", sep=mps["meeting"])
    bb = ctx.market.latest("asx_bb", asof)
    if bb is None:
        g.reason = "no ASX BB quotes"
        return g
    rows = [r for r in bb.rows if r["instrument"] == "bb_90d_nz_bank_bill_futures"]
    for pt in mps["bank_bill_90d"]:
        qb = quarter_bounds(pt["period"])
        gy = GapYear(int(pt["period"][:4]), bank_median=pt["value"], note=pt["period"])
        g.years.append(gy)
        if qb is None:
            gy.note += ": unreadable period"
            continue
        # the MPS quarter is the average of the 90-day rate over the quarter: the BB contract whose 90-day period STARTS in it
        # (its expiry date), the one closest to the middle of the quarter when there are several
        mid = qb[0] + (qb[1] - qb[0]) / 2
        starts = [r for r in rows if qb[0] <= r["ref_start"] < qb[1]]
        best = min(starts, key=lambda r: abs((r["ref_start"] - mid).days), default=None)
        if best is None:
            gy.note += ": no BB contract starts in the quarter"
            continue
        gy.market_rate, gy.market_flag = _raw_rate(best), "UPPER_BOUND"
        gy.market_window = (best["ref_start"], best["ref_end"])
        gy.gap_bp = (gy.market_rate - gy.bank_median) * 100
    return g


# ---------------------------------------------------------------------------
# The bank's own path (shown next to the market's; not a GAP unless the basis is the same)
# ---------------------------------------------------------------------------

@dataclass
class BankPath:
    kind: str                                    # dots | ocr_track | n/a
    source: Optional[date] = None                # SEP / MPS date
    finalised: Optional[date] = None
    note: str = ""
    points: list = field(default_factory=list)   # [(period label, value)]: OCR track quarters, Fed dot medians by year
    dots: dict = field(default_factory=dict)     # Fed: year -> [(level, count)]
    reason: str = ""


def bank_path(ctx: Context, cur: str, gap: Gap, asof: date) -> BankPath:
    if cur == "USD":
        if not gap.years:
            return BankPath("n/a", reason=gap.reason or "no SEP on record")
        return BankPath("dots", gap.sep, note="FOMC SEP: median of the dots at year end; the dots are individual participants' projections",
                        points=[(str(y.year), y.bank_median) for y in gap.years if y.bank_median is not None],
                        dots={y.year: y.dots for y in gap.years})
    if cur == "NZD":
        mps = latest_mps(ctx, asof)
        if mps is None:
            return BankPath("n/a", reason="no filled MPS on record")
        return BankPath("ocr_track", mps["meeting"], mps.get("projections_finalised"),
                        note="RBNZ OCR track, quarterly averages, rounded to 0.1 as published (transcribed by hand from the MPS PDF)",
                        points=[(pt["period"], pt["value"]) for pt in mps["ocr_track"]])
    return BankPath("n/a", reason="the bank does not publish its own rate path")


def gap_for(ctx: Context, cur: str, tr: Trajectory, asof: date) -> Gap:
    if cur == "USD":
        return fed_gap(ctx, tr, asof)
    if cur == "NZD":
        return rbnz_gap(ctx, asof)
    return Gap(cur, "n/a", "the bank does not publish its own rate path")


# ---------------------------------------------------------------------------
# Surprises and reaction
# ---------------------------------------------------------------------------

PER_MEETING = ("EXACT", "CURVE", "PROXY_CURVE")


@dataclass
class SurpriseRow:
    meeting: date
    decided: bool
    delta_bp: Optional[float] = None
    vs_consensus_bp: Optional[float] = None
    vs_market_bp: Optional[float] = None
    vs_market_flag: Optional[str] = None
    vs_market_reason: str = ""
    implied_step_bp: Optional[float] = None            # T-1 for decided meetings, today for upcoming ones
    reaction_next_bp: Optional[float] = None
    reaction_next_flag: Optional[str] = None
    reaction_next_target: Optional[date] = None
    reaction_year_bp: Optional[float] = None
    reaction_year_flag: Optional[str] = None
    reaction_year_target: Optional[date] = None
    reaction_reason: dict = field(default_factory=dict)


def _level(tr: Trajectory, meeting: date) -> tuple:
    """(level, flag, reason): the value the delta metrics use - the implied policy rate, the BKBM level or the raw government
    curve level (no basis needed for a difference of two levels of the same instrument)."""
    p = tr.point_for(meeting)
    if p is None:
        return None, None, "meeting not in the trajectory"
    return (p.level, p.flag, "") if p.level is not None else (None, p.flag, p.reason or "n/a")


def surprises(ctx: Context, cur: str, asof: date, tr_now: Trajectory, n: int = 4) -> list:
    rows = []
    cal = primary_calendar(ctx, cur)
    start = primary_history_start(ctx, cur)
    decs = [d for d in ctx.decisions.get(cur, []) if d["meeting_date"] <= asof][-n:]
    for d in decs:
        T = d["meeting_date"]
        r = SurpriseRow(T, True, d["delta_bp"], d["surprise_consensus_bp"])
        rows.append(r)
        if not ctx.primary(cur):
            why = "no market path for this currency"
            r.vs_market_reason = why
            r.reaction_reason = {"next": why, "year": why}
            continue
        Tm1 = cal.prev_business_day(T)
        if start is None or start > Tm1:
            why = f"no market history before {start} (needs {Tm1})"
            r.vs_market_reason = why
            r.reaction_reason = {"next": why, "year": why}
            continue
        t1, t0 = trajectory(ctx, cur, Tm1), trajectory(ctx, cur, T)
        p1 = t1.point_for(T)
        if p1 is None or p1.step_bp is None or p1.method not in PER_MEETING:
            r.vs_market_reason = (step_reason(p1) if p1 and p1.method in PER_MEETING else
                                  f"{p1.method if p1 else 'n/a'}: only EXACT / CURVE / PROXY monthly give a per-meeting step")
        else:
            r.implied_step_bp, r.vs_market_flag = p1.step_bp, p1.flag
            r.vs_market_bp = d["delta_bp"] - p1.step_bp if d["delta_bp"] is not None else None
        after = [m for m in ctx.meetings.get(cur, []) if m.decision > T]
        targets = {"next": after[0].decision if after else None}
        yr = last_meeting_of(ctx, cur, T.year)
        targets["year"] = yr.decision if yr and yr.decision > T else (last_meeting_of(ctx, cur, T.year + 1).decision if last_meeting_of(ctx, cur, T.year + 1) else None)
        for key, m in targets.items():
            if m is None:
                r.reaction_reason[key] = "no later meeting on record"
                continue
            a, fa, ra = _level(t0, m)
            b, fb, rb = _level(t1, m)
            if a is None or b is None:
                r.reaction_reason[key] = ra or rb
                continue
            setattr(r, f"reaction_{key}_bp", (a - b) * 100)
            setattr(r, f"reaction_{key}_flag", weakest(fa, fb))
            setattr(r, f"reaction_{key}_target", m)
    for p in tr_now.points[:2]:                                         # the upcoming ones: what the market prices now
        rows.append(SurpriseRow(p.meeting, False, implied_step_bp=p.step_bp, vs_market_flag=p.flag,
                                vs_market_reason="" if p.step_bp is not None else step_reason(p)))
    return rows


# ---------------------------------------------------------------------------
# Bank report
# ---------------------------------------------------------------------------

@dataclass
class BankReport:
    currency: str
    asof: date
    trajectory: Trajectory
    next: NextMeeting
    year_ends: dict
    gap: Gap
    repricing: dict
    surprises: list
    crosschecks: list
    bank_path: Optional[BankPath] = None


def bank_report(ctx: Context, cur: str, asof: date, with_history: bool = True) -> BankReport:
    tr = trajectory(ctx, cur, asof)
    ye = {y: year_end(ctx, tr, y) for y in YEAR_ENDS}
    nm = next_meeting(tr)
    gap = gap_for(ctx, cur, tr, asof)
    return BankReport(cur, asof, tr, nm, ye, gap,
                      repricing(ctx, cur, asof, tr, ye, nm) if with_history else {},
                      surprises(ctx, cur, asof, tr) if with_history else [], crosschecks(ctx, tr), bank_path(ctx, cur, gap, asof))


# ---------------------------------------------------------------------------
# Currency pairs
# ---------------------------------------------------------------------------

@dataclass
class PairRow:
    """Base - quote, metric by metric: each metric is n/a on its own (with its reason) when a leg lacks what it needs, never
    the whole pair. Current rate: both base rates. Implied differential / cumulative: both legs POLICY-equivalent (not BKBM, not a
    raw sovereign level). Repricing of the differential: both legs have a change of level (a BKBM or a proxy level has one)."""
    pair: str
    base: str
    quote: str
    current_bp: Optional[float] = None                  # base rate - quote rate, bp
    implied: dict = field(default_factory=dict)         # year -> implied rate differential (percentage points)
    implied_flag: dict = field(default_factory=dict)
    cum_bp: dict = field(default_factory=dict)          # year -> base cum - quote cum
    cum_flag: dict = field(default_factory=dict)
    reprice: dict = field(default_factory=dict)         # (name, year) -> change of the implied level differential, bp
    reprice_flag: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)         # metric key -> [(currency, why n/a)]: "current" | ("implied", y) | ("cum", y) | ("reprice", name, y)

    @property
    def flag(self) -> dict:                             # year -> the weaker flag of the level differential, else of the cumulative
        return {y: self.implied_flag.get(y) or self.cum_flag.get(y) for y in set(self.implied_flag) | set(self.cum_flag)}


def pair_row(pair: str, base: BankReport, quote: BankReport) -> PairRow:
    pr = PairRow(pair, base.currency, quote.currency)
    b, q = base.trajectory.base, quote.trajectory.base
    if b is not None and q is not None:
        pr.current_bp = (b.rate - q.rate) * 100
    else:
        pr.reasons["current"] = [(r.currency, "no base rate") for r in (base, quote) if r.trajectory.base is None]
    for y in YEAR_ENDS:
        yb, yq = base.year_ends[y], quote.year_ends[y]
        legs = ((base.currency, yb), (quote.currency, yq))
        lacking_rate = [(c, x) for c, x in legs if x.rate is None or x.cum_bp is None]
        if lacking_rate:
            pr.reasons[("implied", y)] = pr.reasons[("cum", y)] = [(c, level_reason(x)) for c, x in lacking_rate]
        else:
            pr.implied[y] = yb.rate - yq.rate
            pr.cum_bp[y] = yb.cum_bp - yq.cum_bp
            pr.implied_flag[y] = pr.cum_flag[y] = weakest(yb.flag, yq.flag)
        for name in REPRICING_BD:
            rb, rq = base.repricing.get(name), quote.repricing.get(name)
            if rb and rq and y in rb.level and y in rq.level:
                pr.reprice[(name, y)] = rb.level[y] - rq.level[y]         # change of the implied differential (a decision in between does not enter)
                pr.reprice_flag[(name, y)] = weakest(rb.level_flag.get(y), rq.level_flag.get(y))
            else:
                pr.reasons[("reprice", name, y)] = [
                    (c, (r.reasons.get(str(y)) or r.reasons.get("all") or "n/a") if r else "n/a")
                    for c, r in ((base.currency, rb), (quote.currency, rq)) if r is None or y not in r.level]
    return pr


def level_reason(ye: YearEnd) -> str:
    """Why a year-end point has no policy-equivalent level."""
    if ye.reason:
        return ye.reason
    return {"bkbm": "BKBM level, not the OCR", "sovereign_proxy": "sovereign proxy, not policy-equivalent"}.get(ye.level_kind, "n/a")
