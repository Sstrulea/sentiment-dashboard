"""Collector of the official texts (phase 2a, no AI): statements (text + rate + votes), minutes / summaries, press-conference video and
transcript links, speeches and testimony (bank feeds + BIS for backfill and de-duplication), redlines against the previous statement.

Polite by construction (http.py): robots.txt, conditional GET, per-host rate limit, no workaround of a block. Idempotent: a document whose
extracted-text sha256 did not change is not rewritten. Stage `documents` of cb_collect, after `decisions`.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import yaml

from .. import cb_datasets as ds
from ..cb_probe import _dom, _feed_items, _norm, _paras, _pick
from . import extract as X
from . import parse as P
from . import sources as S
from . import store as ST
from .http import Fetched, Fetcher

STATE_KEY = "http_validators"
VOTE_RANK = {"summary+xlsx": 5, "minutes": 4, "statement": 3, "summary": 2, "n/a": 1}
SPEECH_DAYS = 75                     # speeches / testimony younger than this are (re)collected
MAX_PAGES_PER_BANK = 12              # first-paragraph fetches for the relevance filter, per bank and run
MANUAL_FILE = "documents.yaml"                  # documents that cannot be fetched (RBNZ is behind a Cloudflare challenge; YouTube feeds are disallowed by robots.txt)


@dataclass
class DocsReport:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    by_type: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)              # [(what, why)]
    notes: list = field(default_factory=list)
    statement_rates: int = 0
    rate_rows_changed: bool = False                          # a statement rate is new / different: decisions must be recomputed
    votes: int = 0
    redlines: int = 0
    requests: int = 0
    written: dict = field(default_factory=dict)


def _ts(d: Optional[datetime]) -> Optional[datetime]:
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


class _Run:
    def __init__(self, paths, today: date, fetcher: Fetcher, now: datetime, n: int, banks: tuple, roster: Optional[dict]) -> None:
        self.paths, self.today, self.f, self.now, self.n, self.banks = paths, today, fetcher, now, n, banks
        self.rep = DocsReport()
        self.docs = ST.load_documents(paths)
        self.cfg = ds.load_banks()
        meetings = ds.load_meetings(paths.meetings)
        self.meet = {c: sorted(m["date"] for m in rows if m["date"] <= today)[-n:] for c, rows in meetings.items()}
        self.dec = {(r["currency"], r["meeting_date"]): r for r in ds.load_decisions(paths)}
        self.roster = roster or {}
        self.texts: dict = {}                                  # (ccy, date) -> statement text
        self.votes = {(r["currency"], r["meeting_date"]): r for r in ST.load_votes(paths)}
        self.feeds: dict = {}

    # ---- plumbing -----------------------------------------------------------------------------------------------------------
    def fail(self, what: str, why: str) -> None:
        self.rep.failed.append((what, why))

    def get(self, url: str, what: str, conditional: bool = True) -> Optional[Fetched]:
        r = self.f.get(url, conditional=conditional)
        if r.error:
            self.fail(what, f"{r.error} {url}")
            return None
        return r

    def add(self, row: dict) -> str:
        key = (row["doc_id"],)
        old = self.docs.get(key)
        if old is not None:
            row["first_seen_at"] = old["first_seen_at"]
            if all(old[c] == row[c] for c in ST.CONTENT):
                self.rep.unchanged += 1
                return "unchanged"
        self.docs[key] = row
        self.rep.by_type[row["type"]] = self.rep.by_type.get(row["type"], 0) + 1
        if old is None:
            self.rep.new += 1
            return "new"
        self.rep.updated += 1
        return "updated"

    def base(self, ccy: str, type: str, doc_id: str, **kw) -> dict:
        bank = self.cfg[ccy]["id"]
        return ST.doc_row(doc_id=doc_id, currency=ccy, bank=bank, type=type, first_seen=ST.now_utc() if self.now is None else _ts(self.now),
                          license_note=S.LICENSE.format(bank=S.BANK_NAME[ccy]), **kw)

    def extract(self, r: Fetched, url: str, rule: Optional[dict]) -> tuple:
        """(paragraphs, method, format)"""
        if url.lower().endswith(".pdf") or "pdf" in r.headers.get("Content-Type", "").lower():
            return X.pdf_paragraphs(r.content), X.METHOD_PDF, "pdf"
        return (X.html_paragraphs(r.text, rule) if rule else []), X.METHOD_HTML, "html"

    # ---- feeds --------------------------------------------------------------------------------------------------------------
    def feed(self, url: str, what: str) -> list:
        if url not in self.feeds:
            r = self.f.get(url, conditional=False)                              # a 304 carries no items: feeds are small, always read in full
            if r.error:
                self.fail(what, f"{r.error} {url}")
            self.feeds[url] = _feed_items(r.content) if r.ok else []
        return self.feeds[url]

    def discover(self) -> dict:
        """Links that cannot be derived from a date: ECB statements from the press feed, RBA media releases from the decisions page."""
        links: dict = {"EUR": {}, "AUD": {}}
        for it in self.feed(S.ECB_PRESS_FEED, "ECB press feed"):
            m = re.search(r"ecb\.mp(\d{6})~", it["link"])
            if m:
                links["EUR"][datetime.strptime(m.group(1), "%y%m%d").date()] = it["link"]
        if "AUD" in self.banks:
            r = self.get(f"https://www.rba.gov.au/monetary-policy/int-rate-decisions/{self.today.year}/", "RBA decisions page")
            if r:
                for li in re.findall(r"<li[^>]*>(.*?)</li>", r.text, flags=re.S):
                    m = re.search(r"href=\"(/media-releases/[^\"]+)\"[^>]*>\s*(\d{1,2} [A-Z][a-z]+ \d{4})", li, flags=re.S)
                    if m:
                        links["AUD"][datetime.strptime(m.group(2), "%d %B %Y").date()] = "https://www.rba.gov.au" + m.group(1)
        return links

    # ---- statements ---------------------------------------------------------------------------------------------------------
    def statement(self, ccy: str, d: date, links: dict) -> None:
        doc_id = f"{ccy}:statement:{d.isoformat()}"
        old = self.docs.get((doc_id,))
        if old is not None and old["text"] and (self.today - d).days > 14:            # a statement never changes: no request
            self.texts[(ccy, d)] = old["text"]
            self.rep.unchanged += 1
            return
        url = S.statement_url(ccy, d, links)
        if url is None:
            self.fail(f"{ccy} statement {d}", "URL not derivable (hash) and not in the feed yet")
            return
        r = self.get(url, f"{ccy} statement {d}")
        if r is None:
            return
        if r.not_modified and old is not None and old["text"]:
            self.texts[(ccy, d)] = old["text"]
            self.rep.unchanged += 1
            return
        paras, method, fmt = self.extract(r, url, X.STATEMENT_RULES.get(ccy))
        if not paras:
            self.fail(f"{ccy} statement {d}", "no text extracted: the container was not found (page layout changed?)")
            return
        text = X.to_text(paras)
        rate_kw = {}
        rp = P.parse_rate(ccy, text)
        if rp is None:
            self.fail(f"{ccy} statement {d} rate", "rate sentence not recognised: no rate taken from this statement")
        else:
            prev = self.dec.get((ccy, d), {}).get("rate_before")
            if prev is None:
                prev = next((self.docs[k]["rate_after"] for k in sorted(self.docs, key=lambda k: self.docs[k]["published_date"], reverse=True)
                             if self.docs[k]["currency"] == ccy and self.docs[k]["type"] == "statement" and self.docs[k]["published_date"] < d
                             and self.docs[k]["rate_after"] is not None), None)
            ok, why = P.validate_rate(rp, prev, ccy)
            if ok:
                rate_kw = dict(rate_after=round(rp.rate, 6), rate_lower=rp.lower, rate_upper=rp.upper, rate_note=rp.evidence[:200])
                self.rep.statement_rates += 1
            else:
                self.fail(f"{ccy} statement {d} rate", f"rejected: {why}")
        pub = next((it["pub"] for it in self.feed_pool(ccy) if url.rsplit("/", 1)[-1].split("~")[0] in it["link"]), None)
        row = self.base(ccy, "statement", doc_id, title=f"{S.BANK_NAME[ccy]} monetary policy statement, {d:%d %B %Y}", url=url, published_date=d,
                        published_at=_ts(pub), meeting_date=d, lang="en", format=fmt, text_sha256=X.sha256(text), extraction_method=method, text=text, **rate_kw)
        before = (self.docs.get((doc_id,)) or {}).get("rate_after")
        self.add(row)
        if rate_kw and before != rate_kw["rate_after"]:
            self.rep.rate_rows_changed = True
        self.f.remember(r)
        self.texts[(ccy, d)] = text

    def feed_pool(self, ccy: str) -> list:
        return {"EUR": self.feeds.get(S.ECB_PRESS_FEED, [])}.get(ccy, [])

    # ---- votes --------------------------------------------------------------------------------------------------------------
    def boe_workbook(self) -> dict:
        """{meeting date: {member: preferred Bank Rate %}} from mpcvoting.xlsx (only the rows of the meetings that matter)."""
        r = self.get(S.BOE_VOTING_XLSX, "BoE mpcvoting.xlsx")
        if r is None:
            return {}
        try:
            import io

            import openpyxl
            ws = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)["Bank Rate Decisions"]
            rows = list(ws.iter_rows(values_only=True))
        except Exception as e:                                             # a changed workbook layout must not break the run
            self.fail("BoE mpcvoting.xlsx", f"unreadable: {type(e).__name__}")
            return {}
        hdr = next((i for i, r_ in enumerate(rows) if r_ and r_[2] == "Current members"), None)
        if hdr is None:
            self.fail("BoE mpcvoting.xlsx", "header row 'Current members' not found")
            return {}
        cols = [(j, rows[hdr][j]) for j in range(3, len(rows[hdr])) if rows[hdr][j] and rows[hdr][j] != "Past members"]
        cur_end = next((j for j, name in enumerate(rows[hdr]) if name == "Past members"), len(rows[hdr]))
        out = {}
        for r_ in rows[hdr + 1:]:
            if r_ and isinstance(r_[1], datetime) and r_[1].date() in self.meet.get("GBP", []):
                out[r_[1].date()] = {name: (None if r_[j] is None else round(float(r_[j]) * 100, 4)) for j, name in cols if j < cur_end and r_[j] is not None}
        self.f.remember(r)
        return out

    def add_votes(self, ccy: str, d: date, v: P.Votes, doc_id: str) -> None:
        row = {"currency": ccy, "meeting_date": d, "bank": self.cfg[ccy]["id"], "kind": v.kind, "n_for": v.n_for, "n_against": v.n_against,
               "for_names": ST.jdump(v.for_names), "against": ST.jdump(v.against), "source": v.source, "source_doc_id": doc_id,
               "evidence": v.evidence[:300], "notes": "; ".join(v.notes)}
        old = self.votes.get((ccy, d))
        if old is not None and VOTE_RANK.get(old["source"], 0) > VOTE_RANK.get(v.source, 0) and old["kind"] != "not_published":
            return                                                          # never replace a richer source (names, minutes) with a poorer one
        self.votes[(ccy, d)] = row
        self.rep.votes += 1

    def votes_pass(self) -> None:
        wb = None
        need_wb = any((self.votes.get(("GBP", d)) or {}).get("source") != "summary+xlsx" for d in self.meet.get("GBP", []))
        for ccy in self.banks:
            for d in self.meet.get(ccy, []):
                text = self.texts.get((ccy, d))
                v = P.parse_votes(ccy, text or "") if (text is not None or ccy in P.NOT_PUBLISHED) else None
                if v is None:
                    continue
                doc_id = f"{ccy}:statement:{d.isoformat()}"
                if ccy == "GBP" and need_wb:
                    wb = wb if wb is not None else self.boe_workbook()
                    rp = P.parse_rate("GBP", text or "")
                    if d in wb and rp is not None:
                        v = P.apply_boe_workbook(v, wb[d], rp.rate)
                self.add_votes(ccy, d, v, doc_id)
        for ccy, d in [(c, d) for c in ("NZD",) for d in self.meet.get(c, [])]:
            v = P.parse_votes(ccy, "")
            if v:
                self.add_votes(ccy, d, v, "")

    # ---- redlines -----------------------------------------------------------------------------------------------------------
    def redlines(self) -> list:
        rows = []
        for ccy in self.banks:
            stmts = sorted((k for k in self.docs if self.docs[k]["currency"] == ccy and self.docs[k]["type"] == "statement" and self.docs[k]["text"]),
                           key=lambda k: self.docs[k]["published_date"])
            for a, b in zip(stmts, stmts[1:]):
                da, db = self.docs[a], self.docs[b]
                if db["published_date"] not in self.meet.get(ccy, []):
                    continue
                rl = P.redline(da["text"].split("\n"), db["text"].split("\n"))
                rows.append({"currency": ccy, "meeting_date": db["published_date"], "prev_meeting_date": da["published_date"], "doc_id": db["doc_id"],
                             "prev_doc_id": da["doc_id"], "added_words": rl["added_words"], "removed_words": rl["removed_words"],
                             "unchanged_words": rl["unchanged_words"], "similarity": rl["similarity"], "ops_json": ST.jdump(rl["paras"])})
        self.rep.redlines = len(rows)
        return rows

    # ---- minutes, summaries -------------------------------------------------------------------------------------------------
    def minutes(self, ccy: str, d: date) -> None:
        for typ, url, due, rule in S.minutes_urls(ccy, d):
            doc_id = f"{ccy}:{typ}:{d.isoformat()}"
            if (doc_id,) in self.docs or self.today < due:
                self.rep.unchanged += (doc_id,) in self.docs
                continue
            r = self.get(url, f"{ccy} {typ} {d}", conditional=ccy != "GBP")          # the BoE minutes share the statement's URL, whose validator is already remembered
            if r is None:
                continue
            paras, method, fmt = self.extract(r, url, rule)
            if not paras:
                self.fail(f"{ccy} {typ} {d}", "no text extracted")
                continue
            text = X.to_text(paras)
            pub = header_date(r.headers.get("Last-Modified")) or due
            self.add(self.base(ccy, typ, doc_id, title=f"{S.BANK_NAME[ccy]} {typ.replace('_', ' ')} of the {d:%d %B %Y} meeting", url=url,
                               published_date=pub if pub <= self.today else due, meeting_date=d, format=fmt, text_sha256=X.sha256(text), extraction_method=method))
            self.f.remember(r)
            if ccy == "AUD":                                                # votes are published in the minutes (+14 days)
                v = P.parse_votes_rba(text)
                if v:
                    self.add_votes(ccy, d, v, doc_id)

    def discovered(self, ccy: str, d: date) -> None:
        """CAD (deliberations), ECB (account): found in the bank's feed, matched to the meeting by date (the other banks' follow-ups have derivable URLs)."""
        items = []
        if ccy == "CAD":
            items = [("deliberations", it) for it in self.feed(S.BOC_DELIBERATIONS_FEED, "BoC deliberations feed")]
        elif ccy == "EUR":
            items = [("account", it) for it in self.feed(S.ECB_PRESS_FEED, "ECB press feed") if re.search(r"ecb\.mg\d{6}~", it["link"])]
        for typ, it in items:
            pub = it["pub"].date() if it["pub"] else None
            lag = S.EXPECTED_LAG.get((ccy, typ), 30)
            if pub is None or not (0 <= (pub - d).days <= lag + 21) or self._nearest(ccy, pub, lag) != d:
                continue
            doc_id = f"{ccy}:{typ}:{d.isoformat()}"
            if (doc_id,) in self.docs:
                continue
            self.add(self.base(ccy, typ, doc_id, title=it["title"], url=it["link"], published_date=pub, published_at=_ts(it["pub"]), meeting_date=d,
                               format="pdf" if it["link"].lower().endswith(".pdf") else "html"))

    def _nearest(self, ccy: str, pub: date, lag: int) -> Optional[date]:
        ms = [m for m in self.meet.get(ccy, []) if m <= pub]
        return min(ms, key=lambda m: abs((pub - m).days - lag)) if ms else None

    # ---- press conference ---------------------------------------------------------------------------------------------------
    def presser(self, ccy: str) -> None:
        cid = self.cfg[ccy].get("youtube_channel_id")
        vids = self.feed(S.YT_FEED.format(cid=cid), f"{ccy} YouTube feed") if cid else []
        for d in self.meet.get(ccy, []):
            v = P.match_video(vids, d)
            if v is not None:
                vid = re.search(r"v=([\w-]{6,})", v["link"])
                vid_id = vid.group(1) if vid else hashlib.sha1(v["link"].encode()).hexdigest()[:11]
                self.add(self.base(ccy, "presser_video", f"{ccy}:presser_video:{vid_id}", title=v["title"], url=v["link"], published_date=v["pub"].date(),
                                   published_at=_ts(v["pub"]), meeting_date=d, format="video", meta={"source": "youtube", "channel": cid}))
            for typ, url, mode, rule in S.transcript_targets(ccy, d):
                doc_id = f"{ccy}:{typ}:{d.isoformat()}"
                if (doc_id,) in self.docs:
                    continue
                if mode == "link":
                    h = self.f.head(url)
                    if h.error:
                        continue                                            # not published (yet): nothing to store
                    self.add(self.base(ccy, typ, doc_id, title=f"{S.BANK_NAME[ccy]} press conference transcript, {d:%d %B %Y}", url=url, published_date=d,
                                       meeting_date=d, format="pdf" if url.endswith(".pdf") else "html"))
                else:
                    r = self.get(url, f"{ccy} {typ} {d}")
                    if r is None:
                        continue
                    paras, method, fmt = self.extract(r, url, rule)
                    if not paras:
                        self.fail(f"{ccy} {typ} {d}", "no text extracted")
                        continue
                    text = X.to_text(paras)
                    self.add(self.base(ccy, typ, doc_id, title=f"{S.BANK_NAME[ccy]} introductory statement, {d:%d %B %Y}", url=url, published_date=d,
                                       meeting_date=d, format=fmt, text_sha256=X.sha256(text), extraction_method=method, text=text))
                    self.f.remember(r)

    # ---- speeches -----------------------------------------------------------------------------------------------------------
    def speaker_of(self, ccy: str, it: dict) -> str:
        t = it["title"]
        if ccy == "USD":
            return t.split(",")[0].strip()
        if ccy == "EUR":
            return t.split(":")[0].strip() if ":" in t else ""
        if ccy == "CHF":
            m = re.match(r"\d{4}-\d{2}-\d{2} - ([^:]+):", t)
            return m.group(1).strip() if m else ""
        if ccy == "GBP":
            m = re.search(r"/(?:speech|speeches)/\d{4}/[a-z]+/([a-z\-]+?)-(?:speech|remarks|keynote)", it["link"])
            return m.group(1).replace("-", " ").title() if m else ""
        if ccy == "AUD":
            return it.get("author", "")
        return ""

    def role_of(self, name: str) -> tuple:
        sur = P.surname(name)
        for p in self.roster.get("people", []):
            if P.surname(p["name"]) == sur:
                voter = bool((p.get("voter") or {}).get(str(self.today.year)))
                return p["name"], p.get("role", ""), voter, bool(p.get("chair"))
        return name, "", False, False

    def first_paragraph(self, url: str) -> tuple:
        """(first substantial paragraph, speaker named in the page title: the BoJ lists carry no speaker - "Speech by Board Member MASU in Fukui")."""
        r = self.f.get(url)
        if r.error or "pdf" in r.headers.get("Content-Type", "").lower():
            return "", ""
        root = _dom(r.text)
        node = _pick(root, "article") or _pick(root, "main") or _pick(root, "body")
        paras = [p for p in (_paras(node) if node is not None else []) if len(p) > 80]
        t = re.search(r"<title>\s*(?:Speech|Remarks|Opening Remarks)[^<]*? by (?:Governor|Deputy Governor|Board Member|Member of the Policy Board) ([A-Z]{2,})\b", r.text)
        return (paras[0][:600] if paras else ""), (t.group(1) if t else "")

    def speeches(self, ccy: str) -> list:
        items = []
        cutoff = self.now - timedelta(days=SPEECH_DAYS)
        if ccy in S.FEEDS:
            for url, typ in S.FEEDS[ccy]:
                for it in self.feed(url, f"{ccy} feed"):
                    if ccy == "EUR" and not re.search(r"ecb\.(sp|in)\d{6}", it["link"]):
                        continue
                    if it["pub"] and it["pub"] >= cutoff:
                        it = dict(it, kind=typ)
                        it["speaker"] = self.speaker_of(ccy, it)
                        items.append(it)
        elif ccy in S.HTML_LISTS:
            r = self.get(S.HTML_LISTS[ccy].format(year=self.today.year), f"{ccy} speeches list")
            if r:
                rx = (r'<a[^>]+href="(/en/about/press/koen_\d{4}/ko(\d{6})a\.htm)"[^>]*>(.*?)</a>' if ccy == "JPY"
                      else r'<a[^>]+href="(/speeches/\d{4}/([a-z\-]+)-(\d{4}-\d{2}-\d{2})[^"]*\.html)"[^>]*>(.*?)</a>')
                host = "https://www.boj.or.jp" if ccy == "JPY" else "https://www.rba.gov.au"
                for m in re.finditer(rx, r.text, flags=re.S):
                    if ccy == "JPY":
                        pub = datetime.strptime(m.group(2), "%y%m%d").replace(tzinfo=timezone.utc)
                        it = {"title": _norm(html.unescape(re.sub(r"<[^>]+>", " ", m.group(3)))), "link": host + m.group(1), "pub": pub, "author": "", "desc": ""}
                    else:
                        pub = datetime.strptime(m.group(3), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        it = {"title": _norm(re.sub(r"<[^>]+>", " ", m.group(4))), "link": host + m.group(1), "pub": pub, "author": m.group(2), "desc": ""}
                    if pub >= cutoff and not it["title"].lower().startswith("media conference"):
                        it["kind"] = "speech"
                        it["speaker"] = it.get("author", "")
                        items.append(it)
        pages = 0
        for it in items:
            typ = "testimony" if it["kind"] == "testimony" or (ccy != "USD" and P_HEARING.search(it["title"])) else "speech"
            doc_id = f"{ccy}:{typ}:{hashlib.sha1(it['link'].encode()).hexdigest()[:12]}"
            old = self.docs.get((doc_id,))
            first = (old or {}).get("meta_json") and re.search(r'"first":"([^"]*)"', old["meta_json"])
            desc = _norm(re.sub(r"<[^>]+>", " ", it.get("desc") or ""))
            if not desc and first:
                desc = first.group(1)                                          # keep what the first fetch found: no page request again
            prior = (old or {}).get("speaker") or ""                                     # a page that is not fetched twice keeps the speaker found the first time
            nameless = ccy == "JPY" and not it.get("speaker") and not prior              # the BoJ list carries no speaker: it is in the page title
            if ((not desc and not old) or nameless) and pages < MAX_PAGES_PER_BANK and it["pub"] <= self.now:
                got, byline = self.first_paragraph(it["link"])
                desc, it["speaker"] = desc or got, it.get("speaker") or byline
                pages += 1
            rel = P.monetary_relevance(it["title"], desc)
            who = it.get("speaker") or prior
            name, role, voter, chair = self.role_of(who) if who else ("", "", False, False)
            self.add(self.base(ccy, typ, doc_id, title=it["title"], url=it["link"], published_date=it["pub"].date(), published_at=_ts(it["pub"]), speaker=name, role=role,
                               relevance=rel, format="pdf" if it["link"].lower().endswith(".pdf") else "html",
                               meta={"weight": P.speaker_weight(role, voter, chair), "voter": voter, "chair": chair, "first": desc[:300], "via": "bank"}))
        return items

    def bis(self, own: dict) -> None:
        """BIS 'central bankers' speeches': backfill only - an item the bank feed already has (same speaker surname + title similarity >= 0.6) is dropped."""
        for it in self.feed(S.BIS_FEED, "BIS speeches feed"):
            ccy = next((c for k, c in S.BIS_BANK.items() if k in it["desc"]), None)
            if ccy is None or ccy not in self.banks or not it["pub"]:
                continue
            speaker = it.get("author", "")
            cand = {"speaker": speaker, "title": it["title"]}
            if any(P.same_speech(dict(o, speaker=o.get("speaker") or speaker), it) or P.same_speech(cand, dict(o, author=o.get("speaker", ""), desc=o["title"])) for o in own.get(ccy, [])):
                continue
            if own.get(ccy) and it["pub"] >= min(o["pub"] for o in own[ccy] if o["pub"]):
                continue                                                    # inside the window the bank's own feed covers: the bank is the source
            name, role, voter, chair = self.role_of(speaker) if speaker else ("", "", False, False)
            doc_id = f"{ccy}:speech:{hashlib.sha1(it['link'].encode()).hexdigest()[:12]}"
            self.add(self.base(ccy, "speech", doc_id, title=it["title"], url=it["link"], published_date=it["pub"].date(), speaker=name or speaker, role=role,
                               relevance=P.monetary_relevance(it["title"], it["desc"]), format="html",
                               meta={"weight": P.speaker_weight(role, voter, chair), "voter": voter, "chair": chair, "via": "bis", "note": "BIS posting date, not the speech date"}))

    # ---- manual documents (RBNZ, videos) -------------------------------------------------------------------------------------
    def manual_documents(self) -> None:
        """data/cb/manual/documents.yaml: [{currency, type, title, url, published, meeting?, text?, note?}] - typed by hand from the bank's own page."""
        f = self.paths.manual / MANUAL_FILE
        if not f.exists():
            return
        doc = yaml.safe_load(f.read_text()) or {}
        for e in doc.get("documents") or []:
            try:
                ccy, d, typ = e["currency"], e["published"], e["type"]
                doc_id = f"{ccy}:{typ}:{e.get('id') or hashlib.sha1(e['url'].encode()).hexdigest()[:12]}"
                text = e.get("text")
                self.add(self.base(ccy, typ, doc_id, title=e["title"], url=e["url"], published_date=d, meeting_date=e.get("meeting"), format="manual",
                                   text_sha256=X.sha256(text) if text else None, extraction_method="manual" if text else None, text=text,
                                   meta={"source": "manual", "note": e.get("note", "")}))
            except (KeyError, ValueError, TypeError) as ex:
                self.fail("manual documents", f"entry skipped: {type(ex).__name__} {ex}")


P_HEARING = re.compile(r"hearing|testimon|before the (?:house|senate|treasury select)|parliament", re.I)


def header_date(s: Optional[str]) -> Optional[date]:
    d = P_parse_dt(s) if s else None
    return d.date() if d else None


def P_parse_dt(s: str) -> Optional[datetime]:
    from ..cb_probe import _parse_dt
    return _parse_dt(s)


def load_roster(path=None) -> dict:
    from pathlib import Path
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "cb_roster.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def run_documents(paths, today: date, *, fetcher: Optional[Fetcher] = None, now: Optional[datetime] = None, n: int = 4, banks: tuple = S.BANKS,
                  roster: Optional[dict] = None, state: Optional[dict] = None) -> DocsReport:
    from ..cb_collect import load_state, save_state
    state = state if state is not None else load_state(paths)
    f = fetcher or Fetcher(validators=state.setdefault(STATE_KEY, {}))
    run = _Run(paths, today, f, now or datetime.now(timezone.utc), n, banks, roster if roster is not None else load_roster())
    links = run.discover()
    own: dict = {}
    for ccy in banks:
        for d in run.meet.get(ccy, []):
            run.statement(ccy, d, links)
    for ccy in banks:
        for d in run.meet.get(ccy, []):
            run.minutes(ccy, d)
            run.discovered(ccy, d)
        run.presser(ccy)
        own[ccy] = run.speeches(ccy)
    if "NZD" in run.cfg:
        run.presser("NZD")                                                  # the YouTube feed only: RBNZ pages are behind Cloudflare
    run.bis(own)
    run.manual_documents()
    run.votes_pass()
    rl_rows = run.redlines()
    rep = run.rep
    rep.written["documents"] = ST.write_documents(paths, run.docs)
    rep.written["votes"] = ST.write_votes(paths, sorted(run.votes.values(), key=lambda r: (r["currency"], r["meeting_date"])))
    rep.written["redlines"] = ST.write_redlines(paths, ST.merge_table(ST.load_redlines(paths), rl_rows, ST.REDLINES_KEY))
    rep.requests = f.requests_made
    if f.validators is not state.get(STATE_KEY):
        state[STATE_KEY] = f.validators
    save_state(paths, state)
    rep.notes.append("RBNZ: behind a Cloudflare challenge - no automatic fetch; documents only from data/cb/manual/documents.yaml")
    if any("YouTube" in w for w, _ in rep.failed):
        rep.notes.append("YouTube feeds: disallowed by youtube.com/robots.txt (Disallow: /feeds/videos.xml) - press-conference videos only from data/cb/manual/documents.yaml")
    return rep


# ---- publication expectations (--status) ----------------------------------------------------------------------------------------

def expectations(paths, today: date, n: int = 4) -> list:
    """Warnings for documents that are past their usual publication lag (measured in 0B) and not stored: (bank, type, meeting, expected)."""
    meetings = ds.load_meetings(paths.meetings)
    docs = ST.load_documents(paths)
    have = {(d["currency"], d["type"], d["meeting_date"]) for d in docs.values() if d["meeting_date"]}
    out = []
    for (ccy, typ), lag in sorted(S.EXPECTED_LAG.items()):
        ms = sorted(m["date"] for m in meetings.get(ccy, []) if m["date"] <= today)[-n:]
        for d in ms:
            due = d + timedelta(days=lag + S.GRACE_DAYS)
            if today > due and (ccy, typ, d) not in have:
                out.append(f"{ccy} {typ.replace('_', ' ')} of the {d} meeting overdue: expected around {d + timedelta(days=lag)} (+{lag} d), not stored")
    return out
