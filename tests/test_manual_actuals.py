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
                      {"pattern": "^Retail Sales m/m$", "indicator": "retail_sales"}],
})
CBZ = {"retail_sales"}   # cpi_yoy is NOT can_be_zero, retail_sales IS


def _row(ccy, canon, dt, actual, forecast=1.0, name_raw=None):
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": name_raw or canon,
            "name_canonical": canon, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": 0.5,
            "released": True, "source": "ff"}


def _frame(rows):
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


def _find(rows, **kw):
    kw.setdefault("matcher", MATCHER)
    kw.setdefault("can_be_zero", CBZ)
    kw.setdefault("now_utc", NOW)
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

def test_zero_confirm_flagged_by_default_no_widening_path():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0)]
    out = _find(rows)
    assert len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM


def test_zero_confirm_widened_by_can_be_zero_config():
    """retail_sales is can_be_zero=True by config -> never flagged, per (a)."""
    rows = [_row("USD", "Retail Sales m/m", NOW - pd.Timedelta(hours=1), 0.0)]
    assert _find(rows).empty


def test_zero_confirm_cbz_row_still_flagged_when_flagged_bad_marks_it_bad():
    """fix/cbz-flagged-bad-guard regression: can_be_zero is candidate
    legitimacy, not automatic pass -- flagged_bad has final say over BOTH
    routes now, same as ff_scoring.to_scoring_frame. Mirrors the real AUD
    Cash Rate case (interest_rate_decision, Quality=Strength='Data Not
    Loaded') that motivated that fix: a cbz zero must still surface as
    ZERO_CONFIRM when JBlanked itself flags it, not silently pass through."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "Retail Sales m/m", dt, 0.0)]
    lookup = {("USD", "Retail Sales m/m", dt.date()): True}   # flagged bad
    out = _find(rows, flagged_bad=lookup)
    assert len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM


def test_zero_confirm_cbz_row_kept_when_flagged_bad_clean():
    """Same cbz row, flagged_bad explicitly clean -> still widened, per (a)+gate."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "Retail Sales m/m", dt, 0.0)]
    lookup = {("USD", "Retail Sales m/m", dt.date()): False}   # explicitly clean
    assert _find(rows, flagged_bad=lookup).empty


def test_zero_confirm_widened_by_flagged_bad_suffix_clean():
    """cpi_yoy is NOT can_be_zero, but a real m/m-suffixed name_raw not flagged
    bad passes via path (b) — the suffix-widening reuse from ff_scoring."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0, name_raw="CPI m/m")]
    lookup = {("USD", "CPI m/m", dt.date()): False}   # explicitly clean
    assert _find(rows, flagged_bad=lookup).empty


def test_zero_confirm_blocked_when_flagged_bad_marks_it():
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0, name_raw="CPI m/m")]
    lookup = {("USD", "CPI m/m", dt.date()): True}   # explicitly bad
    out = _find(rows, flagged_bad=lookup)
    assert len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM


def test_zero_confirm_flagged_bad_missing_key_defaults_blocked():
    """Missing lookup key defaults to flagged/blocked (same default as
    to_scoring_frame) -> the suffix path does NOT widen it -> ZERO_CONFIRM."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0, name_raw="CPI m/m")]
    out = _find(rows, flagged_bad={})   # no entry for this key
    assert len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM


def test_zero_confirm_non_mm_qq_suffix_never_widened_even_if_clean():
    """A y/y-suffixed name_raw never takes the widening path regardless of
    flagged_bad -- only m/m|q/q do."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0, name_raw="CPI y/y")]
    lookup = {("USD", "CPI y/y", dt.date()): False}
    out = _find(rows, flagged_bad=lookup)
    assert len(out) == 1 and out.iloc[0]["state"] == ZERO_CONFIRM


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
    rows = [
        _row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0),
        _row("USD", "CPI y/y", NOW - pd.Timedelta(hours=9), float("nan")),
    ]
    out = _find(rows)
    assert list(out.columns) == RESULT_COLUMNS
    assert list(out["state"]) == [MISSING, ZERO_CONFIRM]   # older row first
    assert list(out["datetime_utc"]) == sorted(out["datetime_utc"])


def test_pure_no_mutation_of_input():
    rows = [_row("USD", "CPI y/y", NOW - pd.Timedelta(hours=1), 0.0)]
    src = _frame(rows)
    before = src.copy(deep=True)
    find_actionable_rows(src, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    pd.testing.assert_frame_equal(src, before)


# --- real-config integration (matches tests/test_ff_scoring.py pattern) ------

def test_real_matcher_and_can_be_zero_wire_up():
    """No matcher/can_be_zero override -> loads the live indicator config."""
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0)]     # cpi_yoy: NOT can_be_zero by config
    out = find_actionable_rows(_frame(rows), now_utc=NOW)
    assert len(out) == 1
    assert out.iloc[0]["indicator_key"] == "cpi_yoy"
    assert out.iloc[0]["state"] == ZERO_CONFIRM


def test_real_can_be_zero_config_widens_retail_sales():
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "Retail Sales m/m", dt, 0.0)]   # can_be_zero: true
    assert find_actionable_rows(_frame(rows), now_utc=NOW).empty


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
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
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
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(0.0)
    assert manual.iloc[0]["source"] == "manual"
    assert remaining.empty


def test_apply_overrides_zero_confirm_row_corrected():
    dt = NOW - pd.Timedelta(hours=1)
    rows = [_row("USD", "CPI y/y", dt, 0.0)]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt, 0.4, state_resolved=ZERO_CONFIRM)]
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
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
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    assert manual.empty
    assert remaining.empty   # not actionable either way (real value present)


def test_apply_overrides_never_touches_unrelated_actionable_rows():
    dt1 = NOW - pd.Timedelta(hours=7)
    dt2 = NOW - pd.Timedelta(hours=8)
    rows = [_row("USD", "CPI y/y", dt1, float("nan")),
            _row("USD", "CPI y/y", dt2, float("nan"))]
    ff = _frame(rows)
    overrides = [_override("usd_x", dt1, 3.2)]   # only resolves dt1
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    assert len(manual) == 1
    assert len(remaining) == 1 and remaining.iloc[0]["datetime_utc"] == dt2


def test_apply_overrides_empty_overrides_is_noop():
    dt = NOW - pd.Timedelta(hours=7)
    rows = [_row("USD", "CPI y/y", dt, float("nan"))]
    ff = _frame(rows)
    manual, remaining = apply_overrides(ff, [], now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    assert manual.empty and list(manual.columns) == SCORING_COLUMNS
    assert len(remaining) == 1


def test_apply_overrides_empty_ff_frame():
    manual, remaining = apply_overrides(_frame([]), [_override("usd_x", NOW, 1.0)],
                                        now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
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
    manual, remaining = apply_overrides(ff, overrides, now_utc=NOW, matcher=MATCHER, can_be_zero=CBZ)
    assert len(manual) == 1 and manual.iloc[0]["actual"] == pytest.approx(3.2)   # override still worked
    assert remaining.empty                                                       # resolved, as normal
