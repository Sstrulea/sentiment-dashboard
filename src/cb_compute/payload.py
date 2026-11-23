"""Phase 3a payloads (pure): the dictionaries behind /central-banks - overview, one per bank, one for the pairs. Every value is
computed by `analysis` / `engine`; this module only shapes it for the browser (JSON-ready, rounded, deterministic: nothing here
reads the clock or a file).

A metric that can be n/a is always `{"v": number | null, "flag": ..., "stale": bool, "na": reason | null, ...}`: the page shows
"—" with the reason as a tooltip when `v` is null. Signs: positive = hawkish (higher rates than the reference).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..cb_calendar import compute_blackout
from ..cb_docs.expect import EXPECTED_LAG, GRACE_DAYS, TYPE_LABEL, VIDEO_NA
from .analysis import BankReport, PairRow, bank_report, pair_row, primary_calendar, primary_history_start
from .engine import NOT_COVERED, Context, Point, STRENGTH, YEAR_ENDS, YearEnd, step_reason, trajectory, upcoming_meetings, weakest
from .methods import step_probabilities

ORDER = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
SHORT = {"USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ", "CAD": "BoC", "AUD": "RBA", "NZD": "RBNZ", "CHF": "SNB"}
WINDOW_KEYS = {"1s": "1w", "1l": "1m"}                         # 5 / 21 business days
BASE_URL = "/central-banks"
CHART_MONTHS_BACK = 13

FLAG_LABEL = {"EXACT": "EXACT", "CURVE": "CURVE", "ESTIMATE": "ESTIMATE", "UPPER_BOUND": "UPPER BOUND", "PROXY": "PROXY", "DECIDED": "DECIDED"}
FLAG_HELP = {
    "EXACT": "Solved exactly from 1M month-average futures: the rate changes only on the effective date, so the implied rate after the meeting follows from the monthly average.",
    "CURVE": "OIS curve: USD, EUR, GBP, JPY and CHF use the Eurex Clearing settlement curves (discount factors), with the overnight rate fitted as "
             "constant between effective dates (one rate per interval); elsewhere an OIS forward curve averaged over the interval. Minus the "
             "overnight-to-policy spread.",
    "ESTIMATE": "No contract isolates this meeting: 1M/3M futures averages fitted with the rate changing only on effective dates and the smallest possible step changes. NZD also uses an estimated BKBM-OCR spread.",
    "UPPER_BOUND": "3M contract window: the average covers several meetings, so the cumulative move is an upper bound for this meeting alone.",
    "PROXY": "Government-curve proxy - not policy-equivalent. Shown as a raw sovereign level unless a basis can be measured from a short tenor.",
    "DECIDED": "The meeting has already been decided: it is part of the base rate.",
}
LEVEL_HELP = {"policy": "implied policy rate", "bkbm": "BKBM base: level of the ASX 90-day bank bill, not the OCR - no BKBM-OCR spread exists, so no bp vs the OCR",
              "sovereign_proxy": "sovereign proxy: raw government-curve level, not policy-equivalent"}
LEVEL_LABEL = {"policy": "policy-equivalent", "bkbm": "BKBM base", "sovereign_proxy": "sovereign proxy"}

NZD_SENSITIVITY = "If the actual BKBM-OCR spread is wider than assumed, every NZD point is lower by about the same number of bp."

METHODOLOGY = [
    {"title": "Base rate", "text": "The last rate DECIDED on or before the as-of date, including a decision that has been announced but is not in force yet "
                                   "(shown as 'from <date>'). The Fed is the midpoint of the target range."},
    {"title": "Implied policy rate", "text": "Market instruments give the overnight benchmark path; the benchmark-to-policy spread (median of the last 20 business days, "
                                              "excluding +-2 business days around rate changes and month-ends) is removed to get a policy-equivalent rate."},
    {"title": "Methods", "text": "EXACT: 1M month-average futures chain. CURVE: OIS curve - for USD (fed funds), EUR (EUR STR), GBP (SONIA), JPY (TONA) and CHF "
                                  "(SARON) the daily Eurex Clearing settlement curves: the overnight rate is taken as constant between effective dates and fitted "
                                  "to the published discount factors (least squares, one rate per interval, a small smoothing term only where pillars cannot "
                                  "separate two intervals), then the overnight-to-policy spread is removed (EFFR vs the Fed midpoint, EUR STR vs the deposit rate, "
                                  "SONIA vs Bank Rate, the call rate vs the BoJ guideline, SARON vs the SNB rate); a meeting whose effective date is beyond the last "
                                  "pillar is n/a. Otherwise an OIS forward curve averaged between effective dates. UPPER BOUND: 3M contracts "
                                  "whose window spans several meetings. PROXY: government curves / bills, with a basis measured only from tenors observed before "
                                  "the first effective date - without one ('proxy without short end') no bp, step or probability is computed and only the raw level is shown."},
    {"title": "Estimates (ESTIMATE)", "text": "Where no contract isolates a meeting (CAD beyond the 1M CORRA chain, NZD bank bills), every 1M / 3M futures contract of "
                                              "the exchange is fitted at once: the overnight rate is constant between effective dates, each contract's average over its window "
                                              "equals 100 minus its price (days already past use the realised fixings), and among the paths that fit, the one with the smallest "
                                              "step changes is chosen. The step of an estimated meeting is its level minus the level of the previous point; a meeting no contract "
                                              "reaches is n/a and its move is inside the next one. No probability is computed for an estimate. NZD: bank bills are a BKBM level; "
                                              "the BKBM-OCR spread is an estimate (median of 3M interbank minus OCR over months without an OCR change around them), not a daily observation. "
                                              + NZD_SENSITIVITY},
    {"title": "Step and probability", "text": "For EXACT and CURVE the implied step at the next meeting is split into whole 25 bp moves (n = floor(|step|/25)) and a "
                                              "fraction p of one more: P(n+1 moves) = p, P(n moves) = 1 - p, in the direction of the step."},
    {"title": "Cumulative bp", "text": "Implied policy rate after the last meeting of the year minus the base rate, in bp. Positive = hawkish."},
    {"title": "Repricing", "text": "Change of the implied level at a fixed horizon (last meeting of 2026 / 2027) over 5 business days (1w) and 21 business days (1m); "
                                   "independent of decisions taken in between. The change of the cumulative bp is a secondary field."},
    {"title": "GAP", "text": "Market minus bank, in bp: the Fed's median dot vs the implied rate at the last meeting of each year. Only where both are on the same basis."},
    {"title": "Surprises and reaction", "text": "Surprise vs consensus: decided rate minus consensus. Surprise vs market: decided step minus the step implied at T-1 (EXACT / CURVE / "
                                                "PROXY with a basis only). Reaction: change of the implied level between the close of T-1 and T at the next meeting and at the last meeting of the year."},
    {"title": "Pairs", "text": "Base minus quote, metric by metric: the current rate differential (carry), the implied differential at year end (both legs policy-equivalent) and "
                               "its repricing (both legs need a change of level). The flag is the weaker of the two legs."},
]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def num(x: Optional[float], nd: int = 3) -> Optional[float]:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), nd)


def iso(d) -> Optional[str]:
    return None if d is None else d.isoformat()


def metric(v: Optional[float] = None, *, flag: Optional[str] = None, stale: bool = False, na: Optional[str] = None, nd: int = 3, **extra) -> dict:
    """A value that may be n/a: `na` (the reason) is present exactly when `v` is null."""
    out = {"v": num(v, nd), "flag": flag if v is not None else (flag or None), "stale": bool(stale), "na": None if v is not None else (na or "n/a")}
    out.update(extra)
    return out


def slug(pair: str) -> str:
    return pair.lower()


def bank_href(ccy: str) -> str:
    return f"{BASE_URL}/{ccy.lower()}.html"


def pair_href(pair: str) -> str:
    return f"{BASE_URL}/pair/{slug(pair)}.html"


def decision_instant(ctx: Context, ccy: str, day: date) -> dict:
    """When the decision of `day` is announced: local time + zone, the UTC instant when the time is fixed."""
    bank = ctx.banks[ccy]
    dt_cfg = bank.get("decision_time") or {}
    tz = bank["tz"]
    out = {"tz": tz, "abbr": datetime.combine(day, time(12, 0), ZoneInfo(tz)).tzname(), "local": dt_cfg.get("local"),
           "tbd": bool(dt_cfg.get("variable")), "window_local": dt_cfg.get("window_local"), "unverified": dt_cfg.get("verified") is False, "utc": None}
    if dt_cfg.get("local") and not dt_cfg.get("variable"):
        h, m = dt_cfg["local"].split(":")
        out["utc"] = datetime.combine(day, time(int(h), int(m)), ZoneInfo(tz)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def blackout_of(ctx: Context, ccy: str, m) -> Optional[dict]:
    rule = ctx.banks[ccy].get("blackout_rule")
    if not rule:
        return None
    b = compute_blackout(rule, m.decision, m.first_day, ctx.calendars)
    if b is None:
        return None
    return {"start": b.start.isoformat(), "end": b.end.isoformat(), "start_utc": b.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_utc": b.end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "precision": b.precision, "verified": b.verified, "note": b.note}


def weakest_flag(*flags) -> Optional[str]:
    return weakest(*[f for f in flags if f])


# ---------------------------------------------------------------------------
# points, year ends, horizon
# ---------------------------------------------------------------------------

def point_json(p: Point) -> dict:
    e = p.extra or {}
    window = None if p.window is None else [iso(p.window[0]), iso(p.window[1])]
    return {
        "meeting": iso(p.meeting), "effective": iso(p.eff), "method": p.method, "flag": p.flag, "source": p.source, "source_asof": iso(p.source_asof),
        "lag_bd": p.lag_bd, "stale": p.stale, "rate": num(p.rate), "cum_bp": num(p.cum_bp, 2), "step_bp": num(p.step_bp, 2),
        "level": num(p.level), "level_kind": p.level_kind, "window": window, "upper_bound": p.method == "WINDOW" and p.level is not None,
        "interior": e.get("interior"), "pre_days": e.get("pre_days"), "gap_days": e.get("gap_days"),
        "na": (p.reason or None) if p.rate is None else None, "step_na": step_reason(p) if p.step_bp is None else None, "notes": list(p.notes or []),
    }


def year_metric(ye: YearEnd) -> dict:
    """End-of-year cumulative bp (or, without bp, the level)."""
    common = {"meeting": iso(ye.meeting), "window": None if ye.window is None else [iso(ye.window[0]), iso(ye.window[1])],
              "level": num(ye.level), "level_kind": ye.level_kind, "rate": num(ye.rate)}
    if ye.extra:
        common.update({"interior": ye.extra.get("interior"), "pre_days": ye.extra.get("pre_days")})
    if ye.cum_bp is None:
        return metric(None, flag=ye.flag, stale=ye.stale, na=ye.reason or "n/a", **common)
    return metric(ye.cum_bp, flag=ye.flag, stale=ye.stale, nd=2, **common)


def n_meetings_to(ctx: Context, ccy: str, asof: date, until: date) -> int:
    return sum(1 for m in upcoming_meetings(ctx, ccy, asof) if m.eff < until)


def horizon_json(ctx: Context, rep: BankReport) -> dict:
    tr, nm = rep.trajectory, rep.next
    if tr.na_reason or not tr.points:
        return {"kind": "na", "na": tr.na_reason or "no upcoming meeting", "flag": None, "stale": False}
    p = tr.points[0]
    base = {"meeting": iso(p.meeting), "method": p.method, "flag": p.flag, "stale": p.stale, "level": num(p.level), "level_kind": p.level_kind}
    if p.step_bp is not None and p.method in ("EXACT", "CURVE"):
        probs = nm.probabilities or {}
        return {**base, "kind": "step", "step_bp": num(p.step_bp, 2), "direction": probs.get("direction"),
                "probabilities": [{"moves": k, "p": num(v, 4)} for k, v in (probs.get("moves") or {}).items()], "na": None}
    if p.method == "WINDOW" and p.cum_bp is not None:
        e = p.extra
        return {**base, "kind": "window", "cum_bp": num(p.cum_bp, 2), "window": [iso(p.window[0]), iso(p.window[1])], "upper_bound": True,
                "interior": e.get("interior"), "pre_days": e.get("pre_days"),
                "n_meetings": n_meetings_to(ctx, rep.currency, rep.asof, p.window[1]), "na": None}
    na = p.reason or step_reason(p) or "n/a"
    sr = step_reason(p)
    if sr and sr != na:                                                    # e.g. the level is outside the curve AND the step needs a basis
        na += " | step and probability: " + sr
    return {**base, "kind": "na", "na": na}


# ---------------------------------------------------------------------------
# repricing, GAP, reaction
# ---------------------------------------------------------------------------

def repricing_json(rep: BankReport) -> dict:
    out = {}
    for key, name in WINDOW_KEYS.items():
        rp = rep.repricing.get(key)
        if rp is None:
            out[name] = {"bd": None, "prev": None, "years": {}, "step": metric(None, na="not computed")}
            continue
        allna = rp.reasons.get("all")
        years = {}
        for y in YEAR_ENDS:
            if y in rp.level:
                years[str(y)] = metric(rp.level[y], flag=rp.level_flag.get(y), nd=2, cum_bp=num(rp.cum.get(y), 2))
            else:
                years[str(y)] = metric(None, na=allna or rp.reasons.get(str(y)) or "n/a")
        step = (metric(rp.step_bp, flag=rp.step_flag, nd=2, meeting=iso(rp.step_meeting)) if rp.step_bp is not None
                else metric(None, na=allna or rp.reasons.get("step") or "n/a"))
        out[name] = {"bd": rp.bd, "prev": iso(rp.prev), "years": years, "step": step, "base_change_bp": num(rp.base_change_bp, 2)}
    return out


def gap_json(rep: BankReport) -> dict:
    g = rep.gap
    years = {}
    for gy in g.years:
        years[str(gy.year)] = metric(gy.gap_bp, flag=gy.market_flag, na=gy.note or g.reason or "n/a", nd=1, market=num(gy.market_rate), bank=num(gy.bank_median),
                                     n_dots=gy.n_dots, window=None if not gy.market_window else [iso(gy.market_window[0]), iso(gy.market_window[1])],
                                     period=gy.note if g.kind != "dots" else None)
    first = next((gy for gy in g.years if gy.gap_bp is not None), None)
    return {"kind": g.kind, "na": g.reason or None, "sep": iso(g.sep), "years": years,
            "headline": metric(first.gap_bp, flag=first.market_flag, nd=1) if first else metric(None, na=g.reason or "n/a")}


def reaction_json(rep: BankReport) -> dict:
    row = next((s for s in reversed(rep.surprises) if s.decided), None)
    if row is None:
        na = "no decision on record"
        return {"date": None, "next": metric(None, na=na), "year": metric(None, na=na)}
    out = {"date": iso(row.meeting)}
    for key in ("next", "year"):
        v = getattr(row, f"reaction_{key}_bp")
        out[key] = (metric(v, flag=getattr(row, f"reaction_{key}_flag"), nd=1, target=iso(getattr(row, f"reaction_{key}_target"))) if v is not None
                    else metric(None, na=row.reaction_reason.get(key) or "n/a"))
    return out


# ---------------------------------------------------------------------------
# decisions, calendar, rate
# ---------------------------------------------------------------------------

def decision_rows(ctx: Context, rep: BankReport, limit: int = 4) -> list:
    cur = rep.currency
    by_date = {d["meeting_date"]: d for d in ctx.decisions.get(cur, [])}
    rows = []
    for s in reversed([x for x in rep.surprises if x.decided][-limit:]):
        d = by_date.get(s.meeting, {})
        vs_market = (metric(s.vs_market_bp, flag=s.vs_market_flag, nd=1, implied_step_bp=num(s.implied_step_bp, 2)) if s.vs_market_bp is not None
                     else metric(None, na=s.vs_market_reason or "n/a"))
        react = {}
        for key in ("next", "year"):
            v = getattr(s, f"reaction_{key}_bp")
            react[key] = (metric(v, flag=getattr(s, f"reaction_{key}_flag"), nd=1, target=iso(getattr(s, f"reaction_{key}_target"))) if v is not None
                          else metric(None, na=s.reaction_reason.get(key) or "n/a"))
        mine = {x["type"]: x for x in ctx.documents if x["currency"] == cur and x["meeting_date"] == s.meeting}
        stm = doc_link(mine["statement"]) if "statement" in mine else None
        conf = {t: doc_link(mine[t]) for t in ("presser_video", "presser_transcript", "opening_statement") if t in mine} or None
        rows.append({
            "date": iso(s.meeting), "effective": iso(d.get("effective_date")), "delta_bp": num(s.delta_bp, 1), "rate_before": num(d.get("rate_before")),
            "rate_after": num(d.get("rate_after")), "lower": num(d.get("lower")), "upper": num(d.get("upper")), "consensus": num(d.get("consensus")),
            "surprise_consensus_bp": num(s.vs_consensus_bp, 1), "status": d.get("status"), "vs_market": vs_market, "reaction": react,
            "slots": {"votes": votes_json(ctx, cur, s.meeting), "statement": stm, "conference": conf},
            "summary": summary_slot(ctx, mine.get("statement")),
        })
    return rows


def rate_json(ctx: Context, rep: BankReport, asof: date) -> dict:
    decs = ctx.decisions.get(rep.currency, [])
    in_force = [d for d in decs if d["effective_date"] <= asof]
    cur = in_force[-1] if in_force else None
    b = rep.trajectory.base
    pending = bool(b and b.pending)
    return {
        "value": num(b.rate) if b else None, "in_force": num(cur["rate_after"]) if cur else None, "lower": num(cur["lower"]) if cur else None,
        "upper": num(cur["upper"]) if cur else None, "pending": pending, "from": iso(b.eff) if pending else None, "decided": iso(b.meeting) if b else None,
        "status": cur["status"] if cur else None, "range": bool(cur and cur.get("lower") is not None),
        "decision_status": next((d["status"] for d in reversed(decs) if d["meeting_date"] <= asof), None),
    }


def next_json(ctx: Context, rep: BankReport, asof: date) -> dict:
    cur = rep.currency
    upcoming = upcoming_meetings(ctx, cur, asof)
    if not upcoming:
        return {"decision": None, "na": "no upcoming meeting on record"}
    m = upcoming[0]
    bank = ctx.banks[cur]
    conf = bank.get("conference") or {}
    proj = bank.get("projections") or {}
    return {"decision": iso(m.decision), "effective": iso(m.eff), "first_day": iso(m.first_day), "time": decision_instant(ctx, cur, m.decision),
            "has_projections": m.has_projections, "projections_name": proj.get("name"), "has_presser": m.has_presser,
            "conference": {"local": conf.get("local"), "held": conf.get("held"), "verified": conf.get("verified")},
            "blackout": blackout_of(ctx, cur, m), "source": m.source, "date_verified": m.verified, "na": None}


# ---------------------------------------------------------------------------
# official texts (phase 2a)
# ---------------------------------------------------------------------------

FOLLOW_UP = ("minutes", "account", "summary_of_opinions", "deliberations")
SPEECH_WINDOW_DAYS = 60


def vote_label(v: dict) -> str:
    k = v["kind"]
    if k == "counted":
        return f"{v['n_for']}\u2013{v['n_against']}"
    if k == "unanimous":
        return f"{v['n_for']}\u20130" if v["n_for"] else "unanimous"
    return {"not_published": "not published", "consensus": "consensus"}.get(k, k)


def votes_json(ctx: Context, ccy: str, meeting: date) -> Optional[dict]:
    v = ctx.votes.get((ccy, meeting))
    if v is None:
        return None
    import json as _json
    return {"kind": v["kind"], "label": vote_label(v), "n_for": v["n_for"], "n_against": v["n_against"], "for": _json.loads(v["for_names"] or "[]"),
            "against": _json.loads(v["against"] or "[]"), "source": v["source"], "evidence": v["evidence"], "notes": v["notes"] or None, "doc_id": v["source_doc_id"] or None}


def doc_link(d: dict, expected: Optional[date] = None) -> dict:
    return {"doc_id": d["doc_id"], "type": d["type"], "label": TYPE_LABEL.get(d["type"], d["type"]), "url": d["url"], "published": iso(d["published_date"]), "title": d["title"],
            "format": d["format"], "available": True, "na": None, "expected": None, "sha256": d["text_sha256"]}


def follow_up(ctx: Context, ccy: str, meeting: date, docs_of: dict, asof: date) -> list:
    """The documents of one meeting after the statement: minutes / account / opinions / deliberations, press-conference video, transcript,
    introductory statement - each present (link) or n/a with the reason (not published yet / expected date / overdue / not available)."""
    items = []
    for typ in FOLLOW_UP:
        lag = EXPECTED_LAG.get((ccy, typ))
        d = docs_of.get(typ)
        if d is not None:
            items.append(doc_link(d))
        elif lag is not None:
            due = meeting + timedelta(days=lag)
            why = (f"expected around {due} (+{lag} d after the decision)" if asof <= due + timedelta(days=GRACE_DAYS)
                   else f"overdue: expected around {due} (+{lag} d), not collected")
            items.append({"type": typ, "label": TYPE_LABEL[typ], "url": None, "available": False, "na": why, "expected": iso(due), "published": None, "title": None,
                          "format": None, "sha256": None})
    for typ in ("presser_video", "presser_transcript", "opening_statement"):
        d = docs_of.get(typ)
        if d is not None:
            items.append(doc_link(d))
    if not docs_of.get("presser_video"):
        items.append({"type": "presser_video", "label": TYPE_LABEL["presser_video"], "url": None, "available": False, "published": None, "title": None, "format": None,
                      "sha256": None, "expected": None,
                      "na": VIDEO_NA.get(ccy, "not collected") + " (a link can be added in data/cb/manual/documents.yaml)"})
    return items


def statement_json(d: dict) -> dict:
    return {"doc_id": d["doc_id"], "url": d["url"], "title": d["title"], "published": iso(d["published_date"]), "format": d["format"], "method": d["extraction_method"],
            "sha256": d["text_sha256"], "paragraphs": (d["text"] or "").split("\n") if d["text"] else [], "rate_after": num(d["rate_after"]),
            "license": d["license_note"]}


def redline_json(ctx: Context, ccy: str, meeting: date) -> Optional[dict]:
    r = ctx.redlines.get((ccy, meeting))
    if r is None:
        return None
    import json as _json
    return {"prev_meeting": iso(r["prev_meeting_date"]), "added_words": r["added_words"], "removed_words": r["removed_words"], "unchanged_words": r["unchanged_words"],
            "similarity": num(r["similarity"], 4), "paras": _json.loads(r["ops_json"])}


SUMMARY_NOTE = "factual summary, no interpretation"


def text_directive(text: str) -> str:
    from urllib.parse import quote
    enc = lambda t: quote(t, safe="").replace("-", "%2D")                            # noqa: E731
    words = text.split()
    if len(text) <= 120 or len(words) <= 10:
        return enc(text)
    return enc(" ".join(words[:5])) + "," + enc(" ".join(words[-5:]))


def text_fragment(text: str) -> str:
    """A URL text fragment (#:~:text=) that makes the browser scroll to and highlight the quote on the bank's own page. Short quotes are matched whole,
    long ones by their first and last words; `-`, `,` and `&` are percent-encoded because they are the directive's own syntax."""
    return "#:~:text=" + text_directive(text)


def text_fragments(texts: list) -> str:
    """Several passages in one link: #:~:text=a&text=b (the browser highlights each one it finds)."""
    return "#:~:text=" + "&text=".join(text_directive(t) for t in texts)


def summary_slot(ctx: Context, doc: Optional[dict]) -> dict:
    """The summary slot of one document: `ready` (the content is in documents.summaries[doc_id]) or `pending` with the reason for the tooltip. A summary that
    failed validation is never shown: its slot stays pending and says so."""
    if doc is None:
        return {"status": "pending", "label": "summary pending", "doc_id": None, "reason": "the document is not collected"}
    if doc["doc_id"] in ctx.summaries:
        return {"status": "ready", "label": "summary", "doc_id": doc["doc_id"], "reason": None}
    if doc["doc_id"] in ctx.summary_no_text and ctx.summary_no_text[doc["doc_id"]]["url"] == doc["url"]:
        mark = ctx.summary_no_text[doc["doc_id"]]
        if mark.get("kind") == "non_prose":                                       # a deck of tables or slides: not summarised, the page shows the link only
            return {"status": "pending", "label": "not summarised", "doc_id": doc["doc_id"], "reason": f"not prose: {mark['reason']} (marked once, not summarised)"}
        return {"status": "pending", "label": "summary pending", "doc_id": doc["doc_id"],
                "reason": "no extractable text: the page was fetched and holds none (marked once, not retried)"}
    fail = next((f for k, f in sorted(ctx.summary_failures.items()) if k.startswith(doc["doc_id"] + "|")), None)
    if fail:
        return {"status": "pending", "label": "summary pending", "doc_id": doc["doc_id"],
                "reason": f"the generated summary failed the automatic check twice and is not shown ({fail['errors'][0]})"}
    return {"status": "pending", "label": "summary pending", "doc_id": doc["doc_id"], "reason": "not generated yet"}


def summary_json(rec: dict, text: Optional[str] = None) -> dict:
    """What the page shows of a stored summary. Each point carries its evidence (the cited paragraphs and the verbatim fragment, linked to the bank's own page with a
    text fragment - HTML sources only); the paragraphs' words are added only for a document whose text is committed anyway (`text`: a statement). The quotes link the same way."""
    html = rec.get("format") == "html" and "#" not in rec["url"]
    paras = text.split("\n") if text else None
    pts, evidence = [], []
    for p in rec["summary"]:
        pts.append(p["text"] if isinstance(p, dict) else p)                              # (a summary made before the evidence existed has plain strings)
        ev = p.get("evidence") if isinstance(p, dict) else None
        frs = None if ev is None else [{"text": f["text"], "paragraph": f["paragraph"]} for f in ev["fragments"]] if "fragments" in ev \
            else [{"text": ev["fragment"], "paragraph": ev["paragraph"]}]                # (prompt v4: one fragment)
        evidence.append(None if ev is None else {
            "paragraphs": ev["paragraphs"], "fragments": frs, "coverage": ev.get("coverage"),
            "href": rec["url"] + text_fragments([f["text"] for f in frs]) if html else rec["url"],
            "texts": {str(n): paras[n - 1] for n in ev["paragraphs"]} if paras else None})
    return {"doc_id": rec["doc_id"], "type": rec["type"], "label": TYPE_LABEL.get(rec["type"], rec["type"]), "title": rec["title"], "url": rec["url"],
            "points": pts, "evidence": evidence, "changes": rec["changes_vs_previous"],
            "dropped": [{"text": d["text"], "reason": (d["errors"] or [""])[0]} for d in rec.get("dropped_points") or []],
            "quotes": [{"text": q["text"], "paragraph": q["paragraph"], "href": rec["url"] + text_fragment(q["text"]) if html else rec["url"]} for q in rec["quotes"]],
            "provider": rec.get("provider"), "model": rec["model"], "prompt_version": rec["prompt_version"], "generated": rec["generated_at"][:10], "note": SUMMARY_NOTE,
            "truncated": bool(rec["coverage"]["truncated"]), "paragraphs_covered": rec["coverage"]["paragraphs"]}


def documents_json(ctx: Context, ccy: str, asof: date, meetings: list) -> dict:
    """`meetings` = the decided meetings to show, most recent first (the last 4 decisions)."""
    mine = [d for d in ctx.documents if d["currency"] == ccy]
    by_meeting: dict = {}
    for d in mine:
        if d["meeting_date"]:
            by_meeting.setdefault(d["meeting_date"], {})[d["type"]] = d
    timeline = []
    for m in meetings:
        docs_of = by_meeting.get(m, {})
        st = docs_of.get("statement")
        fu = follow_up(ctx, ccy, m, docs_of, asof)
        for it in fu:
            if it["available"] and it.get("doc_id"):
                it["summary"] = summary_slot(ctx, next((d for d in mine if d["doc_id"] == it["doc_id"]), None))
        timeline.append({"meeting": iso(m), "statement": None if st is None else dict(doc_link(st), summary=summary_slot(ctx, st)), "follow_up": fu,
                         "votes": votes_json(ctx, ccy, m)})
    latest = None
    if meetings:
        m = meetings[0]
        st = by_meeting.get(m, {}).get("statement")
        latest = {"meeting": iso(m), "statement": None if st is None else statement_json(st), "redline": redline_json(ctx, ccy, m),
                  "votes": votes_json(ctx, ccy, m), "follow_up": timeline[0]["follow_up"] if timeline else [],
                  "summary": summary_slot(ctx, st),
                  "na": None if st is not None else ("RBNZ: the site is behind a Cloudflare challenge - texts only from the manual file" if ccy == "NZD"
                                                     else "statement not collected yet")}
    import json as _json
    cutoff = asof - timedelta(days=SPEECH_WINDOW_DAYS)
    speeches = []
    for d in sorted((d for d in mine if d["type"] in ("speech", "testimony") and d["published_date"] >= cutoff and d["relevance"] != "non_document"),
                    key=lambda d: (d["published_date"], d["doc_id"]), reverse=True):                     # a "media availability" is an announcement, not a speech
        meta = _json.loads(d["meta_json"]) if d["meta_json"] else {}
        speeches.append({"doc_id": d["doc_id"], "type": d["type"], "speaker": d["speaker"] or None, "role": d["role"] or None, "title": d["title"], "url": d["url"],
                         "published": iso(d["published_date"]), "relevance": d["relevance"], "weight": meta.get("weight", 3), "voter": bool(meta.get("voter")),
                         "chair": bool(meta.get("chair")), "via": meta.get("via", "bank"), "summary": summary_slot(ctx, d)})
    speeches = speeches[:30]
    shown = ({it["summary"]["doc_id"] for t in timeline for it in t["follow_up"] if it.get("summary", {}).get("status") == "ready"}
             | {t["statement"]["doc_id"] for t in timeline if t["statement"]} | {x["doc_id"] for x in speeches})
    text_of = {d["doc_id"]: d["text"] for d in mine if d["text"]}
    summaries = {k: summary_json(ctx.summaries[k], text_of.get(k)) for k in sorted(shown) if k in ctx.summaries}          # only what this page shows
    return {"latest": latest, "timeline": timeline, "speeches": speeches, "n_speeches": len(speeches), "summaries": summaries, "summary_note": SUMMARY_NOTE,
            "manual_only": ccy == "NZD", "warnings": [w for w in ctx.doc_warnings if w.startswith(ccy + " ")]}


def calendar_json(ctx: Context, ccy: str, asof: date, limit: int = 12) -> list:
    bank = ctx.banks[ccy]
    proj = bank.get("projections") or {}
    conf = bank.get("conference") or {}
    rows = []
    for m in upcoming_meetings(ctx, ccy, asof)[:limit]:
        rows.append({"decision": iso(m.decision), "first_day": iso(m.first_day), "effective": iso(m.eff), "has_projections": m.has_projections,
                     "projections_name": proj.get("name") if m.has_projections else None, "has_presser": m.has_presser,
                     "conference_local": conf.get("local") if m.has_presser else None, "source": m.source, "date_verified": m.verified,
                     "time": decision_instant(ctx, ccy, m.decision), "blackout": blackout_of(ctx, ccy, m)})
    return rows


def sources_json(ctx: Context, ccy: str, asof: date) -> list:
    out = []
    for sid, c in ctx.sources.items():
        if c["currency"] != ccy:
            continue
        snap = ctx.market.latest(sid, asof)
        cal = ctx.source_cal(sid)
        lag = cal.lag(snap.asof, asof) if snap else None
        out.append({"id": sid, "role": c.get("role"), "methods": sorted({i["method"] for i in c["instruments"].values()}), "license": c.get("license"),
                    "url": c.get("url"), "asof": iso(snap.asof) if snap else None, "lag_bd": lag,
                    "stale": bool(snap is None or (lag is not None and lag > ctx.stale_limit(sid))), "proxy": bool(c.get("proxy"))})
    return out


# ---------------------------------------------------------------------------
# chart data
# ---------------------------------------------------------------------------

def history_steps(ctx: Context, ccy: str, asof: date) -> list:
    """The policy rate as steps [{date, rate, lower, upper}] from ~13 months before the as-of; the last step may lie in the future
    (a decided change that is not in force yet)."""
    decs = ctx.decisions.get(ccy, [])
    start = asof - timedelta(days=CHART_MONTHS_BACK * 31)
    steps = [{"date": iso(d["effective_date"]), "rate": num(d["rate_after"]), "lower": num(d.get("lower")), "upper": num(d.get("upper")),
              "decided": iso(d["meeting_date"])} for d in decs if d["meeting_date"] <= asof and d["effective_date"] >= start]
    before = [d for d in decs if d["effective_date"] < start and d["meeting_date"] <= asof]
    opening = before[-1] if before else (decs[0] if decs else None)
    if opening is not None:
        rate = opening["rate_after"] if before else opening["rate_before"]
        steps.insert(0, {"date": iso(start), "rate": num(rate), "lower": num(opening.get("lower")) if before else None,
                         "upper": num(opening.get("upper")) if before else None, "decided": None, "opening": True})
    return steps


def bank_path_json(rep: BankReport) -> dict:
    bp = rep.bank_path
    if bp is None or bp.kind == "n/a":
        return {"kind": "n/a", "na": (bp.reason if bp else "n/a")}
    out = {"kind": bp.kind, "source": iso(bp.source), "finalised": iso(bp.finalised), "note": bp.note}
    if bp.kind == "dots":
        out["years"] = [{"year": int(lbl), "median": num(v), "dots": [{"level": num(l), "count": n} for l, n in bp.dots.get(int(lbl), [])]} for lbl, v in bp.points]
    else:
        out["quarters"] = [{"period": lbl, "value": num(v, 2)} for lbl, v in bp.points]
        out["vs_market"] = ocr_vs_market_json(rep)
    return out


OCR_VS_MARKET_NOTE = "RBNZ: Q4 average of the published OCR track vs the market at year-end; the market side is an estimate (BKBM-OCR spread)."


def ocr_vs_market_json(rep: BankReport) -> dict:
    """RBNZ OCR track vs the market, the Fed rule: per year, the Q4 average of the published track against the policy-equivalent
    rate in force after the last meeting of the year (`year_end`). Only years with both values. Pure and deterministic."""
    q4 = {int(lbl[:4]): v for lbl, v in rep.bank_path.points if str(lbl).endswith("Q4") and v is not None}
    years = []
    for y in YEAR_ENDS:
        ye = rep.year_ends.get(y)
        if y not in q4 or ye is None or ye.rate is None:
            continue
        years.append({"year": y, "bank": num(q4[y], 2), "market": num(ye.rate), "gap_bp": num((ye.rate - q4[y]) * 100, 1), "flag": ye.flag,
                      "est": ye.flag == "ESTIMATE", "meeting": iso(ye.meeting)})
    return {"note": OCR_VS_MARKET_NOTE, "years": years}


def chart_json(ctx: Context, rep: BankReport, asof: date) -> dict:
    return {"asof": iso(asof), "history": history_steps(ctx, rep.currency, asof), "market": [point_json(p) for p in rep.trajectory.points],
            "bank": bank_path_json(rep), "meetings": [{"decision": iso(m.decision), "effective": iso(m.eff)} for m in upcoming_meetings(ctx, rep.currency, asof)],
            "base": num(rep.trajectory.base.rate) if rep.trajectory.base else None}


# ---------------------------------------------------------------------------
# rate paths (stage 3): the implied path over 12 months, now / 1w / 3w ago
# ---------------------------------------------------------------------------

PATH_DAYS = 365
PATH_HISTORY_BD = {"1w": 5, "3w": 15}
PER_MEETING_PROB = ("EXACT", "CURVE")


def prob_json(p: Point) -> Optional[dict]:
    """{dir, n, p}: n whole 25 bp moves certain, p = probability of one more, in the direction of the step (EXACT / CURVE only)."""
    if p.step_bp is None or p.method not in PER_MEETING_PROB:
        return None
    sp = step_probabilities(p.step_bp)
    n = min(sp["moves"])
    return {"dir": sp["direction"], "n": n, "p": num(sp["moves"].get(n + 1, 0.0), 4)}


INCLUDES_RX = re.compile(r"includes the (.+?) meetings?$")


def includes_of(p: Point) -> list:
    """Meetings whose move is inside this point (FIT: the n/a meetings no contract reaches, `_fit_steps`)."""
    out = []
    for n in p.notes or []:
        m = INCLUDES_RX.match(n)
        if m:
            out += [x.strip() for x in m.group(1).split(",")]
    return out


def path_point_json(p: Point) -> dict:
    na = None
    if p.rate is None:
        na = p.reason or step_reason(p) or "n/a"
    return {"includes": includes_of(p), "joint": None,"meeting": iso(p.meeting), "effective": iso(p.eff), "rate": num(p.rate), "cum_bp": num(p.cum_bp, 2), "step_bp": num(p.step_bp, 2),
            "flag": p.flag, "method": p.method, "est": p.flag == "ESTIMATE", "prob": prob_json(p) if p.rate is not None else None, "na": na,
            "step_na": (step_reason(p) or "n/a") if p.rate is not None and p.step_bp is None else None}


def path_json(ctx: Context, rep: BankReport, asof: date) -> dict:
    """The implied path of one bank over the next 12 months (decision dates <= as-of + 365 days), its 12-month end point, the current
    rate and overnight benchmark, the same meetings 5 and 15 business days earlier (primary source calendar) and the change of the
    12-month level against them. Pure and deterministic (no clock)."""
    ccy, tr = rep.currency, rep.trajectory
    until = asof + timedelta(days=PATH_DAYS)
    pts = [p for p in tr.points if p.meeting is not None and p.meeting <= until]
    points = [path_point_json(p) for p in pts]
    for i, pt in enumerate(points):                                    # a meeting no contract isolates: the later point that carries its move
        if pt["na"] == NOT_COVERED:
            q = next((x for x in points[i + 1:] if pt["meeting"] in x["includes"]), None)
            if q is not None:
                pt["joint"] = {"meeting": q["meeting"], "step_bp": q["step_bp"], "cum_bp": q["cum_bp"]}
    last = next((p for p in reversed(pts) if p.rate is not None), None)
    m12 = ({"meeting": iso(last.meeting), "cum_bp": num(last.cum_bp, 2), "moves": num(last.cum_bp / 25, 2), "rate": num(last.rate),
            "flag": last.flag, "est": last.flag == "ESTIMATE", "na": None} if last is not None and last.cum_bp is not None
           else {"meeting": None, "cum_bp": None, "moves": None, "rate": None, "flag": None, "est": False,
                 "na": tr.na_reason or (pts[-1].reason if pts else "no meeting in the next 12 months")})
    rj = rate_json(ctx, rep, asof)
    bench = None
    if tr.spread is not None and getattr(tr.spread, "estimated", False):
        bench = {"id": None, "name": f"OCR + estimated BKBM-OCR spread ({tr.spread.bp:+.1f} bp)", "value": None, "date": None, "estimated": True}
    elif tr.spread is not None and tr.spread.benchmark:
        sid = tr.spread.benchmark
        ob = ctx.view.obs(sid, asof)
        bench = {"id": sid, "name": ctx.series_names.get(sid, sid), "value": num(ob[1]) if ob else None, "date": iso(ob[0]) if ob else None,
                 "estimated": bool(getattr(tr.spread, "estimated", False))}
    current = {"rate": rj["value"], "lower": rj["lower"] if rj["range"] else None, "upper": rj["upper"] if rj["range"] else None,
               "range": rj["range"], "pending": rj["pending"], "from": rj["from"], "benchmark": bench}
    history, delta = {}, {}
    cal = primary_calendar(ctx, ccy)
    start = primary_history_start(ctx, ccy)
    prim = ctx.primary(ccy)
    snap = ctx.market.latest(prim[0][0], asof) if prim else None
    data_asof = snap.asof if snap else None                             # the date of THIS bank's market data (Eurex: T-1 until the next day)
    data_source = prim[0][0] if prim else None
    data_label = (prim[0][1].get("label") or data_source) if prim else None
    for key, n in PATH_HISTORY_BD.items():
        if tr.na_reason:
            history[key] = {"asof": None, "points": [], "na": tr.na_reason}
            delta[key] = metric(None, na=tr.na_reason, nd=2)
            continue
        prev = cal.add_business_days(data_asof or asof, -n)               # counted from the bank's own data date, not the global as-of
        if start is None or start > prev:
            why = f"history starts {start}" if start else "no history"
            history[key] = {"asof": iso(prev), "points": [], "na": why}
            delta[key] = metric(None, na=why, nd=2)
            continue
        tp = trajectory(ctx, ccy, prev)
        hp = []
        for p in pts:
            q = tp.point_for(p.meeting)
            hp.append({"meeting": iso(p.meeting), "rate": num(q.rate) if q else None, "cum_bp": num(q.cum_bp, 2) if q else None,
                       "est": bool(q and q.flag == "ESTIMATE"), "na": None if q and q.rate is not None else ((q.reason if q else "") or "not priced on " + iso(prev))})
        history[key] = {"asof": iso(prev), "points": hp, "na": None}
        if last is None:
            delta[key] = metric(None, na=m12["na"], nd=2)
        else:
            q = tp.point_for(last.meeting)
            if q is None or q.level is None:
                delta[key] = metric(None, na=f"the {last.meeting} meeting has no level on {prev}" + (f": {q.reason}" if q and q.reason else ""), nd=2)
            else:
                delta[key] = metric((last.level - q.level) * 100, flag=weakest(last.flag, q.flag), nd=2, meeting=iso(last.meeting), prev=iso(prev))
    upcoming = upcoming_meetings(ctx, ccy, asof)
    nxt = None
    if upcoming:
        u = upcoming[0]
        nxt = {"decision": iso(u.decision), "effective": iso(u.eff), "time": decision_instant(ctx, ccy, u.decision),
               "sort_utc": instant_sort_utc(ctx, ccy, u.decision), "end_utc": window_end_utc(ctx, ccy, u.decision)}
    est = bool(tr.spread is not None and getattr(tr.spread, "estimated", False))
    spread_note = (tr.spread.note + ". " + NZD_SENSITIVITY) if est else None
    return {"ccy": ccy, "short": SHORT.get(ccy), "name": display_name(ctx, ccy), "href": bank_href(ccy), "asof": iso(asof), "data_asof": iso(data_asof), "data_source": data_source,
            "data_label": data_label, "points": points, "m12": m12, "current": current, "history": history, "delta": delta, "next": nxt,
            "spread_note": spread_note, "na": tr.na_reason or None}


def display_name(ctx: Context, ccy: str) -> str:
    """The bank's name without the decision body ("Bank of Canada (Governing Council)" -> "Bank of Canada")."""
    name = (ctx.banks.get(ccy) or {}).get("name") or SHORT.get(ccy) or ccy
    return re.sub(r"\s*\([^)]*\)\s*$", "", name)


def instant_sort_utc(ctx: Context, ccy: str, day: date) -> str:
    """UTC instant used to order decisions: the fixed time, or the start of the window (BoJ), else noon local."""
    t = decision_instant(ctx, ccy, day)
    if t["utc"]:
        return t["utc"]
    hhmm = (t.get("window_local") or ["12:00"])[0]
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m)), ZoneInfo(t["tz"])).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_end_utc(ctx: Context, ccy: str, day: date) -> Optional[str]:
    """BoJ (no fixed time): the end of its announcement window in UTC; None for a fixed time."""
    t = decision_instant(ctx, ccy, day)
    if t["utc"] or not t.get("window_local"):
        return None
    h, m = t["window_local"][1].split(":")
    return datetime.combine(day, time(int(h), int(m)), ZoneInfo(t["tz"])).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_decision_json(ctx: Context, paths: dict, asof: date) -> Optional[dict]:
    """The nearest upcoming decision of all banks (by the UTC instant; BoJ: the start of its window) with the move priced for it."""
    cands = []
    for ccy, pj in paths.items():
        if pj["next"] is None:
            continue
        day = date.fromisoformat(pj["next"]["decision"])
        cands.append((instant_sort_utc(ctx, ccy, day), ccy, day))
    if not cands:
        return None
    sort_utc, ccy, day = min(cands)
    pj = paths[ccy]
    pt = next((p for p in pj["points"] if p["meeting"] == iso(day)), None)
    return {"ccy": ccy, "short": SHORT.get(ccy), "href": bank_href(ccy), "decision": iso(day), "time": decision_instant(ctx, ccy, day),
            "sort_utc": sort_utc, "end_utc": window_end_utc(ctx, ccy, day), "point": pt}


# ---------------------------------------------------------------------------
# overview row / bank page / pairs
# ---------------------------------------------------------------------------

def bank_block(ctx: Context, ccy: str) -> dict:
    b = ctx.banks[ccy]
    return {"id": b["id"], "name": b["name"], "short": SHORT.get(ccy, b["id"].upper()), "tz": b["tz"], "url": b.get("calendar_url")}


def overview_row(ctx: Context, rep: BankReport, asof: date) -> dict:
    ccy = rep.currency
    last = next(iter(reversed([d for d in ctx.decisions.get(ccy, []) if d["meeting_date"] <= asof])), None)
    hz = horizon_json(ctx, rep)
    ends = {str(y): year_metric(rep.year_ends[y]) for y in YEAR_ENDS}
    stale = bool(hz.get("stale") or any(e["stale"] for e in ends.values()))
    return {
        "ccy": ccy, "bank": bank_block(ctx, ccy), "href": bank_href(ccy), "rate": rate_json(ctx, rep, asof), "next": next_json(ctx, rep, asof),
        "horizon": hz, "end": ends, "gap": gap_json(rep), "repricing": repricing_json(rep), "reaction": reaction_json(rep),
        "last_decision": (None if last is None else {"date": iso(last["meeting_date"]), "delta_bp": num(last["delta_bp"], 1),
                                                     "rate_after": num(last["rate_after"]), "effective": iso(last["effective_date"]),
                                                     "surprise_consensus_bp": num(last["surprise_consensus_bp"], 1), "status": last["status"]}),
        "stale": stale, "na": rep.trajectory.na_reason or None,
    }


def bank_page(ctx: Context, rep: BankReport, asof: date) -> dict:
    row = overview_row(ctx, rep, asof)
    bank = ctx.banks[rep.currency]
    tr = rep.trajectory
    spread = None
    if tr.spread and tr.spread.value is not None:
        s = tr.spread
        spread = {"bp": num(s.bp, 2), "start": iso(s.start), "end": iso(s.end), "n": s.n, "excluded": len(s.excluded), "benchmark": s.benchmark, "policy": s.policy}
    horizons = {}
    for y in YEAR_ENDS:
        rp = {name: row["repricing"][name]["years"][str(y)] for name in ("1w", "1m")}
        horizons[str(y)] = {**row["end"][str(y)], "repricing": rp, "gap": row["gap"]["years"].get(str(y)) if rep.currency == "USD" else None}
    unverified = []
    rule = bank.get("blackout_rule")
    if rule and (not rule.get("verified") or rule.get("precision") == "approximate"):
        unverified.append({"label": "blackout approximate", "text": "The blackout window is approximate / unverified: " + (rule.get("note") or "rule not confirmed on the official page")})
    eff_rule = bank.get("effective_rule") or {}
    if eff_rule.get("verified") is False:
        unverified.append({"label": "effective date derived", "text": "Effective-date rule not verified against an official page" + (f" (derived from {eff_rule['derived_from']})" if eff_rule.get("derived_from") else "") + ". " + (eff_rule.get("note") or "")})
    if (bank.get("decision_time") or {}).get("verified") is False:
        unverified.append({"label": "decision time unverified", "text": "The decision time is not verified on the bank's own pages: " + ((bank.get("decision_time") or {}).get("evidence") or "")})
    return {
        "ccy": rep.currency, "bank": {**bank_block(ctx, rep.currency), "conference": bank.get("conference"), "projections": bank.get("projections"),
                                     "policy_rate": (bank.get("policy_rate") or {}).get("definition"), "youtube": (bank.get("video_page") or "").rsplit("/channel/", 1)[-1] or None},
        "summary": row, "unverified": unverified, "spread": spread, "proxy_basis_bp": num((tr.extra.get("proxy_basis") or 0) * 100, 1) if tr.extra.get("proxy_basis") is not None else None,
        "trajectory": [point_json(p) for p in tr.points], "notes": list(tr.notes), "consistency": [{"source": c[0], "period": c[1], "dev_bp": num(c[2], 2), "detail": c[3]} for c in tr.consistency],
        "chart": chart_json(ctx, rep, asof), "next_card": {"horizon": row["horizon"], "next": row["next"]}, "horizons": horizons, "gap": row["gap"],
        "decisions": decision_rows(ctx, rep), "documents": documents_json(ctx, rep.currency, asof, [x.meeting for x in reversed([x for x in rep.surprises if x.decided][-4:])]),
        "calendar": calendar_json(ctx, rep.currency, asof), "sources": sources_json(ctx, rep.currency, asof),
        "crosschecks": [{"name": c.name, "period": c.period, "primary": num(c.a), "second": num(c.b), "diff_bp": num(c.diff_bp, 2), "note": c.note, "na": c.na_reason or None}
                        for c in rep.crosschecks],
        "meta": meta(ctx, asof), "latency": latency_block(ctx, rep.currency), "path": path_json(ctx, rep, asof),
    }


def latency_block(ctx: Context, ccy: str) -> Optional[dict]:
    """The decision -> site latency of this bank's statements (phase 4), for the methodology panel; None when no statement is stored."""
    from ..cb_trigger import latency as lat
    block = lat.payload(ctx.documents, ctx.banks, ccy)
    return block if block["rows"] else None


def meta(ctx: Context, asof: date) -> dict:
    return {"asof": iso(asof), "stale_after_bd": ctx.stale_after_bd, "flags": {k: {"label": FLAG_LABEL[k], "help": v} for k, v in FLAG_HELP.items()},
            "level_help": LEVEL_HELP, "level_label": LEVEL_LABEL, "methodology": METHODOLOGY}


def pair_json(pr: PairRow, display: str) -> dict:
    def why(key):
        return "; ".join(f"{c}: {r}" for c, r in pr.reasons.get(key, [])) or "n/a"
    implied = {}
    for y in YEAR_ENDS:
        if y in pr.implied:
            implied[str(y)] = {"diff_pp": num(pr.implied[y]), "cum_bp": metric(pr.cum_bp[y], flag=pr.cum_flag.get(y), nd=1),
                               "diff_bp": metric(pr.implied[y] * 100, flag=pr.implied_flag.get(y), nd=1)}
        else:
            implied[str(y)] = {"diff_pp": None, "cum_bp": metric(None, na=why(("cum", y))), "diff_bp": metric(None, na=why(("implied", y)))}
    repricing = {}
    for key, name in WINDOW_KEYS.items():
        repricing[name] = {}
        for y in YEAR_ENDS:
            if (key, y) in pr.reprice:
                repricing[name][str(y)] = metric(pr.reprice[(key, y)], flag=pr.reprice_flag.get((key, y)), nd=1)
            else:
                repricing[name][str(y)] = metric(None, na=why(("reprice", key, y)))
    flags = [f for f in list(pr.implied_flag.values()) + list(pr.reprice_flag.values()) if f]
    return {"pair": pr.pair, "slug": slug(pr.pair), "display": display, "href": pair_href(pr.pair), "base": pr.base, "quote": pr.quote,
            "current": metric(pr.current_bp, nd=1, na=why("current")), "implied": implied, "repricing": repricing, "flag": weakest_flag(*flags)}


def build(ctx: Context, asof: date, pair_defs: list, currencies=ORDER) -> dict:
    """{"overview": ..., "banks": {ccy: page}, "pairs": ...} at `asof`. `pair_defs` = [(pair, base, quote, display)]."""
    reports = {c: bank_report(ctx, c, asof) for c in currencies if c in ctx.banks}
    rows = [overview_row(ctx, reports[c], asof) for c in currencies if c in reports]
    paths = {c: path_json(ctx, reports[c], asof) for c in reports}
    pairs = [pair_json(pair_row(n, reports[b], reports[q]), disp) for n, b, q, disp in pair_defs if b in reports and q in reports]
    m = meta(ctx, asof)
    return {
        "overview": {"meta": m, "banks": rows, "paths": paths, "next_decision": next_decision_json(ctx, paths, asof)},
        "banks": {c: {**bank_page(ctx, reports[c], asof), "path": paths[c]} for c in reports},
        "pairs": {"meta": m, "pairs": pairs, "banks": {c: {"short": SHORT.get(c), "href": bank_href(c)} for c in reports}},
    }


# ---------------------------------------------------------------------------
# Stance (phase S1; written to public/ by cb_render since S2): pure payload of one bank
# ---------------------------------------------------------------------------

STANCE_ND = 6                                                      # the UI rounds; keep the computation's precision


def _sm(v, flags: list, na: str, flag: Optional[str] = None, **extra) -> dict:
    """{v, flag, stale, na} for a stance metric (numbers keep STANCE_ND decimals; strings pass through)."""
    if v is None:
        return {"v": None, "flag": flag, "stale": "stale" in flags, "na": na or "n/a", **extra}
    return {"v": num(v, STANCE_ND) if isinstance(v, float) else v, "flag": flag, "stale": "stale" in flags, "na": None, **extra}


def stance_json(r: dict) -> dict:
    """One bank's stance in the payload convention, with the intermediate values for 'Show calculation'."""
    fl = list(r.get("flags") or [])
    N = r.get("N")
    no_n = "no neutral estimate (need only)"
    lens = r.get("lens") or {}
    lens_v = lens.get("delta_bp") if lens.get("kind") in ("market", "constant_rate") else lens.get("gap_bp")
    basis_flag = "headline_basis" if r.get("pi_12m_basis") == "headline" else None
    return {
        "bank": r["currency"], "asof": r["asof"], "flags": fl, "mandate_id": r["mandate_id"], "stance_version": r["stance_version"],
        "code_version": r["code_version"], "flags_12m": list(r.get("flags_12m") or []),
        "situation": _sm(r.get("situation"), fl, no_n, direction=r.get("direction")),
        "situation_12m": _sm(r.get("situation_12m"), fl, r.get("na_12m") or no_n, flag=basis_flag, direction=r.get("direction_12m"),
                             basis=r.get("pi_12m_basis") or None),
        "stance": _sm(r.get("S"), fl, no_n, flag=N["flag"] if N else None, lo=num(r.get("S_lo"), STANCE_ND), hi=num(r.get("S_hi"), STANCE_ND)),
        "need": _sm(r.get("x"), fl, "no underlying inflation"),
        "need_headline": _sm(r.get("x_headline"), fl, "no headline inflation"),
        "need_12m": _sm(r.get("x_12m"), fl, r.get("na_12m") or "no projection for the 12-month period", basis=r.get("pi_12m_basis") or None,
                        period=r.get("pi_12m_period")),
        "pressure_12m": _sm(r.get("G_12m"), fl, r.get("na_12m") if r.get("x_12m") is None else no_n,
                            lo=num(r.get("G_12m_lo"), STANCE_ND), hi=num(r.get("G_12m_hi"), STANCE_ND)),
        "pressure": _sm(r.get("G"), fl, no_n, lo=num(r.get("G_lo"), STANCE_ND), hi=num(r.get("G_hi"), STANCE_ND)),
        "pivot": _sm(r.get("pivot"), fl, "no declared target point (band) or no neutral"),
        "lens": _sm(lens_v, fl, lens.get("na") or "n/a", flag=lens.get("flag"), kind=lens.get("kind"), basis=lens.get("basis"), year=lens.get("year"),
                    horizon_gap=num(lens.get("horizon_gap"), STANCE_ND), horizon_period=lens.get("horizon_period"),
                    horizon_basis=lens.get("horizon_basis"), consistent=lens.get("consistent")),
        "cycle": r.get("cycle"), "market": r.get("market"),
        "calc": {"rate": r.get("rate"), "target": r.get("target_star"), "band": list(r["band"]) if r.get("band") else None,
                 "pi_under": r.get("pi_under"), "pi_head": r.get("pi_head"), "gap_under": r.get("gap_under"), "gap_head": r.get("gap_head"),
                 "y": r.get("y"), "y_how": r.get("y_how"), "x": r.get("x"),
                 "neutral": None if not N else {"mid": N["mid"], "lo": N["lo"], "hi": N["hi"], "asof": N["asof"], "flag": N["flag"],
                                                "stale": N["stale"], "nominal_from": N["nominal_from"]},
                 "S": r.get("S"), "S_lo": r.get("S_lo"), "S_hi": r.get("S_hi"), "G": r.get("G"), "G_lo": r.get("G_lo"), "G_hi": r.get("G_hi"),
                 "x_headline": r.get("x_headline"), "pivot": r.get("pivot"),
                 "pi_12m": r.get("pi_12m"), "pi_12m_period": r.get("pi_12m_period"), "pi_12m_basis": r.get("pi_12m_basis"), "gap_12m": r.get("gap_12m"),
                 "y_12m": r.get("y_12m"), "y_12m_how": r.get("y_12m_how"), "x_12m": r.get("x_12m"), "G_12m": r.get("G_12m"),
                 "neutral_check": N.get("check") if N else None, "inputs": r.get("inputs")},
    }


def _r2(x: Optional[float]) -> Optional[float]:
    """pp for display: half away from zero on the decimal value (0.6749999999999998 -> 0.68)."""
    if x is None:
        return None
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(repr(round(x, 9))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _period_short(p: str) -> str:
    p = str(p or "")
    if len(p) == 7 and p[4] == "-":
        return MONTHS_SHORT[int(p[5:7]) - 1]
    if len(p) == 6 and p[4] == "Q":
        return p[4:]
    return p


MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def stance_notes(r: dict, short: str, newer_forecast: Optional[date], core_ids: list) -> dict:
    """The staleness the engine already computed, as short grey notes (the conclusion stays shown):
    a newer projection publication has passed -> Why; the neutral older than the staleness limit -> Position; the core series overdue -> Need."""
    notes = list(r.get("notes") or [])
    N = r.get("N") or {}
    why = (f"Newer {short} forecast out on {newer_forecast.day} {MONTHS_SHORT[newer_forecast.month - 1]} {newer_forecast.year}, not yet entered."
           if newer_forecast and any(n.startswith("a newer projection publication") for n in notes) else None)
    pos = None
    if N.get("stale") and N.get("asof"):
        y, m = int(str(N["asof"])[:4]), int(str(N["asof"])[5:7])
        pos = f"from {MONTHS_SHORT[m - 1]} {y}"
    need = "update overdue" if any(f"{sid} stale" in notes for sid in core_ids) else None
    return {"why": why, "position": pos, "need": need}


# ---------------------------------------------------------------------------
# Stance v6 (stance-ui-v6): Position / Need / Why without a verdict - only what is displayed
# ---------------------------------------------------------------------------
# A displayed line is a list of segments: a fixed English string, a number {"k": key in the bank's stance block, "f": format}
# (rendered with data-k / data-f / data-v for scripts/check_stance_ui.py), a method badge {"badge": flag}, a stale badge
# {"stale": tooltip} or a probability {"prob": {dir, n, p}} (probText, as in "By meeting"). Numbers shown as pp / d2 / pub are stored
# already rounded for display; bp and r3 keep the value of the "By meeting" table (formatted by the browser the same way).

ST_MINUS = "−"
QUIET_FLAGS = ("EXACT", "CURVE")


def _pub(x: Optional[float], nd: int = 3) -> Optional[float]:
    """A published / input value for display: half-up at `nd` decimals (3.4905 -> 3.491; 0.2000000001 -> 0.2)."""
    if x is None:
        return None
    from decimal import ROUND_HALF_UP, Decimal
    v = float(Decimal(repr(round(x, 9))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))
    return 0.0 if v == 0 else v


def st_fmt(v: float, f: str) -> str:
    """The text of a number in format f - the browser's ST_FMT (static/cb.js) and scripts/check_stance_ui.py do the same."""
    from decimal import ROUND_HALF_UP, Decimal
    v = float(v)
    if f in ("pp", "bp"):
        nd = 2 if f == "pp" else 1
        s = str(Decimal(abs(v)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))
        return s if float(s) == 0 else ("+" if v > 0 else ST_MINUS) + s
    if f == "d2":
        s = str(Decimal(abs(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return s if float(s) == 0 or v > 0 else ST_MINUS + s
    if f == "r3":
        s = str(Decimal(abs(v)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
        return s if float(s) == 0 or v > 0 else ST_MINUS + s
    if f == "pub":
        s = f"{abs(v):g}"
        return s if v >= 0 or float(s) == 0 else ST_MINUS + s
    raise ValueError(f)


def st_at(block: dict, key: str):
    o = block
    for k in key.split("."):
        o = o.get(k) if isinstance(o, dict) else (o[int(k)] if isinstance(o, list) and k.isdigit() and int(k) < len(o) else None)
    return o


def prob_text(pr: Optional[dict]) -> str:
    """static/cb.js probText: '62.0% hike' (n = 0), '+25 bp + 28.0% of +50' (n >= 1), 'hold'."""
    if not pr:
        return ""
    cut = pr.get("dir") == "cut"
    sgn, word = (ST_MINUS if cut else "+"), ("cut" if cut else "hike")
    p = float(pr.get("p") or 0)
    if pr.get("dir") == "hold" or (pr.get("n") == 0 and p < 0.005):
        return "hold"
    from decimal import ROUND_HALF_UP, Decimal
    pc = str(Decimal(p * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%"
    if pr.get("n") == 0:
        return f"{pc} {word}"
    n = int(pr["n"])
    return f"{sgn}{25 * n} bp" + (f" + {pc} of {sgn}{25 * (n + 1)}" if p >= 0.005 else "")


def st_text(block: dict, line) -> str:
    """The plain text of a displayed line (badges as [FLAG], [stale]) - for the tests and the report."""
    segs = line["segs"] if isinstance(line, dict) else line
    out = []
    for s in segs:
        if isinstance(s, str):
            out.append(s)
        elif "k" in s:
            v = st_at(block, s["k"])
            out.append("n/a" if v is None else st_fmt(v, s["f"]))
        elif "badge" in s:
            out.append(f" [{s['badge']}]")
        elif "stale" in s:
            out.append(" [stale]")
        elif "prob" in s:
            out.append(prob_text(s["prob"]))
        elif "note" in s:
            out.append(s["note"])
    return "".join(out)


def _day(iso_: str) -> str:
    y, m, d = (int(x) for x in str(iso_)[:10].split("-"))
    return f"{d} {MONTHS_SHORT[m - 1]}"


def _date(iso_: str) -> str:
    return f"{_day(iso_)} {str(iso_)[:4]}"


def _L(*segs, title: Optional[str] = None, cls: Optional[str] = None, note: Optional[str] = None) -> dict:
    out = {"segs": [s for s in segs if s not in ("", None)]}
    if title:
        out["title"] = title
    if cls:
        out["cls"] = cls
    if note:
        out["note"] = note
    return out


def _n(key: str, f: str) -> dict:
    return {"k": key, "f": f}


def _target_text(star, band) -> str:
    """The target as v5 showed it: 'vs 2%' (point or declared midpoint), 'target 0–2%' (band without a midpoint)."""
    if star is not None:
        return f"vs {star:g}%"
    return f"target {band[0]:g}–{band[1]:g}%" if band else ""


def _measure(basis: str) -> str:
    return "core inflation" if basis == "underlying" else "total inflation"


def _conditioning_text(v6: dict) -> str:
    bp_ = v6["bank_path"]
    k = bp_.get("kind")
    if k == "dots":
        return "the Fed's own rate path (dots)"
    if k == "ocr_track":
        return "the RBNZ's own OCR track"
    if k == "market":
        return f"the market curve of {_day(bp_['cutoff'])}" if bp_.get("cutoff") else "the market curve (cutoff not published)"
    if k == "constant_rate":
        return "an unchanged rate"
    return "a rate path that is not published"


def _vote_line(vote: Optional[dict], decided: Optional[str]) -> dict:
    head = f"Last decision {_day(decided)}: " if decided else "Last decision: "
    if not decided:
        return _L(head + "vote not recorded")
    if vote is None:
        return _L(head + "vote not recorded")
    k = vote.get("kind")
    if k == "consensus":
        return _L(head + "decided by consensus")
    if k == "not_published":
        return _L(head + "vote not published")
    if k == "unanimous":
        return _L(head + f"vote {vote['label']}")
    words = {"raise": "for a hike", "lower": "for a cut", "hold": "for no change"}
    dirs = [a.get("direction") for a in vote.get("against") or [] if a.get("direction") in words]
    groups = []
    if len(set(dirs)) == 1 and vote.get("n_against"):                   # one direction: the counted dissents (RBA: one entry for "3 members")
        groups = [(dirs[0], int(vote["n_against"]))]
    else:
        for d in ("raise", "lower", "hold"):
            if dirs.count(d):
                groups.append((d, dirs.count(d)))
    tail = " (" + ", ".join(f"{n} {words[d]}" for d, n in groups) + ")" if groups else ""
    return _L(head + f"vote {vote['label']}{tail}")


def stance_page_v6(r: dict, ui: dict, rate: Optional[dict], mandate: Optional[dict] = None, next_test: Optional[dict] = None,
                   history: Optional[list] = None, neutral_na: Optional[str] = None, notes: Optional[dict] = None, *,
                   path: Optional[dict] = None, bank_row: Optional[dict] = None, vote: Optional[dict] = None, stale_after_bd: int = 2) -> dict:
    """The stance block (stance-ui-v6): Position / Need / Why (Taylor rule, Bank, Market), only what is displayed.
    r = stance.compute (v3 + r["v6"]); ui = {measure_short, measure_name}; path = overview paths[ccy] (the "By meeting" data);
    bank_row = overview banks[] row (rate.decided, horizon.stale); vote = votes_json of the last decision; notes = stance_notes."""
    v6 = r["v6"]
    B = v6["short"]
    notes = notes or {}
    fc = v6.get("forecast")
    star, band = r.get("target_star"), r.get("band")
    inp = r.get("inputs") or {}
    core = inp.get("pi_under") or {}
    newer = bool(v6.get("newer_forecast"))
    na_text = lambda x: (neutral_na or "no neutral estimate") if x == "neutral" else x     # noqa: E731

    # ---- Position
    p6 = v6["position"]
    pos: dict = {"v": _r2(p6["P"]), "lo": _r2(p6["P_lo"]), "hi": _r2(p6["P_hi"]), "label": p6["label"], "na": None, "lines": [], "title": None}
    if p6["P"] is None:
        pos["na"] = na_text(p6["na"])
    else:
        rn = p6["real_neutral"]
        pos["x"] = {"real_rate": _r2(p6["real_rate"]), "rn_mid": _r2(rn["mid"]), "rn_lo": _r2(rn["lo"]), "rn_hi": _r2(rn["hi"]), "pi": _pub(p6["pi"])}
        pos["lines"] = [
            _L("real rate ", _n("position.x.real_rate", "d2"), "% vs real neutral ", _n("position.x.rn_mid", "d2"), "% (",
               _n("position.x.rn_lo", "d2"), " to ", _n("position.x.rn_hi", "d2"), "%)"),
            _L(f"with the {B} forecast: {_measure(fc['basis'])} ", _n("position.x.pi", "pub"), f"%, {fc['period_label']}")]
        n_ = p6["neutral"]
        pos["title"] = (f"Over the neutral range: {st_fmt(pos['lo'], 'pp')} to {st_fmt(pos['hi'], 'pp')}. "
                        f"Real rate = {st_fmt(_pub(p6['rate']), 'pub')}% − {st_fmt(_pub(p6['pi']), 'pub')}%. "
                        f"Real neutral = neutral {st_fmt(_pub(n_['mid']), 'pub')}% ({st_fmt(_pub(n_['lo']), 'pub')} to {st_fmt(_pub(n_['hi']), 'pub')}%) "
                        f"− target {st_fmt(_pub(p6['target']), 'pub')}%.")
        if notes.get("position"):
            pos["lines"].append(_L(f"({notes['position']})", cls="note", note="position"))
        if newer:
            pos["lines"].append(_L("newer forecast not entered", cls="note", note="position_forecast"))

    # ---- Need
    n6 = v6["need"]
    need: dict = {"v": _r2(n6["N"]), "label": n6["label"], "na": None, "lines": [], "title": None}
    now_segs: list = []
    if core.get("v") is not None:
        need.setdefault("x", {})["core"] = _pub(core["v"])
        now_segs = [f"now: {ui.get('measure_short', 'Core')} ", _n("need.x.core", "pub"), f"% ({v6_period(core.get('asof'))})"]
        now_segs += _slack_now(r, v6, need)
    if n6["N"] is None:
        need["na"] = na_text(n6["na"])
    else:
        need.setdefault("x", {})["pi"] = _pub(fc["v"])
        pubd = f" of {_date(v6['bank_path']['published'])}" if v6["bank_path"].get("published") else ""
        need["lines"] = [
            _L(f"{B} forecast{pubd}: {_measure(fc['basis'])} ", _n("need.x.pi", "pub"), f"%, {fc['period_label']}, {_target_text(star, band)}"
               + ("; no core forecast published" if v6.get("headline_basis") else "")),
            _L(f"conditional on {_conditioning_text(v6)}")]
    if now_segs:
        if notes.get("need"):                                             # the core series is overdue: a grey note on the "now" line
            now_segs += [" ", {"note": notes["need"], "name": "need"}]
        need["lines"].append(_L(*now_segs))
    src = []
    if fc is not None:
        src.append(f"forecast: {fc.get('source')} ({fc['period_label']})")
    src.append(f"core inflation: {ui.get('measure_name') or core.get('source')} — {core.get('asof')}" + (f", {core.get('note')}" if core.get("note") else ""))
    yt, u, us = inp.get("y_today") or {}, inp.get("u") or {}, inp.get("u_star") or {}
    if inp.get("slack_type") == "output_gap" and yt.get("v") is not None:
        src.append(f"output gap: {yt.get('source')}")
    elif inp.get("slack_type") == "unemployment_gap" and u.get("v") is not None:
        src.append(f"unemployment: {u.get('source')}; u*: {us.get('source')}")
    else:
        src.append(f"output gap: the {B} publishes no estimate")
    need["title"] = "\n".join(src)
    if notes.get("why"):                                                  # the newer forecast note moves from Why (v5) to Need
        need["lines"].append(_L(notes["why"], cls="note", note="need_forecast"))

    # ---- Why: Taylor rule
    t6 = v6["taylor"]
    tay: dict = {"label": "Taylor rule", "reading": t6.get("reading"), "lines": [], "na": None}
    if t6.get("na") == "neutral" or t6.get("reading") is None:
        tay["na"] = na_text(t6["na"] or "neutral")
        tay["lines"] = [_L(f"n/a: {tay['na']}")]
    else:
        tay["x"] = {}
        for sc in ("today", "forecast"):
            s = t6[sc]
            if s["T"] is None:
                tay["lines"].append(_L(f"{sc} n/a: {na_text(s['na'])}"))
                continue
            i_ = s["inputs"]
            tay["x"].update({f"{sc}_T": _r2(s["T"]), f"{sc}_lo": _r2(s["lo"]), f"{sc}_hi": _r2(s["hi"]), f"{sc}_pi": _pub(i_["pi"])})
            no_slack = "no_slack" in t6["flags"] or i_["y"] is None
            if not no_slack:
                tay["x"][f"{sc}_y"] = _pub(i_["y"])
            head = [f"{sc} ", _n(f"why.taylor.x.{sc}_T", "pp"), " (", _n(f"why.taylor.x.{sc}_lo", "pp"), " to ", _n(f"why.taylor.x.{sc}_hi", "pp"), "): "]
            if sc == "today":
                body = [f"{ui.get('measure_short', 'Core')} ", _n("why.taylor.x.today_pi", "pub"), f"% ({v6_period(i_['pi_period'])})"]
            else:
                body = [f"{_measure(fc['basis'])} ", _n("why.taylor.x.forecast_pi", "pub"), f"% {fc['period_label']}"]
            if not no_slack:
                body += [", output gap ", _n(f"why.taylor.x.{sc}_y", "pub")] + ([" (held)"] if sc == "forecast" and i_["held"] else [])
            if sc == "forecast" and newer:
                body += [" · newer forecast not entered"]
            tay["lines"].append(_L(*head, *body, title=_formula(i_, s, no_slack)))
        for f in t6["flags"]:
            if f == "no_slack":
                tay["lines"].append(_L(f"no output gap term: the {B} publishes no estimate", cls="flag"))
            elif f == "headline_basis" and t6["forecast"]["T"] is not None:
                tay["lines"].append(_L("forecast is total inflation", cls="flag"))

    # ---- Why: Bank
    bk = v6["bank_path"]
    bank: dict = {"label": "Bank", "lines": []}
    k = bk.get("kind")
    if k == "dots":
        bank["lines"].append(_L(f"Dots (SEP {_date(bk['published'])}): " + ", ".join(f"{lbl} {v:g}%" for lbl, v in bk["path"]),
                                title=(r.get("lens") or {}).get("source")))
    elif k == "ocr_track":
        bank["lines"].append(_L(f"OCR track ({bk.get('publication')}): " + ", ".join(f"{lbl} {v:g}%" for lbl, v in bk["path"]),
                                title=(r.get("lens") or {}).get("source")))
    elif k == "market":
        bank["lines"].append(_L(f"Forecast conditioned on the market curve of {_day(bk['cutoff'])}" if bk.get("cutoff") else
                                "Forecast conditioned on the market curve (cutoff not published)",
                                " (estimated cutoff)" if bk.get("cutoff_flag") == "ESTIMATE" else "", title=bk.get("publication")))
    elif k == "constant_rate":
        bank["x"] = {"assumed": _pub(bk.get("assumed_rate"))}
        bank["lines"].append(_L("Forecast conditioned on an unchanged rate (", _n("why.bank.x.assumed", "pub"), "%)", title=bk.get("publication")))
    else:
        bank["lines"].append(_L("Forecast rate path not published", title=bk.get("publication")))
    bank["lines"].append(_vote_line(vote, ((bank_row or {}).get("rate") or {}).get("decided")))

    # ---- Why: Market
    market = _market_row(r, path or {}, bank_row or {}, stale_after_bd)

    return {
        "bank": r["currency"], "asof": r["asof"], "rate_display": rate,
        "position": pos, "need": need,
        "why": {"taylor": tay, "bank": bank, "market": market},
        "mandate": mandate, "next_test": next_test, "history": history or [],
    }


def v6_period(p) -> str:
    from .stance_v6 import period_label
    return period_label(p, "")


def _slack_now(r: dict, v6: dict, need: dict) -> list:
    """', output gap {y} ({period})' | ', output gap {y} (from unemployment {u}% vs u* {u*}%, {period})' | ', no output gap published'."""
    from .stance_v6 import period_label
    inp = r.get("inputs") or {}
    y = r.get("y")
    x = need.setdefault("x", {})
    if inp.get("slack_type") == "output_gap" and y is not None:
        yt = inp.get("y_today") or {}
        x["y"] = _pub(y)
        per = v6.get("y_today_period") or period_label(yt.get("asof"), "")
        if yt.get("lo") is not None and yt.get("hi") is not None:
            x["y_lo"], x["y_hi"] = _pub(yt["lo"]), _pub(yt["hi"])
            return [", output gap ", _n("need.x.y", "pub"), " (", _n("need.x.y_lo", "pub"), " to ", _n("need.x.y_hi", "pub"), f", {per})"]
        return [", output gap ", _n("need.x.y", "pub"), f" ({per})"]
    if inp.get("slack_type") == "unemployment_gap" and y is not None:
        u, us = inp.get("u") or {}, inp.get("u_star") or {}
        x.update(y=_pub(y), u=_pub(u.get("v")), u_star=_pub(us.get("v")))
        return [", output gap ", _n("need.x.y", "pub"), " (from unemployment ", _n("need.x.u", "pub"), "% vs u* ", _n("need.x.u_star", "pub"),
                f"%, {period_label(u.get('asof'), '')})"]
    return [", no output gap published"]


def _formula(i_: dict, s: dict, no_slack: bool) -> str:
    """'3.2 + 1.5 × (3.1 − 2) + 0.5 × 0.2 − 3.875 = +1.08; neutral 2.9 to 3.9 → +0.78 to +1.78'."""
    f = lambda v: st_fmt(_pub(v), "pub")                                   # noqa: E731
    n = i_["neutral"]
    y = "" if no_slack else f" + 0.5 × {f(i_['y'])}"
    return (f"{f(n['mid'])} + 1.5 × ({f(i_['pi'])} − {f(i_['target'])}){y} − {f(i_['rate'])} = {st_fmt(_r2(s['T']), 'pp')}; "
            f"neutral {f(n['lo'])} to {f(n['hi'])} → {st_fmt(_r2(s['lo']), 'pp')} to {st_fmt(_r2(s['hi']), 'pp')}")


def _market_row(r: dict, pj: dict, row: dict, stale_bd: int) -> dict:
    """The next 2 meetings of the implied path (the "By meeting" numbers), their 1-week change, the data date and its stale flag, and
    the v3 lens (market minus the bank path at the same date)."""
    out: dict = {"label": "Market", "lines": [], "x": {}}
    x = out["x"]
    pts = list((pj or {}).get("points") or [])[:2]
    w1 = ((pj or {}).get("history") or {}).get("1w") or {}
    tips = []
    if not pts:
        out["lines"].append(_L(f"market pricing n/a ({(pj or {}).get('na') or 'no meeting in the next 12 months'})"))
    else:
        segs: list = []
        for j, p in enumerate(pts, 1):
            if j > 1:
                segs.append(" · ")
            if p.get("cum_bp") is None:
                segs.append(f"{_day(p['meeting'])}: n/a ({p.get('na') or 'not priced'})")
                continue
            x[f"m{j}_cum"] = p["cum_bp"]
            segs += [f"{_day(p['meeting'])}: " + ("≈ " if p.get("est") else ""), _n(f"why.market.x.m{j}_cum", "bp"), " bp"]
            if not p.get("est") and prob_text(p.get("prob")):
                segs += [", ", {"prob": p["prob"]}]
            if p.get("flag") and p["flag"] not in QUIET_FLAGS:
                segs.append({"badge": p["flag"]})
            tips.append(f"{_day(p['meeting'])}: method {p.get('flag') or p.get('method')}, implied rate {p.get('rate')}%")
        out["lines"].append(_L(*segs))
        # 1 week: exactly the "Δ vs 1w" column of "By meeting": (rate - rate 1 week ago on the same meeting) * 100
        segs = [f"1w (vs {_day(w1['asof'])}): " if w1.get("asof") else "1w: "]
        for j, p in enumerate(pts, 1):
            if j > 1:
                segs.append(" / ")
            hp = next((q for q in w1.get("points") or [] if q.get("meeting") == p["meeting"]), None)
            if hp is not None and hp.get("rate") is not None and p.get("rate") is not None:
                x[f"m{j}_1w"] = (p["rate"] - hp["rate"]) * 100
                segs += ["≈ " if p.get("est") else "", _n(f"why.market.x.m{j}_1w", "bp")]
            else:
                segs.append("n/a")
        segs.append(" bp")
        if pj.get("data_asof"):
            segs.append(f" · data {_day(pj['data_asof'])}" + (f" {pj['data_label']}" if pj.get("data_label") else ""))
        if ((row or {}).get("horizon") or {}).get("stale"):
            segs.append({"stale": f"older than {stale_bd} business days"})
        out["lines"].append(_L(*segs, title="; ".join(tips + [f"1 week ago = the snapshot of {w1.get('asof')} on the same meeting"
                                                              + (f" ({w1['na']})" if w1.get("na") else ""),
                                                              f"market data: {pj.get('data_label') or pj.get('data_source')}, {pj.get('data_asof')}"])))
    # the lens (v3): market - bank path at the same date
    lens = r.get("lens") or {}
    kind = lens.get("kind")
    badge = {"badge": lens["flag"]} if lens.get("flag") and lens["flag"] not in QUIET_FLAGS else ""
    if kind == "unpublished":
        out["lines"].append(_L("no bank rate path to compare"))
    elif kind == "own_path" and lens.get("gap_bp") is not None and lens.get("basis") == "gap":
        x.update(lens_m=lens.get("market"), lens_b=_pub(lens.get("bank")), lens_v=lens["gap_bp"])
        out["lines"].append(_L(f"vs dots, end-{lens['year']}: market ", _n("why.market.x.lens_m", "r3"), "% vs ", _n("why.market.x.lens_b", "pub"),
                               "% = ", _n("why.market.x.lens_v", "bp"), " bp", badge, title=lens.get("source")))
    elif kind == "own_path" and lens.get("gap_bp") is not None:
        x.update(lens_m=lens.get("market"), lens_b=_pub(lens.get("bank")), lens_v=lens["gap_bp"])
        out["lines"].append(_L(f"vs OCR track, {lens['year']}: market ", _n("why.market.x.lens_m", "r3"), "% (year-end) vs ",
                               _n("why.market.x.lens_b", "pub"), "% (Q4 average) = ", _n("why.market.x.lens_v", "bp"), " bp", badge,
                               title=lens.get("source")))
    elif kind == "market" and lens.get("delta_bp") is not None:
        x.update(lens_m=lens["rate_today_curve"], lens_b=lens["rate_cutoff_curve"], lens_v=lens["delta_bp"])
        out["lines"].append(_L(f"vs the {_day(lens['cutoff'])} curve, at {_date(lens['target_date'])}: ", _n("why.market.x.lens_m", "r3"), "% vs ",
                               _n("why.market.x.lens_b", "r3"), "% = ", _n("why.market.x.lens_v", "bp"), " bp", badge,
                               title=f"the implied rate at {lens['target_date']} on today's curve minus the same date on the curve of the forecast cutoff {lens['cutoff']}"))
    elif kind == "constant_rate" and lens.get("delta_bp") is not None:
        x.update(lens_m=lens["rate_today_curve"], lens_b=_pub(lens["assumed_rate"]), lens_v=lens["delta_bp"])
        out["lines"].append(_L(f"vs an unchanged rate, at {_date(lens['target_date'])}: ", _n("why.market.x.lens_m", "r3"), "% vs ",
                               _n("why.market.x.lens_b", "pub"), "% = ", _n("why.market.x.lens_v", "bp"), " bp", badge,
                               title=f"the implied rate at {lens['target_date']} on today's curve minus the rate the forecast assumes"))
    else:
        out["lines"].append(_L(f"vs bank path: n/a ({lens.get('na') or 'n/a'})"))
    if not x:
        del out["x"]
    return out
