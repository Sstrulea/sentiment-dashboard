"""FAZA 1B — src/history_compute.py tests.

Includes the mandatory non-regression check: the existing /economic and
/strength compute path (economic_render._load_calendar_frame,
economic_compute.compute_currency_scorecard) must produce byte-identical
output whether or not src/history_compute has been imported/used in the
same process — proving the new module never touches shared state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src import history_compute as hc
from src.data_integrity import build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros
from src.economic_compute import compute_currency_scorecard
from src.ff_scoring import CCY2COUNTRY, build_matcher, load_can_be_zero
from src.jb_actuals import build_flagged_bad_lookup

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]


def _row(ccy, key, cid, name_raw, dt, actual, forecast, previous):
    return {"currency": ccy, "indicator_key": key, "canonical_id": cid,
            "name_raw": name_raw, "release_dt": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": previous}


# ---------------------------------------------------------------------------
# Non-regression: existing /economic + /strength compute path is untouched
# ---------------------------------------------------------------------------

def _snapshot_production_output(as_of: pd.Timestamp) -> str:
    from src.economic_render import _load_calendar_frame
    cal = _load_calendar_frame(as_of)
    ind_cfg = hc.load_indicators_cfg()
    with open(ROOT / "data" / "economic_instruments.yaml") as f:
        inst_cfg = yaml.safe_load(f) or {}
    cards = {ccy: compute_currency_scorecard(cal, ccy, ind_cfg, inst_cfg, as_of) for ccy in CCYS}
    return json.dumps({"calendar": cal.to_dict(orient="records"), "scorecards": cards},
                      default=str, sort_keys=True)


@pytest.mark.skipif(not FF_PARQUET.exists(), reason="requires local data/economic_calendar_ff.parquet")
def test_non_regression_economic_and_strength_output_unchanged():
    as_of = pd.Timestamp("2026-08-20 12:00:00")
    before = _snapshot_production_output(as_of)

    # Exercise the new module fully — load catalog, build the quarantine
    # proposal, compute the full catalog, build a payload.
    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    catalog = hc.load_catalog()
    ind_cfg = hc.load_indicators_cfg()
    matcher = build_matcher()
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})[
        ["currency", "indicator_key", "canonical_id", "name_raw", "release_dt",
         "actual", "forecast", "previous"]]
    ghosts = detect_ghost_rows(df)
    zeros = detect_implausible_zeros(df)
    quarantine_df = build_quarantine_proposal(ghosts, zeros)
    series_cache = hc.compute_catalog(ff, ind_cfg, catalog, quarantine_df, as_of=as_of)
    hc.build_payload(catalog, series_cache, catalog_version="test", as_of=as_of)

    after = _snapshot_production_output(as_of)
    assert before == after


# ---------------------------------------------------------------------------
# compute_series_history
# ---------------------------------------------------------------------------

@pytest.fixture
def ind_cfg():
    return {
        "defaults": {"surprise_window_k": 12, "z_buckets": [1.54, 0.81],
                    "fallback_min_prints": 6, "pct_buckets": [0.10, 0.02],
                    "max_age_days": 120, "default_frequency": "monthly",
                    "max_age_by_frequency": {"monthly": 45}, "dedup_gap_days": {"monthly": 18}},
        "indicators": {"cpi_yoy": {"category": "inflation", "direction": 1, "weight": 1.0,
                                   "frequency": "monthly"}},
    }


def _scoring_row(ccy, key, dt, actual, consensus, previous, name_raw="CPI y/y", source="ff"):
    return {"currency": ccy, "indicator_key": key, "release_dt": pd.Timestamp(dt),
            "actual": actual, "consensus": consensus, "previous": previous,
            "source": source, "name_raw": name_raw}


def test_quarantined_row_excluded_from_other_rows_scoring(ind_cfg):
    """A quarantined ghost row must not appear as `previous` bait or contribute
    to sigma for its neighbors — the clean sequence skips it entirely."""
    full_frame = pd.DataFrame([
        _scoring_row("USD", "cpi_yoy", "2023-01-11 13:30:00", 3.4, 3.2, 3.1),   # ghost
        _scoring_row("USD", "cpi_yoy", "2023-01-12 13:30:00", 6.5, 6.5, 7.1),   # real
        _scoring_row("USD", "cpi_yoy", "2023-02-14 13:30:00", 6.4, 6.2, 6.5),
        _scoring_row("USD", "cpi_yoy", "2024-01-11 13:30:00", 3.4, 3.2, 3.1),
    ])
    qkeys = {("USD", "CPI y/y", pd.Timestamp("2023-01-11 13:30:00"))}
    out = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, qkeys)
    ghost_row = out[out["release_dt"] == pd.Timestamp("2023-01-11 13:30:00")].iloc[0]
    assert ghost_row["quarantined"]
    assert pd.isna(ghost_row["z"]) and pd.isna(ghost_row["bucket"])
    real_row = out[out["release_dt"] == pd.Timestamp("2023-01-12 13:30:00")].iloc[0]
    assert not real_row["quarantined"]
    # revised_from must NOT fire off the ghost's previous=3.1 vs the ghost's own
    # actual — the ghost is excluded from the clean pair sequence entirely.
    assert real_row["revised_from"] is None


def test_override_wins_over_quarantine(ind_cfg):
    """A row with source='manual' is NEVER quarantined, even if its
    (currency, name_raw, release_dt) is in the quarantine key set — a user
    correction is the final say, per FAZA 1B decision."""
    full_frame = pd.DataFrame([
        _scoring_row("USD", "cpi_yoy", "2023-01-11 13:30:00", 3.4, 3.2, 3.1, source="manual"),
    ])
    qkeys = {("USD", None, pd.Timestamp("2023-01-11 13:30:00")),
            ("USD", "CPI y/y", pd.Timestamp("2023-01-11 13:30:00"))}
    out = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, qkeys)
    assert len(out) == 1
    assert not out.iloc[0]["quarantined"]
    assert out.iloc[0]["has_override"]


def test_revised_from_epsilon_and_direction(ind_cfg):
    full_frame = pd.DataFrame([
        _scoring_row("USD", "cpi_yoy", "2023-01-01", 3.0, 2.9, 2.8),
        _scoring_row("USD", "cpi_yoy", "2023-02-01", 3.1, 2.9, 3.05),   # 3.05 vs 3.0 -> 1.7% -> no revision
        _scoring_row("USD", "cpi_yoy", "2023-03-01", 3.2, 3.0, 3.5),    # 3.5 vs 3.1 -> 12.9% -> revised_from=3.1
    ])
    out = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, set())
    assert out.iloc[1]["revised_from"] is None
    assert out.iloc[2]["revised_from"] == 3.1


def test_z_bucket_matches_compute_indicator_score_for_latest_point(ind_cfg):
    """The last row's z/bucket from compute_series_history must equal a direct
    compute_indicator_score call on the same clean series, as_of=that release —
    same function, not a re-implementation."""
    from src.economic_compute import compute_indicator_score
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0 + 0.1 * m, 2.9, 3.0 + 0.1 * (m - 1))
           for m in range(1, 8)]
    full_frame = pd.DataFrame(rows)
    out = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, set())
    last = out.iloc[-1]
    direct = compute_indicator_score(full_frame, ind_cfg["indicators"]["cpi_yoy"], ind_cfg["defaults"],
                                     last["release_dt"], allow_stale=True,
                                     currency="USD", indicator_key="cpi_yoy")
    assert last["z"] == pytest.approx(direct["z"])
    assert last["bucket"] == direct["score"]


def test_no_history_returns_empty_frame(ind_cfg):
    empty = pd.DataFrame(columns=["currency", "indicator_key", "release_dt", "actual",
                                  "consensus", "previous", "source", "name_raw"])
    out = hc.compute_series_history(empty, "USD", "cpi_yoy", ind_cfg, set())
    assert out.empty


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def test_payload_window_min_points_and_quarantine_visibility(ind_cfg):
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0, 2.9, 2.9) for m in range(1, 4)]
    df = hc.compute_series_history(pd.DataFrame(rows), "USD", "cpi_yoy", ind_cfg, set())
    payload = hc.build_payload(catalog, {("USD", "cpi_yoy"): df}, "v1", as_of=as_of)
    entry = payload["categories"]["inflation"]["USD"][0]
    # only 3 points -> below WINDOW_MIN_POINTS(4) -> no window renders at all
    assert entry["window_options"] == {}


def test_payload_quarantined_row_never_visible_without_override(ind_cfg):
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0 + m, 2.9, 2.9) for m in range(1, 6)]
    full_frame = pd.DataFrame(rows)
    qkeys = {("USD", "CPI y/y", pd.Timestamp("2023-03-01"))}
    df = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, qkeys)
    payload = hc.build_payload(catalog, {("USD", "cpi_yoy"): df}, "v1", as_of=as_of)
    entry = payload["categories"]["inflation"]["USD"][0]
    all_dts = {p["release_dt"] for w in entry["window_options"].values() for p in w["points"]}
    assert "2023-03-01T00:00:00" not in all_dts
    assert entry["quarantine_count"] == 1
