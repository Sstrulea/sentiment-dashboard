"""Audit F4 — freshness.policy_rates: a past meeting without its decision is stale."""
from __future__ import annotations

import pandas as pd
import yaml

from src import economic_render as er

MEETINGS = {"meetings": {
    "CHF": [{"date": "2025-06-19"}, {"date": "2025-09-25"}, {"date": "2025-12-11"},
            {"date": "2026-03-19"}, {"date": "2026-06-18"}, {"date": "2026-09-24"},
            {"date": "2026-12-10"}],
    "USD": [{"date": "2026-07-29"}, {"date": "2026-09-16"}, {"date": "2026-10-28"}],
}}
DECISIONS = [("CHF", "2025-09-25"), ("CHF", "2025-12-11"), ("CHF", "2026-03-19"),
             ("CHF", "2026-06-18"), ("USD", "2026-07-29"), ("USD", "2026-09-16")]


def _files(tmp_path, decisions=DECISIONS):
    m = tmp_path / "meetings.yaml"
    m.write_text(yaml.safe_dump(MEETINGS))
    d = tmp_path / "decisions.parquet"
    pd.DataFrame(decisions, columns=["currency", "meeting_date"]).to_parquet(d)
    return m, d


def test_snb_decision_missing_the_day_after_the_meeting_is_stale(tmp_path):
    m, d = _files(tmp_path)
    out = er._policy_rates_freshness(pd.Timestamp("2026-09-25T07:00"), m, d)
    assert out["per_currency"]["CHF"]["missing"] == ["2026-09-24"]
    assert out["stale_currencies"] == ["CHF"] and out["stale"]


def test_meeting_day_itself_is_not_due_yet(tmp_path):
    m, d = _files(tmp_path)
    out = er._policy_rates_freshness(pd.Timestamp("2026-09-24T12:00"), m, d)
    assert not out["stale"]
    # 2025-06-19 is older than the oldest decision kept (retention), not missing
    assert out["per_currency"]["CHF"]["missing"] == []


def test_decision_landed_clears_it(tmp_path):
    m, d = _files(tmp_path, DECISIONS + [("CHF", "2026-09-24")])
    out = er._policy_rates_freshness(pd.Timestamp("2026-09-25T07:00"), m, d)
    assert not out["stale"] and out["per_currency"]["CHF"]["last_decision"] == "2026-09-24"


def test_currency_without_any_decision_is_stale(tmp_path):
    m, d = _files(tmp_path, [r for r in DECISIONS if r[0] != "USD"])
    out = er._policy_rates_freshness(pd.Timestamp("2026-09-25T07:00"), m, d)
    assert out["per_currency"]["USD"]["stale"]


def test_rolls_into_any_stale(monkeypatch, tmp_path):
    m, d = _files(tmp_path)
    monkeypatch.setattr(er, "MEETINGS_YAML", m)
    monkeypatch.setattr("src.policy_rate.DECISIONS_PARQUET", d)
    fr = er._freshness(pd.Timestamp("2026-09-25T07:00"), trend_enabled=False)
    assert fr["policy_rates"]["stale"] and fr["any_stale"]


def test_repo_files_are_fresh_at_the_audit_as_of():
    out = er._policy_rates_freshness(pd.Timestamp("2026-09-23T07:06:11"))
    assert out is not None and out["stale_currencies"] == [], out
