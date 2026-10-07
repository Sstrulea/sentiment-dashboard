"""/carry reads the policy rate from data/cb/decisions.parquet (the latest
decision at as_of, src.policy_rate.policy_rates_at); data/policy_rates.yaml is a
per-currency fallback. Staleness per leg, on the server: the meeting check for
decision legs, the `verified` rule for fallback legs. 0.00 (CHF) is a rate."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.carry_compute import leg_configured
from src.carry_render import build_carry_payload
from src.policy_rate import load_decisions, policy_rates_at

T = pd.Timestamp


def _decision(ccy, bank, meeting, time, after, effective, lower=None, upper=None):
    return {"bank": bank, "currency": ccy, "meeting_date": T(meeting).date(),
            "decision_time_utc": None if time is None else T(time, tz="UTC"),
            "rate_before": None, "rate_after": after, "lower": lower, "upper": upper, "delta_bp": None,
            "effective_date": T(effective).date(), "consensus": None, "surprise_consensus_bp": None,
            "rate_source": f"{bank}:test", "status": "official", "notes": ""}


def _parquet(tmp_path, rows):
    p = tmp_path / "decisions.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


ROWS = [
    _decision("USD", "fed", "2026-07-29", "2026-07-29 18:00", 4.125, "2026-07-30", 4.0, 4.25),
    _decision("USD", "fed", "2026-09-16", "2026-09-16 18:00", 3.875, "2026-09-17", 3.75, 4.0),
    _decision("CHF", "snb", "2026-09-24", "2026-09-24 07:30", 0.0, "2026-09-25"),
    _decision("EUR", "ecb", "2026-06-04", "2026-06-04 12:15", 2.5, "2026-06-10"),
]


def _manual(tmp_path, rates, stale_after_days=45):
    p = tmp_path / "policy_rates.yaml"
    p.write_text(yaml.safe_dump({"meta": {"stale_after_days": stale_after_days}, "rates": rates}))
    return p


def _leg(rate, verified, url="https://example.org"):
    return {"rate_pct": rate, "effective": "2026-01-01", "verified": verified,
            "source_name": "x", "source_url": url}


MANUAL = {"USD": _leg(3.50, "2026-09-30"), "EUR": _leg(2.25, "2026-09-30"),
          "CHF": _leg(0.25, "2026-09-30"), "GBP": _leg(3.75, "2026-07-01")}


def _meetings(tmp_path, meetings):
    p = tmp_path / "meetings.yaml"
    p.write_text(yaml.safe_dump({"meetings": meetings}))
    return p


def test_latest_decision_before_and_after_a_decision_date(tmp_path):
    d = load_decisions(_parquet(tmp_path, ROWS))
    before = policy_rates_at(d, T("2026-09-16 17:59"))
    after = policy_rates_at(d, T("2026-09-16 18:00"))
    assert before["USD"]["rate_pct"] == 4.125 and before["USD"]["meeting_date"] == "2026-07-29"
    assert after["USD"]["rate_pct"] == 3.875 and after["USD"]["effective_date"] == "2026-09-17"
    assert (after["USD"]["lower"], after["USD"]["upper"]) == (3.75, 4.0)
    assert "CHF" not in policy_rates_at(d, T("2026-09-24 07:00"))       # nothing at/before as_of


def test_chf_zero_is_a_real_rate(tmp_path):
    d = load_decisions(_parquet(tmp_path, ROWS))
    chf = policy_rates_at(d, T("2026-10-07"))["CHF"]
    assert chf["rate_pct"] == 0.0
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, {}))
    leg = p["rates"]["CHF"]
    assert leg["source"] == "cb_decisions" and leg["rate_pct"] == 0.0 and leg_configured(leg)
    row = next(r for r in p["rows"] if r["symbol"] == "USDCHF")
    assert row["available"] and row["carry_pct"] == pytest.approx(3.875)


def test_decisions_win_and_a_currency_without_a_decision_falls_back(tmp_path):
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, {}))
    r = p["rates"]
    assert (r["USD"]["source"], r["USD"]["rate_pct"]) == ("cb_decisions", 3.875)     # not the yaml 3.50
    assert (r["GBP"]["source"], r["GBP"]["rate_pct"]) == ("manual", 3.75)            # no GBP decision
    assert r["USD"]["source_url"] == "https://example.org"                          # the link stays
    assert p["configured"] == "4/4"


def test_missing_parquet_falls_back_to_the_yaml_everywhere(tmp_path):
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=tmp_path / "nope.parquet", meetings_path=_meetings(tmp_path, {}))
    assert {c: (l["source"], l["rate_pct"]) for c, l in p["rates"].items()} == {
        "USD": ("manual", 3.50), "EUR": ("manual", 2.25), "CHF": ("manual", 0.25), "GBP": ("manual", 3.75)}


def test_decision_leg_stale_only_from_the_meeting_check(tmp_path):
    # EUR decided 2026-06-04 (> 45 days before as_of): NOT stale while no meeting is missing
    meetings = {"EUR": [{"date": "2026-06-04"}], "USD": [{"date": "2026-07-29"}, {"date": "2026-09-16"}]}
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, meetings))
    assert p["rates"]["EUR"]["stale"] is False and p["rates"]["USD"]["stale"] is False
    # an ECB meeting on 2026-09-10 with no decision in the parquet -> EUR stale
    meetings["EUR"].append({"date": "2026-09-10"})
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, meetings))
    eur = p["rates"]["EUR"]
    assert eur["stale"] is True and "2026-09-10" in eur["stale_reason"]
    assert next(r for r in p["rows"] if r["symbol"] == "EURUSD")["stale"] is True


def test_fallback_leg_stale_from_verified(tmp_path):
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, MANUAL),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, {}))
    gbp = p["rates"]["GBP"]                       # manual, verified 2026-07-01 = 98 days
    assert gbp["source"] == "manual" and gbp["stale"] is True and "verified 2026-07-01" in gbp["stale_reason"]
    assert next(r for r in p["rows"] if r["symbol"] == "GBPUSD")["stale"] is True
    fresh = dict(MANUAL, GBP=_leg(3.75, "2026-09-30"))
    p = build_carry_payload(T("2026-10-07 12:00"), rates_yaml=_manual(tmp_path, fresh),
                            decisions_path=_parquet(tmp_path, ROWS), meetings_path=_meetings(tmp_path, {}))
    assert p["rates"]["GBP"]["stale"] is False
