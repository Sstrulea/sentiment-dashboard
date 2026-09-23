"""Audit 1.2 — previous-consistency integrity check (src/previous_consistency.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ff_scoring import SCORING_COLUMNS, build_matcher
from src.previous_consistency import check_previous_consistency, revision_tolerance

CID, CCY, NAME = "aud_import_prices", "AUD", "Import Prices q/q"
KEY = "import_prices"


def _ff(rows):
    """rows: [(datetime, actual, previous)] for the AUD import prices series."""
    return pd.DataFrame([{
        "canonical_id": CID, "currency": CCY, "name_raw": NAME, "name_canonical": NAME,
        "datetime_utc": pd.Timestamp(dt), "actual": a, "forecast": np.nan,
        "previous": p, "released": True, "source": "ff",
    } for dt, a, p in rows])


def _scoring(rows, manual=()):
    """Scoring frame: the same rows as scored (actual as given, NaN = quarantined),
    plus manual override rows [(datetime, actual)]."""
    recs = [{"currency": CCY, "indicator_key": KEY, "release_dt": pd.Timestamp(dt),
             "actual": a, "consensus": np.nan, "previous": p, "source": "ff",
             "name_raw": NAME} for dt, a, p in rows]
    recs += [{"currency": CCY, "indicator_key": KEY, "release_dt": pd.Timestamp(dt),
              "actual": a, "consensus": np.nan, "previous": np.nan, "source": "manual",
              "name_raw": np.nan} for dt, a in manual]
    return pd.DataFrame(recs, columns=SCORING_COLUMNS)


HIST = [("2025-01-30", 0.2, -1.4), ("2025-05-01", 3.3, 0.2), ("2025-07-31", -0.8, 3.3),
        ("2025-10-30", -0.4, -0.8), ("2026-01-29", 0.9, -0.4)]


@pytest.fixture(scope="module")
def matcher():
    return build_matcher()


def test_scored_zero_contradicted_by_next_previous_is_flagged(matcher):
    rows = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 0.1)]
    res = check_previous_consistency(_ff(rows), _scoring(rows), matcher)
    assert [f["release_dt"][:10] for f in res["findings"]] == ["2026-04-30"]
    f = res["findings"][0]
    assert f["scored_source"] == "ff" and f["next_previous"] == 0.1
    assert f["tolerance"] == 0.0     # series never revised -> tol 0


def test_quarantined_zero_is_reported_as_quarantined(matcher):
    """The real AUD 2026-04-30 shape: 0.0 quarantined to NaN at scoring."""
    rows = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 0.1)]
    scored = [(d, (np.nan if d == "2026-04-30" else a), p) for d, a, p in rows]
    res = check_previous_consistency(_ff(rows), _scoring(scored), matcher)
    assert [(f["release_dt"][:10], f["scored_source"]) for f in res["findings"]] == \
        [("2026-04-30", "quarantined")]


def test_manual_override_value_is_what_is_checked(matcher):
    rows = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 0.1)]
    quarantined = [(d, (np.nan if d == "2026-04-30" else a), p) for d, a, p in rows]
    ok = check_previous_consistency(_ff(rows), _scoring(quarantined, manual=[("2026-04-30", 0.1)]), matcher)
    assert ok["findings"] == []
    bad = check_previous_consistency(_ff(rows), _scoring(quarantined, manual=[("2026-04-30", 0.0)]), matcher)
    assert [(f["release_dt"][:10], f["scored_source"]) for f in bad["findings"]] == \
        [("2026-04-30", "manual")]


def test_upcoming_weekly_feed_supplies_next_previous(matcher):
    rows = HIST + [("2026-04-30", 0.0, 0.9)]
    upcoming = _ff([("2026-07-30", np.nan, 0.1)])
    res = check_previous_consistency(_ff(rows), _scoring(rows), matcher, upcoming=upcoming)
    assert len(res["findings"]) == 1 and res["findings"][0]["next_from"] == "ff_weekly"
    assert check_previous_consistency(_ff(rows), _scoring(rows), matcher)["findings"] == []


def test_zero_confirmed_by_next_previous_is_not_flagged(matcher):
    rows = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 0.0)]
    assert check_previous_consistency(_ff(rows), _scoring(rows), matcher)["findings"] == []


def test_revision_within_series_tolerance_is_not_flagged(matcher):
    # a series revised by ~0.5 every release -> tol > 0.3
    hist = [(f"2024-{m:02d}-15", 1.0, 1.5) for m in range(1, 12)]
    rows = hist + [("2025-01-15", 0.0, 1.5), ("2025-02-15", 1.0, 0.3)]
    res = check_previous_consistency(_ff(rows), _scoring(rows), matcher)
    assert res["findings"] == []


def test_same_day_relisting_is_one_release(matcher):
    """FF lists the release twice (00:30 and 01:30); the later one is the release."""
    rows = HIST + [("2026-04-30 00:30", 0.0, 0.9), ("2026-04-30 01:30", 0.0, 0.9),
                   ("2026-07-30 01:30", 1.0, 0.1)]
    res = check_previous_consistency(_ff(rows), _scoring(rows), matcher)
    assert [f["release_dt"] for f in res["findings"]] == ["2026-04-30T01:30:00"]


def test_tolerance_is_robust_to_ghost_pairs():
    """Two spurious archive pairs must not lift the tolerance of a never-revised
    series (a quantile would: q95 here is 0.87)."""
    rev = np.array([1.0, 0.7] + [0.0] * 12)
    actual = np.ones_like(rev)
    tol, n = revision_tolerance(actual, actual + rev)
    assert n == 14 and tol == 0.0


def test_zero_actuals_do_not_enter_the_tolerance():
    tol, n = revision_tolerance(np.array([0.0, 0.0, 1.0]), np.array([5.0, 5.0, 1.0]))
    assert (tol, n) == (0.0, 1)


def test_repo_history_aud_import_prices_2026_04_30_is_recovered(matcher):
    """Acceptance (phase 2, Z2/Z4): AUD import prices 2026-04-30 — 0.0 contradicted
    by the next previous (0.1) — is now recovered in scoring (actual_origin
    ff_previous), so previous-consistency no longer reports it."""
    from src import economic_render as er
    as_of = pd.Timestamp("2026-09-23T07:06:11")
    cal = er._load_calendar_frame(as_of)
    row = cal[(cal["currency"] == "AUD") & (cal["indicator_key"] == "import_prices")
              & (cal["release_dt"] == pd.Timestamp("2026-04-30 01:30"))]
    assert row["actual"].tolist() == [0.1] and row["actual_origin"].tolist() == ["ff_previous"]
    report = er._integrity_report(cal, as_of)
    found = {(f["canonical_id"], f["release_dt"][:10])
             for f in report["checks"]["previous_consistency"]["findings"]}
    assert ("aud_import_prices", "2026-04-30") not in found


def test_integrity_counter_is_warn_and_not_stale():
    from src.economic_render import _integrity_summary
    # 4B: the summary is "warn" only for unresolved findings inside a scoring
    # window (level WARN); INFO-only findings (resolved / old) read "ok".
    rep = {"checks": {"previous_consistency": {
        "findings": [{"level": "WARN", "new": True}, {"level": "INFO"}, {"level": "INFO"}],
        "findings_by_source": {"ff": 3}}}}
    s = _integrity_summary(rep)
    assert s["previous_consistency"] == 3 and s["level"] == "warn"
    assert (s["warn"], s["new_warn"]) == (1, 1)
    info_only = {"checks": {"previous_consistency": {"findings": [{"level": "INFO"}] * 2}}}
    assert _integrity_summary(info_only)["level"] == "ok"
    assert "stale" not in s        # a WARN never rolls into any_stale
    assert _integrity_summary({"checks": {}})["level"] == "unavailable"


# --- F3: the whole class, not only zeros ------------------------------------------

def test_manual_typo_is_flagged_as_value(matcher):
    """A manual 7.5 where the next print says previous 5.7 (digits swapped)."""
    rows = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 5.7)]
    quarantined = [(d, (np.nan if d == "2026-04-30" else a), p) for d, a, p in rows]
    res = check_previous_consistency(_ff(rows), _scoring(quarantined, manual=[("2026-04-30", 7.5)]), matcher)
    f = res["findings"]
    assert [(x["release_dt"][:10], x["kind"], x["scored_source"], x["scored_actual"]) for x in f] == \
        [("2026-04-30", "value", "manual", 7.5)]
    assert res["findings_by_kind"] == {"value": 1}


def test_nonzero_actual_contradicted_beyond_tolerance_is_flagged(matcher):
    rows = HIST + [("2026-04-30", 0.4, 0.9), ("2026-07-30", 1.0, 2.0)]
    res = check_previous_consistency(_ff(rows), _scoring(rows), matcher)
    assert [(f["release_dt"][:10], f["kind"], f["diff"]) for f in res["findings"]] == \
        [("2026-04-30", "value", 1.6)]


def test_report_is_rewritten_only_when_findings_change(tmp_path):
    from src.economic_render import _write_integrity_report
    path = tmp_path / "integrity_report.json"
    rep = {"as_of": "2026-09-23T07:00:00", "checks": {"previous_consistency": {
        "findings": [{"canonical_id": "a", "release_dt": "2026-01-01"}], "formula": "f",
        "n_checked": 10}}}
    assert _write_integrity_report(rep, "2026-09-23T07:00:05+00:00", path)
    first = path.read_text()
    later = {**rep, "as_of": "2026-09-23T08:00:00"}
    later["checks"]["previous_consistency"] = {**rep["checks"]["previous_consistency"], "n_checked": 11}
    assert not _write_integrity_report(later, "2026-09-23T08:00:05+00:00", path)
    assert path.read_text() == first                  # timestamps alone never rewrite
    changed = {"as_of": "x", "checks": {"previous_consistency": {"findings": [], "formula": "f"}}}
    assert _write_integrity_report(changed, "t", path)


# --- F3 floor: value class only ------------------------------------------------------

def test_value_class_ignores_a_last_digit_revision_but_zero_class_does_not(matcher):
    # one-decimal, never-revised series: tol 0, value floor 2 * 0.1 = 0.2
    small = HIST + [("2026-04-30", 0.4, 0.9), ("2026-07-30", 1.0, 0.5)]   # revised by 0.1
    assert check_previous_consistency(_ff(small), _scoring(small), matcher)["findings"] == []
    big = HIST + [("2026-04-30", 0.4, 0.9), ("2026-07-30", 1.0, 0.7)]     # 0.3 > 0.2
    assert len(check_previous_consistency(_ff(big), _scoring(big), matcher)["findings"]) == 1
    zero = HIST + [("2026-04-30", 0.0, 0.9), ("2026-07-30", 1.0, 0.1)]    # zero: no floor
    f = check_previous_consistency(_ff(zero), _scoring(zero), matcher)["findings"]
    assert [(x["kind"], x["threshold"]) for x in f] == [("zero", 0.0)]


def test_series_resolution():
    from src.previous_consistency import series_resolution
    assert series_resolution(np.array([0.2, 3.3, -1.4, np.nan])) == pytest.approx(0.1)
    assert series_resolution(np.array([333.0, 206.0])) == 1.0
    assert series_resolution(np.array([8.93, 1.5])) == pytest.approx(0.01)
