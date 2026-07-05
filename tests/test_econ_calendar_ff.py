"""Tests for src.econ_calendar_ff — standalone FF calendar ingest (Phase 1).

Covers the 5 spike traps (positive + negative), dual-parser convergence, idempotency.
No network: uses tests/fixtures/ff_range_sample.json + ff_weekly_sample.json.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.econ_calendar_ff import (
    CANON_COLUMNS,
    canonical_id,
    detect_revisions,
    flash_final_revisions,
    iso_to_utc,
    jblanked_to_utc,
    normalize_ff_value,
    parse_ff_weekly,
    parse_jblanked_range,
)

FIX = Path(__file__).resolve().parent / "fixtures"
RANGE = FIX / "ff_range_sample.json"
WEEKLY = FIX / "ff_weekly_sample.json"
NOW = pd.Timestamp("2026-07-05 00:00:00")   # fixed clock for the released gate


# --- timezone normalization (trap 4) ----------------------------------------

def test_jblanked_tz_summer_is_utc_plus_3():
    # NFP known case: 2026-07-03 08:30 ET (EDT) -> 12:30 UTC; feed shows ET+7 = 15:30.
    assert jblanked_to_utc("2026.07.03 15:30:00") == pd.Timestamp("2026-07-03 12:30:00")


def test_jblanked_tz_winter_is_utc_plus_2():
    # 2026-01-09 08:30 ET (EST) -> 13:30 UTC; feed shows ET+7 = 15:30.
    assert jblanked_to_utc("2026.01.09 15:30:00") == pd.Timestamp("2026-01-09 13:30:00")


def test_iso_weekly_uses_explicit_offset():
    # weekly feed carries the offset -> direct conversion (documented divergence)
    assert iso_to_utc("2026-07-03T08:30:00-04:00") == pd.Timestamp("2026-07-03 12:30:00")
    assert iso_to_utc("2026-01-09T08:30:00-05:00") == pd.Timestamp("2026-01-09 13:30:00")


def test_bad_datetime_returns_none():
    assert jblanked_to_utc("not-a-date") is None
    assert iso_to_utc("") is None


# --- value normalization (unit divergence fix: strip suffix to bare mantissa) ---

@pytest.mark.parametrize("raw,expected", [
    # suffix stripped to bare mantissa (range convention), NOT scaled:
    ("172K", 172.0),          # K  (NFP 311.0 ↔ "311K")
    ("11.01M", 11.01),        # M  (JOLTS 11.01 ↔ "11.01M") — not ×1e6
    ("-68.9B", -68.9),        # B  negative w/ suffix (Trade Balance -63.2 ↔ "-63.2B")
    ("0.3%", 0.3),            # %  stripped
    ("-0.3%", -0.3),
    ("52.7", 52.7),           # no suffix, unchanged
    ("1,234", 1234.0),        # thousands separator
    (4.3, 4.3), (0.0, 0.0),   # numeric passthrough
    ("", np.nan), (None, np.nan), ("N/A", np.nan), ("-", np.nan),
])
def test_normalize_ff_value(raw, expected):
    got = normalize_ff_value(raw)
    if isinstance(expected, float) and np.isnan(expected):
        assert np.isnan(got)
    else:
        assert got == pytest.approx(expected)


def test_normalize_unknown_suffix_raises_loud_with_name():
    # unknown suffix -> ValueError (fail loud, no silent mis-scale), name in message
    with pytest.raises(ValueError, match="Fancy Index"):
        normalize_ff_value("5X", name="Fancy Index")


# --- canonical id (period-suffix stripped) ----------------------------------

def test_canonical_id_strips_period_suffix():
    assert canonical_id("USD", "Nonfarm Payrolls") == "usd_nonfarm_payrolls"
    assert canonical_id("USD", "Core CPI y/y") == "usd_core_cpi"
    assert canonical_id("CHF", "GDP q/q") == "chf_gdp"


# --- schema -----------------------------------------------------------------

def test_output_schema_matches_canonical():
    df = parse_jblanked_range(RANGE, now_utc=NOW)
    assert list(df.columns) == CANON_COLUMNS
    assert (df["source"] == "ff").all()


# --- trap 1: matcher (unmapped excluded + aggregated summary; out-of-scope ccy dropped) --

def test_unmapped_event_excluded_with_aggregated_summary(caplog):
    with caplog.at_level(logging.INFO):
        df = parse_jblanked_range(RANGE, now_utc=NOW)
    # the FOMC speech has no alias -> excluded from scoring
    assert not (df["name_raw"] == "FOMC Member Bowman Speaks").any()
    # unmapped names are aggregated into a single reviewable INFO summary (not per-event WARNINGs)
    assert any("unmapped event(s) excluded across" in r.message for r in caplog.records)
    # out-of-scope currency (CNY) is silently dropped
    assert (df["currency"] != "CNY").all()


def test_unmapped_summary_lists_names():
    from src.econ_calendar_ff import unmapped_summary
    events = [
        {"Name": "FOMC Member Bowman Speaks", "Currency": "USD", "Date": "2026.06.10 18:00:00",
         "Actual": 0.0, "Forecast": 0.0, "Previous": 0.0},
        {"Name": "CPI y/y", "Currency": "USD", "Date": "2026.06.10 12:30:00",
         "Actual": 4.2, "Forecast": 4.0, "Previous": 4.0},
    ]
    um = unmapped_summary(events)
    assert (um["name_raw"] == "FOMC Member Bowman Speaks").any()   # unmapped listed
    assert not (um["name_raw"] == "CPI y/y").any()                 # mapped not listed


def test_mapped_event_canonicalized():
    df = parse_jblanked_range(RANGE, now_utc=NOW)
    nfp = df[df["canonical_id"] == "usd_nonfarm_payrolls"]
    assert len(nfp) == 3
    assert (nfp["name_canonical"] == "Nonfarm Payrolls").all()


# --- trap 2: EUR aggregate whitelist ----------------------------------------

def test_eur_member_states_dropped_aggregate_kept(caplog):
    with caplog.at_level(logging.DEBUG):
        df = parse_jblanked_range(RANGE, now_utc=NOW)
    eur = df[df["currency"] == "EUR"]
    # member-state prints excluded...
    assert not eur["name_raw"].isin(["Italian Prelim CPI m/m", "German Prelim CPI m/m"]).any()
    # ...aggregate kept
    assert (eur["name_raw"] == "CPI Flash Estimate y/y").any()
    assert (eur[eur["name_raw"] == "CPI Flash Estimate y/y"]["name_canonical"] == "CPI y/y").all()


# --- trap 3: released gate keyed by DATE, not value -------------------------

def test_released_gate_zero_past_is_real_future_is_nan():
    # CHF CPI m/m: 0.0 on a PAST date = real zero (the Italian-CPI-0.0 case, but Italian
    # itself is EUR-whitelist-dropped); 0.0 on a FUTURE date = unreleased -> NaN.
    df = parse_jblanked_range(RANGE, now_utc=NOW)
    chf = df[df["canonical_id"] == "chf_cpi"].sort_values("datetime_utc")
    past = chf[chf["datetime_utc"] < NOW].iloc[0]
    future = chf[chf["datetime_utc"] >= NOW].iloc[0]
    assert past["released"] is True or past["released"] == True  # noqa: E712
    assert past["actual"] == 0.0                                 # real zero kept
    assert future["released"] == False                          # noqa: E712
    assert np.isnan(future["actual"])                            # unreleased -> NaN


# --- trap 5: revisions are telemetry (INFO), never an error -----------------

def test_detect_revisions_reports_not_raises():
    df = parse_jblanked_range(RANGE, now_utc=NOW)
    rev = detect_revisions(df)  # must not raise
    # NFP previous(t) != actual(t-1) by design (FF revised-previous) -> detected
    nfp_rev = rev[rev["canonical_id"] == "usd_nonfarm_payrolls"]
    assert len(nfp_rev) >= 1
    assert set(rev.columns) >= {"canonical_id", "prior_actual", "reported_previous", "revision"}


# --- dual-parser convergence ------------------------------------------------

def test_dual_parser_same_event_same_id_datetime_and_value():
    r = parse_jblanked_range(RANGE, now_utc=NOW)
    w = parse_ff_weekly(WEEKLY, now_utc=NOW)
    r_nfp = r[r["canonical_id"] == "usd_nonfarm_payrolls"]
    w_nfp = w[w["canonical_id"] == "usd_nonfarm_payrolls"]
    assert len(w_nfp) == 1 and len(r_nfp) == 3
    shared = pd.Timestamp("2026-06-05 12:30:00")
    rrow = r_nfp[r_nfp["datetime_utc"] == shared].iloc[0]   # range: 172.0 / 50.0 / 129.0
    wrow = w_nfp.iloc[0]                                     # weekly: "172K" / "50K" / "129K"
    assert wrow["datetime_utc"] == shared
    assert wrow["canonical_id"] == "usd_nonfarm_payrolls"
    # unit divergence closed: identical NUMERIC values after normalization (strict)
    for col in ("actual", "forecast", "previous"):
        assert rrow[col] == pytest.approx(wrow[col], abs=1e-9), col
    assert wrow["actual"] == pytest.approx(172.0)   # "172K" -> 172.0, not 172000


def test_weekly_member_state_also_dropped():
    w = parse_ff_weekly(WEEKLY, now_utc=NOW)
    assert not (w["name_raw"] == "German Prelim CPI m/m").any()


# --- deterministic flash/final (alias-level, no proximity) ------------------

def test_flash_scored_final_in_telemetry_only():
    # EUR CPI: the flash estimate is the ONLY variant that enters scoring; the final
    # is excluded from scoring and surfaces in flash→final revision telemetry.
    events = [
        {"Name": "CPI Flash Estimate y/y", "Currency": "EUR", "Date": "2026.05.31 12:00:00",
         "Actual": 3.0, "Forecast": 3.0, "Previous": 3.2},
        {"Name": "CPI Flash Estimate y/y", "Currency": "EUR", "Date": "2026.06.30 12:00:00",
         "Actual": 2.8, "Forecast": 3.0, "Previous": 3.0},
        {"Name": "Final CPI y/y", "Currency": "EUR", "Date": "2026.06.17 12:00:00",
         "Actual": 3.2, "Forecast": 3.2, "Previous": 3.2},
        {"Name": "Final CPI y/y", "Currency": "EUR", "Date": "2026.07.17 12:00:00",
         "Actual": 2.9, "Forecast": 2.8, "Previous": 2.8},
    ]
    df = parse_jblanked_range(events, now_utc=pd.Timestamp("2026-07-20"))
    cpi = df[(df["currency"] == "EUR") & (df["name_canonical"] == "CPI y/y")]
    assert set(cpi["name_raw"]) == {"CPI Flash Estimate y/y"}   # ONLY flash scored
    assert "Final CPI y/y" not in set(cpi["name_raw"])

    rev = flash_final_revisions(events)
    assert (rev["final_name"] == "Final CPI y/y").any()          # final -> telemetry
    # the 2026-07-17 final pairs with the nearest preceding flash (2026-06-30, 2.8)
    r = rev[rev["final_dt"].dt.date == pd.Timestamp("2026-07-17").date()].iloc[0]
    assert r["flash_actual"] == pytest.approx(2.8)
    assert r["revision"] == pytest.approx(0.1)                   # 2.9 − 2.8


# --- idempotency ------------------------------------------------------------

def test_idempotent_double_parse():
    a = parse_jblanked_range(RANGE, now_utc=NOW)
    b = parse_jblanked_range(RANGE, now_utc=NOW)
    assert a.equals(b)
