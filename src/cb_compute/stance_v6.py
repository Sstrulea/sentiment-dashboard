"""Stance v6 (stance-ui-v6) - Position / Need / Why without a verdict. Pure and deterministic; the thresholds, labels and the reading
rule are pre-registered in config/cb_stance.yaml (section v6) and never tuned on results. ARCHITECTURE section 23f.

    pi_fc  = the bank's projection chosen by the v3 horizon_rule (the shortest published period containing as-of + 12 months):
             the underlying measure, else the headline (flag headline_basis)
    P      = (rate - pi_fc) - (neutral_mid - target)        P_lo with neutral_hi, P_hi with neutral_lo   (= v3 S - gap_12m)
    N      = gap(pi_fc) = v3 gap_12m                        (a band without a declared midpoint: 0 inside, else the nearest edge)
    labels on one scale |x| <= 0.25 / 0.75 / 1.5 / above (unrounded values)
    Taylor = neutral + 1.5 * gap(pi) + 0.5 * y - rate        = v3 G (today) and v3 G_12m (forecast), each at neutral mid / lo / hi
    reading: a = T_today, b = T_forecast; a * b < 0 -> Mixed signals; the four ends all > 0 / all < 0 -> robust; else sensitive by
             sign(a) (sign(b) when a = 0); a = b = 0 -> no pressure; one scenario n/a -> the same test on the other alone.

v6 READS the v3 values (G, G_12m, gap_12m, lens) already computed by stance.compute and never recomputes them; the texts are
written by payload.stance_page_v6 (fixed templates). DISPLAY-ONLY: never imported by /economic.
"""
from __future__ import annotations

from typing import Optional

SCALE = (0.25, 0.75, 1.5)
P_LABELS = {1: ("Neutral", "Slightly restrictive", "Restrictive", "Very restrictive"),
            -1: ("Neutral", "Slightly stimulative", "Stimulative", "Very stimulative")}
N_LABELS = {1: ("Near target", "Slightly above target", "Above target", "Well above target"),
            -1: ("Near target", "Slightly below target", "Below target", "Well below target")}
INSIDE = "Inside target range"
READ_MIXED = "Mixed signals"
READ_ROBUST = "{} pressure, robust to the neutral range"
READ_SENSITIVE = "{} pressure, sensitive to the neutral range"
READ_ZERO = "No pressure at the neutral midpoint"
DIRECTION = {1: "Tightening", -1: "Easing"}
EPS = 1e-9                                         # float noise of the unrounded values counts as zero (sign only)
SHORT = {"USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ", "CAD": "BoC", "AUD": "RBA", "NZD": "RBNZ", "CHF": "SNB"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def no_forecast_reason(short: str) -> str:
    return f"the latest {short} forecast does not cover the period containing today + 12 months"


def period_label(period, transform: str = "") -> str:
    """THE period label (one function, used everywhere): from the published period and its transform."""
    p = "" if period is None else str(period)
    t = transform or ""
    if len(p) == 7 and p[4] == "-" and p[:4].isdigit() and p[5:].isdigit():                      # a month: "Sep 2026"
        return f"{MONTHS[int(p[5:]) - 1]} {p[:4]}"
    if len(p) == 10 and p[4] == "-" and p[7] == "-" and p.replace("-", "").isdigit():             # a day: "16 Sep 2026"
        return f"{int(p[8:])} {MONTHS[int(p[5:7]) - 1]} {p[:4]}"
    if t == "q4_q4":
        return f"{p} (Q4/Q4)"
    if t == "annual_average":
        return f"{p} (annual average)"
    if t == "yoy_quarter":
        return f"{p} (y/y)"
    if t == "fiscal_year" and p.startswith("FY") and p[2:].isdigit():
        y = int(p[2:])
        return f"{p} (Apr {y} to Mar {y + 1})"
    if t == "year_average_march" and p.startswith("M") and p[1:].isdigit():
        return f"year to Mar {p[1:]}"
    if t == "quarter_level":
        return p
    return p


def sgn(x: float) -> int:
    return 0 if abs(x) <= EPS else (1 if x > 0 else -1)


def label(x: Optional[float], kind: str) -> Optional[str]:
    """The label of P ('P') or N ('N') on the shared scale, from the unrounded value."""
    if x is None:
        return None
    s = 1 if x >= 0 else -1
    i = next((k for k, t in enumerate(SCALE) if abs(x) <= t + 1e-12), 3)
    return (P_LABELS if kind == "P" else N_LABELS)[s][i]


def _ends_test(ends: list, mids: list) -> tuple:
    """(code, text) of the robust / sensitive / zero part of the rule. ends: every end; mids: the mid values in order of priority."""
    if all(sgn(e) > 0 for e in ends):
        return "robust_tightening", READ_ROBUST.format(DIRECTION[1])
    if all(sgn(e) < 0 for e in ends):
        return "robust_easing", READ_ROBUST.format(DIRECTION[-1])
    d = next((sgn(m) for m in mids if sgn(m) != 0), 0)
    if d == 0:
        return "no_pressure", READ_ZERO
    return ("sensitive_tightening" if d > 0 else "sensitive_easing"), READ_SENSITIVE.format(DIRECTION[d])


def reading(today: Optional[tuple], forecast: Optional[tuple]) -> tuple:
    """(code, text). today / forecast: (T at neutral_mid, T at neutral_lo, T at neutral_hi) or None when the scenario is n/a."""
    if today is None and forecast is None:
        return "na", None
    if today is None or forecast is None:
        one = today or forecast
        return _ends_test([one[1], one[2]], [one[0]])
    a, b = today[0], forecast[0]
    if sgn(a) * sgn(b) < 0:
        return "mixed", READ_MIXED
    return _ends_test([today[1], today[2], forecast[1], forecast[2]], [a, b])


# ---------------------------------------------------------------- inputs shown next to the values --

def display_inputs(inp) -> dict:
    """What v6 displays from a stance.BankInputs besides the v3 result (periods, transforms, sources, the forecast publication and its
    rate conditioning). Pure extraction; no value is changed."""
    def val(v, **extra):
        if v is None:
            return None
        return {"v": v.v, "period": v.asof, "source": v.source, "note": v.note, "lo": v.lo, "hi": v.hi, **extra}
    disp = dict(getattr(inp, "display", None) or {})
    return {
        "bank": inp.currency, "short": SHORT.get(inp.currency, inp.currency), "slack_type": inp.slack_type,
        "core": val(inp.pi_under), "pi_fc": val(inp.pi_12m, transform=inp.pi_12m_transform, basis=inp.pi_12m_basis),
        "y_today": val(inp.y_today, transform=disp.get("y_today_transform", "")), "y_12m": val(inp.y_12m, transform=disp.get("y_12m_transform", "")),
        "u": val(inp.u), "u_star": val(inp.u_star), "u_12m": val(inp.u_12m),
        "conditioning": inp.conditioning or "unpublished", "publication": disp.get("publication"), "published": disp.get("published"),
        "cutoff": disp.get("cutoff"), "cutoff_flag": disp.get("cutoff_flag"), "assumed_rate": disp.get("assumed_rate"),
        "newer_forecast": any(str(n).startswith("a newer projection publication") for n in inp.notes or []),
    }


# ---------------------------------------------------------------- evaluate --

def _path_upto(lens: dict, pi_fc: Optional[dict]) -> list:
    """The bank's own published path (lens.path) up to the period of pi_fc (the lens already stops at the period of as-of + 12 months)."""
    from .stance import period_bounds
    pts = [(str(k), v) for k, v in lens.get("path") or []]
    if not pi_fc or not pts:
        return pts
    end = (period_bounds(pi_fc["period"], pi_fc.get("transform", "")) or (None, None))[1]
    if end is None:
        return pts
    out = []
    for k, v in pts:
        b = period_bounds(k, "")
        if b is None or b[0] <= end:
            out.append((k, v))
    return out


def evaluate(r: dict, d: dict) -> dict:
    """The v6 block of one bank: r = the v3 result of stance.compute (G, G_12m, gap_12m, lens ...), d = display_inputs(BankInputs).
    Values are NOT rounded (the payload rounds); `na` texts: 'neutral' stands for the bank's neutral reason (cb_render.neutral_reason)."""
    B = d["short"]
    Nn = r.get("N")
    star, band, rate = r.get("target_star"), r.get("band"), r.get("rate")
    fc = d.get("pi_fc") if r.get("pi_12m") is not None else None
    fc_na = no_forecast_reason(B)
    flags = set(r.get("flags") or [])

    # Position
    pos = {"P": None, "P_lo": None, "P_hi": None, "label": None, "na": None}
    if Nn is None or rate is None:
        pos["na"] = "neutral"
    elif fc is None:
        pos["na"] = fc_na
    elif star is None:
        pos["na"] = "a target band without a declared midpoint"
    else:
        pi = r["pi_12m"]
        rr = rate - pi
        rn, rn_lo, rn_hi = Nn["mid"] - star, Nn["lo"] - star, Nn["hi"] - star
        P = rr - rn
        pos.update(P=P, P_lo=rr - rn_hi, P_hi=rr - rn_lo, label=label(P, "P"), real_rate=rr, real_neutral={"mid": rn, "lo": rn_lo, "hi": rn_hi},
                   rate=rate, pi=pi, neutral={"mid": Nn["mid"], "lo": Nn["lo"], "hi": Nn["hi"]}, target=star)

    # Need = v3 gap_12m
    N = r.get("gap_12m")
    inside = band is not None and star is None and N is not None and N == 0
    need = {"N": N, "label": INSIDE if inside else label(N, "N"), "na": None if N is not None else fc_na}

    # Taylor
    def scenario(G, lo, hi, pi, pi_period, pi_transform, y, y_how, y_period, y_transform, held):
        return {"T": G, "lo": lo, "hi": hi, "na": None,
                "inputs": {"neutral": None if Nn is None else {"mid": Nn["mid"], "lo": Nn["lo"], "hi": Nn["hi"]}, "rate": rate, "pi": pi,
                           "pi_period": pi_period, "pi_transform": pi_transform, "target": star, "band": list(band) if band else None,
                           "y": y, "y_how": y_how, "y_period": y_period, "y_transform": y_transform, "held": held}}
    core = d.get("core") or {}
    yt = d.get("y_today") or {}
    if Nn is None or rate is None:
        today = {"T": None, "na": "neutral"}
    elif r.get("G") is None:
        today = {"T": None, "na": "no core inflation observation"}
    else:
        u = d.get("u") or {}
        y_period = yt.get("period") if d["slack_type"] == "output_gap" else u.get("period")
        today = scenario(r["G"], r["G_lo"], r["G_hi"], r.get("pi_under"), core.get("period"), "", r.get("y"), r.get("y_how"), y_period,
                         yt.get("transform", "") if d["slack_type"] == "output_gap" else "", False)
    if Nn is None or rate is None:
        fcs = {"T": None, "na": "neutral"}
    elif r.get("G_12m") is None:
        fcs = {"T": None, "na": fc_na}
    else:
        held = "slack_held" in flags
        if held:
            y12_period, y12_tr = today.get("inputs", {}).get("y_period"), today.get("inputs", {}).get("y_transform", "")
        elif d["slack_type"] == "output_gap":
            y12_period, y12_tr = (d.get("y_12m") or {}).get("period"), (d.get("y_12m") or {}).get("transform", "")
        else:
            y12_period, y12_tr = (d.get("u_12m") or {}).get("period"), ""
        fcs = scenario(r["G_12m"], r["G_12m_lo"], r["G_12m_hi"], r.get("pi_12m"), r.get("pi_12m_period"), (d.get("pi_fc") or {}).get("transform", ""),
                       r.get("y_12m"), r.get("y_12m_how"), y12_period, y12_tr, held)
    t_ = None if today["T"] is None else (today["T"], today["lo"], today["hi"])
    f_ = None if fcs["T"] is None else (fcs["T"], fcs["lo"], fcs["hi"])
    code, text = reading(t_, f_)
    missing = None
    if (t_ is None) != (f_ is None):
        missing = {"scenario": "today" if t_ is None else "forecast", "reason": (today if t_ is None else fcs)["na"]}
    taylor = {"today": today, "forecast": fcs, "reading_code": code, "reading": text, "na": "neutral" if (Nn is None or rate is None) else
              (None if code != "na" else today["na"]), "missing": missing,
              "flags": [f for f in ("no_slack", "slack_held", "headline_basis") if f in flags]}

    # Bank path (own published path up to the period of pi_fc), else the rate conditioning of the forecast
    lens = r.get("lens") or {}
    cond = d.get("conditioning") or "unpublished"
    bank = {"conditioning": cond, "publication": d.get("publication"), "published": d.get("published")}
    if cond == "own_path" and lens.get("path"):
        bank.update(kind=lens.get("path_kind"), path=_path_upto(lens, fc))
    elif cond == "market":
        bank.update(kind="market", cutoff=d.get("cutoff"), cutoff_flag=d.get("cutoff_flag"))
    elif cond == "constant_rate":
        bank.update(kind="constant_rate", assumed_rate=d.get("assumed_rate"))
    else:
        bank.update(kind="unpublished")

    return {"bank": d["bank"], "short": B, "forecast": None if fc is None else {**fc, "period_label": period_label(fc["period"], fc.get("transform", ""))},
            "position": pos, "need": need, "taylor": taylor, "bank_path": bank, "headline_basis": "headline_basis" in flags,
            "y_today_period": period_label(yt.get("period"), yt.get("transform", "")) if yt else None,
            "newer_forecast": bool(d.get("newer_forecast"))}
