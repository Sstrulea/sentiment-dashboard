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
    """FAZA 1H: the revision belongs to the EARLIER row (N), not the later
    one (N+1) that merely reports the updated `previous`. Row 1
    (2023-02-01)'s own actual (3.1) differs from row 2's `previous` (3.5) by
    more than the epsilon -> row 1 carries revised_from=3.1 AND its `actual`
    is overwritten to the effective/current value (3.5) — never row 2, which
    is the row that PRINTED (a print can't already be "revised from"
    something before it exists)."""
    full_frame = pd.DataFrame([
        _scoring_row("USD", "cpi_yoy", "2023-01-01", 3.0, 2.9, 2.8),
        _scoring_row("USD", "cpi_yoy", "2023-02-01", 3.1, 2.9, 3.05),   # 3.05 vs 3.0 -> 1.7% -> no revision
        _scoring_row("USD", "cpi_yoy", "2023-03-01", 3.2, 3.0, 3.5),    # 3.5 vs 3.1 -> 12.9% -> row 1 revised
    ])
    out = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, set())
    assert out.iloc[0]["revised_from"] is None
    assert out.iloc[1]["revised_from"] == 3.1
    assert out.iloc[1]["actual"] == 3.5          # effective value replaces the original
    assert out.iloc[2]["revised_from"] is None   # last row: nothing revises it (yet)
    assert out.iloc[2]["actual"] == 3.2          # unrevised rows are untouched


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


def test_null_actual_row_never_inherits_a_prior_row_score(ind_cfg):
    """FAZA 1C P1.1: the exact confirmed bug — a row with actual=NaN (nulled
    upstream, e.g. by to_scoring_frame's own zero-gate) must get z=None/
    bucket=None/score_status='no_actual', never the PRIOR real print's score
    (compute_indicator_score's own `fresh` filter drops actual-null rows, so
    calling it with as_of=this row's date silently returns an earlier row's
    result — attaching that to this row mislabels it)."""
    full_frame = pd.DataFrame([
        _scoring_row("EUR", "cpi_yoy", "2025-09-02", 2.1, 2.1, 2.0),
        _scoring_row("EUR", "cpi_yoy", "2025-10-01", None, 2.2, 2.0),   # no actual
        _scoring_row("EUR", "cpi_yoy", "2025-10-31", 2.1, 2.1, 2.2),
    ])
    out = hc.compute_series_history(full_frame, "EUR", "cpi_yoy", ind_cfg, set())
    null_row = out[out["release_dt"] == pd.Timestamp("2025-10-01")].iloc[0]
    assert pd.isna(null_row["z"]) and pd.isna(null_row["bucket"])
    assert null_row["score_status"] == "no_actual"
    assert pd.isna(null_row["revised_from"])   # P1.2: no actual -> can't be a revision side


def test_score_status_scored_for_real_print(ind_cfg):
    """Rows before fallback_min_prints(6) pairs have accumulated correctly
    fall to 'insufficient_history' (production's own fallback path, real
    behavior, not a bug) — only once >=6 pairs exist is a row 'scored'."""
    full_frame = pd.DataFrame([
        _scoring_row("EUR", "cpi_yoy", f"2023-{m:02d}-01", 2.0 + 0.1 * m, 2.0, 2.0 + 0.1 * (m - 1))
        for m in range(1, 8)
    ])
    out = hc.compute_series_history(full_frame, "EUR", "cpi_yoy", ind_cfg, set())
    assert (out["score_status"].iloc[-2:] == "scored").all()
    assert out["z"].iloc[-2:].notna().all()
    assert (out["score_status"].iloc[:5] == "insufficient_history").all()


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


def test_payload_json_has_no_bare_nan(ind_cfg):
    """A row whose actual is NaN (e.g. nulled by to_scoring_frame's own
    zero-widening gate, unrelated to quarantine) must serialize as JSON
    `null`, never the bare `NaN` token — json.dumps(allow_nan=False) must not
    raise on the real payload."""
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0, 2.9, 2.9) for m in range(1, 5)]
    rows[2]["actual"] = float("nan")
    df = hc.compute_series_history(pd.DataFrame(rows), "USD", "cpi_yoy", ind_cfg, set())
    payload = hc.build_payload(catalog, {("USD", "cpi_yoy"): df}, "v1", as_of=as_of)
    hc.payload_size_bytes(payload)   # raises on a bare NaN (allow_nan=False) if any leaked
    raw = json.dumps(payload, default=str, allow_nan=False)
    assert "NaN" not in raw


def test_payload_policy_dedup_via_reference_not_copy(ind_cfg):
    """P1.4: a `policy` entry referencing the SAME indicator_key as an earlier
    `market` entry in the same (category, currency) must carry `points_ref`
    instead of a second copy of window_options."""
    catalog = {"categories": {"inflation": {"EUR": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
        {"indicator_key": "cpi_yoy", "role": "policy", "rank": 2,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY",
        "target": {"kind": "point", "value": 2.0}},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    rows = [_scoring_row("EUR", "cpi_yoy", f"2023-{m:02d}-01", 2.0, 2.0, 2.0) for m in range(1, 6)]
    df = hc.compute_series_history(pd.DataFrame(rows), "EUR", "cpi_yoy", ind_cfg, set())
    payload = hc.build_payload(catalog, {("EUR", "cpi_yoy"): df}, "v1", as_of=as_of)
    market, policy = payload["categories"]["inflation"]["EUR"]
    assert "window_options" in market and "points_ref" not in market
    assert "window_options" not in policy
    assert policy["points_ref"] == {"role": "market"}
    assert policy["target"]["value"] == 2.0


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


# ---------------------------------------------------------------------------
# Catalog health (FAZA 1G 3.1) — stale / no-data, logged + surfaced in payload
# ---------------------------------------------------------------------------

def test_payload_flags_zero_prints_as_no_data_and_health(ind_cfg):
    """A catalog entry whose indicator_key/currency has ZERO real prints (a
    typo, a matcher regression) must be flagged has_data=False on the entry
    itself AND appear in payload['health']['no_data'] — never just an empty
    window_options with no signal as to WHY."""
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "totally_made_up_key", "role": "market", "rank": 1,
        "display_label": "Fake", "unit": "pct"},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    empty = pd.DataFrame(columns=["currency", "indicator_key", "release_dt", "actual",
                                  "consensus", "previous", "source", "name_raw",
                                  "quarantined", "has_override", "z", "bucket",
                                  "score_status", "revised_from"])
    payload = hc.build_payload(catalog, {("USD", "totally_made_up_key"): empty}, "v1",
                               ind_cfg=ind_cfg, as_of=as_of)
    entry = payload["categories"]["inflation"]["USD"][0]
    assert entry["has_data"] is False
    assert entry["stale"] is False   # can't be stale with no last print at all
    assert entry["last_print_release_dt"] is None
    assert len(payload["health"]["no_data"]) == 1
    assert payload["health"]["no_data"][0]["indicator_key"] == "totally_made_up_key"
    assert payload["health"]["stale"] == []


def test_payload_flags_old_last_print_as_stale_and_health(ind_cfg):
    """A series WITH real prints, but whose latest one is older than its
    effective max_age (45d for monthly, per the ind_cfg fixture), must be
    flagged stale=True with age_days/last_print_release_dt, and appear in
    payload['health']['stale'] — not silently render like a fresh series."""
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
    ]}}}
    as_of = pd.Timestamp("2023-08-01")
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0, 2.9, 2.9) for m in range(1, 6)]
    full_frame = pd.DataFrame(rows)
    df = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, set())
    payload = hc.build_payload(catalog, {("USD", "cpi_yoy"): df}, "v1", ind_cfg=ind_cfg, as_of=as_of)
    entry = payload["categories"]["inflation"]["USD"][0]
    assert entry["has_data"] is True
    assert entry["stale"] is True
    assert entry["last_print_release_dt"] == "2023-05-01T00:00:00"
    assert entry["age_days"] == (as_of - pd.Timestamp("2023-05-01")).days
    assert len(payload["health"]["stale"]) == 1
    assert payload["health"]["stale"][0]["age_days"] == entry["age_days"]
    assert payload["health"]["no_data"] == []


def test_payload_fresh_series_is_neither_stale_nor_no_data(ind_cfg):
    """A series whose latest real print is within the recency window must
    NOT be flagged either way, and the health lists must stay empty."""
    catalog = {"categories": {"inflation": {"USD": [
        {"indicator_key": "cpi_yoy", "role": "market", "rank": 1,
        "display_label": "CPI (YoY)", "unit": "pct", "transform_real": "YoY"},
    ]}}}
    as_of = pd.Timestamp("2023-05-10")   # 9 days after the last (2023-05-01) print
    rows = [_scoring_row("USD", "cpi_yoy", f"2023-{m:02d}-01", 3.0, 2.9, 2.9) for m in range(1, 6)]
    full_frame = pd.DataFrame(rows)
    df = hc.compute_series_history(full_frame, "USD", "cpi_yoy", ind_cfg, set())
    payload = hc.build_payload(catalog, {("USD", "cpi_yoy"): df}, "v1", ind_cfg=ind_cfg, as_of=as_of)
    entry = payload["categories"]["inflation"]["USD"][0]
    assert entry["has_data"] is True
    assert entry["stale"] is False
    assert payload["health"] == {"no_data": [], "stale": []}


# ---------------------------------------------------------------------------
# Display-only duplicate collapse (FAZA 1I 2) — a raw-feed ±1h artifact
# ---------------------------------------------------------------------------

def _raw_row(ccy, cid, dt, actual, forecast, previous, name_raw="X"):
    return {"currency": ccy, "canonical_id": cid, "name_raw": name_raw,
            "datetime_utc": pd.Timestamp(dt), "actual": actual,
            "forecast": forecast, "previous": previous}


def test_drop_display_duplicate_rows_collapses_identical_pair():
    """The exact USD NFP 2026-07-02 pattern: two rows, same canonical_id,
    38min apart, actual/forecast/previous all identical -> the later one is
    dropped, the earlier one is untouched."""
    ff = pd.DataFrame([
        _raw_row("USD", "usd_nonfarm_payrolls", "2026-07-02 11:30:00", 57.0, 114.0, 129.0),
        _raw_row("USD", "usd_nonfarm_payrolls", "2026-07-02 12:30:00", 57.0, 114.0, 129.0),
        _raw_row("USD", "usd_cpi", "2026-07-02 12:30:00", 3.0, 2.9, 2.8),
    ])
    out = hc._drop_display_duplicate_rows(ff)
    assert len(out) == 2   # the NFP duplicate collapsed; the unrelated CPI row untouched
    nfp = out[out["canonical_id"] == "usd_nonfarm_payrolls"]
    assert len(nfp) == 1
    assert nfp.iloc[0]["datetime_utc"] == pd.Timestamp("2026-07-02 11:30:00")


def test_drop_display_duplicate_rows_keeps_both_on_any_divergence():
    """If actual, forecast, OR previous differs even slightly, this is a real
    disagreement, not a duplicate -- both rows must survive untouched."""
    ff = pd.DataFrame([
        _raw_row("JPY", "jpy_x", "2026-02-19 21:00:00", 0.0, 0.0, 52.3),
        _raw_row("JPY", "jpy_x", "2026-02-19 22:00:00", 51.5, 0.0, 52.3),   # actual differs
    ])
    out = hc._drop_display_duplicate_rows(ff)
    assert len(out) == 2


def test_drop_display_duplicate_rows_ignores_gap_over_one_hour():
    """A gap > 1h is out of scope for this artifact (the documented DST-style
    window) -- both rows are kept even if the values happen to match."""
    ff = pd.DataFrame([
        _raw_row("EUR", "eur_x", "2026-01-01 08:00:00", 1.0, 1.0, 1.0),
        _raw_row("EUR", "eur_x", "2026-01-01 10:00:00", 1.0, 1.0, 1.0),   # 2h apart
    ])
    out = hc._drop_display_duplicate_rows(ff)
    assert len(out) == 2


def test_drop_display_duplicate_rows_collapses_three_way_identical_chain():
    """A scheduled (not-yet-printed) event re-delivered twice more before it
    ever prints -- all three identical (NaN actual) -- collapses to exactly
    the earliest one, not "compare against an already-dropped row" (the
    JPY BOJ 2026-07-31 case: 02:30/02:50/03:11, all NaN/1.0/1.0)."""
    ff = pd.DataFrame([
        _raw_row("JPY", "jpy_boj", "2026-07-31 02:30:00", None, 1.0, 1.0),
        _raw_row("JPY", "jpy_boj", "2026-07-31 02:50:00", None, 1.0, 1.0),
        _raw_row("JPY", "jpy_boj", "2026-07-31 03:11:00", None, 1.0, 1.0),
    ])
    out = hc._drop_display_duplicate_rows(ff)
    assert len(out) == 1
    assert out.iloc[0]["datetime_utc"] == pd.Timestamp("2026-07-31 02:30:00")


def test_build_full_frame_passes_the_undeduped_ff_to_apply_overrides(monkeypatch):
    """The regression caught during FAZA 1I development: apply_overrides
    must still see the ORIGINAL (undeduped) ff, or an override keyed to the
    exact (canonical_id, datetime_utc) of a row the display dedup drops
    would silently stop resolving (real case: JPY BOJ 2026-07-31 03:11:00 —
    a raw triplicate collapsed to its 02:30 sibling orphaned an override
    entered against 03:11 specifically). Spies on apply_overrides rather
    than exercising the full matcher/override-resolution chain, which is
    already covered elsewhere (manual_actuals's own test suite) — this test
    is only about WHICH `ff` build_full_frame hands it."""
    ff = pd.DataFrame([
        _raw_row("USD", "usd_x", "2026-07-02 11:30:00", 57.0, 114.0, 129.0),
        _raw_row("USD", "usd_x", "2026-07-02 12:30:00", 57.0, 114.0, 129.0),   # dropped by the dedup
    ])
    seen = {}

    def spy_apply_overrides(ff_arg, overrides, **kwargs):
        seen["n_rows"] = len(ff_arg)
        return pd.DataFrame(columns=hc.SCORING_COLUMNS), pd.DataFrame()

    monkeypatch.setattr(hc, "apply_overrides", spy_apply_overrides)
    monkeypatch.setattr(hc, "to_scoring_frame", lambda *a, **k: pd.DataFrame(columns=hc.SCORING_COLUMNS))

    hc.build_full_frame(ff, matcher=None, cbz=set(), flagged_bad=None,
                        overrides=[], as_of=pd.Timestamp("2026-08-01"))

    assert seen["n_rows"] == 2, "apply_overrides must see the ORIGINAL row count, not the deduped one"
