"""Stance S2 - the UI payload of the stance (pure, deterministic, no AI): the summary sentences, the cycle and market lines, the
mandate card, the next test, the history. ARCHITECTURE section 23d.

Every text is a fixed template filled with payload values (B.4 rounding: pp 2 decimals with a sign, bp signed integers, inflation as
published, probabilities with one decimal). A sentence whose value is n/a is omitted (None). DISPLAY-ONLY: never imported by /economic.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

MINUS = "−"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

S_TEXT = {"S0": "roughly in balance", "S1": "behind the curve, more {moves} needed", "S2": "room to move {way} toward neutral",
          "S3": "at or past the required level, hold"}
CHIPS = {("S0", 0): "S0 balanced", ("S1", 1): "S1 hike", ("S1", -1): "S1 cut", ("S2", 1): "S2 up", ("S2", -1): "S2 down",
         ("S3", 1): "S3 hold", ("S3", -1): "S3 hold", ("S3", 0): "S3 hold"}
FLAG_TIPS = {
    "boundary": ("near a threshold", "The situation is within 0.05 pp of a threshold of the rule."),
    "neutral_sensitive": ("neutral-sensitive", "Depends on where neutral is: the situation differs at the two ends of the neutral interval."),
    "headline_basis": ("headline basis", "The bank does not project its underlying measure for this period: the headline projection is used."),
    "estimate": ("estimate", "An input is an estimate (market side or publication cutoff)."),
    "stale": ("stale", "An input is older than its freshness threshold."),
    "pending": ("pending", "An input the rule needs has not been entered yet (see the reason)."),
    "slack_held": ("slack held", "The bank publishes no slack projection for the 12-month period: today's slack is held."),
    "no_slack": ("no slack estimate", "The bank publishes no estimate of spare capacity: the slack term is left out."),
    "need_only": ("need only", "No neutral-rate estimate: only the need is shown, no stance, pressure or situation."),
}
FLAG_ORDER = ("boundary", "neutral_sensitive", "headline_basis", "estimate", "stale", "pending", "slack_held", "no_slack", "need_only")


HOW_TO_READ = [
    "Need: how far the bank's preferred underlying inflation is from target, plus one third of the bank's own estimate of spare capacity (pp).",
    "Stance: policy rate minus the bank's own neutral-rate estimate (pp). Above zero is restrictive, below zero is stimulative.",
    "Pressure: 1.5 \u00d7 need \u2212 stance (bp). Positive points up, negative points down.",
    "S0 \u00b7 Balanced: little need and the rate is close to where the rule puts it. Hold.",
    "S1 \u00b7 Behind the curve: clear need and the rate has not caught up. Keep moving in the same direction.",
    "S2 \u00b7 Room to normalise: little need, but the rate is away from neutral. Moves toward neutral only if the data allow.",
    "S3 \u00b7 At or past the level: clear need, but the rate already matches or exceeds what the rule asks. Hold; reverse when the need fades.",
    "If held, in 12 months: the same rule on the bank's own projections, with the rate unchanged.",
    "This is a mandate benchmark, not a forecast. Market pricing already reflects these data; read it together with the cycle, the market and the bank's projections.",
]


# ---------------------------------------------------------------- formatting (B.4) --

def pp(v: Optional[float]) -> Optional[str]:
    """pp with 2 decimals and a sign ("+0.68", "−0.55", "0.00")."""
    if v is None:
        return None
    s = f"{abs(v):.2f}"
    if float(s) == 0:
        return "0.00"
    return ("+" if v > 0 else MINUS) + s


def bp(v: Optional[float]) -> Optional[str]:
    """bp as a signed integer ("+70", "−3", "0"); half away from zero."""
    if v is None:
        return None
    n = int(abs(v) + 0.5)
    return "0" if n == 0 else ("+" if v > 0 else MINUS) + str(n)


def bp_abs(v: float) -> str:
    return str(int(abs(v) + 0.5))


def pct1(p: float) -> str:
    return f"{p * 100:.1f}%"


def day(iso: str) -> str:
    y, m, d = (int(x) for x in str(iso)[:10].split("-"))
    return f"{d} {MONTHS[m - 1]}"


def chip(situation: Optional[str], direction: Optional[int]) -> Optional[str]:
    if situation is None:
        return None
    return CHIPS.get((situation, direction if situation != "S0" else 0), situation)


# ---------------------------------------------------------------- market --

def next_outcome(point: Optional[dict]) -> Optional[tuple]:
    """(word, probability) of the most likely outcome at the next meeting: n or n+1 moves of the priced step, whichever is likelier;
    None for an estimate (no probability)."""
    if not point or not point.get("prob"):
        return None
    pr = point["prob"]
    k, p = (pr["n"] + 1, pr["p"]) if pr["p"] > 0.5 else (pr["n"], 1 - pr["p"])
    word = "no change" if k == 0 else ("a hike" if pr["dir"] == "hike" else "a cut")
    return word, p


def first_point(path: dict) -> Optional[dict]:
    nxt = (path or {}).get("next") or {}
    return next((p for p in (path or {}).get("points") or [] if p.get("meeting") == nxt.get("decision")), None)


def market_sentence(path: Optional[dict]) -> Optional[str]:
    o = next_outcome(first_point(path or {}))
    m12 = ((path or {}).get("m12") or {}).get("cum_bp")
    if o is None or m12 is None:
        return None
    return f"The market prices {o[0]} at the next meeting ({pct1(o[1])}) and {bp(m12)} bp over 12 months."


def market_line(path: Optional[dict]) -> Optional[str]:
    """"Market: 9 Dec 61.2% hike · 12M +85 bp" (an estimate: "≈ +45 bp" instead of the probability)."""
    pt, m12 = first_point(path or {}), ((path or {}).get("m12") or {})
    if pt is None:
        return None
    o = next_outcome(pt)
    if o is not None:
        nxt = "hold" if o[0] == "no change" else o[0][2:]
        head = f"{day(pt['meeting'])} {pct1(o[1])} {nxt}"
    elif pt.get("cum_bp") is not None:
        head = f"{day(pt['meeting'])} ≈ {bp(pt['cum_bp'])} bp"
    else:
        head = f"{day(pt['meeting'])} n/a"
    tail = f" · 12M {'≈ ' if m12.get('est') else ''}{bp(m12['cum_bp'])} bp" if m12.get("cum_bp") is not None else ""
    return f"Market: {head}{tail}"


# ---------------------------------------------------------------- bank --

def bank_sentence(r: dict, fc: Optional[dict]) -> Optional[str]:
    lens = r.get("lens") or {}
    kind = lens.get("kind")
    if kind == "own_path":
        g = lens.get("gap_bp")
        if g is None or lens.get("year") is None:
            return None
        return f"The market is {bp_abs(g)} bp {'above' if g >= 0 else 'below'} the bank's own path for {lens['year']}."
    if kind == "market":
        d = lens.get("delta_bp")
        lp = (r.get("inputs") or {}).get("last_projection") or {}
        if d is None or not fc or not fc.get("publication") or lp.get("v") is None or lens.get("consistent") is None:
            return None
        measure = str(lp.get("source", "")).split(": ", 1)[-1]
        rel = "consistent with" if lens["consistent"] else ("above" if lens["horizon_gap"] > 0 else "below")
        return (f"Since the {fc['publication']} cutoff the market has moved {bp(d)} bp; the bank sees {measure} at {lp['v']:g}% "
                f"at the horizon ({rel} target).")
    if kind == "constant_rate":
        d = lens.get("delta_bp")
        if d is None:
            return None
        return f"The market prices {bp(d)} bp more than the constant rate assumed in the bank's forecast."
    if kind == "unpublished":
        return "The bank does not publish a rate path."
    return None


# ---------------------------------------------------------------- rule --

def s_text(situation: str, direction: Optional[int]) -> str:
    t = S_TEXT[situation]
    if situation == "S1":
        return t.format(moves="hikes" if direction == 1 else "cuts")
    if situation == "S2":
        return t.format(way="up" if direction == 1 else "down")
    return t


def rule_sentence(r: dict) -> Optional[str]:
    if r.get("situation") is None or r.get("situation_12m") is None:
        return None
    head = " (on headline inflation)" if r.get("pi_12m_basis") == "headline" else ""
    return (f"On today's data: {s_text(r['situation'], r.get('direction'))}. "
            f"If the rate is held: {s_text(r['situation_12m'], r.get('direction_12m'))} in 12 months{head}.")


def summary(r: dict, path: Optional[dict], fc: Optional[dict]) -> list:
    """The three sentences in the order Market -> Bank -> Rule; an n/a one is left out."""
    return [s for s in (market_sentence(path), bank_sentence(r, fc), rule_sentence(r)) if s]


# ---------------------------------------------------------------- cycle --

def _mon(iso: str) -> tuple:
    y, m = int(iso[:4]), int(iso[5:7])
    return y, MONTHS[m - 1]


def _months(moves: list) -> str:
    """'Jun, Sep 2026' / 'Dec 2025, Jun, Sep 2026' (up to 3 moves); 'Mar 2024–Sep 2026' beyond."""
    if len(moves) > 3:
        a, b = _mon(moves[0]["date"]), _mon(moves[-1]["date"])
        return f"{a[1]}–{b[1]} {b[0]}" if a[0] == b[0] else f"{a[1]} {a[0]}–{b[1]} {b[0]}"
    groups: list = []
    for m in moves:
        y, mo = _mon(m["date"])
        if groups and groups[-1][0] == y:
            groups[-1][1].append(mo)
        else:
            groups.append((y, [mo]))
    return ", ".join(", ".join(ms) + f" {y}" for y, ms in groups)


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def cycle_line(c: Optional[dict]) -> Optional[str]:
    """'Cycle: hiking · 2 hikes (Jun, Sep 2026) after 8 cuts' (+ ' · 5 holds since' when the bank has held since)."""
    if not c or not c.get("direction"):
        return None
    word = "hike" if c["direction"] == "hike" else "cut"
    out = f"Cycle: {'hiking' if word == 'hike' else 'cutting'} · {_n(c['count'], word)} ({_months(c['moves'])})"
    p = c.get("previous")
    if p:
        out += f" after {_n(p['count'], 'hike' if p['direction'] == 'hike' else 'cut')}"
    if c.get("holds_since"):
        out += f" · {_n(c['holds_since'], 'hold')} since"
    return out


# ---------------------------------------------------------------- mandate, next test, history --

def mandate_card(v: dict, series_names: dict, underlying_rule: list) -> dict:
    """The mandate as entered (data/cb/manual/mandates.yaml, the version in force at the as-of)."""
    t = v["target"]
    if t["kind"] == "point":
        tgt = f"{t['value']:g}%"
    else:
        mp = (t.get("midpoint") or {}).get("value") if isinstance(t.get("midpoint"), dict) else t.get("midpoint")
        tgt = f"{t['low']:g}\u2013{t['high']:g}%" + (f" (midpoint {mp:g}%)" if mp is not None else " (no declared midpoint)")
    u = v.get("underlying") or {}
    ra = u.get("rule_applied")
    src = (v.get("sources") or [{}])[0]
    prim = u.get("primary")
    prim = prim if isinstance(prim, list) else [prim]
    return {"target": tgt, "target_measure": t.get("measure"), "underlying": " and ".join(str(series_names.get(x, x)) for x in prim)
            + (" (mean of the two)" if len(prim) > 1 else ""), "underlying_id": ", ".join(str(x) for x in prim), "rule_applied": ra,
            "rule_text": underlying_rule[ra - 1] if isinstance(ra, int) and 1 <= ra <= len(underlying_rule) else None,
            "underlying_note": u.get("note"), "horizon": (v.get("horizon") or {}).get("text"), "mandate_type": (v.get("mandate") or {}).get("type"),
            "effective_from": str(v["effective_from"]), "effective_to": str(v["effective_to"]) if v.get("effective_to") else None,
            "last_verified": str((v.get("last_verified") or {}).get("date") or ""), "source_title": src.get("title"), "source_url": src.get("url")}


def next_test(cur: str, events: list, mapping: dict, asof: date) -> Optional[dict]:
    """The next release of the bank's underlying measure from the ForexFactory calendar already collected (read only). None when the
    bank has no mapping (hidden); {date: None, na} when the release is not in the calendar yet."""
    m = (mapping or {}).get(cur)
    if not m:
        return None
    titles = set(m["ff_titles"])
    nxt = sorted(e["datetime_utc"] for e in events if e["currency"] == cur and e["name_raw"] in titles and e["datetime_utc"].date() >= asof)
    if not nxt:
        return {"date": None, "title": m["ff_titles"][0], "schedule": "ForexFactory",
                "na": "not in the ForexFactory calendar yet (it lists the current week)"}
    return {"date": nxt[0].date().isoformat(), "utc": nxt[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "title": m["ff_titles"][0],
            "schedule": "ForexFactory", "na": None}


def history_points(rows: list) -> list:
    """Pressure over time from data/cb/stance_history (bp, as stored; the UI rounds)."""
    out = []
    for r in sorted(rows, key=lambda r: r["snapshot_date"]):
        f = lambda k: None if r.get(k) is None else round(r[k] * 100, 4)          # noqa: E731
        out.append({"date": r["snapshot_date"].isoformat(), "G_bp": f("G"), "G_lo_bp": f("G_lo"), "G_hi_bp": f("G_hi"),
                    "situation": r.get("situation"), "direction": r.get("direction")})
    return out
