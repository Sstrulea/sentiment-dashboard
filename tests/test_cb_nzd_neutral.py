"""fix/cb-data-gaps: the RBNZ neutral from the September 2026 MPS data file (sheet 6.4), combined by config/cb_stance.yaml
neutral_combine.NZD: mid = the 'policy horizon' estimate, range = min-max of the estimates. The other banks are unchanged."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.cb_compute import stance as ST
from src import cb_datasets as ds

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "cb" / "manual" / "neutral.yaml").read_text())
ASOF = date(2026, 11, 23)
STAR = {"EUR": 2.0, "GBP": 2.0, "JPY": 2.0, "CAD": 2.0, "AUD": 2.5, "NZD": 2.0, "CHF": None}
ROSTER = yaml.safe_load((ROOT / "config" / "cb_roster.yaml").read_text())


def neutral(cur: str, doc: dict = DOC):
    members = {p["name"] for p in ROSTER.get("people", []) if p.get("currency") == cur}
    return ST.neutral_from_estimates(cur, doc["banks"][cur], STAR[cur], ASOF, members, 15)


def test_the_rule_is_the_preregistered_one():
    cfg = yaml.safe_load((ROOT / "config" / "cb_stance.yaml").read_text())
    cur = next(v for v in cfg["versions"] if v["version"] == cfg["meta"]["current"])
    assert cur["neutral_combine"]["NZD"] == "mid = the 'policy horizon' estimate when published; range = min-max of the estimates"


def test_nzd_mid_3_5_range_2_3_to_4_1():
    n, why = neutral("NZD")
    assert why == "" and (n.mid, n.lo, n.hi) == (3.5, 2.3, 4.1)
    assert n.asof == "2026-09-02" and n.flag == "BANK_ESTIMATE" and not n.stale and n.nominal_from == "nominal"
    assert [lbl for lbl, _ in n.used] == ["Long-term (mean), 2026Q3", "Forecast horizon (mean), 2026Q3", "Short-term (mean), 2026Q3",
                                          "Lower bound of long-run neutral OCR suite, 2026Q3", "Upper bound of long-run neutral OCR suite, 2026Q3"]


def test_the_file_entry():
    e = DOC["banks"]["NZD"]
    assert e["status"] == "filled" and e["flag"] == "BANK_ESTIMATE" and "reason" not in e and "next_step" not in e
    assert [x for x in e["estimates"] if x.get("policy_horizon")] == [e["estimates"][1]] and e["estimates"][1]["point"] == 3.5
    for x in e["estimates"]:
        assert x["basis"] == "nominal" and x["published"] == date(2026, 9, 2) and x["transcription"] == "file"
        assert x["source"]["url"].endswith("/2026/sep-2926/mpssep26-data.xlsx") and x["source"]["retrieved"] == date(2026, 11, 23)
        assert "sheet '6.4'" in x["source"]["page"] and "row 65" in x["source"]["page"]
    assert ds.validate_neutral(DOC, ASOF) == []


def test_without_one_policy_horizon_estimate_it_is_na():
    doc = copy.deepcopy(DOC)
    doc["banks"]["NZD"]["estimates"][1].pop("policy_horizon")
    n, why = neutral("NZD", doc)
    assert n is None and "'policy horizon'" in why
    doc["banks"]["NZD"]["estimates"][0]["policy_horizon"] = True
    doc["banks"]["NZD"]["estimates"][1]["policy_horizon"] = True
    assert any("at most one 'policy horizon'" in e for e in ds.validate_neutral(doc, ASOF))


@pytest.mark.parametrize("cur, want", [("EUR", (2.0, 1.75, 2.25)), ("GBP", (2.9375, 2.75, 3.0)), ("JPY", (1.8, 1.1, 2.5)),
                                       ("CAD", (2.75, 2.25, 3.25)), ("AUD", (3.490555140314484, 3.02024403284537, 4.416598826587419))])
def test_the_other_banks_are_unchanged(cur, want):
    n, _ = neutral(cur)
    assert (n.mid, n.lo, n.hi) == pytest.approx(want, abs=1e-12)
    doc = copy.deepcopy(DOC)                                       # a 'policy_horizon' mark elsewhere changes nothing: the branch is NZD only
    doc["banks"][cur]["estimates"][0]["policy_horizon"] = True
    m, _ = neutral(cur, doc)
    assert (m.mid, m.lo, m.hi) == (n.mid, n.lo, n.hi)
    assert neutral("CHF")[0] is None
