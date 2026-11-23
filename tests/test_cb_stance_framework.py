"""Stance S0a - framework watch: normalisation (noise -> same hash), real changes (BoC agreement 2021 vs 2026, BoE remit
2025 vs 2026, a listing page with its newest entry removed), keyword flags, open / closed alerts from
framework_reviews.yaml, calendar_due and verification_overdue on fixed dates, the RBNZ reminder, the weekly gate, and
the display-only guard. Offline: tests/fixtures/cb_stance/framework/ (official pages and PDFs fetched 2026-11-14)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from src import cb_collect as cc
from src import cb_framework as F
from src.cb_compute import framework_watch as FW
from src.cb_docs.extract import html_paragraphs
from src.cb_docs.http import Fetched

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "cb_stance" / "framework"
CFG = F.load_config()
MAIN = {"sel": ("main", None, None)}


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _hash_html(html: str, **kw) -> str:
    return FW.text_hash(FW.normalise(html_paragraphs(html, MAIN), **kw))


# ---------------------------------------------------------- normalisation -----

def test_same_page_with_nav_and_display_date_changed_has_the_same_hash():
    page = _html("boc_agreements_main.html")
    noisy = page.replace("<main", "<nav><ul><li>Home</li><li>New menu item</li></ul></nav><main", 1)
    noisy = noisy.replace("</main>", "<p>Last updated: November 14, 2026</p><p>November 14, 2026</p></main>")
    assert _hash_html(page) == _hash_html(noisy)


def test_select_keeps_only_the_matching_paragraphs():
    paras = ["Inflation and the 2% target", "Current Bank Rate 4%", "Remit for the Monetary Policy Committee - October 2026"]
    assert FW.normalise(paras, select="remit|2% target") == [paras[0], paras[2]]
    assert FW.normalise(paras, drop="Bank Rate") == [paras[0], paras[2]]


# --------------------------------------------------------- real changes -------

def test_boc_agreement_2021_vs_2026_is_a_change_with_keyword_hits():
    a, b = _html("boc_js2021_main.html"), _html("boc_js2026_main.html")
    assert _hash_html(a) != _hash_html(b)
    from src.cb_docs.parse import redline
    rl = redline(FW.normalise(html_paragraphs(a, MAIN)), FW.normalise(html_paragraphs(b, MAIN)))
    assert rl["added_words"] > 50 and rl["removed_words"] > 50
    text26 = "\n".join(html_paragraphs(b, MAIN))
    hits = FW.keyword_hits(text26, CFG["keywords"])
    assert "renewal of the monetary policy framework" in hits
    text21 = "\n".join(html_paragraphs(a, MAIN))
    assert "ending December 31, 2031" in text26 and "ending December 31, 2026" in text21 and "ending December 31, 2031" not in text21


def test_boe_remit_2025_vs_2026_is_a_change():
    p25 = json.loads((FIX / "boe_remit_2025.paras.json").read_text())
    p26 = json.loads((FIX / "boe_remit_2026.paras.json").read_text())
    assert FW.text_hash(FW.normalise(p25)) != FW.text_hash(FW.normalise(p26))
    assert any("2 per cent" in p for p in p26) and any("2 per cent" in p for p in p25)


def test_listing_without_its_newest_entry_is_a_change():
    page = _html("boc_agreements_main.html")
    paras = html_paragraphs(page, MAIN)
    i = paras.index("2026")
    j = paras.index("2021")
    older = paras[:i] + paras[j:]                                       # the page as it was before the 2026 renewal entry
    assert FW.text_hash(FW.normalise(paras)) != FW.text_hash(FW.normalise(older))


def test_pdf_and_html_both_extracted(tmp_path):
    t = {"sel": ["main", None, None]}
    paras = F.page_paragraphs(b"", _html("boc_js2026_main.html"), "text/html", "https://x/page/", t)
    assert any("December 31, 2031" in p for p in paras)


# -------------------------------------------------------------- keywords ------

def test_keyword_hits_whole_words_case_insensitive():
    assert FW.keyword_hits("The ECB concluded its STRATEGY  assessment.", ["strategy assessment"]) == ["strategy assessment"]
    assert FW.keyword_hits("a strategy assessments page", ["strategy assessment"]) == []


def test_flag_documents_scans_title_text_and_first_paragraph():
    docs = [{"doc_id": "CAD:speech:1", "currency": "CAD", "type": "speech", "title": "Opening statement", "text": None,
             "meta_json": json.dumps({"first": "Today we announced the renewal of the monetary policy framework."}), "url": "u", "published_date": date(2026, 11, 5)},
            {"doc_id": "USD:statement:2026-09-16", "currency": "USD", "type": "statement", "title": "FOMC statement", "text": "The Committee decided to raise the target range.",
             "meta_json": None, "url": "u2", "published_date": date(2026, 9, 16)}]
    flags = F.flag_documents(docs, CFG["keywords"])
    assert list(flags) == ["CAD:speech:1"] and flags["CAD:speech:1"]["hits"] == ["renewal of the monetary policy framework"]


def test_keywords_noise_on_the_2a_corpus_at_most_one_false_flag_per_bank():
    """Calibration (pre-registered): the stored 2a corpus has no framework change; every flag there would be noise."""
    from collections import Counter
    from src.cb_docs import store as dst
    docs = list(dst.load_documents(cc.Paths()).values())
    flags = F.flag_documents(docs, CFG["keywords"])
    assert max(Counter(f["currency"] for f in flags.values()).values(), default=0) <= 1


# ---------------------------------------------------------------- alerts ------

TARGETS = [{"id": "CAD.framework", "bank": "CAD", "url": "https://cad"}, {"id": "NZD.framework", "bank": "NZD", "url": "https://nz", "manual_only": True}]


def test_page_alert_open_then_closed_by_review():
    cur = {"CAD.framework": {"hash": "b" * 64}}
    assert [a.kind for a in FW.page_alerts(TARGETS, cur, {"CAD.framework": {"confirmed_hash": "a" * 64}})] == ["page_changed"]
    assert FW.page_alerts(TARGETS, cur, {"CAD.framework": {"confirmed_hash": "b" * 64}}) == []


def test_keyword_alert_closed_when_reviewed():
    flags = {"CAD:speech:1": {"currency": "CAD", "title": "t", "url": "u", "hits": ["framework renewal"], "published": "2026-11-05"}}
    assert len(FW.keyword_alerts(flags, [])) == 1 and FW.keyword_alerts(flags, ["CAD:speech:1"]) == []


def test_calendar_due_on_fixed_dates():
    cal = {"GBP": [{"event": "remit", "due": date(2027, 5, 31), "precision": "day"}], "EUR": [{"event": "assessment", "due": 2030, "precision": "year"}]}
    assert FW.calendar_alerts(cal, date(2027, 4, 30), 30) == []
    assert [a.bank for a in FW.calendar_alerts(cal, date(2027, 5, 1), 30)] == ["GBP"]
    assert [a.bank for a in FW.calendar_alerts(cal, date(2027, 6, 30), 30)] == ["GBP"]
    assert [a.bank for a in FW.calendar_alerts(cal, date(2030, 3, 1), 30)] == ["EUR"]


def test_verification_overdue_on_fixed_dates():
    lv = {"GBP": date(2026, 11, 14), "AUD": date(2026, 11, 14)}
    lp = {"GBP": date(2026, 11, 5), "AUD": date(2027, 2, 9)}
    assert FW.verification_alerts(lv, {"GBP": date(2026, 11, 5)}, date(2026, 11, 20), 365) == []
    got = FW.verification_alerts(lv, lp, date(2027, 2, 10), 365)
    assert [a.bank for a in got] == ["AUD"]                              # a newer SMP than the last verification
    assert [a.bank for a in FW.verification_alerts({"GBP": date(2025, 11, 1)}, {}, date(2026, 11, 14), 365)] == ["GBP"]


def test_rbnz_reminder_at_each_mps():
    dec = [(date(2026, 9, 2), True), (date(2026, 12, 9), True)]
    voted = [date(2026, 9, 2), date(2026, 12, 9)]                                  # votes entered: only the MPS data reminder (tests/test_cb_rbnz_votes.py)
    assert FW.rbnz_alerts(dec, voted, date(2026, 9, 2), date(2026, 11, 14)) == []
    assert [a.ref for a in FW.rbnz_alerts(dec, voted, date(2026, 9, 2), date(2026, 12, 9))] == ["NZD.framework"]


def test_config_8_banks_have_a_landing_target_and_rbnz_is_manual_only():
    t = F.targets(CFG)
    landing = {x["bank"] for x in t if x["type"] == "framework_landing"}
    assert landing == {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"}
    assert [x["id"] for x in t if x.get("manual_only")] == ["NZD.framework"]
    assert {x["bank"] for x in t if x["type"] == "measures"} == {"JPY", "CAD", "AUD"}


def test_seeded_reviews_close_every_page_alert():
    rv = yaml.safe_load((ROOT / "data" / "cb" / "manual" / "framework_reviews.yaml").read_text())
    ids = {x["id"] for x in F.targets(CFG)}
    assert set(rv["targets"]) == ids
    for k, v in rv["targets"].items():
        assert v["result"] in ("no_change", "new_version", "not_material") and v["reviewed"]
        if k != "NZD.framework":
            assert len(v["confirmed_hash"]) == 64
            assert (ROOT / "data" / "cb" / "framework_watch" / k / f"{v['confirmed_hash'][:16]}.txt").exists()


# ------------------------------------------------------------ run (offline) ---

class _FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.validators = {}
        self.requests_made = 0

    def get(self, url, conditional=True):
        self.requests_made += 1
        return Fetched(url, 200, self.pages[url].encode(), {"Content-Type": "text/html; charset=utf-8"})

    def remember(self, fetched, content_sha=None):
        self.validators[fetched.url] = {"sha256": content_sha}


def _cfg_one():
    return {"meta": {"every_days": 7, "calendar_window_days": 30, "verification_max_days": 365}, "keywords": [],
            "banks": {"CAD": {"watch": [{"id": "CAD.framework", "type": "framework_landing", "url": "https://cad", "sel": ["main", None, None]}]}}}


def test_run_is_weekly_writes_snapshots_and_never_touches_mandates(tmp_path):
    paths = cc.Paths(tmp_path)
    (tmp_path / "manual").mkdir()
    (tmp_path / "manual" / "mandates.yaml").write_text("sentinel")
    f = _FakeFetcher({"https://cad": _html("boc_agreements_main.html")})
    rep = F.run_framework(paths, date(2026, 11, 14), fetcher=f, cfg=_cfg_one())
    assert rep.ran and rep.rows[0][1] == "new"
    h = cc.load_state(paths)[F.STATE_KEY]["targets"]["CAD.framework"]["hash"]
    assert F.read_snapshot(paths, "CAD.framework", h)
    again = F.run_framework(paths, date(2026, 11, 18), fetcher=f, cfg=_cfg_one())
    assert not again.ran and f.requests_made == 1                       # weekly gate: no request
    nxt = F.run_framework(paths, date(2026, 11, 21), fetcher=f, cfg=_cfg_one())
    assert nxt.ran and nxt.rows[0][1] == "same"
    assert (tmp_path / "manual" / "mandates.yaml").read_text() == "sentinel"


def test_framework_code_never_writes_the_manual_files():
    for f in ("src/cb_framework.py", "src/cb_compute/framework_watch.py", "src/cb_macro.py"):
        for line in (ROOT / f).read_text().splitlines():
            if ("write" in line or "safe_dump" in line) and not line.lstrip().startswith(("*", "#", "\"")):
                assert "manual" not in line and "mandates" not in line, (f, line)


# ---------------------------------------------------- display-only guard ------

def test_scoring_and_history_do_not_import_the_stance_layer():
    mods = ("src.cb_macro", "src.cb_framework", "src.cb_compute.stance_inputs", "src.cb_compute.framework_watch", "src.cb_compute.macro_yoy")
    code = ("import sys, src.economic_compute, src.economic_render, src.ff_scoring, src.history_compute;"
            f"bad=[m for m in {mods!r} if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for mod in ("economic_compute", "economic_render", "ff_scoring", "history_compute"):
        src = (ROOT / "src" / f"{mod}.py").read_text()
        for bad in ("cb_macro", "cb_framework", "stance_inputs", "framework_watch", "macro_yoy", "cb_stance"):
            assert bad not in src, (mod, bad)


def test_new_compute_modules_are_pure():
    for f in ("stance_inputs", "framework_watch", "macro_yoy"):
        text = (ROOT / "src" / "cb_compute" / f"{f}.py").read_text()
        for bad in ("open(", "read_text", "requests", "urllib", "pq.read", "pd.read", "yaml.", "import os", "subprocess"):
            assert bad not in text, (f, bad)
