"""Stance UI (v5) - the parts of the bank zone that are not computed by stance_v5.py: the mandate card, the next test, the history,
the "How to read it" text. Pure, deterministic, no AI. ARCHITECTURE section 23d.

Every text is a fixed template filled with payload values (B.4 rounding: pp 2 decimals with a sign, bp signed integers, inflation as
published, probabilities with one decimal). A sentence whose value is n/a is omitted (None). DISPLAY-ONLY: never imported by /economic.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

MINUS = "−"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

HOW_TO_READ = [
    "Position: the policy rate minus the bank's own estimate of the neutral rate (pp). Above zero policy is restrictive, below zero stimulative.",
    "Need: the bank's preferred core inflation minus its target (pp). For a target range: 0 inside the range, otherwise the distance to the nearest edge.",
    "Both bars use the same scale, \u00b12 pp, capped at the ends.",
    "Why: the likely next move. Where the bank publishes its own rate path (Fed dots, RBNZ OCR track) it is read from that path; "
    "otherwise from the bank's own inflation forecast and the rate path that forecast assumes.",
    "Borderline: the conclusion would change inside the bank's published neutral range or within 0.05 pp of a threshold.",
    "A reading of each bank's own numbers, not a forecast. Market pricing already reflects these data.",
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
    """Position (S) and Need (gap_under) over time from data/cb/stance_history (pp, as stored; the UI rounds)."""
    out = []
    for r in sorted(rows, key=lambda r: r["snapshot_date"]):
        p = r.get("v5_position") if r.get("v5_position") is not None else r.get("S")
        n = r.get("v5_need") if r.get("v5_need") is not None else r.get("gap_under")
        out.append({"date": r["snapshot_date"].isoformat(), "position": None if p is None else round(p, 6), "need": None if n is None else round(n, 6)})
    return out
