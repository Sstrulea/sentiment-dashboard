"""Where each bank's official texts live (the sources of the 0B report) and when they are due. Pure tables + URL builders; the HTTP is in
http.py and the orchestration in collect.py. RBNZ sits behind a Cloudflare challenge: its texts come from data/cb/manual/documents.yaml."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from ..cb_probe import ECB_MPS, ECB_SEEDS, _statement_url

BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF")            # fetched; NZD is manual
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
SEP = "→"

FEEDS = {                                                       # speeches / testimony (RSS) per bank
    "USD": [("https://www.federalreserve.gov/feeds/speeches.xml", "speech"), ("https://www.federalreserve.gov/feeds/testimony.xml", "testimony")],
    "EUR": [("https://www.ecb.europa.eu/rss/press.html", "speech")],                       # filtered ecb.sp* / ecb.in*
    "GBP": [("https://www.bankofengland.co.uk/rss/speeches", "speech")],
    "CAD": [("https://www.bankofcanada.ca/content_type/speeches/feed/", "speech")],
    "CHF": [("https://www.snb.ch/public/rss/en/speeches", "speech"), ("https://www.snb.ch/public/rss/en/interviews", "speech")],
}
HTML_LISTS = {"JPY": "https://www.boj.or.jp/en/about/press/koen_{year}/index.htm", "AUD": "https://www.rba.gov.au/speeches/"}
BIS_FEED = "https://www.bis.org/doclist/cbspeeches.rss"
BIS_BANK = {"Federal Reserve": "USD", "European Central Bank": "EUR", "Bank of England": "GBP", "Bank of Japan": "JPY", "Bank of Canada": "CAD",
            "Reserve Bank of Australia": "AUD", "Reserve Bank of New Zealand": "NZD", "Swiss National Bank": "CHF"}
ECB_PRESS_FEED = "https://www.ecb.europa.eu/rss/press.html"
BOJ_RSS = "https://www.boj.or.jp/en/rss/whatsnew.xml"                                         # what's new: the decision documents carry the time the BoJ published them
BOC_DELIBERATIONS_FEED = "https://www.bankofcanada.ca/content_type/summary-of-deliberations/feed/"
BOE_VOTING_XLSX = "https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-summary-and-minutes/mpcvoting.xlsx"

from .expect import EXPECTED_LAG, GRACE_DAYS                       # publication lags measured in 0B (shared with the payloads)
LICENSE = "Official publication of the {bank}; copyright of the bank. Kept for analysis with attribution and the link to the source."
BANK_NAME = {"USD": "Federal Reserve", "EUR": "European Central Bank", "GBP": "Bank of England", "JPY": "Bank of Japan", "CAD": "Bank of Canada",
             "AUD": "Reserve Bank of Australia", "NZD": "Reserve Bank of New Zealand", "CHF": "Swiss National Bank"}


ECB_PRESS_LANDING = "https://www.ecb.europa.eu/press/press_conference/html/index.en.html"      # carries the video of the LAST press conference only


def video_page(ccy: str, decision: date) -> Optional[str]:
    """The bank's own page that carries (or links) the press-conference video, when its URL follows from the decision date. Not the YouTube feeds."""
    if ccy == "USD":
        return f"https://www.federalreserve.gov/monetarypolicy/fomcpresconf{decision:%Y%m%d}.htm"            # Brightcove player embedded in the page
    if ccy == "CAD":
        return statement_url("CAD", decision, {})                                                               # the press release links the /multimedia/ page
    if ccy == "AUD":
        return f"https://www.rba.gov.au/speeches/{decision.year}/mc-gov-{decision:%Y-%m-%d}.html"           # "Watch video: Media conference held on ..."
    if ccy == "GBP":
        return f"https://www.bankofengland.co.uk/monetary-policy-report/{decision.year}/{decision:%B}-{decision.year}".lower()   # only the MPR meetings have a conference
    return None


def statement_url(ccy: str, decision: date, feed_links: dict) -> Optional[str]:
    """URL of the decision-day statement. ECB: the hash cannot be derived - the seed of a past meeting or the link seen in the press feed."""
    if ccy == "EUR":
        return ECB_SEEDS.get(decision) or feed_links.get("EUR", {}).get(decision)
    tgt = _statement_url(ccy, decision, {"rba_decisions": feed_links.get("AUD", {})})
    return tgt[0] if tgt else None


def minutes_urls(ccy: str, decision: date) -> list:
    """[(type, url, expected publication date, extraction rule)] for documents whose URL follows from the meeting date."""
    if ccy == "USD":
        return [("minutes", f"https://www.federalreserve.gov/monetarypolicy/fomcminutes{decision:%Y%m%d}.htm", decision + timedelta(days=21),
                 {"sel": ("div", "article", None), "loose": True})]
    if ccy == "AUD":
        return [("minutes", f"https://www.rba.gov.au/monetary-policy/rba-board-minutes/{decision.year}/{decision:%Y-%m-%d}.html", decision + timedelta(days=14),
                 {"sel": ("div", "content", None)})]
    if ccy == "JPY":                                                    # PDFs whose names follow the decision date (0B): summary of opinions ~+14 d, minutes ~+50 d
        base, ymd = f"https://www.boj.or.jp/en/mopo/mpmsche_minu", f"{decision:%y%m%d}"
        return [("summary_of_opinions", f"{base}/opinion_{decision.year}/opi{ymd}.pdf", decision + timedelta(days=EXPECTED_LAG[("JPY", "summary_of_opinions")]), {}),
                ("minutes", f"{base}/minu_{decision.year}/g{ymd}.pdf", decision + timedelta(days=EXPECTED_LAG[("JPY", "minutes")]), {})]
    if ccy == "GBP":                                                    # the summary and the minutes share one page, published together (0B: 4/4 meetings)
        url = statement_url("GBP", decision, {})
        return [("minutes", url, decision, {"sel": ("div", "output", None), "start": r"^Minutes of the Monetary Policy Committee meeting"})] if url else []
    if ccy == "CHF":
        d = decision + timedelta(days=28)
        return [("deliberations", f"https://www.snb.ch/en/publications/communication/summaries/zus_{d:%Y%m%d}", d, {"sel": ("div", None, "a-text")})]
    return []


def transcript_targets(ccy: str, decision: date) -> list:
    """[(type, url, mode)] - mode 'link' keeps URL + metadata only (long transcripts), 'text' commits the text (short opening statements)."""
    if ccy == "USD":
        return [("presser_transcript", f"https://www.federalreserve.gov/mediacenter/files/FOMCpresconf{decision:%Y%m%d}.pdf", "link", None)]
    if ccy == "EUR" and decision in ECB_MPS:
        return [("presser_transcript", ECB_MPS[decision], "link", None)]
    if ccy == "AUD":
        return [("presser_transcript", f"https://www.rba.gov.au/speeches/{decision.year}/mc-gov-{decision:%Y-%m-%d}.html", "link", None)]
    if ccy == "CAD":
        return [("opening_statement", f"https://www.bankofcanada.ca/{decision.year}/{decision:%m}/opening-statement-{decision:%Y-%m-%d}/", "text",
                 {"sel": ("div", None, "post-content")})]
    if ccy == "CHF":
        return [("opening_statement", f"https://www.snb.ch/en/publications/communication/speeches-restricted/ref_{decision:%Y%m%d}_mslanmargpe", "text",
                 {"sel": ("div", None, "a-text")})]
    return []
