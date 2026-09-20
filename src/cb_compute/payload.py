"""Phase 3a payloads (pure): the dictionaries behind /central-banks - overview, one per bank, one for the pairs. Every value is
computed by `analysis` / `engine`; this module only shapes it for the browser (JSON-ready, rounded, deterministic: nothing here
reads the clock or a file).

A metric that can be n/a is always `{"v": number | null, "flag": ..., "stale": bool, "na": reason | null, ...}`: the page shows
"—" with the reason as a tooltip when `v` is null. Signs: positive = hawkish (higher rates than the reference).
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..cb_calendar import compute_blackout
from ..cb_docs.expect import EXPECTED_LAG, GRACE_DAYS, TYPE_LABEL, VIDEO_NA
from .analysis import BankReport, PairRow, bank_report, pair_row
from .engine import Context, Point, STRENGTH, YEAR_ENDS, YearEnd, step_reason, weakest

ORDER = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
SHORT = {"USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ", "CAD": "BoC", "AUD": "RBA", "NZD": "RBNZ", "CHF": "SNB"}
WINDOW_KEYS = {"1s": "1w", "1l": "1m"}                         # 5 / 21 business days
BASE_URL = "/central-banks"
CHART_MONTHS_BACK = 13

FLAG_LABEL = {"EXACT": "EXACT", "CURVE": "CURVE", "UPPER_BOUND": "UPPER BOUND", "PROXY": "PROXY", "DECIDED": "DECIDED"}
FLAG_HELP = {
    "EXACT": "Solved exactly from 1M month-average futures: the rate changes only on the effective date, so the implied rate after the meeting follows from the monthly average.",
    "CURVE": "OIS forward curve averaged over the interval between effective dates, minus the overnight-to-policy spread.",
    "UPPER_BOUND": "3M contract window: the average covers several meetings, so the cumulative move is an upper bound for this meeting alone.",
    "PROXY": "Government-curve proxy - not policy-equivalent. Shown as a raw sovereign level unless a basis can be measured from a short tenor.",
    "DECIDED": "The meeting has already been decided: it is part of the base rate.",
}
LEVEL_HELP = {"policy": "implied policy rate", "bkbm": "BKBM base: level of the ASX 90-day bank bill, not the OCR - no BKBM-OCR spread exists, so no bp vs the OCR",
              "sovereign_proxy": "sovereign proxy: raw government-curve level, not policy-equivalent"}
LEVEL_LABEL = {"policy": "policy-equivalent", "bkbm": "BKBM base", "sovereign_proxy": "sovereign proxy"}

METHODOLOGY = [
    {"title": "Base rate", "text": "The last rate DECIDED on or before the as-of date, including a decision that has been announced but is not in force yet "
                                   "(shown as 'from <date>'). The Fed is the midpoint of the target range."},
    {"title": "Implied policy rate", "text": "Market instruments give the overnight benchmark path; the benchmark-to-policy spread (median of the last 20 business days, "
                                              "excluding +-2 business days around rate changes and month-ends) is removed to get a policy-equivalent rate."},
    {"title": "Methods", "text": "EXACT: 1M month-average futures chain. CURVE: OIS forward curve averaged between effective dates. UPPER BOUND: 3M contracts "
                                  "whose window spans several meetings. PROXY: government curves / bills, with a basis measured only from tenors observed before "
                                  "the first effective date - without one ('proxy without short end') no bp, step or probability is computed and only the raw level is shown."},
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
    return sum(1 for m in ctx.meetings.get(ccy, []) if m.decision > asof and m.eff < until)


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
    upcoming = [m for m in ctx.meetings.get(cur, []) if m.decision > asof]
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


def text_fragment(text: str) -> str:
    """A URL text fragment (#:~:text=) that makes the browser scroll to and highlight the quote on the bank's own page. Short quotes are matched whole,
    long ones by their first and last words; `-`, `,` and `&` are percent-encoded because they are the directive's own syntax."""
    from urllib.parse import quote
    enc = lambda t: quote(t, safe="").replace("-", "%2D")                            # noqa: E731
    words = text.split()
    if len(text) <= 120 or len(words) <= 10:
        return "#:~:text=" + enc(text)
    return "#:~:text=" + enc(" ".join(words[:5])) + "," + enc(" ".join(words[-5:]))


def summary_slot(ctx: Context, doc: Optional[dict]) -> dict:
    """The summary slot of one document: `ready` (the content is in documents.summaries[doc_id]) or `pending` with the reason for the tooltip. A summary that
    failed validation is never shown: its slot stays pending and says so."""
    if doc is None:
        return {"status": "pending", "label": "summary pending", "doc_id": None, "reason": "the document is not collected"}
    if doc["doc_id"] in ctx.summaries:
        return {"status": "ready", "label": "summary", "doc_id": doc["doc_id"], "reason": None}
    fail = next((f for k, f in sorted(ctx.summary_failures.items()) if k.startswith(doc["doc_id"] + "|")), None)
    if fail:
        return {"status": "pending", "label": "summary pending", "doc_id": doc["doc_id"],
                "reason": f"the generated summary failed the automatic check twice and is not shown ({fail['errors'][0]})"}
    return {"status": "pending", "label": "summary pending", "doc_id": doc["doc_id"], "reason": "not generated yet"}


def summary_json(rec: dict) -> dict:
    """What the page shows of a stored summary. The quotes carry a link to the bank's own page with a text fragment (HTML sources only: a PDF has none)."""
    html = rec.get("format") == "html"
    return {"doc_id": rec["doc_id"], "type": rec["type"], "label": TYPE_LABEL.get(rec["type"], rec["type"]), "title": rec["title"], "url": rec["url"],
            "points": rec["summary"], "changes": rec["changes_vs_previous"],
            "quotes": [{"text": q["text"], "paragraph": q["paragraph"], "href": rec["url"] + text_fragment(q["text"]) if html and "#" not in rec["url"] else rec["url"]}
                       for q in rec["quotes"]],
            "model": rec["model"], "prompt_version": rec["prompt_version"], "generated": rec["generated_at"][:10], "note": SUMMARY_NOTE,
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
    for d in sorted((d for d in mine if d["type"] in ("speech", "testimony") and d["published_date"] >= cutoff), key=lambda d: (d["published_date"], d["doc_id"]), reverse=True):
        meta = _json.loads(d["meta_json"]) if d["meta_json"] else {}
        speeches.append({"doc_id": d["doc_id"], "type": d["type"], "speaker": d["speaker"] or None, "role": d["role"] or None, "title": d["title"], "url": d["url"],
                         "published": iso(d["published_date"]), "relevance": d["relevance"], "weight": meta.get("weight", 3), "voter": bool(meta.get("voter")),
                         "chair": bool(meta.get("chair")), "via": meta.get("via", "bank"), "summary": summary_slot(ctx, d)})
    speeches = speeches[:30]
    shown = ({it["summary"]["doc_id"] for t in timeline for it in t["follow_up"] if it.get("summary", {}).get("status") == "ready"}
             | {t["statement"]["doc_id"] for t in timeline if t["statement"]} | {x["doc_id"] for x in speeches})
    summaries = {k: summary_json(ctx.summaries[k]) for k in sorted(shown) if k in ctx.summaries}          # only what this page shows
    return {"latest": latest, "timeline": timeline, "speeches": speeches, "n_speeches": len(speeches), "summaries": summaries, "summary_note": SUMMARY_NOTE,
            "manual_only": ccy == "NZD", "warnings": [w for w in ctx.doc_warnings if w.startswith(ccy + " ")]}


def calendar_json(ctx: Context, ccy: str, asof: date, limit: int = 12) -> list:
    bank = ctx.banks[ccy]
    proj = bank.get("projections") or {}
    conf = bank.get("conference") or {}
    rows = []
    for m in [m for m in ctx.meetings.get(ccy, []) if m.decision > asof][:limit]:
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
    return out


def chart_json(ctx: Context, rep: BankReport, asof: date) -> dict:
    return {"asof": iso(asof), "history": history_steps(ctx, rep.currency, asof), "market": [point_json(p) for p in rep.trajectory.points],
            "bank": bank_path_json(rep), "meetings": [{"decision": iso(m.decision), "effective": iso(m.eff)} for m in ctx.meetings.get(rep.currency, []) if m.decision > asof],
            "base": num(rep.trajectory.base.rate) if rep.trajectory.base else None}


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
                                     "policy_rate": (bank.get("policy_rate") or {}).get("definition"), "youtube": bank.get("youtube_channel_id")},
        "summary": row, "unverified": unverified, "spread": spread, "proxy_basis_bp": num((tr.extra.get("proxy_basis") or 0) * 100, 1) if tr.extra.get("proxy_basis") is not None else None,
        "trajectory": [point_json(p) for p in tr.points], "notes": list(tr.notes), "consistency": [{"source": c[0], "period": c[1], "dev_bp": num(c[2], 2), "detail": c[3]} for c in tr.consistency],
        "chart": chart_json(ctx, rep, asof), "next_card": {"horizon": row["horizon"], "next": row["next"]}, "horizons": horizons, "gap": row["gap"],
        "decisions": decision_rows(ctx, rep), "documents": documents_json(ctx, rep.currency, asof, [x.meeting for x in reversed([x for x in rep.surprises if x.decided][-4:])]),
        "calendar": calendar_json(ctx, rep.currency, asof), "sources": sources_json(ctx, rep.currency, asof),
        "crosschecks": [{"name": c.name, "period": c.period, "primary": num(c.a), "second": num(c.b), "diff_bp": num(c.diff_bp, 2), "note": c.note, "na": c.na_reason or None}
                        for c in rep.crosschecks],
        "meta": meta(ctx, asof),
    }


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
    pairs = [pair_json(pair_row(n, reports[b], reports[q]), disp) for n, b, q, disp in pair_defs if b in reports and q in reports]
    m = meta(ctx, asof)
    return {
        "overview": {"meta": m, "banks": rows},
        "banks": {c: bank_page(ctx, reports[c], asof) for c in reports},
        "pairs": {"meta": m, "pairs": pairs, "banks": {c: {"short": SHORT.get(c), "href": bank_href(c)} for c in reports}},
    }
