"""fix/cb-data-gaps: the RBNZ votes typed by hand in data/cb/manual/rbnz.yaml `votes` (the site is bot-walled; since May 2026 the RBNZ
publishes the vote when the Committee does not reach consensus). The kinds of the file, the manual row replacing the old 'consensus'
assumption, a decision without an entry (no row), the validation and the reminder after each decision."""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from src import cb_datasets as ds
from src.cb_compute import framework_watch as FW
from src.cb_docs import collect as C
from src.cb_docs import store as ST

ROOT = Path(__file__).resolve().parents[1]
RBNZ = ds.load_rbnz(ROOT / "data" / "cb" / "manual" / "rbnz.yaml")


def test_the_file_is_valid_and_has_the_six_2026_decisions():
    assert ds.validate_rbnz(RBNZ) == []
    v = ds.rbnz_votes(RBNZ)
    assert {d.isoformat(): e["kind"] for d, e in v.items()} == {
        "2026-02-18": "consensus", "2026-04-08": "consensus", "2026-05-27": "counted", "2026-07-08": "consensus", "2026-09-02": "consensus",
        "2026-10-28": "counted"}
    may, oct_ = v[date(2026, 5, 27)], v[date(2026, 10, 28)]
    assert (may["n_for"], may["n_against"]) == (3, 3) and "casting vote" in may["note"] and "casting vote" in may["source"]["quote"]
    assert [a["name"] for a in may["against"]] == ["Carl Hansen", "Hayley Gourley", "Prasanna Gai"]
    assert (oct_["n_for"], oct_["n_against"]) == (4, 3) and {a["direction"] for a in oct_["against"]} == {"raise"}
    assert oct_["for"] == ["Anna Breman", "Carl Hansen", "Rebecca Williams", "Prasanna Gai"]
    assert all(e["source"]["retrieved"] == date(2026, 11, 23) and e["source"]["url"].startswith("https://www.rbnz.govt.nz/news-and-events/news/2026/")
               for e in v.values())


@pytest.mark.parametrize("mutate, msg", [
    (lambda e: e.update(kind="majority"), "kind must be"),
    (lambda e: e.update(n_for=None), "counted needs n_for"),
    (lambda e: e.update(n_against=2), "do not match the names"),
    (lambda e: e["against"][0].update(direction="up"), "against must be"),
    (lambda e: e.update(source={"url": "x"}), "source needs url, quote and retrieved"),
])
def test_validation_rejects_a_bad_entry(mutate, msg):
    doc = copy.deepcopy(RBNZ)
    mutate(next(e for e in doc["votes"] if e["date"] == date(2026, 10, 28)))
    assert any(msg in e for e in ds.validate_rbnz(doc))
    doc = copy.deepcopy(RBNZ)
    doc["votes"][0].update(n_for=5)
    assert any("consensus has no count" in e for e in ds.validate_rbnz(doc))
    doc["votes"] = RBNZ["votes"] + [RBNZ["votes"][0]]
    assert any("duplicate decision" in e for e in ds.validate_rbnz(doc))


class _Paths:
    def __init__(self, d: Path):
        self.manual = d


def _run(tmp_path: Path, old_rows: list, votes_section) -> C._Run:
    doc = copy.deepcopy(RBNZ)
    if votes_section is None:
        doc.pop("votes")
    else:
        doc["votes"] = votes_section
    import yaml
    (tmp_path / "rbnz.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    run = object.__new__(C._Run)
    run.paths, run.rep, run.votes = _Paths(tmp_path), C.DocsReport(), {(r["currency"], r["meeting_date"]): r for r in old_rows}
    run.cfg = {"NZD": {"id": "rbnz"}}
    run.rbnz_votes()
    return run


def _old(ccy: str, d: date, kind="consensus", source="n/a") -> dict:
    return {"currency": ccy, "meeting_date": d, "bank": "x", "kind": kind, "n_for": None, "n_against": None, "for_names": "[]", "against": "[]",
            "source": source, "source_doc_id": "", "evidence": "consensus", "notes": ""}


def test_the_manual_rows_replace_the_old_consensus_rows(tmp_path):
    old = [_old("NZD", date(2026, 10, 28)), _old("NZD", date(2026, 5, 27)), _old("NZD", date(2025, 11, 26)), _old("USD", date(2026, 10, 28), "counted", "statement")]
    run = _run(tmp_path, old, RBNZ["votes"])
    nz = {d: r for (c, d), r in run.votes.items() if c == "NZD"}
    assert sorted(nz) == sorted(ds.rbnz_votes(RBNZ))                                    # 2025-11-26 (assumed consensus, no entry) is gone
    o = nz[date(2026, 10, 28)]
    assert (o["kind"], o["n_for"], o["n_against"], o["source"]) == ("counted", 4, 3, "manual")
    assert json.loads(o["against"])[0] == {"name": "Hayley Gourley", "direction": "raise", "note": "favoured a 25 bp increase to 3.00%"}
    assert "Rebecca Williams" in json.loads(o["for_names"]) and o["evidence"].startswith("The Monetary Policy Committee today voted 4 - 3")
    assert "casting vote" in nz[date(2026, 5, 27)]["notes"] and nz[date(2026, 2, 18)]["kind"] == "consensus"
    assert run.votes[("USD", date(2026, 10, 28))]["source"] == "statement"           # other banks untouched
    assert C.VOTE_RANK["manual"] > max(v for k, v in C.VOTE_RANK.items() if k != "manual")


def test_a_decision_without_an_entry_has_no_row(tmp_path):
    run = _run(tmp_path, [_old("NZD", date(2026, 10, 28))], [e for e in RBNZ["votes"] if e["date"] != date(2026, 10, 28)])
    assert ("NZD", date(2026, 10, 28)) not in run.votes and ("NZD", date(2026, 9, 2)) in run.votes
    run = _run(tmp_path, [_old("NZD", date(2026, 10, 28))], None)                       # no section at all: no NZD row
    assert not [k for k in run.votes if k[0] == "NZD"]


def test_vote_label_and_stance_line():
    from src.cb_compute.payload import _vote_line, vote_label
    row = {"kind": "counted", "n_for": 4, "n_against": 3}
    lab = vote_label(row)
    assert lab == "4–3"
    v = {"kind": "counted", "label": lab, "n_against": 3, "against": [{"direction": "raise"}] * 3}
    assert "".join(_vote_line(v, "2026-10-28")["segs"]) == "Last decision 28 Oct: vote 4–3 (3 for a hike)"
    assert "".join(_vote_line(None, "2026-12-09")["segs"]) == "Last decision 9 Dec: vote not recorded"


# ---- the reminder ----

DEC = [(date(2026, 9, 2), True), (date(2026, 10, 28), False), (date(2026, 12, 9), True), (date(2027, 2, 10), False)]
VOTED = [date(2026, 2, 18), date(2026, 9, 2), date(2026, 10, 28)]


def test_reminder_after_each_decision_until_its_vote_is_entered():
    assert FW.rbnz_alerts(DEC, VOTED, date(2026, 9, 2), date(2026, 11, 23)) == []              # every past decision has its vote, the MPS is reviewed
    got = FW.rbnz_alerts(DEC, VOTED[:-1], date(2026, 9, 2), date(2026, 11, 23))               # the 28 Oct Review: vote missing
    assert [a.ref for a in got] == ["NZD.vote.2026-10-28"] and "Review" in got[0].detail and "votes" in got[0].detail
    got = FW.rbnz_alerts(DEC, VOTED, date(2026, 9, 2), date(2026, 12, 9))                     # the 9 Dec MPS: its vote AND its data
    assert [a.ref for a in got] == ["NZD.vote.2026-12-09", "NZD.framework"]
    assert all(w in got[1].detail for w in ("OCR track", "forecasts", "neutral"))
    got = FW.rbnz_alerts(DEC, VOTED + [date(2026, 12, 9)], date(2026, 12, 9), date(2026, 12, 10))
    assert got == []                                                                              # vote entered, MPS reviewed
    got = FW.rbnz_alerts(DEC, VOTED + [date(2026, 12, 9)], date(2026, 9, 2), date(2026, 12, 10))
    assert [a.ref for a in got] == ["NZD.framework"]                                              # vote entered, the MPS data not yet


def test_reminder_without_a_votes_section_only_for_the_latest_decision():
    got = FW.rbnz_alerts(DEC, [], date(2026, 9, 2), date(2026, 11, 23))
    assert [a.ref for a in got] == ["NZD.vote.2026-10-28"]


def test_the_live_files_raise_no_rbnz_reminder_today():
    from src import cb_collect as cc
    from src import cb_framework as F
    alerts = [a for a in F.open_alerts(cc.Paths(), date(2026, 11, 23)) if a.kind == "rbnz_manual"]
    assert alerts == []
