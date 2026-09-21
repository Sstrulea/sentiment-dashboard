"""Phase 2a in the payloads: the latest-decision block (statement text, redline, votes, follow-up links), the documents timeline, the
speeches, the votes / statement / press-conference columns of the decisions table. Built from the frozen real documents, offline."""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src import cb_collect as cc
from src import cb_render
from src.cb_compute import payload as P
from src.cb_docs import collect as C
from src.cb_loader import load_context, load_pair_defs

from .cb_docs_helpers import FakeSession
from .test_cb_docs_collect import ENGINE_FIX, data_dir, fetcher
from .test_cb_docs_parse import DAYS

ASOF = date(2026, 9, 18)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = data_dir(tmp_path_factory.mktemp("payload"))
    for sub in ("market_quotes", "official_series"):
        shutil.copytree(ENGINE_FIX / sub, d / sub)
    for f in ("projections.parquet",):
        shutil.copy(ENGINE_FIX / f, d / f)
    C.run_documents(cc.Paths(d), date(2026, 9, 20), fetcher=fetcher(FakeSession()), roster=C.load_roster(), now=datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
    ctx = load_context(d)
    return P.build(ctx, ASOF, load_pair_defs()), d


def docs(built, ccy):
    return built[0]["banks"][ccy]["documents"]


def test_latest_decision_block_of_the_fed(built):
    L = docs(built, "USD")["latest"]
    assert L["meeting"] == "2026-09-16" and L["summary"] == {"status": "pending", "label": "summary pending", "doc_id": "USD:statement:2026-09-16", "reason": "not generated yet"}
    st = L["statement"]
    assert st["rate_after"] == 3.875 and len(st["paragraphs"]) == 4 and st["paragraphs"][0].startswith("The Federal Open Market Committee approved")
    assert st["url"].endswith("monetary20260916a.htm") and len(st["sha256"]) == 64 and "copyright of the bank" in st["license"]
    rl = L["redline"]
    assert rl["prev_meeting"] == "2026-07-29" and rl["added_words"] > 0 and rl["removed_words"] > 0 and {x["kind"] for x in rl["paras"]} >= {"changed"}
    assert L["votes"]["label"] == "12–0" and L["votes"]["kind"] == "unanimous"
    by = {i["type"]: i for i in L["follow_up"]}
    assert by["minutes"]["available"] is False and by["minutes"]["na"] == "expected around 2026-10-07 (+21 d after the decision)"
    assert by["presser_transcript"]["available"] and by["presser_transcript"]["url"].endswith("FOMCpresconf20260916.pdf")
    assert by["presser_video"]["available"] and by["presser_video"]["url"].endswith("fomcpresconf20260916.htm")           # the page of the Fed carries its video


def test_redline_payload_reproduces_the_statement_from_the_previous_one(built):
    from src.cb_docs import parse as PP
    L = docs(built, "USD")["latest"]
    prev = next(t for t in docs(built, "USD")["timeline"] if t["meeting"] == "2026-07-29")
    prev_text = built[0]["banks"]["USD"]["documents"]                                                       # the previous statement text is in the store
    from tests.cb_docs_helpers import statement_paras
    assert PP.apply_redline(statement_paras("USD", "2026-07-29"), L["redline"]) == L["statement"]["paragraphs"]


def test_decisions_table_columns_are_populated(built):
    exp = {"USD": ["12–0", "9–3", "12–0", "8–4"], "GBP": ["6–3", "6–3", "7–2", "8–1"], "JPY": ["7–2", "8–1", "7–1", "6–3"],
           "AUD": ["unanimous", "unanimous", "8–1", "5–4"], "EUR": ["not published"] * 4, "CAD": ["not published"] * 4, "CHF": ["not published"] * 4,
           "NZD": ["consensus"] * 4}
    for ccy, labels in exp.items():
        rows = built[0]["banks"][ccy]["decisions"]
        assert [r["slots"]["votes"]["label"] for r in rows] == labels, ccy
        assert all(r["summary"]["label"] == "summary pending" and r["summary"]["status"] == "pending" for r in rows)
    usd = built[0]["banks"]["USD"]["decisions"]
    assert all(r["slots"]["statement"]["url"].startswith("https://www.federalreserve.gov/") for r in usd)
    assert all(set(r["slots"]["conference"]) == {"presser_video", "presser_transcript"} for r in usd)
    cad = built[0]["banks"]["CAD"]["decisions"]
    assert all(set(r["slots"]["conference"]) == {"presser_video", "opening_statement"} for r in cad)
    assert built[0]["banks"]["NZD"]["decisions"][0]["slots"]["statement"] is None                              # RBNZ: nothing fetched


def test_votes_names_directions_and_notes_are_in_the_payload(built):
    boj = docs(built, "JPY")["latest"]["votes"]
    assert [(a["name"], a["direction"]) for a in boj["against"]] == [("Toichiro Asada", "hold"), ("Ayano Sato", "hold")] and len(boj["for"]) == 7
    boe = docs(built, "GBP")["latest"]["votes"]
    assert boe["source"] == "summary+xlsx" and sorted(a["name"] for a in boe["against"]) == ["Catherine L Mann", "Huw Pill", "Megan Greene"]
    fed = [t for t in docs(built, "USD")["timeline"] if t["meeting"] == "2026-04-29"][0]["votes"]
    assert fed["label"] == "8–4" and [a["direction"] for a in fed["against"]] == ["lower", "hold", "hold", "hold"]
    eur = docs(built, "EUR")["latest"]["votes"]
    assert eur["kind"] == "not_published" and eur["label"] == "not published" and eur["n_for"] is None


def test_timeline_has_the_last_four_meetings_with_their_follow_up_documents(built):
    tl = docs(built, "AUD")["timeline"]
    assert [t["meeting"] for t in tl] == ["2026-08-11", "2026-06-16", "2026-05-05", "2026-03-17"]
    assert all(t["statement"]["available"] and any(i["type"] == "minutes" and i["available"] for i in t["follow_up"]) for t in tl)
    chf = docs(built, "CHF")["timeline"][0]
    assert {i["type"] for i in chf["follow_up"] if i["available"]} == {"opening_statement"}                        # the SNB summary is not in the fixtures
    assert next(i for i in chf["follow_up"] if i["type"] == "deliberations")["na"].startswith("overdue: expected around 2026-07-16")
    eur = docs(built, "EUR")["timeline"]
    assert any(i["type"] == "account" and not i["available"] and i["na"].startswith("overdue") for i in eur[-1]["follow_up"])


def test_speeches_are_listed_with_relevance_weight_and_voter_flags(built):
    d = docs(built, "USD")
    assert d["n_speeches"] > 3 and d["speeches"] == sorted(d["speeches"], key=lambda x: (x["published"], x["doc_id"]), reverse=True)
    assert {x["relevance"] for x in d["speeches"]} >= {"monetary"} and all(x["weight"] in (1, 2, 3) for x in d["speeches"])
    named = [x for x in d["speeches"] if x["speaker"]]
    assert named and all(x["role"] for x in named)                                                            # the roster gave every Fed speaker a role
    assert any(x["chair"] and x["weight"] == 1 for x in named) or not any(x["chair"] for x in named)


def test_rbnz_documents_are_manual_only_and_say_why(built):
    nz = docs(built, "NZD")
    assert nz["manual_only"] and nz["latest"]["statement"] is None and "Cloudflare" in nz["latest"]["na"]


def test_payload_is_strict_json_and_the_shell_stays_empty(built, tmp_path):
    json.dumps(built[0]["banks"]["USD"], allow_nan=False)
    res = cb_render.render(built[1], tmp_path, ASOF)
    html = (tmp_path / "central-banks" / "usd.html").read_text()
    assert "Federal Open Market Committee approved" not in html and "12–0" not in html                    # nothing of the texts is in the HTML
    data = json.loads((tmp_path / "data" / "cb" / "usd.json").read_text())
    assert data["documents"]["latest"]["statement"]["paragraphs"]
    assert len(res["files"]) == 47


def test_a_meeting_without_a_video_says_why_per_bank(built):
    def latest(ccy: str) -> dict:
        timeline = docs(built, ccy)["timeline"]
        return {i["type"]: i for i in max(timeline, key=lambda m: m["meeting"])["follow_up"]}
    assert "YouTube channel" in latest("JPY")["presser_video"]["na"] and "robots.txt" in latest("JPY")["presser_video"]["na"]
    assert "robots.txt" in latest("CHF")["presser_video"]["na"]
    assert "pooled broadcast interview" in latest("GBP")["presser_video"]["na"]                                        # 17 Sep: no MPR press conference
    assert all(latest(c)["presser_video"]["na"].endswith("(a link can be added in data/cb/manual/documents.yaml)") for c in ("JPY", "CHF", "GBP"))
    assert latest("USD")["presser_video"]["available"] and latest("USD")["presser_video"]["url"].endswith("fomcpresconf20260916.htm")


def test_a_non_document_is_not_listed_as_a_speech(built):
    speeches = docs(built, "CAD")["speeches"]
    assert speeches and not any("/media-availability" in x["url"] for x in speeches) and all(x["relevance"] in ("monetary", "other") for x in speeches)
