"""Stance (phase S1) - pure computation, no I/O. ARCHITECTURE section 23 (S1 contract); parameters config/cb_stance.yaml v2.

    gap(pi)   = pi - pi*                     (band without declared midpoint, CHF: max(0, pi - high) + min(0, pi - low))
    y         = output gap as published, or -okun * (u - u*)          (none -> the term is left out, flag no_slack)
    x         = gap(pi_underlying) + y / 3;   x_headline = gap(pi_headline) + y / 3
    S         = i - N_mid;  S_lo = i - N_hi;  S_hi = i - N_lo
    G         = 1.5 * x - S                  (and at both ends of the neutral)
    situation |x| <= eps: S0 if |G| <= delta else S2 (direction sign G); otherwise d = sign x: S1 (direction d) if d*G > delta else S3
    12 months the same with the bank's projection for the published period containing as-of + 12 months, the rate held (same S)
    pivot     = pi* + S / 1.5 - y / 3        (the underlying level at which G = 0)

`assemble` builds the per-bank inputs from objects the loader has already read (the engine Context, the validated manual
files, the macro store, the roster, the open framework alerts); `compute` does the arithmetic. Values are NOT rounded (the
UI rounds). Every input keeps its as-of and its source. DISPLAY-ONLY: never imported by /economic (guard test).
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from .engine import base_rate, next_meeting, trajectory, weakest
from .macro_yoy import yoy

STANCE_CODE_VERSION = "s1.0"
BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
FLAGS = ("stale", "pending", "estimate", "headline_basis", "slack_held", "no_slack", "neutral_sensitive", "boundary", "need_only",
         "mandate_review_pending")
ROLES_FOR_PI = ("underlying", "headline")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class Val:
    """A value with its provenance. lo / hi: a published range (the value is its middle)."""
    v: Optional[float]
    asof: Optional[str] = None
    source: str = ""
    note: str = ""
    lo: Optional[float] = None
    hi: Optional[float] = None


@dataclass
class Neutral:
    mid: float
    lo: float
    hi: float
    asof: str                                    # publication date of the newest estimate used
    flag: str                                    # BANK_ESTIMATE | STAFF_ESTIMATE | SEP_LONGER_RUN | MEMBERS_VIEW
    stale: bool = False
    nominal_from: str = "nominal"                # nominal | real+target
    used: list = field(default_factory=list)     # [(label, nominal value)]
    source: str = ""


@dataclass
class BankInputs:
    currency: str
    asof: str
    mandate_id: str                              # effective_from of the current mandate version
    target_kind: str                             # point | band
    target_star: Optional[float]                 # point value or the DECLARED midpoint; None for a band without one (CHF)
    band: Optional[tuple]                        # (low, high)
    rate: Val
    pi_under: Val
    pi_head: Val
    slack_type: str                              # output_gap | unemployment_gap | none
    y_today: Optional[Val] = None                # output gap (published; a range -> middle, kept in lo / hi)
    u: Optional[Val] = None
    u_star: Optional[Val] = None
    neutral: Optional[Neutral] = None
    neutral_status: str = ""
    pi_12m: Optional[Val] = None
    pi_12m_basis: str = ""                       # underlying | headline
    y_12m: Optional[Val] = None                  # projected output gap for the period containing as-of + 12 months
    u_12m: Optional[Val] = None                  # projected unemployment rate for that period
    conditioning: str = ""
    lens: dict = field(default_factory=dict)
    cycle: dict = field(default_factory=dict)
    market: dict = field(default_factory=dict)
    last_projection: Optional[Val] = None        # the bank's furthest projection of the measure (horizon gap)
    last_projection_basis: str = ""
    flags: set = field(default_factory=set)
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def period_bounds(horizon: str, transform: str) -> Optional[tuple]:
    """(start, end) of a published projection period: 'YYYY' calendar year; 'YYYYQn' quarter; 'FYYYYY' fiscal year April-March;
    'MYYYY' the year ended in March YYYY; 'all' -> None (holds for every date)."""
    h = str(horizon)
    if h == "all":
        return None
    if h.startswith("FY"):
        y = int(h[2:])
        return date(y, 4, 1), date(y + 1, 3, 31)
    if h.startswith("M"):
        y = int(h[1:])
        return date(y - 1, 4, 1), date(y, 3, 31)
    if "Q" in h:
        y, q = int(h[:4]), int(h[-1])
        start = date(y, 3 * q - 2, 1)
        end = add_months(start, 3)
        return start, date.fromordinal(end.toordinal() - 1)
    y = int(h)
    return date(y, 1, 1), date(y, 12, 31)


def _span(b: tuple) -> int:
    return b[1].toordinal() - b[0].toordinal()


def pick_containing(points: list, d: date) -> Optional[dict]:
    """The projection point whose published period contains d; the narrowest such period. points: [{horizon, transform, value|lo/hi,...}]."""
    cands = []
    for p in points:
        b = period_bounds(p["horizon"], p["transform"])
        if b is None or b[0] <= d <= b[1]:
            cands.append((_span(b) if b else 10 ** 6, p))
    return min(cands, key=lambda t: t[0])[1] if cands else None


def pick_begun(points: list, d: date) -> Optional[dict]:
    """The latest estimate for a period already begun at d: the latest start, a tie -> the narrower period."""
    cands = []
    for p in points:
        b = period_bounds(p["horizon"], p["transform"])
        if b is not None and b[0] <= d:
            cands.append((b[0], -_span(b), p))
    return max(cands, key=lambda t: (t[0], t[1]))[2] if cands else None


def point_value(p: dict) -> tuple:
    """(value, lo, hi): a range without a point -> its middle."""
    v, lo, hi = p.get("value"), p.get("low"), p.get("high")
    if v is None and lo is not None and hi is not None:
        v = (lo + hi) / 2
    return v, lo, hi


def gap(pi: Optional[float], inp: BankInputs) -> Optional[float]:
    if pi is None:
        return None
    if inp.target_star is not None:
        return pi - inp.target_star
    lo, hi = inp.band
    return max(0.0, pi - hi) + min(0.0, pi - lo)


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def classify(x: Optional[float], g: Optional[float], delta: float, eps: float) -> dict:
    """{situation, direction, margin}: margin = the distance to the nearest threshold the rule applied."""
    if x is None or g is None:
        return {"situation": None, "direction": None, "margin": None}
    if abs(x) <= eps:
        margins = [eps - abs(x), abs(abs(g) - delta)]
        if abs(g) <= delta:
            return {"situation": "S0", "direction": 0, "margin": min(margins)}
        return {"situation": "S2", "direction": sign(g), "margin": min(margins)}
    d = sign(x)
    margins = [abs(x) - eps, abs(d * g - delta)]
    if d * g > delta:
        return {"situation": "S1", "direction": d, "margin": min(margins)}
    return {"situation": "S3", "direction": d, "margin": min(margins)}


# ---------------------------------------------------------------------------
# Neutral
# ---------------------------------------------------------------------------

def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def neutral_from_estimates(cur: str, entry: dict, target_star: Optional[float], asof: date, roster_members: set,
                           stale_months: int) -> tuple:
    """(Neutral | None, reason) from a neutral.yaml entry (config/cb_stance.yaml v2 neutral_combine)."""
    if not entry or entry.get("status") != "filled":
        return None, (entry or {}).get("reason") or f"neutral {(entry or {}).get('status', 'missing')}"
    ests = [e for e in entry.get("estimates") or [] if e["published"] <= asof]
    if not ests:
        return None, "no estimate published at the as-of"

    def nominal(e: dict, v: float) -> Optional[float]:
        if e["basis"] == "nominal":
            return v
        return None if target_star is None else v + target_star

    def val(e: dict) -> Optional[float]:
        if e.get("point") is not None:
            return e["point"]
        return (e["low"] + e["high"]) / 2

    if cur == "GBP":                                            # members in office, latest statement each, last 12 months
        window_start = add_months(asof, -12)
        latest: dict = {}
        for e in sorted(ests, key=lambda e: e["published"]):
            if e.get("member") in roster_members and e["published"] > window_start:
                latest[e["member"]] = e
        if not latest:
            return None, "no statement of a member in office in the last 12 months"
        vals = {m: nominal(e, val(e)) for m, e in latest.items()}
        xs = sorted(v for v in vals.values() if v is not None)
        n = Neutral(statistics.median(xs), min(xs), max(xs), max(e["published"] for e in latest.values()).isoformat(), entry["flag"],
                    used=[(f"{m} ({latest[m]['published']})", vals[m]) for m in sorted(vals)],
                    nominal_from="nominal" if all(e["basis"] == "nominal" for e in latest.values()) else "real+target")
    else:
        prim = [e for e in ests if e.get("role", "primary") in ("primary", "primary_nominal")]
        nom = [e for e in prim if e["basis"] == "nominal"]
        use = nom or prim                                       # a source that publishes nominal is used directly (ECB)
        if len(use) > 1 and all(e.get("point") is not None for e in use):            # a model list (RBA): median, min-max
            xs = sorted(nominal(e, e["point"]) for e in use)
            n = Neutral(statistics.median(xs), xs[0], xs[-1], max(e["published"] for e in use).isoformat(), entry["flag"],
                        used=[(e["label"], nominal(e, e["point"])) for e in use])
        else:
            e = max(use, key=lambda e: e["published"])
            if e.get("point") is not None:
                lo = hi = mid = nominal(e, e["point"])
            else:
                lo, hi = nominal(e, e["low"]), nominal(e, e["high"])
                mid = None if lo is None else (lo + hi) / 2
            if mid is None:
                return None, "a real estimate needs a point target or a declared midpoint"
            n = Neutral(mid, lo, hi, e["published"].isoformat(), entry["flag"], used=[(e["label"], mid)])
        n.nominal_from = "nominal" if all(e["basis"] == "nominal" for e in use) else "real+target"
    newest = date.fromisoformat(n.asof)
    n.stale = _months_between(newest, asof) > stale_months
    n.source = "data/cb/manual/neutral.yaml"
    return n, ""


def sep_neutral(projections: list, asof: date) -> tuple:
    """USD: mid = median of the SEP longer-run dots, range = min-max of the dots."""
    seps = sorted({r["meeting_date"] for r in projections if r["kind"] == "dot" and r["meeting_date"] <= asof})
    if not seps:
        return None, "no SEP on record"
    sep = seps[-1]
    dots = [(r["level"], r["count"]) for r in projections if r["meeting_date"] == sep and r["kind"] == "dot" and r["horizon"] == "longer_run"]
    xs = sorted(lv for lv, c in dots for _ in range(int(c)))
    if not xs:
        return None, f"the {sep} SEP has no longer-run dot"
    return Neutral(statistics.median(xs), xs[0], xs[-1], sep.isoformat(), "SEP_LONGER_RUN", used=[("longer-run dots", len(xs))],
                   source=f"data/cb/projections.parquet (SEP {sep}, {len(xs)} longer-run dots)"), ""


def sep_value(projections: list, sep: date, variable: str, horizon: str) -> Optional[float]:
    r = next((r for r in projections if r["meeting_date"] == sep and r["variable"] == variable and r["horizon"] == horizon
              and r["kind"] == "median_published"), None)
    return None if r is None else r["value"]


# ---------------------------------------------------------------------------
# Market lens, cycle, market
# ---------------------------------------------------------------------------

def rate_at(tr, when: date) -> tuple:
    """(rate, flag, reason): the implied policy rate in force on `when` on trajectory `tr` (the rate after the last meeting effective
    on or before it; the base when no meeting falls before it)."""
    if tr.na_reason:
        return None, None, tr.na_reason
    pts = [p for p in tr.points if p.eff is not None and p.eff <= when]
    if not pts:
        return (tr.base.rate if tr.base else None), "DECIDED", "" if tr.base else "no base rate"
    p = max(pts, key=lambda p: p.eff)
    if p.rate is None:
        return None, p.flag, p.reason or "no implied rate for the meeting before the date"
    return p.rate, p.flag, ""


def market_m12(tr, asof: date, days: int = 365) -> dict:
    """The engine's 12-month end point (same rule as payload.path_json m12)."""
    from datetime import timedelta
    until = asof + timedelta(days=days)
    pts = [p for p in tr.points if p.meeting is not None and p.meeting <= until]
    last = next((p for p in reversed(pts) if p.rate is not None), None)
    if last is None or last.cum_bp is None:
        return {"meeting": None, "rate": None, "cum_bp": None, "flag": None, "na": tr.na_reason or (pts[-1].reason if pts else "no meeting in the next 12 months")}
    return {"meeting": last.meeting.isoformat(), "rate": last.rate, "cum_bp": last.cum_bp, "flag": last.flag, "na": None}


def cycle_of(decisions: list, asof: date) -> dict:
    """Direction of the last change, the consecutive moves in that direction since the last reversal (with dates), the holds since."""
    rows = sorted((d for d in decisions if d["meeting_date"] <= asof and d.get("delta_bp") is not None), key=lambda d: d["meeting_date"])
    changes = [d for d in rows if d["delta_bp"] != 0]
    if not changes:
        return {"direction": None, "moves": [], "holds_since": len(rows), "last_change": None}
    last = changes[-1]
    d = sign(last["delta_bp"])
    run = []
    for c in reversed(changes):
        if sign(c["delta_bp"]) != d:
            break
        run.append({"date": c["meeting_date"].isoformat(), "bp": c["delta_bp"]})
    holds = sum(1 for r in rows if r["meeting_date"] > last["meeting_date"] and r["delta_bp"] == 0)
    return {"direction": "hike" if d > 0 else "cut", "moves": list(reversed(run)), "holds_since": holds,
            "last_change": last["meeting_date"].isoformat()}


# ---------------------------------------------------------------------------
# Assemble (pure on already-loaded objects)
# ---------------------------------------------------------------------------

def _obs(per: dict, sid: str, asof: date) -> Optional[Val]:
    """The latest y/y (published or derived from the index) of a macro series on or before as-of."""
    rows = {p: r for p, r in (per.get(sid) or {}).items() if r["period_start"] <= asof}
    if not rows:
        return None
    p = max(rows)
    r = rows[p]
    if r["published_as"] == "index":
        y = yoy({k: x["value"] for k, x in rows.items()}, p)
        v = None if y is None else float(y)
    else:
        v = r["value"]
    rel = r["release_date"].isoformat() if r.get("release_date") else None
    return Val(v, p, f"{sid} ({r['source_url']})", note=f"released {rel}" if rel else "")


def _forecast_points(entry: dict, role: str) -> list:
    """The values of a forecasts.yaml role, each with its period, transform, label and source (the series' own source when it
    has one, e.g. the BoJ output gap file; else the publication's)."""
    out = []
    for s in (entry or {}).get("series") or []:
        if s["role"] == role and s.get("status") != "pending":
            src = s.get("source") or entry.get("source") or {}
            for v in s.get("values") or []:
                out.append(dict(v, transform=s["transform"], variable=s["variable"], horizon=str(v["horizon"]),
                                src=f"{src.get('url', '')} {src.get('page', '')}".strip()))
    return out


def assemble(ctx, mandates_cur: dict, neutral_doc: dict, forecasts_doc: dict, macro_per: dict, macro_cfg: dict, roster: dict,
             open_alert_banks: set, params: dict, asof: date, stale_series: set, newer_projection: dict) -> dict:
    """{currency: BankInputs}. mandates_cur: {ccy: MandateVersion}; macro_per: {series_id: {period: row}} (cb_macro.by_series);
    stale_series: macro series ids stale at the as-of; newer_projection: {ccy: True} when a newer projection publication than
    the one entered has passed."""
    out = {}
    T = add_months(asof, int(params["horizon_months"]))
    okun = params["rule"]["okun_coefficient"]
    for cur in BANKS:
        mv = mandates_cur.get(cur)
        if mv is None:
            continue
        t = mv.target
        star = t.value if t.kind == "point" else t.midpoint
        decs = ctx.decisions.get(cur, [])
        br = base_rate(decs, asof)
        rate = Val(br.rate if br else None, br.meeting.isoformat() if br and br.meeting else None, "data/cb/decisions.parquet (engine.base_rate)",
                   note="Fed: midpoint of the target range" if cur == "USD" else "")
        inp = BankInputs(cur, asof.isoformat(), mv.effective_from.isoformat(), t.kind, star, (t.low, t.high) if t.kind == "band" else None,
                         rate, Val(None), Val(None), mv.slack_type, conditioning=mv.conditioning)
        # inflation today
        unders = [_obs(macro_per, sid, asof) for sid in mv.underlying]
        if all(u is not None and u.v is not None for u in unders):
            v = sum(u.v for u in unders) / len(unders)               # CAD: the mean of CPI-trim and CPI-median, not rounded
            inp.pi_under = Val(v, max(u.asof for u in unders), " + ".join(u.source for u in unders),
                               note="mean of " + ", ".join(f"{u.v}" for u in unders) if len(unders) > 1 else unders[0].note)
        else:
            inp.notes.append("underlying: no observation")
            inp.flags.add("pending")
        h = _obs(macro_per, mv.headline, asof)
        inp.pi_head = h or Val(None)
        for sid in list(mv.underlying) + [mv.headline]:
            if sid in stale_series:
                inp.flags.add("stale")
                inp.notes.append(f"{sid} stale")
        fc = (forecasts_doc.get("banks") or {}).get(cur)
        # slack today
        if mv.slack_type == "output_gap":
            p = pick_begun(_forecast_points(fc, "output_gap"), asof)
            if p is None:
                inp.notes.append("output gap: no estimate for a period already begun")
            else:
                v, lo, hi = point_value(p)
                inp.y_today = Val(v, p["horizon"], f"{fc['publication']}: {p['variable']} [{p['src']}]", lo=lo, hi=hi)
        elif mv.slack_type == "unemployment_gap":
            usid = next((sid for sid, s in macro_cfg.items() if s.get("currency") == cur and s.get("role") == "unemployment"), None)
            inp.u = _obs(macro_per, usid, asof) if usid else None
            if usid in stale_series:
                inp.flags.add("stale")
                inp.notes.append(f"{usid} stale")
            if cur == "USD":
                seps = sorted({r["meeting_date"] for r in ctx.projections if r["kind"] == "median_published" and r["meeting_date"] <= asof})
                if seps:
                    us = sep_value(ctx.projections, seps[-1], "unemployment_rate", "longer_run")
                    inp.u_star = Val(us, seps[-1].isoformat(), f"FOMC SEP {seps[-1]}: longer-run median unemployment rate")
            else:
                nr = _forecast_points(fc, "nairu")
                if nr:
                    inp.u_star = Val(nr[0]["value"], fc["published"].isoformat(), f"{fc['publication']}: NAIRU [{nr[0]['src']}]")
        # neutral
        if cur == "USD":
            n, why = sep_neutral(ctx.projections, asof)
            inp.neutral_status = "fed_sep"
        else:
            entry = (neutral_doc.get("banks") or {}).get(cur)
            members = {p["name"] for p in roster.get("people", []) if p.get("currency") == cur}
            n, why = neutral_from_estimates(cur, entry, star, asof, members, params["staleness"]["neutral_months"])
            inp.neutral_status = (entry or {}).get("status", "missing")
        if n is None:
            inp.notes.append(f"neutral: {why}")
            if inp.neutral_status == "pending":
                inp.flags.add("pending")
        else:
            if cur == "USD":
                n.stale = _months_between(date.fromisoformat(n.asof), asof) > params["staleness"]["neutral_months"]
            inp.neutral = n
            if n.stale:
                inp.flags.add("stale")
        # 12 months: the bank's projection for the published period containing as-of + 12 months
        if cur == "USD":
            seps = sorted({r["meeting_date"] for r in ctx.projections if r["kind"] == "median_published" and r["meeting_date"] <= asof})
            if seps:
                sep = seps[-1]
                pts = [{"horizon": r["horizon"], "transform": "q4_q4", "value": r["value"], "variable": r["variable"]}
                       for r in ctx.projections if r["meeting_date"] == sep and r["kind"] == "median_published" and r["horizon"] != "longer_run"]
                core = pick_containing([p for p in pts if p["variable"] == "core_pce_inflation"], T)
                un = pick_containing([p for p in pts if p["variable"] == "unemployment_rate"], T)
                if core:
                    inp.pi_12m, inp.pi_12m_basis = Val(core["value"], core["horizon"], f"FOMC SEP {sep}: core PCE inflation (Q4/Q4)"), "underlying"
                if un:
                    inp.u_12m = Val(un["value"], un["horizon"], f"FOMC SEP {sep}: unemployment rate (Q4 average)")
                last = max((p for p in pts if p["variable"] == "core_pce_inflation"), key=lambda p: p["horizon"], default=None)
                if last:
                    inp.last_projection, inp.last_projection_basis = Val(last["value"], last["horizon"], f"FOMC SEP {sep}: core PCE inflation"), "underlying"
        elif fc:
            und = pick_containing(_forecast_points(fc, "underlying"), T)
            if und is not None:
                inp.pi_12m, inp.pi_12m_basis = Val(point_value(und)[0], und["horizon"], f"{fc['publication']}: {und['variable']}"), "underlying"
            else:
                hd = pick_containing(_forecast_points(fc, "headline"), T)
                if hd is not None:
                    inp.pi_12m, inp.pi_12m_basis = Val(point_value(hd)[0], hd["horizon"], f"{fc['publication']}: {hd['variable']}"), "headline"
                    inp.flags.add("headline_basis")
            if mv.slack_type == "output_gap":
                og = pick_containing(_forecast_points(fc, "output_gap"), T)
                if og is not None:
                    inp.y_12m = Val(point_value(og)[0], og["horizon"], f"{fc['publication']}: {og['variable']}", lo=og.get("low"), hi=og.get("high"))
            elif mv.slack_type == "unemployment_gap":
                un = pick_containing(_forecast_points(fc, "unemployment"), T)
                if un is not None:
                    inp.u_12m = Val(point_value(un)[0], un["horizon"], f"{fc['publication']}: {un['variable']}")
            for role in ROLES_FOR_PI:                               # the furthest projection: underlying, else headline
                pts = _forecast_points(fc, role)
                if pts:
                    last = max(pts, key=lambda p: (period_bounds(p["horizon"], p["transform"]) or (date.max, date.max))[1])
                    inp.last_projection, inp.last_projection_basis = Val(point_value(last)[0], last["horizon"], f"{fc['publication']}: {last['variable']}"), role
                    break
            if any(s.get("status") == "pending" for s in fc.get("series") or []):
                inp.flags.add("pending")
            if fc.get("cutoff_flag") == "ESTIMATE":
                inp.flags.add("estimate")
        if newer_projection.get(cur):
            inp.flags.add("stale")
            inp.notes.append("a newer projection publication than the one entered has passed")
        if cur in open_alert_banks:
            inp.flags.add("mandate_review_pending")
        # lens
        tr = trajectory(ctx, cur, asof)
        inp.lens = lens_inputs(ctx, cur, mv.conditioning, fc, tr, asof, T)
        if inp.lens.get("flag") == "ESTIMATE" or inp.lens.get("cutoff_flag") == "ESTIMATE":
            inp.flags.add("estimate")
        inp.cycle = cycle_of(decs, asof)
        nm = next_meeting(tr)
        inp.market = {"next": {"meeting": nm.decision.isoformat() if nm.decision else None, "step_bp": nm.step_bp, "flag": nm.flag,
                               "stale": nm.stale, "na": nm.step_reason or None},
                      "m12": market_m12(tr, asof)}
        if inp.market["m12"].get("flag") == "ESTIMATE":
            inp.flags.add("estimate")
        out[cur] = inp
    return out


def lens_inputs(ctx, cur: str, conditioning: str, fc: Optional[dict], tr, asof: date, T: date) -> dict:
    """The bank-vs-market lens by conditioning (S1 B.10). own_path reuses the engine's GAP (not recomputed)."""
    if conditioning == "own_path":
        from .analysis import gap_for
        g = gap_for(ctx, cur, tr, asof)
        gy = next((y for y in g.years if getattr(y, "year", None) == T.year), None)
        if gy is None:
            return {"kind": "own_path", "gap_bp": None, "na": g.reason or f"no GAP for {T.year}", "source": f"engine GAP ({g.kind})"}
        return {"kind": "own_path", "year": T.year, "gap_bp": gy.gap_bp, "bank": gy.bank_median, "market": gy.market_rate, "flag": gy.market_flag,
                "na": None if gy.gap_bp is not None else (gy.note or "n/a"), "source": f"engine GAP ({g.kind}, {g.sep or ''})"}
    if conditioning in ("market", "constant_rate"):
        today, f1, why1 = rate_at(tr, T)
        out = {"kind": conditioning, "target_date": T.isoformat(), "rate_today_curve": today, "flag_today": f1}
        if conditioning == "market":
            cutoff = (fc or {}).get("cutoff")
            out.update(cutoff=cutoff.isoformat() if cutoff else None, cutoff_flag=(fc or {}).get("cutoff_flag"))
            if cutoff is None:
                return {**out, "delta_bp": None, "na": "no market cutoff published"}
            src = ctx.primary(cur)
            first = min((ctx.market.first_date(s) for s, *_ in src if ctx.market.first_date(s)), default=None) if src else None
            if first is None or first > cutoff:
                return {**out, "delta_bp": None, "na": f"no market history at the cutoff {cutoff} (first data {first})"}
            trc = trajectory(ctx, cur, cutoff)
            then, f2, why2 = rate_at(trc, T)
            out.update(rate_cutoff_curve=then, flag_cutoff=f2)
            if today is None or then is None:
                return {**out, "delta_bp": None, "na": why1 or why2}
            return {**out, "delta_bp": (today - then) * 100, "flag": weakest(f1, f2), "na": None}
        assumed = next((v.get("value") for s in (fc or {}).get("series") or [] if s["role"] == "rate_assumption" for v in s.get("values") or []), None)
        out.update(assumed_rate=assumed)
        if today is None or assumed is None:
            return {**out, "delta_bp": None, "na": why1 or "no assumed rate"}
        return {**out, "delta_bp": (today - assumed) * 100, "flag": f1, "na": None}
    return {"kind": conditioning or "unpublished", "delta_bp": None, "na": "the bank publishes no rate conditioning"}


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

def _y(inp: BankInputs, which: str, okun: float) -> tuple:
    """(y, Val used, how)."""
    if inp.slack_type == "output_gap":
        v = inp.y_today if which == "today" else inp.y_12m
        return (None, None, "") if v is None else (v.v, v, "output gap as published")
    if inp.slack_type == "unemployment_gap":
        u = inp.u if which == "today" else inp.u_12m
        if u is None or inp.u_star is None or u.v is None or inp.u_star.v is None:
            return None, None, ""
        return -okun * (u.v - inp.u_star.v), u, f"-{okun} * (u {u.v} - u* {inp.u_star.v})"
    return None, None, ""


def compute(inp: BankInputs, params: dict) -> dict:
    delta, eps = params["situations"]["delta_pp"], params["situations"]["epsilon_pp"]
    tol, cons = params["boundary_tol_pp"], params["horizon_consistency_pp"]
    okun, infl = params["rule"]["okun_coefficient"], params["rule"]["inflation_response"]
    flags = set(inp.flags)
    r: dict = {"currency": inp.currency, "asof": inp.asof, "mandate_id": inp.mandate_id, "stance_version": params["version"],
               "code_version": STANCE_CODE_VERSION, "target_star": inp.target_star, "band": inp.band, "rate": inp.rate.v}
    gu, gh = gap(inp.pi_under.v, inp), gap(inp.pi_head.v, inp)
    y, yval, yhow = _y(inp, "today", okun)
    if inp.slack_type == "none" or y is None:
        flags.add("no_slack")
    third = (y / 3) if y is not None else 0.0
    x = None if gu is None else gu + third
    xh = None if gh is None else gh + third
    r.update(pi_under=inp.pi_under.v, pi_head=inp.pi_head.v, gap_under=gu, gap_head=gh, y=y, y_how=yhow, x=x, x_headline=xh)
    # 12 months, the rate held: the need does not depend on the neutral
    y12, y12val, y12how = _y(inp, "12m", okun)
    if y12 is None and y is not None:
        y12, y12how = y, "today's slack held (no projection)"
        flags.add("slack_held")
    g12 = gap(inp.pi_12m.v if inp.pi_12m else None, inp)
    x12 = None if g12 is None else g12 + ((y12 / 3) if y12 is not None else 0.0)
    r.update(pi_12m=inp.pi_12m.v if inp.pi_12m else None, pi_12m_period=inp.pi_12m.asof if inp.pi_12m else None, pi_12m_basis=inp.pi_12m_basis,
             gap_12m=g12, y_12m=y12, y_12m_how=y12how, x_12m=x12,
             na_12m=None if inp.pi_12m else "the latest publication does not cover the period containing as-of + 12 months")
    n = inp.neutral
    if n is None or inp.rate.v is None:
        flags.add("need_only")
        r.update(N=None, S=None, S_lo=None, S_hi=None, G=None, G_lo=None, G_hi=None, situation=None, direction=None, margin=None,
                 pivot=None, G_12m=None, situation_12m=None, direction_12m=None)
    else:
        i = inp.rate.v
        S, S_lo, S_hi = i - n.mid, i - n.hi, i - n.lo
        G = None if x is None else infl * x - S
        G_at_lo = None if x is None else infl * x - S_hi          # neutral at its low end
        G_at_hi = None if x is None else infl * x - S_lo          # neutral at its high end
        c = classify(x, G, delta, eps)
        c_lo, c_hi = classify(x, G_at_lo, delta, eps), classify(x, G_at_hi, delta, eps)
        if c_lo["situation"] != c_hi["situation"] or c_lo["direction"] != c_hi["direction"]:
            flags.add("neutral_sensitive")
        if c["margin"] is not None and c["margin"] < tol:
            flags.add("boundary")
        pivot = None if inp.target_star is None else inp.target_star + S / infl - third
        G12 = None if x12 is None else infl * x12 - S
        c12 = classify(x12, G12, delta, eps)
        r.update(N={"mid": n.mid, "lo": n.lo, "hi": n.hi, "asof": n.asof, "flag": n.flag, "stale": n.stale, "nominal_from": n.nominal_from,
                    "used": n.used, "source": n.source},
                 S=S, S_lo=S_lo, S_hi=S_hi, G=G, G_lo=G_at_lo, G_hi=G_at_hi, situation=c["situation"], direction=c["direction"], margin=c["margin"],
                 situation_at_N_lo=c_lo["situation"], situation_at_N_hi=c_hi["situation"], pivot=pivot,
                 G_12m=G12, situation_12m=c12["situation"], direction_12m=c12["direction"])
    # lens + horizon gap
    lens = dict(inp.lens)
    if lens.get("kind") in ("market", "constant_rate") and inp.last_projection is not None and inp.last_projection.v is not None:
        hg = gap(inp.last_projection.v, inp)
        lens.update(horizon_gap=hg, horizon_period=inp.last_projection.asof, horizon_basis=inp.last_projection_basis,
                    consistent=abs(hg) <= cons)
        if inp.last_projection_basis == "headline":
            lens["horizon_flag"] = "headline_basis"
    r.update(lens=lens, cycle=inp.cycle, market=inp.market, flags=sorted(flags), notes=list(inp.notes),
             inputs=inputs_record(inp))
    return r


def inputs_record(inp: BankInputs) -> dict:
    """Every input with its as-of and source (the arithmetic of the report and the history)."""
    def v(x):
        return None if x is None else asdict(x)
    return {"rate": v(inp.rate), "pi_under": v(inp.pi_under), "pi_head": v(inp.pi_head), "slack_type": inp.slack_type,
            "y_today": v(inp.y_today), "u": v(inp.u), "u_star": v(inp.u_star), "pi_12m": v(inp.pi_12m), "pi_12m_basis": inp.pi_12m_basis,
            "y_12m": v(inp.y_12m), "u_12m": v(inp.u_12m), "neutral_status": inp.neutral_status, "conditioning": inp.conditioning,
            "last_projection": v(inp.last_projection), "last_projection_basis": inp.last_projection_basis}


def compute_all(inputs: dict, params: dict) -> dict:
    return {c: compute(i, params) for c, i in inputs.items()}
