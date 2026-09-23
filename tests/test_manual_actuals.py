"""Manual Actuals Panel — Phase A pure detection (feat/manual-actuals-panel).

Unit tests use a small synthetic CompiledMatcher for full control over the
matched/unmatched boundary; two integration tests wire up the REAL
ff_scoring.build_matcher()/load_can_be_zero() to confirm the module composes
with production config, matching the pattern in tests/test_ff_scoring.py.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.econ_calendar_ff import CANON_COLUMNS
from src.economic_fetch import CompiledMatcher
from src.ff_scoring import SCORING_COLUMNS, build_matcher, load_can_be_zero
from src.manual_actuals import (MISSING, RELEVANCE_WINDOW, RESULT_COLUMNS,
                                ZERO_CONFIRM, apply_overrides,
                                apply_relevance_window, find_actionable_rows,
                                load_overrides)

NOW = pd.Timestamp("2026-08-11 12:00:00")

MATCHER = CompiledMatcher({
    "United States": [{"pattern": "^CPI y/y$", "indicator": "cpi_yoy"},
                      {"pattern": "^Retail Sales m/m$", "indicator": "retail_sales"},
                      {"pattern": "^Fed Funds Rate$", "indicator": "interest_rate_decision"}],
})
CBZ = {"retail_sales"}   # cpi_yoy is NOT can_be_zero, retail_sales IS

# feat/manual-actuals-dedupe: synthetic config for rule (c)'s
# effective_frequency/dedup_gap_days lookup — mirrors the shape of
# data/economic_indicators.yaml's `indicators`/`defaults` keys.
INDICATORS_CFG = {
    "indicators": {
        "cpi_yoy": {"frequency": "monthly"},
        "retail_sales": {"frequency": "monthly"},
        "interest_rate_decision": {"frequency": "monthly"},
    },
    "defaults": {
        "default_frequency": "monthly",
        "dedup_gap_days": {"weekly": 3, "monthly": 18, "quarterly": 45},
    },
}


_PLACEHOLDER = object()


def _row(ccy, canon, dt, actual, forecast=1.0, name_raw=None, canonical_id=None,
         jb_status=_PLACEHOLDER, forecast_origin="ff"):
    # audit 2.3: a 0.0 is JBlanked's placeholder iff jb_status says
    # "Data Not Loaded" -> that is what a test 0.0 stands for unless told otherwise.
    if jb_status is _PLACEHOLDER:
        jb_status = "Data Not Loaded" if actual == 0.0 else None
    return {"canonical_id": canonical_id or f"{ccy.lower()}_x", "currency": ccy,
            "name_raw": name_raw or canon,
            "name_canonical": canon, "datetime_utc": pd.Timestamp(dt),
            # previous unknown by default: under Z2/Z4 a known next.previous would
            # contradict and auto-recover a test 0.0 (no human needed).
            "actual": actual, "forecast": forecast, "previous": float("nan"),
            "released": True, "source": "ff",
            "forecast_origin": forecast_origin, "jb_status": jb_status}


class _AllZeroPossible(dict):
    """Synthetic series ids ('usd_x', ...) are all zero_possible=True here: a test
    0.0 is a placeholder through its jb_status (Z2), not through Z1."""
    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return True


ZP_ALL = _AllZeroPossible()


def _frame(rows):
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


def _find(rows, **kw):
    kw.setdefault("matcher", MATCHER)
    kw.setdefault("can_be_zero", CBZ)
    kw.setdefault("now_utc", NOW)
    kw.setdefault("indicators_cfg", INDICATORS_CFG)
    kw.setdefault("zero_possible", ZP_ALL)
    return find_actionable_rows(_frame(rows), **kw)


# --- MISSING -------------------------------------------------------------

def test_missing_flagged_when_old_and_null():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=7), float("nan"))]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["state"] == MISSING


def test_missing_not_flagged_within_grace_window():
    """Under 6h old and still null — that's `stale`, not actionable yet."""
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=5, minutes=59), float("nan"))]
    assert _find(rows).empty


def test_missing_grace_window_is_configurable():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=2), float("nan"))]
    out = _find(rows, missing_after=pd.Timedelta(hours=1))
    assert len(out) == 1 and out.iloc[0]["state"] == MISSING


def test_future_null_row_never_missing():
    rows = [_row("USD", "CPI y/y", NOW + pd.Timedelta(days=3), float("nan"))]
    assert _find(rows).empty


# --- ZERO_CONFIRM ----------------------------------------------------------

@pytest.mark.parametrize("status,actionable", [
    ("Data Not Loaded", True),   # JB placeholder -> needs a human
    ("Good Data", False),        # a real 0.0 print (Good/Bad Data is a direction tag)
    ("Bad Data", False),
    (None, False),               # no JB payload ever carried it -> FF 0.0 kept
])
def test_zero_confirm_iff_jb_status_data_not_loaded(status, actionable):
    """audit 2.3: one rule, the same as ff_scoring.to_scoring_frame's."""
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0, jb_status=status)]
    out = _find(rows)
    assert (len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM) is actionable


def test_can_be_zero_and_flagged_bad_are_ignored():
    """The old routes are gone: can_be_zero no longer excuses a placeholder and
    a flagged_bad lookup no longer blocks a real zero."""
    dt = NOW - pd.Timedelta(hours=1)
    placeholder = [_row("USD", "Retail Sales m/m", dt, 0.0, jb_status="Data Not Loaded")]
    assert len(_find(placeholder, can_be_zero={"retail_sales"})) == 1
    real = [_row("USD", "CPI y/y", dt, 0.0, name_raw="CPI m/m", jb_status="Bad Data")]
    assert _find(real, flagged_bad={("USD", "CPI m/m", dt.date()): True}).empty


def test_manual_row_consensus_follows_the_provenance_rule():
    """audit 2.2: a manual row's consensus no longer bypasses the rule —
    FF "0.0%" (ff) is kept, a JB 0.0 is not."""
    dt = NOW - pd.Timedelta(hours=8)
    ov = [{"canonical_id": "usd_x", "datetime_utc": str(dt), "actual": 5.7}]
    kept, _ = apply_overrides(_frame([_row("USD", "CPI y/y", dt, 0.0, forecast=0.0)]), ov,
                              now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ,
                              indicators_cfg=INDICATORS_CFG, zero_possible=ZP_ALL)
    assert kept.iloc[0]["consensus"] == 0.0
    dropped, _ = apply_overrides(_frame([_row("USD", "CPI y/y", dt, 0.0, forecast=0.0,
                                               forecast_origin="jb")]), ov,
                                 now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ,
                                 indicators_cfg=INDICATORS_CFG, zero_possible=ZP_ALL)
    assert pd.isna(dropped.iloc[0]["consensus"])


# --- exclusions --------------------------------------------------------------

def test_unmatched_row_excluded_regardless_of_state():
    rows = [_row("USD", "Some Unmodeled Event", NOW - pd.Timedelta(days=1), float("nan")),
            _row("USD", "Some Unmodeled Event", NOW - pd.Timedelta(hours=1), 0.0)]
    assert _find(rows).empty


def test_real_nonzero_value_row_excluded():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 3.2)]
    assert _find(rows).empty


def test_empty_frame_returns_empty_with_columns():
    out = find_actionable_rows(pd.DataFrame(columns=CANON_COLUMNS), now_utc=NOW)
    assert out.empty and list(out.columns) == RESULT_COLUMNS


# --- shape / ordering ----------------------------------------------------

def test_sorted_oldest_first_and_columns():
    # >18d apart -- two UNRELATED rows for this test, not a duplicate pair
    # (feat/manual-actuals-dedupe would otherwise collapse a closer same-
    # canonical_id pair via rule (b)/(c), same default canonical_id).
    rows = [
        _row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0),
        _row("USD", "CPI y/y", NOW - pd.Timedelta(days=30, hours=9), float("nan")),
    ]
    out = _find(rows)
    assert list(out.columns) == RESULT_COLUMNS
    assert list(out["state"]) == [MISSING, ZERO_CONFIRM]   # older row first
    assert list(out["datetime_utc"]) == sorted(out["datetime_utc"])


def test_pure_no_mutation_of_input():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0)]
    src = _frame(rows)
    before = src.copy(deep=True)
    find_actionable_rows(src, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    pd.testing.assert_frame_equal(src, before)


# --- real-config integration (matches tests/test_ff_scoring.py pattern) ------

def test_real_matcher_and_can_be_zero_wire_up():
    """No matcher/can_be_zero override -> loads the live indicator config."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0)]     # cpi_yoy: NOT can_be_zero by config
    out = find_actionable_rows(_frame(rows), now_utc=NOW, zero_possible=ZP_ALL)
    assert len(out) == 1
    assert out.iloc[0]["indicator_key"] == "cpi_yoy"
    assert out.iloc[0]["state"] == ZERO_CONFIRM


# --- duplicate suppression (feat/manual-actuals-dedupe) ----------------------

def test_rule_a_same_day_valid_sibling_suppresses_ambiguous_row():
    """Pattern 2: a same-calendar-day sibling with a real actual makes the
    ambiguous row redundant -> suppressed entirely, not merely deduped to 1."""
    dt_zero = NOW - pd.Timedelta(days=1, hours=1)
    dt_valid = dt_zero + pd.Timedelta(hours=1)   # same day, real print
    rows = [
        _row("USD", "CPI y/y", dt_zero, 0.0, canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt_valid, 1.9, canonical_id="usd_x"),
    ]
    out = _find(rows)
    assert out.empty


def test_rule_a_scans_the_full_ff_frame_not_just_actionable_rows():
    """The valid sibling is never itself actionable (real actual != NaN/0.0)
    -- rule (a) must still see it by scanning `ff` directly, not `raw`."""
    dt_missing = NOW - pd.Timedelta(days=1, hours=7)
    dt_valid = dt_missing + pd.Timedelta(hours=1)
    rows = [
        _row("USD", "CPI y/y", dt_missing, float("nan"), canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt_valid, 3.4, canonical_id="usd_x"),
    ]
    out = _find(rows)
    assert out.empty


def test_rule_a_scope_is_same_day_only_not_wider():
    """A valid sibling on a DIFFERENT calendar day must not suppress (that is
    rule (c)'s narrower, content-guarded job, not rule (a)'s)."""
    dt_zero = NOW - pd.Timedelta(days=2)
    dt_valid = NOW - pd.Timedelta(days=1)   # next day, not same calendar day
    rows = [
        _row("USD", "CPI y/y", dt_zero, 0.0, canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt_valid, 1.9, canonical_id="usd_x"),
    ]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["datetime_utc"] == dt_zero


def test_rule_b_same_day_no_valid_actual_keeps_latest():
    """Pattern 1 (DST +-1h): two same-day duplicates, neither resolved ->
    keep the latest datetime_utc, per the confirmed tie-break."""
    dt1 = NOW - pd.Timedelta(days=1, hours=2)
    dt2 = dt1 + pd.Timedelta(hours=1)
    rows = [
        _row("USD", "CPI y/y", dt1, 0.0, canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt2, 0.0, canonical_id="usd_x"),
    ]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["datetime_utc"] == dt2


def test_rule_c_cross_day_cluster_interest_rate_decision_keeps_latest():
    """Pattern 3: an FF-revised interest_rate_decision timestamp spanning
    calendar days collapses to the latest (most-confirmed) revision."""
    dt1 = NOW - pd.Timedelta(days=3)
    dt2 = dt1 + pd.Timedelta(days=2)     # within the 7d interest_rate_decision override
    dt3 = dt2 + pd.Timedelta(hours=5)
    rows = [_row("USD", "Fed Funds Rate", dt, float("nan"), forecast=1.0,
                canonical_id="jpy_boj_interest_rate_decision") for dt in (dt1, dt2, dt3)]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["datetime_utc"] == dt3


def test_rule_c_interest_rate_decision_uses_7d_override_not_18d_monthly_default():
    """10 days apart: over the 7d interest_rate_decision override, under the
    18d monthly default that would otherwise apply -- must stay unmerged."""
    dt1 = NOW - pd.Timedelta(days=20)
    dt2 = dt1 + pd.Timedelta(days=10)
    rows = [_row("USD", "Fed Funds Rate", dt, float("nan"), forecast=1.0,
                canonical_id="jpy_boj_interest_rate_decision") for dt in (dt1, dt2)]
    out = _find(rows)
    assert len(out) == 2


def test_rule_c_content_guard_blocks_merge_when_forecast_differs():
    """A differing forecast means a genuinely distinct event, regardless of
    how close in time -- the AND (not OR) amendment to rule (c)."""
    dt1 = NOW - pd.Timedelta(days=3)
    dt2 = dt1 + pd.Timedelta(days=2)   # well within the 7d window
    rows = [
        _row("USD", "Fed Funds Rate", dt1, float("nan"), forecast=1.0,
             canonical_id="jpy_boj_interest_rate_decision"),
        _row("USD", "Fed Funds Rate", dt2, float("nan"), forecast=2.0,
             canonical_id="jpy_boj_interest_rate_decision"),
    ]
    out = _find(rows)
    assert len(out) == 2


def test_rule_c_content_guard_does_not_change_current_data_confirmed_cases():
    """The measurement showed all confirmed real clusters (BoJ, CHF cases)
    carry identical forecast -- the guard is a no-op for them, only a net
    going forward. Reproduced here with identical forecast -> still merges."""
    dt1 = NOW - pd.Timedelta(days=3)
    dt2 = dt1 + pd.Timedelta(hours=6)
    rows = [
        _row("USD", "Fed Funds Rate", dt1, float("nan"), forecast=1.0,
             canonical_id="jpy_boj_interest_rate_decision"),
        _row("USD", "Fed Funds Rate", dt2, float("nan"), forecast=1.0,
             canonical_id="jpy_boj_interest_rate_decision"),
    ]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["datetime_utc"] == dt2


def test_rule_c_never_clusters_through_years_of_valid_prints():
    """Regression for the REJECTED wider-window design: clustering must stay
    scoped to the already-ambiguous subset. A canonical_id with many valid
    prints spaced <18d apart (which chained gbp_gdp's entire multi-year
    history together when valid rows were included in the search) must not
    let two genuinely unrelated, widely-separated ambiguous rows merge."""
    rows = []
    base = NOW - pd.Timedelta(days=730)
    for i in range(48):
        rows.append(_row("USD", "CPI y/y", base + pd.Timedelta(days=15 * i), 2.0,
                         canonical_id="usd_x"))
    amb1 = NOW - pd.Timedelta(days=400)
    amb2 = NOW - pd.Timedelta(days=200)
    rows.append(_row("USD", "CPI y/y", amb1, 0.0, canonical_id="usd_x"))
    rows.append(_row("USD", "CPI y/y", amb2, 0.0, canonical_id="usd_x"))
    out = _find(rows)
    assert len(out) == 2


def test_suppression_pure_no_mutation_of_input():
    dt1 = NOW - pd.Timedelta(days=1, hours=2)
    dt2 = dt1 + pd.Timedelta(hours=1)
    rows = [
        _row("USD", "CPI y/y", dt1, 0.0, canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt2, 0.0, canonical_id="usd_x"),
    ]
    src = _frame(rows)
    before = src.copy(deep=True)
    find_actionable_rows(src, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL,
                         indicators_cfg=INDICATORS_CFG)
    pd.testing.assert_frame_equal(src, before)


# --- overrides (feat/manual-actuals-panel, Phase B) ---------------------------

def _override(canonical_id, dt, actual, **extra):
    entry = {"canonical_id": canonical_id, "datetime_utc": str(pd.Timestamp(dt)),
             "actual": actual, "state_resolved": MISSING, "entered_by": "sebastian",
             "entered_at": "2026-08-11T15:00:00", "note": "test"}
    entry.update(extra)
    return entry


def test_load_overrides_missing_file_returns_empty(tmp_path):
    assert load_overrides(tmp_path / "nope.json") == []


def test_load_overrides_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert load_overrides(p) == []


def test_load_overrides_roundtrip(tmp_path):
    import json
    p = tmp_path / "overrides.json"
    entries = [_override("usd_x", NOW, 3.2)]
    p.write_text(json.dumps(entries))
    assert load_overrides(p) == entries


def test_apply_overrides_missing_row_becomes_manual_source_row():
    dt = NOW - pd.Timedelta(hours=7)
    rows = [_row("USD", "CPI y/y", dt, float("nan"))]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 3.2)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert list(manual.columns) == SCORING_COLUMNS
    assert len(manual) == 1
    m = manual.iloc[0]
    assert m["source"] == "manual" and m["actual"] == pytest.approx(3.2)
    assert m["currency"] == "USD" and m["indicator_key"] == "cpi_yoy"
    assert m["consensus"] == pytest.approx(1.0)   # carried from the FF row's forecast
    assert remaining.empty   # resolved -> no longer needs review


def test_apply_overrides_zero_confirm_row_confirmed_at_zero():
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0)]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 0.0, state_resolved=ZERO_CONFIRM)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(0.0)
    assert manual.iloc[0]["source"] == "manual"
    assert remaining.empty


def test_apply_overrides_zero_confirm_row_corrected():
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0)]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 0.4, state_resolved=ZERO_CONFIRM)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(0.4)


def test_apply_overrides_stale_when_real_actual_has_landed():
    """The FF row now carries a REAL actual (JBlanked delivered it since the
    override was written) -> the row is no longer actionable -> the override
    is dropped, never applied. This IS the 'never overwrite a non-null
    actual' guarantee: eligibility is re-derived fresh every call."""
    dt = NOW - pd.Timedelta(hours=7)
    rows = [_row("USD", "CPI y/y", dt, 3.5)]   # real print landed
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 9.9)]   # stale guess from before
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert manual.empty
    assert remaining.empty   # not actionable either way (real value present)


def test_apply_overrides_never_touches_unrelated_actionable_rows():
    # >18d apart -- these represent two UNRELATED actionable rows for this
    # test, not a duplicate pair (feat/manual-actuals-dedupe would otherwise
    # collapse a closer same-canonical_id pair via rule (b)/(c)).
    dt1 = NOW - pd.Timedelta(hours=7)
    dt2 = NOW - pd.Timedelta(days=30, hours=8)
    rows = [_row("USD", "CPI y/y", dt1, float("nan")),
            _row("USD", "CPI y/y", dt2, float("nan"))]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt1, 3.2)]   # only resolves dt1
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert len(manual) == 1
    assert len(remaining) == 1 and remaining.iloc[0]["datetime_utc"] == dt2


def test_apply_overrides_empty_overrides_is_noop():
    dt = NOW - pd.Timedelta(hours=7)
    rows = [_row("USD", "CPI y/y", dt, float("nan"))]
    ff = _frame(rows)
    manual, remaining = apply_overrides(ff, [], now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert manual.empty and list(manual.columns) == SCORING_COLUMNS
    assert len(remaining) == 1


def test_apply_overrides_grandfathered_across_rule_a_same_day_suppression():
    """Regression: an override written on a row rule (a) now suppresses (a
    valid actual landed same-day on a sibling) must keep resolving and keep
    contributing to scoring. Mirrors production: usd_durable_goods_orders
    @ 2026-07-27 12:30 already has a committed override; its same-day sibling
    (11:30, actual=0.3) makes it a rule-(a) suppression target."""
    dt_override_target = NOW - pd.Timedelta(days=1, hours=1)
    dt_valid_sibling = dt_override_target + pd.Timedelta(hours=1)
    rows = [
        _row("USD", "CPI y/y", dt_override_target, 0.0, canonical_id="usd_x"),
        _row("USD", "CPI y/y", dt_valid_sibling, 0.3, canonical_id="usd_x"),
    ]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt_override_target, 0.3, state_resolved=ZERO_CONFIRM)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, zero_possible=ZP_ALL,
                                        can_be_zero=CBZ, indicators_cfg=INDICATORS_CFG)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(0.3)
    assert remaining.empty   # suppressed from the panel, but the override still resolved


def test_apply_overrides_grandfathered_across_rule_c_cross_day_suppression():
    """Regression: an override written on a row rule (c) now suppresses (its
    cluster's later revision was kept instead) must keep resolving. Mirrors
    production: jpy_boj_interest_rate_decision @ 2026-07-29 21:00 already has
    a committed override; the FF-revised 2026-07-31 timestamps put it in a
    rule-(c) cluster whose kept representative is the later revision.

    Known, accepted consequence (not a bug -- see the final report/docs):
    the cluster's kept representative (dt_kept) still shows in `remaining`
    even though the cluster is effectively already resolved via the
    grandfathered override on its sibling -- rule (c) does not propagate
    resolution across a cluster, only exact-key matches do."""
    dt_override_target = NOW - pd.Timedelta(days=3)
    dt_kept = dt_override_target + pd.Timedelta(days=2)
    rows = [
        _row("USD", "Fed Funds Rate", dt_override_target, float("nan"), forecast=1.0,
             canonical_id="jpy_boj_interest_rate_decision"),
        _row("USD", "Fed Funds Rate", dt_kept, float("nan"), forecast=1.0,
             canonical_id="jpy_boj_interest_rate_decision"),
    ]
    ff = _frame(rows)
    overrides = [_override("jpy_boj_interest_rate_decision", dt_override_target, 1.0)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, zero_possible=ZP_ALL,
                                        can_be_zero=CBZ, indicators_cfg=INDICATORS_CFG)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(1.0)
    assert len(remaining) == 1 and remaining.iloc[0]["datetime_utc"] == dt_kept


def test_apply_overrides_empty_ff_frame():
    manual, remaining = apply_overrides(_frame([]), [_override("usd_x", NOW, 1.0)],
                                        now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert manual.empty and remaining.empty


# --- relevance window (panel display filter, 2026-08 feedback) ---------------

def test_relevance_window_default_is_45_days():
    assert RELEVANCE_WINDOW == pd.Timedelta(days=45)


def test_relevance_window_keeps_rows_within_default_window():
    rows = [
        _row("USD", "CPI y/y", NOW - pd.Timedelta(hours=7), float("nan")),          # ~0d old
        _row("USD", "CPI y/y", NOW - pd.Timedelta(days=44), 0.0),                   # 44d old
    ]
    out = _find(rows)
    recent, older = apply_relevance_window(out, now_utc=NOW)
    assert len(recent) == 2 and older == 0


def test_relevance_window_drops_rows_older_than_45d_but_reports_count():
    rows = [
        _row("USD", "CPI y/y", NOW - pd.Timedelta(hours=7), float("nan")),          # kept
        _row("USD", "CPI y/y", NOW - pd.Timedelta(days=46), 0.0),                   # dropped
        _row("USD", "CPI y/y", NOW - pd.Timedelta(days=400), 0.0, name_raw="X"),    # dropped
    ]
    out = _find(rows)
    recent, older = apply_relevance_window(out, now_utc=NOW)
    assert len(recent) == 1 and recent.iloc[0]["state"] == MISSING
    assert older == 2


def test_relevance_window_exactly_at_boundary_is_kept():
    dt = NOW - RELEVANCE_WINDOW   # exactly 45d old -> >= cutoff -> kept
    rows = [_row("USD", "CPI y/y", dt, float("nan"))]
    out = _find(rows)
    recent, older = apply_relevance_window(out, now_utc=NOW)
    assert len(recent) == 1 and older == 0


def test_relevance_window_custom_window_overrides_default():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(days=10), float("nan"))]
    out = _find(rows)
    recent, older = apply_relevance_window(out, now_utc=NOW, window=pd.Timedelta(days=5))
    assert len(recent) == 0 and older == 1


def test_relevance_window_empty_input():
    empty = pd.DataFrame(columns=RESULT_COLUMNS)
    recent, older = apply_relevance_window(empty, now_utc=NOW)
    assert recent.empty and older == 0


def test_relevance_window_never_affects_override_eligibility():
    """A row 46 days old (outside the panel window) must still be a valid
    override target -- the window is a LISTING filter only, apply_overrides
    must keep reconciling against the full, unwindowed universe."""
    dt = NOW - pd.Timedelta(days=46)
    rows = [_row("USD", "CPI y/y", dt, float("nan"))]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 3.2)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ, zero_possible=ZP_ALL)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(3.2)   # override still worked
    assert remaining.empty                                                       # resolved, as normal
