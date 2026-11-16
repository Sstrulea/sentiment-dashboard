"""Stance v5 (stance-ui-v5) - Position / Need / Why. Pure and deterministic; the thresholds are pre-registered in
config/cb_stance.yaml (section v5) and never tuned on results. The texts are fixed templates filled with the inputs.

    P = rate - neutral_mid                      (Position; None without a neutral)
    N = core - target                           (Need; a band without a declared midpoint: 0 inside, else the distance to the nearest edge)
    d = sign(N)
    labels on one scale |x| <= 0.25 / 0.75 / 1.5 / above
    conclusion: a) |N| <= 0.25 no move; b) own published path (Fed dots, RBNZ OCR track): 0.25 steps from the rate to the path at
    the horizon; c) constant-rate forecast (SNB): the f) test without P; d) no P and none of b/c -> n/a; e) P.d <= 0.25 -> move in d;
    f) hold only if the bank's forecast of the same measure cuts |N| by >= 0.25 at the horizon AND its assumed path adds < 0.25 in d.
    borderline: the conclusion changes inside the bank's neutral interval or at +-0.05 of a threshold.
The previous computation (stance.py: Taylor-type pressure, S0-S3) is still computed and stored; it is no longer displayed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

SCALE = (0.25, 0.75, 1.5)
P_LABELS = {1: ("Neutral", "Slightly restrictive", "Restrictive", "Very restrictive"),
            -1: ("Neutral", "Slightly stimulative", "Stimulative", "Very stimulative")}
N_LABELS = {1: ("Near target", "Slightly above target", "Above target", "Well above target"),
            -1: ("Near target", "Slightly below target", "Below target", "Well below target")}
STEP = 0.25
TOL = 0.05


@dataclass
class V5In:
    bank: str
    short: str                                    # Fed, ECB, BoE, BoJ, BoC, RBA, RBNZ, SNB
    rate: float
    core: Optional[float]
    target: Optional[float] = None                # the point or the declared midpoint
    band: Optional[tuple] = None                  # (low, high) of a band WITHOUT declared midpoint (CHF)
    neutral_mid: Optional[float] = None
    neutral_lo: Optional[float] = None
    neutral_hi: Optional[float] = None
    neutral_na: str = ""
    headline: Optional[float] = None
    fc_value: Optional[float] = None              # the bank's projection at the horizon (the period containing as-of + 12 months)
    fc_basis: str = ""                            # underlying | headline
    fc_period: str = ""
    fc_transform: str = ""
    conditioning: str = ""                        # own_path | market | constant_rate | unpublished
    path_kind: str = ""                           # dots | ocr_track (own_path)
    path: list = field(default_factory=list)      # [(label, rate)] in time order, up to the horizon
    path_period: str = ""
    path_transform: str = ""
    market_add: Optional[float] = None            # the assumed (market) path at the cutoff: rate at the horizon - the current rate


def label(x: Optional[float], kind: str) -> Optional[str]:
    if x is None:
        return None
    s = 1 if x >= 0 else -1
    i = next((k for k, t in enumerate(SCALE) if abs(x) <= t + 1e-12), 3)
    return (P_LABELS if kind == "P" else N_LABELS)[s][i]


def need(i: V5In) -> Optional[float]:
    if i.core is None:
        return None
    if i.band is not None and i.target is None:
        lo, hi = i.band
        return 0.0 if lo <= i.core <= hi else (i.core - hi if i.core > hi else i.core - lo)
    return i.core - i.target


def position(i: V5In, mid: Optional[float] = None) -> Optional[float]:
    m = i.neutral_mid if mid is None else mid
    return None if m is None else i.rate - m


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def steps(i: V5In, bump: float = 0.0) -> Optional[int]:
    """Whole 0.25 steps between the current rate and the bank's own path at the horizon (half away from zero)."""
    if not i.path:
        return None
    x = (i.path[-1][1] + bump - i.rate) / STEP
    return int(sign(x) * int(abs(x) + 0.5 + 1e-9))


def move(d: int, n: int = 2) -> str:
    w = "hike" if d > 0 else "cut"
    return f"One more {w} likely" if n == 1 else f"More {w}s likely"


def forecast_now(i: V5In) -> Optional[float]:
    """The value today of the measure the bank forecasts (core when it forecasts core, else total)."""
    return i.core if i.fc_basis == "underlying" else i.headline


def forecast_target_gap(i: V5In, v: float) -> float:
    if i.band is not None and i.target is None:
        lo, hi = i.band
        return 0.0 if lo <= v <= hi else (v - hi if v > hi else v - lo)
    return v - i.target


def conclude(i: V5In, *, N: Optional[float] = None, P: Optional[float] = None, red_bump: float = 0.0, add_bump: float = 0.0,
             path_bump: float = 0.0) -> tuple:
    """(conclusion, case). `N`, `P` and the bumps exist for the borderline test."""
    N = need(i) if N is None else N
    if N is None:
        return "—", "na"
    if abs(N) <= 0.25 + 1e-12:
        return "No reason to move", "a"
    d = sign(N)
    if i.conditioning == "own_path" and i.path:
        n = steps(i, path_bump)
        if n == 0:
            return "Likely on hold", "b"
        return move(1 if n > 0 else -1, abs(n)), "b"
    hold = _forecast_supports_hold(i, d, red_bump, add_bump)
    if i.conditioning == "constant_rate":
        return ("Likely on hold" if hold else move(d)), "c"
    P = position(i) if P is None else P
    if P is None:
        return "—", "d"
    if P * d <= 0.25 + 1e-12:
        return move(d), "e"
    return ("Likely on hold" if hold else move(d)), "f"


def _forecast_supports_hold(i: V5In, d: int, red_bump: float = 0.0, add_bump: float = 0.0) -> bool:
    now = forecast_now(i)
    if i.fc_value is None or now is None:
        return False
    reduction = abs(forecast_target_gap(i, now)) - abs(forecast_target_gap(i, i.fc_value)) + red_bump
    add = 0.0 if i.market_add is None or i.conditioning == "constant_rate" else i.market_add * d
    return reduction >= 0.25 - 1e-12 and add + add_bump < 0.25


def borderline(i: V5In) -> bool:
    base = conclude(i)[0]
    trials = []
    N = need(i)
    if N is not None:
        trials += [dict(N=N + t) for t in (-TOL, TOL)]
    for m in (i.neutral_lo, i.neutral_hi):
        if m is not None:
            trials.append(dict(P=i.rate - m))
    P = position(i)
    if P is not None:
        trials += [dict(P=P + t) for t in (-TOL, TOL)]
    trials += [dict(red_bump=t) for t in (-TOL, TOL)] + [dict(add_bump=t) for t in (-TOL, TOL)] + [dict(path_bump=t) for t in (-TOL, TOL)]
    return any(conclude(i, **kw)[0] != base for kw in trials)


# ---------------------------------------------------------------- texts (fixed templates) --

def pct(v: float) -> str:
    return f"{v:.1f}%" if abs(v * 10 - round(v * 10)) < 1e-9 else f"{v:g}%"


def period_text(period: str, transform: str) -> str:
    p = str(period)
    if p.startswith("FY"):
        return f"in {p}"
    if len(p) == 6 and p[4] == "Q":
        return f"by end-{p[:4]}" if p.endswith("Q4") else f"in {p}"
    if p.startswith("M") and p[1:].isdigit():
        return f"in the year to March {p[1:]}"
    if transform in ("q4_q4", "yoy_quarter", "quarter_level"):
        return f"by end-{p}"
    return f"in {p}"


def measure_name(i: V5In) -> str:
    return "core inflation" if i.fc_basis == "underlying" else "total inflation"


def trend(i: V5In, d: int) -> str:
    now = forecast_now(i)
    per = period_text(i.fc_period, i.fc_transform)
    if now is not None and abs(i.fc_value - now) >= 0.25 - 1e-12:
        verb = "rising" if i.fc_value > now else "easing"
        return f"{measure_name(i)} {verb} from {pct(now)} to {pct(i.fc_value)} {per}"
    tail = ", not coming down" if d > 0 else ", not going up"
    return f"{measure_name(i)} still at {pct(i.fc_value)} {per}{tail}"


def lead(P: float, N: float, d: int) -> tuple:
    """('Already restrictive', False) | ('Slightly restrictive', False) | ('Only ... target', True: a sentence of its own)."""
    lab = label(P, "P")
    side = "above" if d > 0 else "below"
    if d * P > abs(N) + TOL:
        return f"Already {lab.lower()}", False
    if abs(d * P - abs(N)) <= TOL + 1e-12:
        return lab, False
    return f"Only {lab.lower()} for inflation this far {side} target", True


def reason(i: V5In, case: str, conclusion: str) -> str:
    N, P = need(i), position(i)
    d = sign(N) if N else 0
    B = i.short
    if case == "na":
        return "no core inflation observation"
    if case == "a":
        inside = i.band is not None and i.target is None and N == 0
        state = "inside the target range" if inside else "near target"
        if P is not None:
            out = f"Rate {'near neutral' if abs(P) <= 0.25 + 1e-12 else label(P, 'P').lower()} and core inflation {state}."
        else:
            out = f"Core inflation is {state}" + (f", and the {B} sees {measure_name(i)} at {pct(i.fc_value)} {period_text(i.fc_period, i.fc_transform)}."
                                                  if i.fc_value is not None else ".")
        tgt = i.target if i.target is not None else (sum(i.band) / 2 if i.band else None)
        if i.headline is not None and tgt is not None and abs(i.headline - tgt) >= 0.75 - 1e-12 and not inside:
            fc = f", {pct(i.fc_value)} {period_text(i.fc_period, i.fc_transform)}" if i.fc_value is not None and i.fc_basis == "headline" else ""
            out += f" The risk is total inflation: {pct(i.headline)} now{fc}."
        return out
    if case == "b":
        out = ""
        if P is not None:
            lt, sentence = lead(P, N, d)
            out = lt + ". "
        per = period_text(i.path_period, i.path_transform)
        if i.path_kind == "dots":
            n = steps(i)
            w = "hike" if n > 0 else "cut"
            plan = "no change" if n == 0 else (f"one more {w}" if abs(n) == 1 else f"{abs(n)} more {w}s")
            first = next(k for k, (_, v) in enumerate(i.path) if abs(v - i.path[-1][1]) < 1e-9)
            hold = ", then a hold" if n != 0 and first < len(i.path) - 1 else ""
            fc = f", and sees {measure_name(i)} at {pct(i.fc_value)} {period_text(i.fc_period, i.fc_transform)}" if i.fc_value is not None else ""
            return out + f"The {B} plans {plan}{hold}{fc}."
        end = i.path[-1][1]
        verb = "rises" if end > i.rate else "falls" if end < i.rate else "stays"
        tail = f"from {pct(i.rate)} to {pct(end)}" if verb != "stays" else f"at {pct(end)}"
        return out + f"The {B}'s own rate track {verb} {tail} {per}."
    if case == "d":
        return f"No neutral-rate estimate ({i.neutral_na or 'not published'}) and no published rate path."
    if case == "c":
        return f"With the rate unchanged, the {B} sees {trend(i, d)}."
    if case == "e":
        side = "above" if d > 0 else "below"
        pos = "Near neutral" if abs(P) <= 0.25 + 1e-12 else ("Still below neutral" if d > 0 else "Still above neutral")
        return f"{pos} with inflation {side} target, and the {B} sees {trend(i, d)}."
    # f
    lt, _ = lead(P, N, d)
    hold = conclusion == "Likely on hold"
    w = "hikes" if d > 0 else "cuts"
    path = ""
    if i.conditioning == "unpublished":
        path = " (rate path not published)"
    elif i.market_add is not None:
        path = f", even with the {w} markets expected" if i.market_add * d >= 0.25 - 1e-12 else f" without further {w}"
    if i.fc_value is None:
        return f"{lt}, and the {B} publishes no projection of this measure{path}."
    return f"{lt}, {'and' if hold else 'but'} the {B} sees {trend(i, d)}{path}."


def evaluate(i: V5In) -> dict:
    N, P = need(i), position(i)
    c, case = conclude(i)
    r = reason(i, case, c)
    if c != "—" and borderline(i):
        r = r.rstrip(".") + ", borderline."
    inside = i.band is not None and i.target is None and N == 0
    return {"bank": i.bank, "P": P, "N": N, "P_label": label(P, "P"),
            "N_label": "Inside target range" if inside else label(N, "N"), "conclusion": c, "case": case, "reason": r,
            "move": c in ("More hikes likely", "More cuts likely", "One more hike likely", "One more cut likely")}


SHORT = {"USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ", "CAD": "BoC", "AUD": "RBA", "NZD": "RBNZ", "CHF": "SNB"}


def from_inputs(inp) -> V5In:
    """V5In from a stance.BankInputs (the loader's point-in-time inputs)."""
    n, lens = inp.neutral, inp.lens or {}
    rate = inp.rate.v
    add = None
    if inp.conditioning == "market" and lens.get("rate_cutoff_curve") is not None and rate is not None:
        add = lens["rate_cutoff_curve"] - rate
    star = inp.target_star
    return V5In(
        bank=inp.currency, short=SHORT.get(inp.currency, inp.currency), rate=rate, core=inp.pi_under.v, target=star,
        band=tuple(inp.band) if inp.band and star is None else None,
        neutral_mid=n.mid if n else None, neutral_lo=n.lo if n else None, neutral_hi=n.hi if n else None,
        neutral_na="" if n else (inp.neutral_status or "missing"), headline=inp.pi_head.v,
        fc_value=inp.pi_12m.v if inp.pi_12m else None, fc_basis=inp.pi_12m_basis or "", fc_period=inp.pi_12m.asof if inp.pi_12m else "",
        fc_transform=inp.pi_12m_transform or "", conditioning=inp.conditioning or "",
        path_kind=lens.get("path_kind", ""), path=[tuple(p) for p in lens.get("path") or []], path_period=lens.get("path_period", ""),
        path_transform=lens.get("path_transform", ""), market_add=add)
