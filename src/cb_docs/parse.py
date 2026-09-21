"""Pure parsers over the extracted official texts (phase 2a): the rate of a statement, the votes, the word-level redline against the
previous statement, the relevance filter of speeches, the bank <-> BIS de-duplication and the press-conference video match.

Nothing here reads a file or a URL. A parser that cannot find what it looks for returns None / a `not parsed` reason: it never guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

DASHES = "‐‑‒–—−"
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
VERB_DIR = {"maintain": "hold", "maintained": "hold", "held": "hold", "hold": "hold", "leave": "hold", "leaving": "hold", "unchanged": "hold",
            "raise": "raise", "raised": "raise", "raising": "raise", "increase": "raise", "increased": "raise", "increasing": "raise",
            "lower": "lower", "lowered": "lower", "lowering": "lower", "reduce": "lower", "reduced": "lower", "reducing": "lower",
            "decrease": "lower", "decreased": "lower", "cut": "lower", "cutting": "lower"}
MAX_STEP_BP = 100.0                 # a statement that moves the rate more than this is not believed without an official series
RATE_RANGE = (-1.0, 15.0)


def norm(text: str) -> str:
    """Typographic dashes -> '-', non-breaking spaces -> ' ', spaces collapsed (statement numbers use U+2011 hyphens)."""
    t = text.replace(" ", " ")
    for d in DASHES:
        t = t.replace(d, "-")
    return re.sub(r"[ \t]+", " ", t)


def _num(s: str) -> float:
    """'3-1/2' -> 3.5, '3-3/4' -> 3.75, '4' -> 4.0, '0.25' -> 0.25."""
    s = s.strip()
    m = re.fullmatch(r"(\d+)-(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)/(\d+)", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    return float(s)


# ---------------------------------------------------------------------------------------------------------------------------
# The rate of a statement
# ---------------------------------------------------------------------------------------------------------------------------

@dataclass
class RateParse:
    rate: float                                 # the bank's convention: Fed midpoint, ECB deposit facility, BoE Bank Rate, ...
    direction: str                              # raise | lower | hold (what the sentence says)
    evidence: str
    lower: Optional[float] = None               # Fed range
    upper: Optional[float] = None
    effective: Optional[date] = None            # when the statement itself says so (BoJ footnote)


FRAC = r"\d+(?:-\d+/\d+|\.\d+)?"
RATE_RX = {
    "USD": re.compile(rf"decided to (?P<verb>maintain|raise|lower|reduce|increase|cut)\b[^.]*?target range for the federal funds rate "
                      rf"(?:at|by [\d/ ]+ percentage points? to) (?P<lo>{FRAC}) to (?P<hi>{FRAC}) percent", re.I),
    "EUR": re.compile(r"(?P<lead>(?:decided to (?P<verb>raise|lower|reduce|increase|cut)[^.]*?\. )?Accordingly, )?the interest rates? on the deposit facility[^.]*?"
                      r"will (?:(?P<same>remain unchanged at)|be (?P<v2>increased|decreased|reduced|raised|lowered|cut) to) (?P<r>\d+\.\d+)%", re.I),
    "GBP": re.compile(r"voted (?:by a majority of \d+\s*-\s*\d+|unanimously) to (?P<verb>maintain|increase|reduce|cut|lower|raise) Bank Rate "
                      r"(?:at|by [\d.]+ percentage points?,? to) (?P<r>-?\d+(?:\.\d+)?)%", re.I),
    "JPY": re.compile(r"(?:call rate|guideline)[^.]*?to remain at around (?P<r>-?\d+(?:\.\d+)?) percent", re.I),
    "CAD": re.compile(r"(?P<verb>held|raised|lowered) its target for the overnight rate (?:at|by [\d.]+ (?:basis points?|percentage points?) to) "
                      r"(?P<r>-?\d+(?:\.\d+)?)%", re.I),
    "AUD": re.compile(r"decided to (?P<verb>leave|increase|reduce|raise|lower|decrease|cut) the cash rate target "
                      r"(?:unchanged at|by [\d.]+ basis points? to) (?P<r>-?\d+\.\d+) per cent", re.I),
    "CHF": re.compile(r"(?P<verb>leaving|raising|lowering|reducing|increasing|cutting) the SNB policy rate (?:unchanged )?"
                      r"(?:at|by [\d.]+ percentage points? to|to) (?P<r>-?\d+(?:\.\d+)?)%", re.I),
}
BOJ_EFFECTIVE = re.compile(r"guideline for money market operations will be effective from (?P<d>[A-Z][a-z]+ \d{1,2}, \d{4})")


def parse_rate(ccy: str, text: str) -> Optional[RateParse]:
    """The policy rate announced in the statement text, or None (RBNZ, an unrecognised wording)."""
    rx = RATE_RX.get(ccy)
    if rx is None:
        return None
    t = norm(text).replace("\n", " ")
    m = rx.search(t)
    if m is None:
        return None
    g = m.groupdict()
    ev = m.group(0)[:300]
    if ccy == "USD":
        lo, hi = _num(g["lo"]), _num(g["hi"])
        return RateParse((lo + hi) / 2, VERB_DIR[g["verb"].lower()], ev, lo, hi)
    if ccy == "EUR":
        verb = (g.get("verb") or g.get("v2") or "").lower()
        direction = "hold" if g.get("same") else VERB_DIR.get(verb, "hold")
        return RateParse(float(g["r"]), direction, ev)
    if ccy == "JPY":
        eff = None
        mm = BOJ_EFFECTIVE.search(t)
        if mm:
            try:
                eff = datetime.strptime(mm.group("d"), "%B %d, %Y").date()
            except ValueError:
                eff = None
        return RateParse(float(g["r"]), "hold", ev, effective=eff)                # direction is decided against the previous rate
    verb = (g.get("verb") or "").lower()
    return RateParse(float(g["r"]), VERB_DIR.get(verb, "hold"), ev)


def validate_rate(p: RateParse, prev_rate: Optional[float], ccy: str = "") -> tuple:
    """(ok, reason): an implausible parse is rejected, never repaired. `prev_rate` = the rate before the decision, in the same convention."""
    if not (RATE_RANGE[0] <= p.rate <= RATE_RANGE[1]):
        return False, f"rate {p.rate:g} outside {RATE_RANGE[0]:g}..{RATE_RANGE[1]:g}"
    if ccy == "USD" and p.lower is not None and abs((p.upper - p.lower) - 0.25) > 1e-9:
        return False, f"Fed range {p.lower:g}-{p.upper:g} is not 25 bp wide"
    if prev_rate is None:
        return True, ""
    delta = (p.rate - prev_rate) * 100
    if abs(delta) > MAX_STEP_BP:
        return False, f"step {delta:+.0f} bp from {prev_rate:g} exceeds {MAX_STEP_BP:.0f} bp"
    if ccy != "JPY":                                                            # BoJ says only where the guideline stays
        if p.direction == "hold" and abs(delta) > 0.5:
            return False, f"the text says the rate is unchanged but it differs from the previous {prev_rate:g} by {delta:+.0f} bp"
        if p.direction == "raise" and delta <= 0:
            return False, f"the text says raise but the rate moved {delta:+.0f} bp from {prev_rate:g}"
        if p.direction == "lower" and delta >= 0:
            return False, f"the text says lower but the rate moved {delta:+.0f} bp from {prev_rate:g}"
    return True, ""


# ---------------------------------------------------------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------------------------------------------------------

@dataclass
class Votes:
    kind: str                                   # counted | unanimous | consensus | not_published
    n_for: Optional[int] = None
    n_against: Optional[int] = None
    for_names: list = field(default_factory=list)
    against: list = field(default_factory=list)          # [{"name", "direction": raise|lower|hold, "note"}]
    evidence: str = ""
    source: str = ""                                     # statement | summary | minutes | xlsx
    notes: list = field(default_factory=list)


NOT_PUBLISHED = {"EUR": "not published (decisions by consensus)", "CAD": "not published (Governing Council decides by consensus)",
                 "CHF": "not published", "NZD": "consensus"}


def _split_names(s: str) -> list:
    s = re.sub(r"\band\b", ",", s)
    out = []
    for part in re.split(r"[;,]", s):
        p = part.strip().strip(".")
        if p:
            out.append(p)
    return out


def _direction(desc: str) -> str:
    """What a dissenter wanted, read from the explanation: hold (maintain / not raise now), raise or lower. Ordered so that a passing
    'rate of increase in the CPI' does not read as a wish to raise."""
    d = desc.lower()
    if re.search(r"(?:maintain|keep|leave|hold)\w*\b.{0,60}(?:guideline|rate|unchanged|target)|unchanged|not (?:appropriate|desirable|necessary)[^.]{0,80}(?:rais|increas|hik)", d):
        return "hold"
    if re.search(r"(?:prefer\w*|appropriate|desirable|necessary|proposed|would have|should|supported)\b.{0,40}?(?:rais|increas|hik)", d):
        return "raise"
    if re.search(r"(?:prefer\w*|appropriate|desirable|necessary|proposed|would have|should|supported)\b.{0,40}?(?:lower|reduc|cut)", d):
        return "lower"
    return "hold"


def parse_votes_fed(text: str) -> Optional[Votes]:
    t = norm(text)
    head = re.search(r"by an? (\d+)\s*-\s*(\d+) vote", t)
    mf = re.search(r"Voting for the monetary policy action were (?P<f>.+?)\. Voting against", t, re.S) or re.search(r"Voting for the monetary policy action were (?P<f>.+?)\.(?:\s|$)", t, re.S)
    if head is None and mf is None:
        return None
    for_names = []
    if mf:
        for part in re.split(r";", mf.group("f")):
            nm = re.sub(r"^\s*and\s+", "", part).split(",")[0].strip()
            if nm:
                for_names.append(nm)
    against = []
    ma = re.search(r"Voting against (?:this|the monetary policy) action (?:was|were) (?P<a>.+?)(?:\n|$)", t, re.S)
    if ma:
        for clause in re.split(r";\s*(?:and\s+)?", ma.group("a").strip().rstrip(".")):
            m2 = re.match(r"(?P<names>.+?), who (?P<desc>.+)$", clause.strip())
            if not m2:
                continue
            note = m2.group("desc")
            for nm in _split_names(m2.group("names")):
                against.append({"name": nm, "direction": _direction(note), "note": note if "easing bias" in note else ""})
    n_for = int(head.group(1)) if head else len(for_names)
    n_against = int(head.group(2)) if head else len(against)
    return Votes("unanimous" if n_against == 0 else "counted", n_for, n_against, for_names, against, (head.group(0) if head else ""), "statement",
                 [] if len(for_names) in (0, n_for) and len(against) in (0, n_against) else [f"names ({len(for_names)}-{len(against)}) differ from the count"])


def given_first(name: str) -> str:
    """The BoJ writes "UEDA Kazuo": shown, like everywhere else (and in the roster), as "Kazuo Ueda"."""
    m = re.fullmatch(r"([A-Z]{2,}) ([A-Z][a-z]+)", name.strip())
    return f"{m.group(2)} {m.group(1).title()}" if m else name


def parse_votes_boj(text: str) -> Optional[Votes]:
    """Votes of the BoJ statement. A dissenter who proposed another guideline ("They proposed ... around 1.0 percent") is compared with the
    decided one (raise / lower / hold); one who only argued to maintain it is 'hold'."""
    t = norm(text).replace("\n", " ")
    m = re.search(r"by an? (\d+)\s*-\s*(\d+) majority vote", t)
    unanimous = re.search(r"by a unanimous vote", t) is not None
    if m is None and not unanimous:
        return None
    n_for, n_against = (int(m.group(1)), int(m.group(2))) if m else (None, 0)
    decided = parse_rate("JPY", t)
    mf = re.search(r"Voting for the action:\s*(?P<f>.+?)\.\s*(?:Voting against the action:|Absent:)", t) or re.search(r"Voting for the action:\s*(?P<f>.+?)\.\s", t)
    for_names = _split_names(mf.group("f")) if mf else []
    against, notes = [], []
    ma = re.search(r"Voting against the action:\s*(?P<a>.+?)\.\s", t)
    absent = re.search(r"Absent:\s*(?P<a>.+?)\.\s", t)
    if absent:
        notes.append("absent: " + absent.group("a"))
    if ma:
        names = _split_names(ma.group("a"))
        proposals = [(mm.start(), mm.group(1), float(mm.group("r"))) for mm in re.finditer(r"(They|He|She) proposed that the Bank set the guideline[^.]*?remain at around (?P<r>-?\d+(?:\.\d+)?) percent", t)]
        for k, nm in enumerate(names):
            sur = nm.split()[0].capitalize()
            md = re.search(rf"{re.escape(sur)}\b[^.]*?considered,? (?P<why>.*?)(?=\s[A-Z][a-z]+ [A-Z][a-z]+ (?:considered|dissented)|\s(?:They|He|She) proposed|\sReference|\[|$)", t) or \
                 re.search(rf"{re.escape(sur)}\b[^.]*?dissented,? (?P<why>.*?)(?=\s[A-Z][a-z]+ [A-Z][a-z]+ (?:considered|dissented)|\s(?:They|He|She) proposed|\sReference|\[|$)", t)
            why = md.group("why") if md else ""
            direction = _direction(why) if why else "hold"
            pr = next((p for p in proposals if md and p[0] > md.start() and (p[1] == "They" or k == len(names) - 1)), None)
            if pr is not None and decided is not None:
                direction = "raise" if pr[2] > decided.rate + 1e-9 else "lower" if pr[2] < decided.rate - 1e-9 else "hold"
                why += f" [proposed {pr[2]:g}%]"
            against.append({"name": nm, "direction": direction, "note": why[:400]})
    if n_for is None:
        n_for = len(for_names)
    for a in against:
        a["name"] = given_first(a["name"])
    notes = [re.sub(r"[A-Z]{2,} [A-Z][a-z]+", lambda x: given_first(x.group(0)), n) for n in notes]
    return Votes("unanimous" if n_against == 0 else "counted", n_for, n_against, [given_first(n) for n in for_names], against,
                 (m.group(0) if m else "by a unanimous vote"), "statement", notes)


def parse_votes_boe_text(text: str) -> Optional[Votes]:
    t = norm(text).replace("\n", " ")
    m = re.search(r"voted (?:by a majority of (\d+)\s*-\s*(\d+)|(unanimously))", t)
    if m is None:
        return None
    if m.group(3):
        return Votes("unanimous", None, 0, [], [], m.group(0), "summary")
    return Votes("counted", int(m.group(1)), int(m.group(2)), [], [], m.group(0), "summary")


def apply_boe_workbook(v: Votes, member_prefs: dict, decided_rate: float) -> Votes:
    """Names and directions from mpcvoting.xlsx: member -> preferred Bank Rate (percent) at that meeting. The count in the summary and the
    count from the workbook must agree, otherwise the disagreement is recorded (the summary's count stays)."""
    votes_for = sorted(n for n, r in member_prefs.items() if r is not None and abs(r - decided_rate) < 1e-6)
    against = [{"name": n, "direction": "raise" if r > decided_rate else "lower", "note": f"preferred Bank Rate {r:g}%"}
               for n, r in sorted(member_prefs.items()) if r is not None and abs(r - decided_rate) >= 1e-6]
    v.for_names, v.against = votes_for, against
    v.source = "summary+xlsx"
    if v.n_for is not None and (len(votes_for), len(against)) != (v.n_for, v.n_against):
        v.notes.append(f"workbook count {len(votes_for)}-{len(against)} differs from the summary {v.n_for}-{v.n_against}")
    if v.n_for is None:
        v.n_for, v.n_against = len(votes_for), len(against)
    return v


def parse_votes_rba(text: str) -> Optional[Votes]:
    t = norm(text).replace("\n", " ")
    if re.search(r"(?:Board|Monetary Policy Board) decided unanimously", t):
        return Votes("unanimous", None, 0, [], [], "The Board decided unanimously", "minutes")
    m = re.search(r"(?P<a>\w+) members? voted (?:in favour|to (?P<v1>\w+)[^;]*); (?P<b>\w+) members? voted to (?P<v2>\w+)", t)
    if m is None:
        return None
    a, b = NUMBER_WORDS.get(m.group("a").lower()), NUMBER_WORDS.get(m.group("b").lower())
    if a is None or b is None:
        return None
    dissent = VERB_DIR.get(m.group("v2").lower(), "hold")
    return Votes("counted", a, b, [], [{"name": "", "direction": dissent, "note": f"{b} member(s) voted to {m.group('v2')}"}], m.group(0), "minutes")


def parse_votes(ccy: str, text: str) -> Optional[Votes]:
    """From the statement (Fed, BoJ, BoE summary, RBA majority sentence) - or the published `not published` / `consensus` kinds."""
    if ccy == "USD":
        return parse_votes_fed(text)
    if ccy == "JPY":
        return parse_votes_boj(text)
    if ccy == "GBP":
        return parse_votes_boe_text(text)
    if ccy == "AUD":
        return parse_votes_rba(text)
    if ccy in NOT_PUBLISHED:
        return Votes("consensus" if ccy == "NZD" else "not_published", None, None, [], [], NOT_PUBLISHED[ccy], "n/a")
    return None


# ---------------------------------------------------------------------------------------------------------------------------
# Redline
# ---------------------------------------------------------------------------------------------------------------------------

def _words(p: str) -> list:
    return re.findall(r"\S+\s*", p)


def word_ops(a: str, b: str) -> list:
    """Word-level diff of two paragraphs: [["=", text], ["-", text], ["+", text]] - joining the '=' and '+' parts gives `b`."""
    wa, wb = _words(a), _words(b)
    sm = SequenceMatcher(None, [w.strip() for w in wa], [w.strip() for w in wb], autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(["=", "".join(wb[j1:j2])])
        else:
            if i2 > i1:
                ops.append(["-", "".join(wa[i1:i2])])
            if j2 > j1:
                ops.append(["+", "".join(wb[j1:j2])])
    return ops


def redline(prev: list, cur: list, pair_threshold: float = 0.4) -> dict:
    """Paragraph-anchored word diff of a statement against the previous one of the same bank.

    Returns {"paras": [{"p": index in `cur` or None, "prev": index in `prev` or None, "kind": same | changed | added | removed, "ops": [...]}],
    "added_words", "removed_words", "unchanged_words", "similarity"}. `same` paragraphs carry no ops (the current text is stored elsewhere)."""
    sm = SequenceMatcher(None, prev, cur, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out += [{"p": j, "prev": i, "kind": "same", "ops": []} for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        a_idx, b_idx = list(range(i1, i2)), list(range(j1, j2))
        used = set()
        for j in b_idx:                                                            # pair each new paragraph with its closest old one, in order
            best, best_r = None, pair_threshold
            for i in a_idx:
                if i in used:
                    continue
                r = SequenceMatcher(None, prev[i], cur[j], autojunk=False).ratio()
                if r > best_r:
                    best, best_r = i, r
            if best is None:
                out.append({"p": j, "prev": None, "kind": "added", "ops": [["+", cur[j]]]})
            else:
                used.add(best)
                out.append({"p": j, "prev": best, "kind": "changed", "ops": word_ops(prev[best], cur[j])})
        out += [{"p": None, "prev": i, "kind": "removed", "ops": [["-", prev[i]]]} for i in a_idx if i not in used]
    add = sum(len(o[1].split()) for x in out for o in x["ops"] if o[0] == "+")
    rem = sum(len(o[1].split()) for x in out for o in x["ops"] if o[0] == "-")
    same = sum(len(cur[x["p"]].split()) for x in out if x["kind"] == "same") + sum(len(o[1].split()) for x in out if x["kind"] == "changed" for o in x["ops"] if o[0] == "=")
    total = max(add + rem + 2 * same, 1)
    return {"paras": out, "added_words": add, "removed_words": rem, "unchanged_words": same, "similarity": round(2 * same / total, 4)}


def apply_redline(prev: list, rl: dict) -> list:
    """Rebuild the current paragraphs from the previous ones and a redline (round-trip check used by the tests)."""
    cur = []
    for x in rl["paras"]:
        if x["p"] is None:
            continue
        if x["kind"] == "same":
            cur.append(prev[x["prev"]])
        else:
            cur.append("".join(o[1] for o in x["ops"] if o[0] in ("=", "+")))
    return cur


# ---------------------------------------------------------------------------------------------------------------------------
# Speeches: relevance, de-duplication, weight
# ---------------------------------------------------------------------------------------------------------------------------

MONETARY_RX = re.compile(r"monetary|inflation|interest rate|policy rate|rates?\b|price stability|prices?\b|economic outlook|the economy|economic|outlook|"
                         r"labou?r market|growth|financial conditions|balance sheet|central bank|policy", re.I)
NON_MONETARY_RX = re.compile(r"payments?|stablecoin|crypto|digital (?:euro|currency|yen|dollar)|cyber|fintech|regulation|supervis|diversity|climate|"
                             r"cash (?:usage|access)|banknote|museum|history of|award|ceremony|tribute|farewell|opening remarks at the conference", re.I)


NON_DOCUMENT_RX = re.compile(r"^\s*media availability\b", re.I)


def is_non_document(title: str, url: str = "") -> bool:
    """A feed item that is an announcement, not a text: the BoC's "Media availability: <Governor speaks at ...>" pages (a time, a place, a topic - no speech).
    Kept in the store but marked `non_document`: not shown as a speech, never summarised."""
    return bool(NON_DOCUMENT_RX.match(title)) or "/media-availability" in url


def monetary_relevance(title: str, first_paragraph: str = "") -> str:
    """'monetary' when the title or the opening paragraph speaks about policy / inflation / the outlook, else 'other' (kept, marked)."""
    t = f"{title} {first_paragraph[:600]}"
    if MONETARY_RX.search(title) and not (NON_MONETARY_RX.search(title) and not re.search(r"monetary|inflation|interest rate|outlook", title, re.I)):
        return "monetary"
    if MONETARY_RX.search(t) and not NON_MONETARY_RX.search(title):
        return "monetary"
    return "other"


def surname(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return parts[-1].lower().strip(".,") if parts else ""


def normalise_title(t: str) -> str:
    t = re.sub(r"^\d{4}-\d{2}-\d{2} - ", "", t)
    t = re.sub(r"^.*?[,:]\s+", "", t, count=1) if re.match(r"^[^,:]{3,40}[,:]\s", t) else t
    return re.sub(r"\W+", " ", t).strip().lower()


def same_speech(own: dict, bis: dict, threshold: float = 0.6) -> bool:
    """Bank feed item vs BIS item: the speaker's surname appears in the BIS author / description AND the titles are similar (>= 0.6).
    The BIS date is the posting date, 13-19 days after the speech: it is NOT part of the key."""
    last = surname(own.get("speaker", ""))
    if not last:
        return False
    pool = f"{bis.get('author', '')} {bis.get('desc', '')}".lower()
    if last not in pool:
        return False
    return SequenceMatcher(None, normalise_title(own["title"]), normalise_title(bis["title"])).ratio() >= threshold


def speaker_weight(role: str, is_voter: bool, chair: bool = False) -> int:
    """Chair (1) > voters (2) > the rest (3)."""
    return 1 if chair else 2 if is_voter else 3


# ---------------------------------------------------------------------------------------------------------------------------
# Press conference video
# ---------------------------------------------------------------------------------------------------------------------------

PRESS_RX = re.compile(r"press conference|media conference|news conference|conf\u00e9rence de presse|mediengespr\u00e4ch|\u8a18\u8005\u4f1a\u898b|introductory statement|"
                      r"pooled broadcast|monetary policy (?:decision|statement)|policy decision|rate decision", re.I)


def match_video(videos: list, meeting: date, tolerance_days: int = 1) -> Optional[dict]:
    """The official video of the press conference: a press-conference title published within +-1 day of the meeting date (the earliest one)."""
    cands = [v for v in videos if v.get("pub") and PRESS_RX.search(v.get("title", "")) and abs((v["pub"].date() - meeting).days) <= tolerance_days]
    return min(cands, key=lambda v: v["pub"]) if cands else None


# ---- press-conference video on the bank's own pages -------------------------------------------------------------------------------

def video_from_page(ccy: str, html: str, page_url: str = "") -> Optional[dict]:
    """The press-conference video a bank's OWN page carries: {url, player, id, duration}. USD: the FOMC page embeds a Brightcove player (the page is the
    link); CAD: the press release links the /multimedia/ page; AUD: the transcript page links the video; GBP: the Monetary Policy Report page embeds it."""
    if ccy == "USD":
        m = re.search(r'data-video-id="(\d+)"[^>]*data-account="(\d+)"', html)
        return {"url": page_url, "player": "Brightcove", "id": m.group(1), "duration": None} if m else None
    if ccy == "CAD":
        m = re.search(r'href="(https://www\.bankofcanada\.ca/multimedia/press-conference[^"]+)"', html)
        return {"url": m.group(1), "player": "YouTube (bank page)", "id": None, "duration": None} if m else None
    if ccy == "AUD":
        tag = re.search(r'<a\s[^>]*class="[^"]*video-placeholder[^"]*"[^>]*>', html)
        href = re.search(r'href="(https://(?:youtu\.be/|www\.youtube\.com/watch\?v=)([\w-]{11}))"', tag.group(0)) if tag else None
        dur = re.search(r'data-duration="([^"]*)"', tag.group(0)) if tag else None
        return {"url": href.group(1), "player": "YouTube", "id": href.group(2), "duration": dur.group(1) if dur else None} if href else None
    if ccy == "GBP":
        i = html.find('id="press-conference"')
        m = re.search(r'data-video="([\w-]{11})"', html[i:i + 2000]) if i >= 0 else None
        return {"url": f"https://www.youtube.com/watch?v={m.group(1)}", "player": "YouTube", "id": m.group(1), "duration": None} if m else None
    return None


def ecb_landing_video(html: str) -> Optional[tuple]:
    """(date of the meeting, YouTube id) from the ECB press-conference landing page: it shows the last conference (`ecb.is{yymmdd}~` = its statement
    with Q&A) and its video. Older conferences are not on it - they are captured on the day they are current."""
    dates = re.findall(r"ecb\.is(\d{6})~", html)
    vid = re.search(r'<div data-video="([\w-]{11})"', html)
    if not dates or not vid:
        return None
    return datetime.strptime(dates[0], "%y%m%d").date(), vid.group(1)
