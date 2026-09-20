"""Phase 2a, pure parts on REAL frozen documents: deterministic extraction (zero boilerplate), the rate of the statement, the votes, the
redline, the relevance filter, the bank <-> BIS de-duplication, the press-conference video match."""
from __future__ import annotations

import io
from datetime import date, datetime, timezone

import openpyxl
import pytest

from src.cb_docs import extract as X
from src.cb_docs import parse as P
from src.cb_probe import _feed_items

from .cb_docs_helpers import D, FIX, statement_paras, statement_text

DAYS = {
    "USD": ["2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"], "EUR": ["2026-04-30", "2026-06-11", "2026-07-23", "2026-09-10"],
    "GBP": ["2026-04-30", "2026-06-18", "2026-07-30", "2026-09-17"], "JPY": ["2026-04-28", "2026-06-16", "2026-07-31", "2026-09-18"],
    "CAD": ["2026-04-29", "2026-06-10", "2026-07-15", "2026-09-02"], "AUD": ["2026-03-17", "2026-05-05", "2026-06-16", "2026-08-11"],
    "CHF": ["2025-09-25", "2025-12-11", "2026-03-19", "2026-06-18"],
}
ALL = [(c, d) for c, days in DAYS.items() for d in days]


# --- extraction ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ccy,day", ALL)
def test_extraction_is_deterministic_and_free_of_page_furniture(ccy, day):
    a, b = statement_text(ccy, day), statement_text(ccy, day)
    assert a == b and X.sha256(a) == X.sha256(b) and len(a) > 800
    paras = a.split("\n")
    assert not any(p.startswith(bp) for p in paras for bp in X.BOILERPLATE)                 # navigation / cookie banners
    assert not any(X.FURNITURE.match(p) for p in paras)                                     # Share, "For release at", a bare date, the headline
    assert all(p == p.strip() and "  " not in p for p in paras)


def test_paragraph_counts_and_first_paragraphs_of_the_latest_statements():
    exp = {"USD": (4, "The Federal Open Market Committee approved"), "EUR": (11, "The Governing Council today decided"),
           "GBP": (6, "Monetary Policy Summary, September 2026"), "CAD": (10, "The Bank of Canada today held"),
           "AUD": (9, "At its meeting today"), "CHF": (12, "The Swiss National Bank")}
    for ccy, (n, start) in exp.items():
        paras = statement_paras(ccy, DAYS[ccy][-1])
        assert len(paras) >= n - 1 and paras[0].startswith(start), (ccy, len(paras), paras[0][:60])


def test_boj_pdf_spacing_artifacts_are_repaired_where_they_matter():
    for day in DAYS["JPY"]:
        t = statement_text("JPY", day)
        assert "V oting" not in t and "NAKAGAW A" not in t and "TAKA TA" not in t and "SA TO" not in t
        assert "Japa n" not in t and "financia l" not in t and "Corporat e" not in t
    assert "Voting for the action:" in statement_text("JPY", "2026-09-18")


def test_repair_spacing_unit():
    assert X.repair_spacing("V oting for the action: SA TO Ayano and TAKA TA Hajime") == "Voting for the action: SATO Ayano and TAKATA Hajime"
    assert X.repair_spacing("the Bank of Japa n said") == "the Bank of Japan said"                   # the joined word is in the committed vocabulary
    assert X.repair_spacing("a nd the cat sat on a mat") == "a nd the cat sat on a mat"              # 'a' is a real word: never joined
    assert X.normalize_pdf_text("in 202 6 the rate is 0. 25 percent and Funds -Supplying") == "in 2026 the rate is 0.25 percent and Funds-Supplying"


# --- the rate of the statement ---------------------------------------------------------------------------------------------------

RATES = {
    "USD": [3.625, 3.625, 3.625, 3.875], "EUR": [2.0, 2.25, 2.25, 2.5], "GBP": [3.75] * 4, "JPY": [0.75, 1.0, 1.0, 1.25],
    "CAD": [2.25] * 4, "AUD": [4.10, 4.35, 4.35, 4.35], "CHF": [0.0] * 4,
}


@pytest.mark.parametrize("ccy,day", ALL)
def test_rate_from_the_statement(ccy, day):
    p = P.parse_rate(ccy, statement_text(ccy, day))
    assert p is not None and p.rate == pytest.approx(RATES[ccy][DAYS[ccy].index(day)], abs=1e-9)
    assert P.validate_rate(p, None, ccy) == (True, "")
    if ccy == "USD":
        assert (p.lower, p.upper) == ((3.5, 3.75) if day != "2026-09-16" else (3.75, 4.0))


def test_rate_direction_and_boj_effective_date():
    assert [P.parse_rate("USD", statement_text("USD", d)).direction for d in DAYS["USD"]] == ["hold", "hold", "hold", "raise"]
    assert [P.parse_rate("EUR", statement_text("EUR", d)).direction for d in DAYS["EUR"]] == ["hold", "raise", "hold", "raise"]
    assert [P.parse_rate("AUD", statement_text("AUD", d)).direction for d in DAYS["AUD"]] == ["raise", "raise", "hold", "hold"]
    assert P.parse_rate("JPY", statement_text("JPY", "2026-09-18")).effective == D(2026, 9, 24)         # the BoJ footnote
    assert P.parse_rate("JPY", statement_text("JPY", "2026-06-16")).effective == D(2026, 6, 17)


def test_validation_accepts_the_real_sequence_and_rejects_the_implausible():
    for ccy, days in DAYS.items():
        prev = None
        for d in days:
            p = P.parse_rate(ccy, statement_text(ccy, d))
            assert P.validate_rate(p, prev, ccy)[0], (ccy, d)
            prev = p.rate
    raise_ = P.parse_rate("EUR", statement_text("EUR", "2026-09-10"))
    ok, why = P.validate_rate(raise_, 2.50, "EUR")                                       # says 'raise' but the rate did not move
    assert not ok and "says raise" in why
    ok, why = P.validate_rate(P.parse_rate("EUR", statement_text("EUR", "2026-07-23")), 2.75, "EUR")   # says 'unchanged' but differs from the previous
    assert not ok and "unchanged" in why
    assert not P.validate_rate(P.RateParse(9.0, "raise", ""), 2.0, "EUR")[0]              # +700 bp
    assert not P.validate_rate(P.RateParse(99.0, "hold", ""), None, "EUR")[0]             # outside the range
    assert not P.validate_rate(P.RateParse(3.9, "hold", "", 3.7, 4.1), None, "USD")[0]    # a Fed range that is not 25 bp wide


def test_a_statement_that_cannot_be_parsed_gives_none_not_a_guess():
    assert P.parse_rate("USD", "The Committee decided to keep policy where it is.") is None
    assert P.parse_rate("EUR", statement_text("EUR", "2026-09-10").replace("deposit facility", "facility of deposits")) is None
    assert P.parse_rate("NZD", "The Committee agreed the OCR should be 2.75%") is None                # RBNZ: no automatic text
    assert P.parse_rate("CHF", "") is None


# --- votes -----------------------------------------------------------------------------------------------------------------------

def test_fed_votes_counts_names_and_directions():
    exp = {"2026-04-29": ("counted", 8, 4), "2026-06-17": ("unanimous", 12, 0), "2026-07-29": ("counted", 9, 3), "2026-09-16": ("unanimous", 12, 0)}
    for d, (kind, f, a) in exp.items():
        v = P.parse_votes("USD", statement_text("USD", d))
        assert (v.kind, v.n_for, v.n_against) == (kind, f, a), d
    v = P.parse_votes("USD", statement_text("USD", "2026-04-29"))
    assert len(v.for_names) == 8 and "Jerome H. Powell" in v.for_names and [(x["name"], x["direction"]) for x in v.against] == [
        ("Stephen I. Miran", "lower"), ("Beth M. Hammack", "hold"), ("Neel Kashkari", "hold"), ("Lorie K. Logan", "hold")]
    assert "easing bias" in v.against[1]["note"]
    v = P.parse_votes("USD", statement_text("USD", "2026-07-29"))
    assert [(x["name"], x["direction"]) for x in v.against] == [("Beth M. Hammack", "raise"), ("Neel Kashkari", "raise"), ("Lorie K. Logan", "raise")]


def boe_prefs(day: str) -> dict:
    ws = openpyxl.load_workbook(io.BytesIO((FIX / "misc" / "boe_mpcvoting.xlsx").read_bytes()), read_only=True, data_only=True)["Bank Rate Decisions"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r[2] == "Current members")
    cols = [(j, n) for j, n in enumerate(rows[hdr]) if j >= 3 and n and n != "Past members"]
    end = next(j for j, n in enumerate(rows[hdr]) if n == "Past members")
    row = next(r for r in rows[hdr + 1:] if isinstance(r[1], datetime) and r[1].date() == date.fromisoformat(day))
    return {n: round(float(row[j]) * 100, 4) for j, n in cols if j < end and row[j] is not None}


def test_boe_votes_from_the_summary_and_the_workbook():
    exp = {"2026-04-30": (8, 1, ["Huw Pill"]), "2026-06-18": (7, 2, ["Megan Greene", "Huw Pill"]),
           "2026-07-30": (6, 3, ["Catherine L Mann", "Huw Pill", "Megan Greene"]), "2026-09-17": (6, 3, ["Catherine L Mann", "Huw Pill", "Megan Greene"])}
    for d, (f, a, dissent) in exp.items():
        v = P.parse_votes("GBP", statement_text("GBP", d))
        assert (v.kind, v.n_for, v.n_against) == ("counted", f, a) and v.source == "summary"
        v = P.apply_boe_workbook(v, boe_prefs(d), 3.75)
        assert v.source == "summary+xlsx" and not v.notes and len(v.for_names) == f
        assert sorted(x["name"] for x in v.against) == sorted(dissent) and all(x["direction"] == "raise" for x in v.against)      # all wanted 4%


def test_boe_workbook_disagreement_is_recorded_not_hidden():
    v = P.parse_votes("GBP", statement_text("GBP", "2026-04-30"))
    prefs = boe_prefs("2026-04-30")
    prefs["Andrew Bailey"] = 4.0                                                                  # one more dissenter than the summary says
    v = P.apply_boe_workbook(v, prefs, 3.75)
    assert (v.n_for, v.n_against) == (8, 1) and v.notes and "differs from the summary" in v.notes[0]


def test_boj_votes_names_and_the_direction_of_each_dissent():
    exp = {"2026-04-28": (6, 3, {"Junko Nakagawa": "raise", "Hajime Takata": "raise", "Naoki Tamura": "raise"}),
           "2026-06-16": (7, 1, {"Toichiro Asada": "hold"}), "2026-07-31": (8, 1, {"Hajime Takata": "raise"}),
           "2026-09-18": (7, 2, {"Toichiro Asada": "hold", "Ayano Sato": "hold"})}
    for d, (f, a, dis) in exp.items():
        v = P.parse_votes("JPY", statement_text("JPY", d))
        assert (v.kind, v.n_for, v.n_against) == ("counted", f, a) and len(v.for_names) == f, d
        assert {x["name"]: x["direction"] for x in v.against} == dis, d
    assert "absent: Kazuo Ueda" in P.parse_votes("JPY", statement_text("JPY", "2026-06-16")).notes[0]
    v = P.parse_votes("JPY", statement_text("JPY", "2026-09-18"))
    assert "Kazuo Ueda" in v.for_names and "Ayano Sato" not in v.for_names and not any(n.isupper() or n.split()[0].isupper() for n in v.for_names)
    assert P.given_first("UEDA Kazuo") == "Kazuo Ueda" and P.given_first("Andrew Bailey") == "Andrew Bailey" and P.given_first("MASU Kazuyuki") == "Kazuyuki Masu"


def rba_minutes(day: str) -> str:
    from src.cb_probe import _dom, _paras, _pick
    return "\n".join(_paras(_pick(_dom((FIX / "minutes" / f"AUD_{day}.html").read_text()), "div", "content", None)))


def test_rba_votes_from_the_minutes_and_the_statement():
    exp = {"2026-03-17": ("counted", 5, 4), "2026-05-05": ("counted", 8, 1), "2026-06-16": ("unanimous", None, 0), "2026-08-11": ("unanimous", None, 0)}
    for d, (kind, f, a) in exp.items():
        v = P.parse_votes_rba(rba_minutes(d))
        assert (v.kind, v.n_for, v.n_against, v.source) == (kind, f, a, "minutes"), d
    assert [x["direction"] for x in P.parse_votes_rba(rba_minutes("2026-03-17")).against] == ["hold"]          # 4 voted to leave it unchanged
    s = P.parse_votes("AUD", statement_text("AUD", "2026-03-17"))                                             # the majority sentence is already in the statement
    assert (s.kind, s.n_for, s.n_against) == ("counted", 5, 4)
    assert P.parse_votes("AUD", statement_text("AUD", "2026-06-16")) is None                                  # unanimous: only the minutes say so


def test_banks_that_do_not_publish_votes():
    for ccy, kind in (("EUR", "not_published"), ("CAD", "not_published"), ("CHF", "not_published"), ("NZD", "consensus")):
        v = P.parse_votes(ccy, "")
        assert v.kind == kind and v.n_for is None and v.source == "n/a" and v.evidence


# --- redline ---------------------------------------------------------------------------------------------------------------------

def test_redline_between_two_consecutive_real_fed_statements():
    prev, cur = statement_paras("USD", "2026-07-29"), statement_paras("USD", "2026-09-16")
    rl = P.redline(prev, cur)
    assert P.apply_redline(prev, rl) == cur                                                      # the redline reproduces the new statement exactly
    ops = [(o[0], o[1].strip()) for x in rl["paras"] for o in x["ops"] if x["kind"] == "changed"]
    assert ("-", "maintain") in ops and ("+", "raise") in ops                                    # the decision sentence
    assert any(x["kind"] == "removed" and "Voting against" in x["ops"][0][1] for x in rl["paras"])   # the dissent paragraph is gone
    assert rl["added_words"] > 0 and rl["removed_words"] > rl["added_words"] and 0.4 < rl["similarity"] < 0.95
    assert {x["kind"] for x in rl["paras"]} <= {"same", "changed", "added", "removed"} and all(x["ops"] == [] for x in rl["paras"] if x["kind"] == "same")


def test_redline_of_identical_and_of_unrelated_statements():
    paras = statement_paras("EUR", "2026-09-10")
    same = P.redline(paras, paras)
    assert same["added_words"] == same["removed_words"] == 0 and same["similarity"] == 1.0 and {x["kind"] for x in same["paras"]} == {"same"}
    other = P.redline(statement_paras("EUR", "2026-07-23"), statement_paras("EUR", "2026-09-10"))
    assert P.apply_redline(statement_paras("EUR", "2026-07-23"), other) == paras
    assert other["added_words"] > 20 and other["similarity"] < 0.9


def test_word_ops_unit():
    ops = P.word_ops("the rate is unchanged today", "the rate is raised today")
    assert ops == [["=", "the rate is "], ["-", "unchanged "], ["+", "raised "], ["=", "today"]]
    assert "".join(o[1] for o in ops if o[0] in "=+") == "the rate is raised today"


# --- speeches --------------------------------------------------------------------------------------------------------------------

def feed(name):
    return _feed_items((FIX / "feeds" / name).read_bytes())


def test_relevance_filter_marks_but_keeps():
    fed = {i["title"]: i for i in feed("fed_speeches.xml")}
    rel = {t: P.monetary_relevance(t, i["desc"]) for t, i in fed.items()}
    assert any(v == "monetary" for v in rel.values()) and any(v == "other" for v in rel.values())            # both kinds occur in the real Fed feed
    assert P.monetary_relevance("Modernizing Bank Regulatory Stress Testing", "Speech At the Luncheon of the Lord Mayor") == "other"
    assert P.monetary_relevance("Monetary Policy at a Crossroads", "") == "monetary"
    assert P.monetary_relevance("Inflation and the outlook for interest rates", "") == "monetary"
    assert P.monetary_relevance("Stablecoins and the future of payments", "") == "other"


def test_bank_bis_dedup_key_is_speaker_surname_plus_title_similarity_never_the_date():
    own = {"speaker": "Schnabel", "title": "Schnabel: Monetary policy in a fragmenting world", "pub": datetime(2026, 8, 20, tzinfo=timezone.utc)}
    bis = {"author": "Isabel Schnabel", "desc": "Speech by Ms Isabel Schnabel, Member of the Executive Board of the European Central Bank",
           "title": "Monetary policy in a fragmenting world", "pub": datetime(2026, 9, 8, tzinfo=timezone.utc)}                # 19 days later
    assert P.same_speech(own, bis)
    assert not P.same_speech(own, dict(bis, title="Financial stability report 2026 overview"))                                   # same speaker, another speech
    assert not P.same_speech(own, dict(bis, author="Philip Lane", desc="Speech by Mr Philip Lane"))                              # same title, another speaker
    assert P.same_speech(own, dict(bis, pub=datetime(2027, 1, 1, tzinfo=timezone.utc)))                                          # the BIS date is not part of the key
    # titles are compared after normalisation (date / speaker prefix, case, punctuation) and a close - not identical - title still matches
    assert P.same_speech(dict(own, title="2026-08-20 - Isabel Schnabel: Inflation outlook"), dict(bis, title="Inflation Outlook"))
    assert P.same_speech(own, dict(bis, title="Monetary policy in a fragmenting world (revised remarks)"))


def test_dedup_on_the_real_feeds_finds_the_bank_items_in_the_bis_feed():
    bis = feed("bis_cbspeeches.xml")
    fed_items = feed("fed_speeches.xml") + feed("fed_testimony.xml")
    hits = 0
    for it in fed_items:
        own = dict(it, speaker=it["title"].split(",")[0])
        hits += any(P.same_speech(own, b) for b in bis)
    assert len(bis) >= 10 and hits <= len(fed_items)                                                                             # coverage depends on the day; the rule itself is unit-tested above


def test_speaker_weight_chair_then_voters_then_the_rest():
    assert P.speaker_weight("Chair", True, chair=True) == 1 and P.speaker_weight("Governor", True) == 2 and P.speaker_weight("President", False) == 3


# --- press conference video ------------------------------------------------------------------------------------------------------

def test_video_match_on_the_meeting_date_plus_minus_one_day():
    v = lambda title, day: {"title": title, "pub": datetime(2026, 9, *day, 19, 0, tzinfo=timezone.utc), "link": f"https://www.youtube.com/watch?v={title[:6]}"}   # noqa: E731
    vids = [v("FOMC Press Conference September 16, 2026", (16,)), v("Speech by the Chair", (16,)), v("FOMC Press Conference (replay)", (17,)),
            v("Press Conference archive", (30,))]
    m = P.match_video(vids, date(2026, 9, 16))
    assert m["title"] == "FOMC Press Conference September 16, 2026"                                                     # the earliest press-conference title
    assert P.match_video(vids, date(2026, 9, 18))["title"] == "FOMC Press Conference (replay)"                            # +-1 day
    assert P.match_video(vids, date(2026, 9, 25)) is None and P.match_video([], date(2026, 9, 16)) is None
    assert P.match_video([v("Monetary Policy Decision - press conference", (11,))], date(2026, 9, 10)) is not None


# --- press-conference video on the banks' own pages ---------------------------------------------------------------------------------

def video_fixture(name: str) -> str:
    import json
    from pathlib import Path
    root = Path(__file__).parent / "fixtures" / "cb_docs"
    m = json.loads((root / "manifest.json").read_text())
    url = next(u for u in m if u.endswith(name))
    return (root / m[url]["file"]).read_text(), url


def test_video_from_the_real_pages_of_each_bank():
    fed = {d: P.video_from_page("USD", *video_fixture(f"fomcpresconf{d.replace('-', '')}.htm")) for d in DAYS["USD"]}
    assert [fed[d]["id"] for d in DAYS["USD"]] == ["6394190841112", "6398687166112", "6402426667112", "6405157968112"]
    assert all(v["player"] == "Brightcove" and v["url"].endswith(".htm") for v in fed.values())                      # the FOMC page is the link
    boc = [P.video_from_page("CAD", video_fixture(f"fad-press-release-{d}/")[0])["url"] for d in DAYS["CAD"]]
    assert boc == ["https://www.bankofcanada.ca/multimedia/press-conference-monetary-policy-report-april-2026/",
                   "https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-june-2026/",
                   "https://www.bankofcanada.ca/multimedia/press-conference-monetary-policy-report-july-2026/",
                   "https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-september-2026/"]
    aud = {d: P.video_from_page("AUD", video_fixture(f"mc-gov-{d}.html")[0]) for d in DAYS["AUD"]}
    assert aud["2026-08-11"] == {"url": "https://youtu.be/-VdeRdWUgDc", "player": "YouTube", "id": "-VdeRdWUgDc", "duration": "47:30"}
    assert aud["2026-03-17"]["id"] == "PvCmnI_oEvE" and aud["2026-03-17"]["url"].startswith("https://www.youtube.com/watch?v=")
    assert P.video_from_page("GBP", video_fixture("july-2026")[0])["url"] == "https://www.youtube.com/watch?v=G5m9FOeBD1Q"
    assert P.video_from_page("GBP", video_fixture("april-2026")[0])["id"] == "VXYrFSBxhHU"


def test_video_from_page_is_none_when_the_page_has_no_player_and_for_banks_without_one():
    bare = "<html><body><p>Statement text with no player</p></body></html>"
    assert all(P.video_from_page(c, bare, "https://x") is None for c in ("USD", "CAD", "AUD", "GBP"))
    assert P.video_from_page("JPY", "<a href=\"https://youtu.be/AAAAAAAAAAA\" class=\"video-placeholder\">x</a>") is None      # no such source for the BoJ / SNB / RBNZ
    assert P.video_from_page("GBP", '<div class="video" data-video="G5m9FOeBD1Q"></div>') is None                     # a video outside the press-conference section is not the conference
    far = '<div data-video="AAAAAAAAAAA"></div><span id="press-conference"></span>' + " " * 3000 + '<div data-video="BBBBBBBBBBB"></div>'
    assert P.video_from_page("GBP", far) is None                                                                       # another video of the page, far from the section
    near = '<div data-video="AAAAAAAAAAA"></div><span id="press-conference"></span><div class="video" data-video="BBBBBBBBBBB"></div>'
    assert P.video_from_page("GBP", near)["id"] == "BBBBBBBBBBB"                                                        # the one that follows the heading


def test_ecb_landing_page_gives_the_last_conference_and_its_video():
    html, _ = video_fixture("index.en.html") if False else video_fixture("press_conference/html/index.en.html")
    assert P.ecb_landing_video(html) == (date(2026, 9, 10), "rCBHa4xjqvI")
    assert P.ecb_landing_video("<html><body>no conference</body></html>") is None
    assert P.ecb_landing_video('<a href="/press/x/ecb.is260910~abc.en.html"></a>') is None                          # a statement link without a video
