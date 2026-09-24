"""Audit 4B — one publication = one row; next valid release; missing releases."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_scoring import build_matcher, load_zero_possible, scoring_view, to_scoring_frame
from src.release_integrity import (SeriesChain, find_series_gaps, find_unfed_scheduled,
                                   resolve_conflicts, _prep)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"


@pytest.fixture(scope="module")
def frozen():
    return pd.read_parquet(FROZEN_FF)


@pytest.fixture(scope="module")
def scored(frozen):
    sc = scoring_view(to_scoring_frame(frozen, build_matcher()))
    sc["release_dt"] = pd.to_datetime(sc["release_dt"])
    return sc


def _day(sc, ccy, key, day):
    s = sc[(sc.currency == ccy) & (sc.indicator_key == key) & (sc.release_dt.dt.date == pd.Timestamp(day).date())]
    return sorted(float(a) for a in s["actual"] if pd.notna(a))


# --- gates (frozen snapshot 4ace910) ---------------------------------------------

def test_contradicted_same_day_rows_leave_scoring(frozen, scored):
    ex, findings = resolve_conflicts(frozen, load_zero_possible())
    keys = {(k[0], k[1].strftime("%Y-%m-%d %H:%M")) for k in ex}
    assert ("jpy_prelim_industrial_production", "2026-02-26 23:50") in keys
    assert ("usd_advance_gdp_price_index", "2026-02-20 13:30") in keys
    # neither release is scored any more (the real rows' actuals are placeholders)
    assert _day(scored, "JPY", "industrial_production_mm", "2026-02-26") == []
    assert _day(scored, "USD", "gdp_price_index", "2026-02-20") == []
    assert all(f["check"] == "release_conflict" and f["reason"] for f in findings)


@pytest.mark.parametrize("ccy,key,day,expected", [
    ("CHF", "retail_sales", "2026-09-01", [2.3]),
    ("USD", "jobless_claims", "2026-02-19", [206.0]),
    ("GBP", "ppi_yoy", "2025-10-22", [0.0]),
])
def test_rows_the_chain_cannot_reject_stay(scored, ccy, key, day, expected):
    assert _day(scored, ccy, key, day) == expected


# --- the chain (synthetic) ---------------------------------------------------------

def _series(rows, cid="usd_x"):
    return _prep(pd.DataFrame([{
        "canonical_id": cid, "currency": "USD", "name_raw": "X m/m", "name_canonical": "X m/m",
        "datetime_utc": pd.Timestamp(dt), "actual": a, "forecast": f, "previous": p,
        "released": True, "source": "ff", "forecast_origin": "ff", "jb_status": st,
    } for dt, a, f, p, st in rows], columns=CANON_COLUMNS))


MONTHLY = [(f"2026-0{m}-15", 0.1 * m, 0.1 * m, 0.1 * (m - 1), "Good Data") for m in range(1, 7)]


def test_next_valid_skips_0_0_0_rows_and_same_publication_relistings():
    rows = MONTHLY + [("2026-07-15", 0.0, 0.0, 0.0, "Good Data"),         # 0/0/0 row
                      ("2026-07-16", 0.7, 0.7, 0.6, "Good Data"),         # re-listed next day
                      ("2026-08-14", 0.8, 0.8, 0.7, "Good Data")]
    ch = SeriesChain(_series(rows), True)
    nxt = ch.next_valid(pd.Timestamp("2026-06-15").date())
    # 7D: the 0/0/0 row (07-15) and the real row (07-16) are ONE publication;
    # its representative is the real, latest-listed row
    assert str(nxt["datetime_utc"])[:10] == "2026-07-16" and nxt["previous"] == 0.6


def test_nothing_is_compared_across_a_gap():
    rows = MONTHLY + [("2026-09-15", 0.9, 0.9, 0.8, "Good Data")]        # Jul/Aug missing
    ch = SeriesChain(_series(rows), True)
    assert ch.next_valid(pd.Timestamp("2026-06-15").date()) is None


def test_conflict_rule_picks_the_row_the_chain_contradicts():
    rows = MONTHLY + [("2026-07-15 12:30", 0.0, 2.8, 0.6, "Data Not Loaded"),   # real release
                      ("2026-07-15 13:30", 0.4, 0.4, 0.4, "Good Data"),         # wrong row
                      ("2026-08-14", 0.8, 0.8, 0.7, "Good Data")]
    ex, f = resolve_conflicts(_series(rows), {})
    assert list(ex) == [("usd_x", pd.Timestamp("2026-07-15 13:30"))]
    assert "next previous 0.7" in f[0]["reason"]


# --- missing releases ---------------------------------------------------------------

def test_good_friday_2026_04_03_is_a_series_gap(frozen):
    gaps = find_series_gaps(frozen, pd.Timestamp("2026-09-23T07:06"), load_zero_possible())
    got = {(g["canonical_id"], g["after"], g["before"]) for g in gaps}
    for cid in ("usd_nonfarm_payrolls", "usd_unemployment_rate", "usd_average_hourly_earnings"):
        assert (cid, "2026-03-06", "2026-05-08") in got
    assert ("usd_ism_non_manufacturing_pmi", "2026-03-04", "2026-05-05") in got
    assert not any(c.endswith("_interest_rate_decision") for c, _a, _b in got)


def test_scheduled_event_that_never_reached_the_parquet_is_reported():
    ff = _series(MONTHLY, cid="usd_nonfarm_payrolls")
    ff["name_raw"] = ff["name_canonical"] = "Non-Farm Employment Change"
    weekly = [("2026-07-01", [{"title": "Non-Farm Employment Change", "country": "USD",
                               "date": "2026-07-03T08:30:00-04:00", "forecast": "100K", "previous": "90K"}])]
    out = find_unfed_scheduled(ff, weekly, pd.Timestamp("2026-07-10"))
    assert [(f["kind"], f["release_dt"][:10]) for f in out] == [("not_ingested", "2026-07-03")]
    assert find_unfed_scheduled(ff, weekly, pd.Timestamp("2026-07-02")) == []   # not due yet


# --- report levels + alert only on new findings ------------------------------------

def test_levels_and_new_findings(tmp_path, monkeypatch):
    from src import economic_render as er
    as_of = pd.Timestamp("2026-09-23T07:06:11")
    monkeypatch.setattr("src.ff_refresh.FF_PARQUET", FROZEN_FF)
    cal = er._load_calendar_frame(as_of)
    prev = tmp_path / "integrity_report.json"
    first = er._integrity_report(cal, as_of, previous_path=prev)
    allf = [f for c in first["checks"].values() for f in c["findings"]]
    assert allf and all(f["level"] in ("WARN", "INFO") for f in allf) and all(f["new"] for f in allf)
    assert all(f["level"] == "INFO" for f in first["checks"]["release_conflict"]["findings"])
    assert all(f["level"] == "INFO" for f in first["checks"]["previous_consistency"]["findings"]
               if f["scored_source"] == "quarantined")
    er._write_integrity_report(first, "t0", prev)
    second = er._integrity_report(cal, as_of, previous_path=prev)
    assert not any(f["new"] for c in second["checks"].values() for f in c["findings"])
    assert er._integrity_summary(second)["new_warn"] == 0


# --- 5C: signal, not a counter; known gaps ------------------------------------------

def test_known_gaps_explain_only_gaps_fully_inside_a_window():
    from src.release_integrity import explain_gap, load_known_gaps
    known = load_known_gaps()
    f = lambda ccy, a, b, cad=30: {"kind": "series_gap", "currency": ccy, "after": a, "before": b,
                                   "cadence_days": cad}
    assert explain_gap(f("USD", "2026-03-06", "2026-05-08"), known)["id"] == "jb_archive_hole_2026_04"
    assert explain_gap(f("USD", "2025-10-02", "2025-11-20"), known)["id"] == "us_shutdown_2025"
    assert explain_gap(f("CAD", "2023-11-21", "2024-01-16"), known)["id"] == "jb_archive_hole_2023_12"
    assert explain_gap(f("EUR", "2025-10-02", "2025-11-20"), known) is None      # shutdown is USD only
    assert explain_gap(f("USD", "2025-07-16", "2025-11-25"), known) is None      # Aug/Sep not explained
    assert explain_gap(f("JPY", "2023-11-29", "2024-02-28"), known) is None      # Jan not in the hole


def test_level_warn_only_with_new_warn_findings():
    from src import economic_render as er
    rep = {"checks": {"previous_consistency": {"findings": [{"level": "WARN", "new": False}]},
                      "missing_release": {"findings": [{"level": "INFO", "known_gap": "x", "new": True}]}}}
    s = er._integrity_summary(rep)
    assert (s["warn"], s["new_warn"], s["level"], s["known_gaps"]) == (1, 0, "ok", 1)
    rep["checks"]["previous_consistency"]["findings"][0]["new"] = True
    assert er._integrity_summary(rep)["level"] == "warn"


# --- 6A: per-publication entries -----------------------------------------------------

def test_publication_entry_explains_only_its_exact_gap():
    from src.release_integrity import explain_gap, load_known_gaps
    known = load_known_gaps()
    f = lambda cid, ccy, a, b: {"kind": "series_gap", "canonical_id": cid, "currency": ccy,
                                "after": a, "before": b, "cadence_days": 30}
    assert explain_gap(f("usd_cpi", "USD", "2025-10-24", "2025-12-18"), known)["id"] == "usd_cpi_2025_10"
    assert explain_gap(f("usd_ppi", "USD", "2025-11-25", "2026-01-14"), known)["id"] == "usd_ppi_2025_10"
    assert explain_gap(f("usd_cpi", "USD", "2025-10-24", "2025-12-19"), known) is None      # other gap
    assert explain_gap(f("usd_retail_sales", "USD", "2025-10-24", "2025-12-18"), known) is None


def test_every_publication_entry_carries_evidence_and_windows_did_not_grow():
    from src.release_integrity import _load_known_gaps_raw
    raw = _load_known_gaps_raw()
    for e in raw["missing_publications"]:
        assert e["canonical_ids"] and e["gap"]["after"] < e["gap"]["before"]
        for p in e["publications"]:
            assert p["status"] in ("cancelled", "merged", "not_published", "no_source") and p["evidence"]
            if p["status"] != "no_source":
                assert str(p["source"]).startswith(("https://www.bls.gov/", "https://www.bea.gov/"))
    win = {e["id"]: (str(e["start"]), str(e["end"])) for e in raw["known_gaps"]}
    assert win == {"us_shutdown_2025": ("2025-10-01", "2025-11-12"),
                   "jb_archive_hole_2023_12": ("2023-12-04", "2023-12-29"),
                   "jb_archive_hole_2026_04": ("2026-04-03", "2026-04-06")}


# --- 7D: one publication identity ------------------------------------------------------

def test_publication_relation():
    from types import SimpleNamespace as N
    from src.release_integrity import publication_relation
    t = lambda s: pd.Timestamp(s)
    a = N(datetime_utc=t("2025-12-15 22:00"), actual=-105.0, forecast=0.0, previous=108.0)
    b = N(datetime_utc=t("2025-12-16 13:30"), actual=64.0, forecast=51.0, previous=-105.0)
    assert publication_relation(a, b) == "next_period"          # NFP Oct and Nov, both kept
    c = N(datetime_utc=t("2025-01-05 22:00"), actual=0.0, forecast=0.9, previous=1.5)
    d = N(datetime_utc=t("2025-01-06 07:30"), actual=0.8, forecast=0.9, previous=1.5)
    assert publication_relation(c, d) == "same"                 # re-listed across midnight
    e = N(datetime_utc=t("2023-01-03 15:00"), actual=47.4, forecast=47.2, previous=46.7)
    f = N(datetime_utc=t("2023-01-04 15:00"), actual=48.4, forecast=48.5, previous=49.0)
    assert publication_relation(e, f) == "conflict"             # the chain decides
    g = N(datetime_utc=t("2025-01-07 07:30"), actual=0.8, forecast=0.9, previous=1.5)
    assert publication_relation(c, g) is None                   # > 26 h apart


def test_chain_picks_the_real_ism_print_of_january_2023(frozen):
    ex, _f = resolve_conflicts(frozen, load_zero_possible())
    assert ("usd_ism_manufacturing_pmi", pd.Timestamp("2023-01-03 15:00")) in ex
    assert ("usd_ism_manufacturing_pmi", pd.Timestamp("2023-01-04 15:00")) not in ex


def test_nfp_october_and_november_both_scored(frozen):
    sc = scoring_view(to_scoring_frame(frozen, build_matcher()))
    sc["release_dt"] = pd.to_datetime(sc["release_dt"])
    nfp = sc[(sc.currency == "USD") & (sc.indicator_key == "employment_change")
             & (sc.release_dt >= "2025-12-15") & (sc.release_dt <= "2025-12-17")]
    assert sorted(nfp["actual"].dropna().tolist()) == [-105.0, 64.0]
