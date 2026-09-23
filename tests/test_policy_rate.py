"""Audit 1.6 — the displayed policy rate comes only from data/cb/decisions.parquet."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import yaml

from src.ff_scoring import SCORING_COLUMNS
from src.policy_rate import (DECISIONS_PARQUET, KEY, decision_rows, display_fields,
                             load_decisions, replace_in_calendar)

ROOT = DECISIONS_PARQUET.parents[2]
POLICY_RATES_YAML = ROOT / "data" / "policy_rates.yaml"


def _decisions(rows):
    cols = ["bank", "currency", "meeting_date", "decision_time_utc", "rate_before",
            "rate_after", "lower", "upper", "effective_date", "consensus",
            "rate_source", "status"]
    df = pd.DataFrame(rows, columns=cols)
    df["decision_time_utc"] = pd.to_datetime(df["decision_time_utc"], utc=True)
    t = df["decision_time_utc"].dt.tz_localize(None)
    df["time_known"] = t.notna()
    df["release_dt"] = t.fillna(pd.to_datetime(df["meeting_date"]))
    return df


SYNTH = _decisions([
    ("fed", "USD", "2026-09-16", "2026-09-16 18:00", 3.625, 3.875, 3.75, 4.00, "2026-09-17", 3.875, "fred", "official"),
    ("boj", "JPY", "2026-09-18", None, 1.00, 1.25, np.nan, np.nan, "2026-09-24", 1.25, "statement", "statement"),
    ("ecb", "EUR", "2026-10-29", "2026-10-29 13:15", 2.50, 2.75, np.nan, np.nan, "2026-11-04", 2.75, "ecb:DFR", "official"),
])


def test_decision_rows_conventions_and_no_lookahead():
    rows = decision_rows(SYNTH, pd.Timestamp("2026-09-23T07:06"), SCORING_COLUMNS)
    assert list(rows.columns) == SCORING_COLUMNS
    assert set(rows["currency"]) == {"USD", "JPY"}          # the EUR row is in the future
    usd = rows[rows.currency == "USD"].iloc[0]
    assert (usd.actual, usd.previous, usd.source) == (3.875, 3.625, "cb")   # Fed midpoint
    jpy = rows[rows.currency == "JPY"].iloc[0]
    assert jpy.release_dt == pd.Timestamp("2026-09-18")      # no time -> meeting date


def test_ff_jb_and_manual_policy_rows_are_replaced():
    cal = pd.DataFrame([
        {"currency": "USD", "indicator_key": KEY, "release_dt": pd.Timestamp("2026-09-16 18:00"),
         "actual": 4.00, "consensus": 4.00, "previous": 3.75, "source": "ff", "name_raw": "x"},
        {"currency": "JPY", "indicator_key": KEY, "release_dt": pd.Timestamp("2026-09-18 02:54"),
         "actual": 1.25, "consensus": 1.25, "previous": 1.0, "source": "manual", "name_raw": None},
        {"currency": "USD", "indicator_key": "cpi_yoy", "release_dt": pd.Timestamp("2026-09-11 12:30"),
         "actual": 3.4, "consensus": 3.4, "previous": 3.4, "source": "ff", "name_raw": "CPI y/y"},
    ], columns=SCORING_COLUMNS)
    out = replace_in_calendar(cal, SYNTH, pd.Timestamp("2026-09-23"))
    ird = out[out.indicator_key == KEY]
    assert set(ird["source"]) == {"cb"} and len(ird) == 2
    assert len(out[out.indicator_key == "cpi_yoy"]) == 1


def test_fed_range_in_display_fields():
    f = display_fields(SYNTH, "USD", "2026-09-16T18:00:00")
    assert f["range"] == {"lower": 3.75, "upper": 4.00} and f["source"] == "cb_decisions"
    assert "range" not in display_fields(SYNTH, "JPY", "2026-09-18T00:00:00")


def test_policy_rates_yaml_matches_last_decision():
    """Carry (data/policy_rates.yaml) and the displayed policy rate must agree:
    fails as soon as a new decision lands in decisions.parquet and the yaml is
    not updated (or the other way round)."""
    dec = load_decisions()
    cfg = yaml.safe_load(POLICY_RATES_YAML.read_text())["rates"]
    diverging = {}
    for ccy, g in dec.groupby("currency"):
        last = g.sort_values("release_dt").iloc[-1]
        y = (cfg.get(ccy) or {}).get("rate_pct")
        if y is None or abs(float(y) - float(last["rate_after"])) > 1e-9:
            diverging[ccy] = {"policy_rates.yaml": y, "decisions.parquet": float(last["rate_after"]),
                              "meeting": str(last["meeting_date"])}
    assert not diverging, diverging
    assert set(cfg) == set(dec["currency"])


@pytest.mark.parametrize("ccy,expected", [("EUR", 2.50), ("USD", 3.875), ("CHF", 0.0)])
def test_repo_decisions_give_the_audited_rates(ccy, expected):
    """ECB = DFR 2.50 (FF showed MRO 2.65), Fed = 3.875 midpoint of 3.75-4.00
    (FF showed the 4.00 upper bound), SNB = 0.00 (FF showed 0.25 from 2025-03)."""
    rows = decision_rows(load_decisions(), pd.Timestamp("2026-09-23T07:06:11"), SCORING_COLUMNS)
    last = rows[rows.currency == ccy].sort_values("release_dt").iloc[-1]
    assert last.actual == expected


# --- audit V1-V3 ---------------------------------------------------------------

def test_policy_rate_stale_comes_only_from_freshness_policy_rates():
    """V1: CHF held at 0.00% for 96 days is not stale; age is not a criterion."""
    from src.economic_render import _attach_policy_rate_fields
    payload = {"currencies": {
        "CHF": {"breakdown": {KEY: {"release_dt": "2026-06-18T07:30:00", "stale": True, "age_days": 96}}},
        "USD": {"breakdown": {KEY: {"release_dt": "2026-07-29T18:00:00", "stale": False, "age_days": 49}}},
    }}
    fresh = {"per_currency": {"CHF": {"stale": False}, "USD": {"stale": True}}}
    _attach_policy_rate_fields(payload, None, fresh)
    assert payload["currencies"]["CHF"]["breakdown"][KEY]["stale"] is False
    assert payload["currencies"]["USD"]["breakdown"][KEY]["stale"] is True   # meeting passed, no decision
    assert payload["currencies"]["CHF"]["breakdown"][KEY]["stale_basis"] == "policy_rates"


def test_decision_without_time_is_date_only():
    """V2: a BoJ decision with no time is displayed as its meeting date only."""
    assert display_fields(SYNTH, "JPY", "2026-09-18T00:00:00")["date_only"] is True
    assert "date_only" not in display_fields(SYNTH, "USD", "2026-09-16T18:00:00")


def test_manual_actuals_panel_does_not_list_policy_rates(monkeypatch):
    """V3: rows for interest_rate_decision are never listed (source = decisions)."""
    from src import economic_render as er
    rows = pd.DataFrame([
        {"canonical_id": "chf_snb_interest_rate_decision", "currency": "CHF",
         "indicator_key": KEY, "state": "ZERO_CONFIRM"},
        {"canonical_id": "chf_cpi", "currency": "CHF", "indicator_key": "cpi_yoy", "state": "MISSING"},
    ])
    monkeypatch.setattr("src.manual_actuals.apply_overrides", lambda *a, **k: (None, rows))
    monkeypatch.setattr("src.jb_actuals.build_flagged_bad_lookup", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr("src.ff_refresh.calendar_source", lambda: "ff")
    out = er._load_actionable_rows(pd.Timestamp("2026-09-23"))
    assert out["indicator_key"].tolist() == ["cpi_yoy"]
