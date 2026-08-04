"""fix/calendar-freshness-per-ccy — per-currency calendar freshness.

Synthetic fixtures for the pure per-indicator-threshold logic; the jb_raw
cross-check is exercised against real retained payloads in a separate,
non-pinned integration test (mirrors the pmi_ingest_guard/jb_raw pattern).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.calendar_freshness_guard import (
    per_currency_indicator_freshness,
    currency_freshness_report,
    check_pending_actuals_in_jb_raw,
)

AS_OF = pd.Timestamp("2026-08-01")

IND_CFG = {
    "defaults": {
        "max_age_by_frequency": {"weekly": 14, "monthly": 45, "quarterly": 110},
        "max_age_days": 120, "default_frequency": "monthly",
    },
    "indicators": {
        "cpi_yoy": {"category": "inflation", "weight": 1.0, "frequency": "monthly"},
        "core_cpi": {"category": "inflation", "weight": 1.0, "frequency": "monthly",
                     "frequency_overrides": {"AUD": "quarterly"}},
        "gdp_qoq": {"category": "growth", "weight": 1.0, "frequency": "quarterly"},
        "cpi_monthly": {"category": "inflation_display", "weight": 0.0, "frequency": "monthly"},
    },
}


def _row(ccy, key, release_dt, actual=1.0):
    return {"currency": ccy, "indicator_key": key, "release_dt": pd.Timestamp(release_dt), "actual": actual}


def test_stale_indicator_flags_the_currency():
    cal = pd.DataFrame([
        _row("USD", "cpi_yoy", "2026-06-10"),   # 52 days before AS_OF, threshold 45 -> stale
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["USD"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is True
    assert "USD" in report["stale_currencies"]
    assert report["by_currency"]["USD"]["stale_indicators"][0]["indicator_key"] == "cpi_yoy"


def test_chf_style_rare_cadence_does_not_false_positive():
    # A currency whose ONE tracked indicator here is quarterly (GDP), last
    # printed well within its own 110-day window -- must NOT be flagged just
    # because it's been "a while" in absolute terms.
    cal = pd.DataFrame([
        _row("CHF", "gdp_qoq", AS_OF - pd.Timedelta(days=60)),   # 60d < 110d threshold
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["CHF"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False


def test_nzd_style_rare_cadence_multiple_indicators_no_false_positive():
    # Multiple indicators, each individually within its own threshold, even
    # though the MOST RECENT one overall is somewhat old.
    cal = pd.DataFrame([
        _row("NZD", "cpi_yoy", AS_OF - pd.Timedelta(days=40)),    # monthly, 40<45
        _row("NZD", "gdp_qoq", AS_OF - pd.Timedelta(days=100)),   # quarterly, 100<110
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["NZD"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False


def test_absent_indicator_is_no_data_not_stale():
    cal = pd.DataFrame([
        _row("GBP", "cpi_yoy", AS_OF - pd.Timedelta(days=10)),
        # gdp_qoq: zero rows for GBP at all
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["GBP"])
    gdp_row = out[out["indicator_key"] == "gdp_qoq"].iloc[0]
    assert gdp_row["status"] == "no_data"
    assert pd.isna(gdp_row["age_scored"])
    cpi_row = out[out["indicator_key"] == "cpi_yoy"].iloc[0]
    assert cpi_row["status"] == "fresh"
    assert gdp_row["status"] != cpi_row["status"]


def test_display_only_indicator_excluded_from_check():
    # cpi_monthly has weight 0.0 -> must never appear in the per-indicator table.
    cal = pd.DataFrame([
        _row("AUD", "cpi_monthly", "2020-01-01"),   # ancient, would be "stale" if checked
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=30)),   # quarterly override for AUD, fresh
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["AUD"])
    assert "cpi_monthly" not in out["indicator_key"].tolist()


def test_report_is_grouped_not_one_alert_per_indicator():
    cal = pd.DataFrame([
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100)),   # stale
        _row("USD", "gdp_qoq", AS_OF - pd.Timedelta(days=200)),   # stale
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["USD"])
    report = currency_freshness_report(out)
    assert report["stale_currencies"] == ["USD"]
    assert len(report["by_currency"]["USD"]["stale_indicators"]) == 2   # one currency entry, both listed inside


def test_all_fresh_is_silent():
    cal = pd.DataFrame([
        _row("EUR", "cpi_yoy", AS_OF - pd.Timedelta(days=5)),
        _row("JPY", "cpi_yoy", AS_OF - pd.Timedelta(days=5)),
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["EUR", "JPY"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False
    assert report["stale_currencies"] == []


# ---------------------------------------------------------------------------
# jb_raw cross-check (real retained payloads, not pinned to exact values)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# fix/freshness-guard-scored-view — severity (STALE/DEAD) and reason
# (NO_ROW/QUARANTINED), + the scored-vs-raw regression itself.
# ---------------------------------------------------------------------------

# An indicator with an explicit `max_age_days` override isolates the pure
# age-vs-threshold ratio from the "periods missed" rule: `frequency: monthly`
# drives a 30-day expected cadence for the periods-missed check, but the
# threshold itself is pinned to 10d (not derived from max_age_by_frequency),
# so 2x-threshold (20d) is reached long before 2 periods (60d) ever could be
# — exactly what's needed to test the 1x/2x age boundaries in isolation.
BOUNDARY_CFG = {
    "defaults": IND_CFG["defaults"],
    "indicators": {
        **IND_CFG["indicators"],
        "boundary_ind": {"category": "growth", "weight": 1.0,
                         "frequency": "monthly", "max_age_days": 10},
    },
}


def _boundary_status(age_days: int) -> tuple[str, "str | None"]:
    cal = pd.DataFrame([_row("USD", "boundary_ind", AS_OF - pd.Timedelta(days=age_days))])
    out = per_currency_indicator_freshness(cal, BOUNDARY_CFG, AS_OF, currencies=["USD"])
    row = out[out["indicator_key"] == "boundary_ind"].iloc[0]
    return row["status"], row["severity"]


def test_severity_exactly_1x_threshold_is_still_fresh():
    # age == threshold (10d): existing status rule is strict `age > threshold`,
    # unchanged by this fix — confirms the boundary sits where it always did.
    status, severity = _boundary_status(10)
    assert status == "fresh"
    assert severity is None


def test_severity_just_over_1x_threshold_is_stale():
    status, severity = _boundary_status(11)
    assert status == "stale"
    assert severity == "STALE"


def test_severity_exactly_2x_threshold_is_stale_not_dead():
    # age == 2x threshold (20d): DEAD requires age > 2x, strictly — 20 is not > 20.
    status, severity = _boundary_status(20)
    assert status == "stale"
    assert severity == "STALE"


def test_severity_just_over_2x_threshold_is_dead():
    status, severity = _boundary_status(21)
    assert status == "stale"
    assert severity == "DEAD"


def test_severity_periods_missed_forces_dead_under_2x_threshold():
    # AUD core_cpi: quarterly, threshold 110d (from max_age_by_frequency, NOT
    # overridden). age=187d is only 1.70x threshold (< 2x = 220) -- the pure
    # ratio rule alone would call this merely STALE. But quarterly's expected
    # cadence is 91d (_CADENCE_DAYS), so 187d is >= 2 missed periods (2.05x)
    # -- classifies DEAD via the periods-missed clause. No raw_calendar_df
    # here -> reason is None (not QUARANTINED) -> severity grades on
    # age_scored, unchanged from before Task 2's age_raw correction.
    cal = pd.DataFrame([_row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=187))])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["AUD"])
    row = out[out["indicator_key"] == "core_cpi"].iloc[0]
    assert row["status"] == "stale"
    assert row["age_scored"] == 187
    assert row["threshold_days"] == 110
    assert row["severity"] == "DEAD"


def test_reason_is_none_without_raw_calendar_df():
    # No raw_calendar_df supplied -> can't distinguish NO_ROW/QUARANTINED,
    # must not guess.
    cal = pd.DataFrame([_row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100))])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["USD"])
    assert out.iloc[0]["status"] == "stale"
    assert out.iloc[0]["reason"] is None


def test_reason_no_row_when_raw_agrees_with_scored():
    # Raw feed has NOTHING more recent than what's already showing as the
    # last valid (scored) actual -- a genuine gap, not a quarantine.
    scored = pd.DataFrame([_row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100))])
    raw = pd.DataFrame([_row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100))])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["USD"],
                                           raw_calendar_df=raw)
    row = out.iloc[0]
    assert row["status"] == "stale"
    assert row["reason"] == "NO_ROW"


def test_reason_quarantined_when_raw_has_newer_row_scored_does_not():
    # This is the AUD import_prices shape: the raw feed HAS a more recent row
    # with a real (non-null) actual -- 0.0, in the real bug -- that the
    # scored frame nulled. The scored frame's last valid actual is older.
    scored = pd.DataFrame([_row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100))])
    raw = pd.DataFrame([
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100)),
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=10), actual=0.0),  # quarantined in `scored`
    ])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["USD"],
                                           raw_calendar_df=raw)
    row = out.iloc[0]
    assert row["status"] == "stale"   # last VALID (scored) actual is still 100d old
    assert row["reason"] == "QUARANTINED"


def test_reason_no_row_for_true_no_data_with_empty_raw():
    cal = pd.DataFrame([_row("GBP", "cpi_yoy", AS_OF - pd.Timedelta(days=10))])
    raw = pd.DataFrame([_row("GBP", "cpi_yoy", AS_OF - pd.Timedelta(days=10))])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["GBP"],
                                           raw_calendar_df=raw)
    gdp_row = out[out["indicator_key"] == "gdp_qoq"].iloc[0]
    assert gdp_row["status"] == "no_data"
    assert gdp_row["reason"] == "NO_ROW"


def test_reason_quarantined_for_no_data_when_raw_has_a_null_actual_row():
    # Scored has NOTHING at all (empty sub -> no_data) but the raw feed DID
    # carry a row with a real actual for this indicator -- fully quarantined,
    # not merely absent.
    scored = pd.DataFrame([], columns=["currency", "indicator_key", "release_dt", "actual"])
    raw = pd.DataFrame([_row("GBP", "gdp_qoq", AS_OF - pd.Timedelta(days=10), actual=0.0)])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["GBP"],
                                           raw_calendar_df=raw)
    gdp_row = out[out["indicator_key"] == "gdp_qoq"].iloc[0]
    assert gdp_row["status"] == "no_data"
    assert gdp_row["reason"] == "QUARANTINED"


def test_real_actual_inside_window_stays_fresh_with_raw_calendar_df_too():
    scored = pd.DataFrame([_row("EUR", "cpi_yoy", AS_OF - pd.Timedelta(days=5), actual=2.1)])
    raw = pd.DataFrame([_row("EUR", "cpi_yoy", AS_OF - pd.Timedelta(days=5), actual=2.1)])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["EUR"],
                                           raw_calendar_df=raw)
    row = out.iloc[0]
    assert row["status"] == "fresh"
    assert row["severity"] is None
    assert row["reason"] is None


def test_scored_frame_input_reports_quarantined_as_stale():
    """CHARACTERIZATION test, not a regression test — it pins the library's
    CONTRACT given correct input, and nothing more. It passes on `main` too
    (verified empirically below), because `per_currency_indicator_freshness`
    was never the buggy code: given a properly-scored `calendar_df`, its
    `actual.notna()` row-filtering was always correct, unchanged by this fix.
    This test alone would NOT have caught the real bug and does NOT protect
    against a regression of it — a caller that goes back to handing this
    function a RAW frame sails right through this test, because the test
    controls its own input and always builds it correctly.

    That protection — the one that actually matters, and the one that WOULD
    fail if the bug came back — is `test_load_scored_calendar_quarantines_
    zero_actual` below: it calls the production loader itself
    (`scripts.check_calendar_freshness._load_scored_calendar`), which is the
    thing that used to build the wrong frame. See that test's docstring for
    the proof it locks the fix (revert-and-rerun output pasted there and in
    the PR description).

    What THIS test does verify: using ONLY names that exist on `main`
    (`per_currency_indicator_freshness`, `currency_freshness_report`, plus
    `src.ff_scoring.to_scoring_frame`/`build_matcher`, unmodified by this fix
    and present on `main` too) to build a realistically-quarantined fixture
    — AUD `import_prices`, one OLD real print (200d before as_of, already
    past its own 110d quarterly threshold on its own) and one RECENT print
    (5d before as_of) whose actual is 0.0, the exact shape `to_scoring_frame`
    quarantines to NaN for a non-`can_be_zero` indicator (confirmed:
    `import_prices` carries no `can_be_zero` in
    `data/economic_indicators.yaml`) — the function correctly reports AUD
    stale, using the OLD (surviving) print's age, not the quarantined one's.

    Confirmed empirically that this passes on `main`: ran this exact fixture
    unmodified through `main`'s own `per_currency_indicator_freshness`
    (4-arg call, no `raw_calendar_df`/`severity`/`reason` — all of that is
    additive) and it ALSO reports AUD stale there. Not a hole in the fix —
    proof positive that this function was never where the bug lived.
    """
    import yaml
    from src.ff_scoring import to_scoring_frame, build_matcher
    from src.econ_calendar_ff import CANON_COLUMNS

    as_of = pd.Timestamp("2026-08-04")
    old_date = as_of - pd.Timedelta(days=200)   # already stale on its own (>110d quarterly threshold)
    new_date = as_of - pd.Timedelta(days=5)     # in-window, but a quarantine-shaped 0.0

    def _canon_row(dt, actual):
        return {"canonical_id": "aud_import_prices_fixture", "currency": "AUD",
                "name_raw": "Import Prices q/q", "name_canonical": "Import Prices q/q",
                "datetime_utc": dt, "actual": actual, "forecast": 0.0 if actual == 0.0 else 0.3,
                "previous": 0.2, "released": True, "source": "ff"}

    raw = pd.DataFrame([_canon_row(old_date, 3.3), _canon_row(new_date, 0.0)],
                       columns=CANON_COLUMNS)

    # Real production matcher + quarantine (both exist on main, unmodified).
    scored = to_scoring_frame(raw, build_matcher())
    assert scored[scored["release_dt"] == new_date]["actual"].isna().all(), (
        "fixture setup check: the recent 0.0 print must actually be the "
        "kind of row to_scoring_frame quarantines, or this test proves nothing"
    )

    with open(Path(__file__).resolve().parents[1] / "data" / "economic_indicators.yaml") as f:
        real_ind_cfg = yaml.safe_load(f)

    out = per_currency_indicator_freshness(scored, real_ind_cfg, as_of, currencies=["AUD"])
    report = currency_freshness_report(out)
    assert "AUD" in report["stale_currencies"]
    row = out[out["indicator_key"] == "import_prices"].iloc[0]
    assert row["status"] == "stale"
    assert row["last_date"] == old_date   # the quarantined newer print is invisible to status, correctly


def test_load_scored_calendar_quarantines_zero_actual(monkeypatch):
    """THE lock for the fix, at the layer where the bug actually lived —
    `scripts.check_calendar_freshness._load_scored_calendar` itself, not the
    pure `per_currency_indicator_freshness` (see the characterization test
    above for why that one alone doesn't protect against this).

    Fixture: one row whose actual is a 0.0 that `to_scoring_frame` quarantines
    (`import_prices` has no `can_be_zero`, and this synthetic (currency,
    name_raw, date) key has no entry in the real `build_flagged_bad_lookup()`
    result — a missing key defaults to flagged/blocked, i.e. NOT widened, by
    that function's own documented contract — so the 0.0 is unconditionally
    nulled). `pd.read_parquet` is monkeypatched (module-global, auto-reverted
    by pytest) to return this fixture regardless of path — safe here because
    `build_flagged_bad_lookup` reads JSON (jb_raw/archive), never parquet.

    Asserts the LOADER returns that row with actual NaN — i.e. proves
    `_load_scored_calendar` genuinely routes through `to_scoring_frame` and
    doesn't just relabel the raw parquet.

    PROOF THIS LOCKS THE FIX (manual, not part of the automated suite — a
    scratch worktree edit, run once, output pasted here and in the PR body):
    reverted `_load_scored_calendar`'s body in a scratch worktree to skip
    `to_scoring_frame` entirely (the exact old-bug shape — raw parquet +
    indicator_key attached, no quarantine), reran this test:

        FAILED tests/test_calendar_freshness_guard.py::test_load_scored_calendar_quarantines_zero_actual
        AssertionError: loader must route through to_scoring_frame's quarantine — got actual=0.0, expected NaN
        assert False

    Failed on the VALUE assertion, not on import or collection — confirms
    this test would have caught the exact original bug, unlike the
    ImportError-based version this replaced.
    """
    import scripts.check_calendar_freshness as ccf
    from src.econ_calendar_ff import CANON_COLUMNS

    fixture = pd.DataFrame([{
        "canonical_id": "aud_import_prices_loader_fixture", "currency": "AUD",
        "name_raw": "Import Prices q/q", "name_canonical": "Import Prices q/q",
        "datetime_utc": pd.Timestamp("2026-07-30"), "actual": 0.0, "forecast": 0.0,
        "previous": 0.1, "released": True, "source": "ff",
    }], columns=CANON_COLUMNS)

    monkeypatch.setattr(ccf.pd, "read_parquet", lambda path: fixture)

    out = ccf._load_scored_calendar()
    row = out[(out["currency"] == "AUD") & (out["indicator_key"] == "import_prices")].iloc[0]
    assert pd.isna(row["actual"]), (
        f"loader must route through to_scoring_frame's quarantine — got "
        f"actual={row['actual']!r}, expected NaN"
    )


def test_anchor_five_scored_stale_pairs_at_as_of_2026_08_04():
    """Numeric anchor, docs/diag-aud-inflation-round1.md Q5 (as corrected by
    Task 2's age_raw severity fix): running the scored-aware guard against
    the real parquet at as_of=2026-08-04 must report EXACTLY these 5
    (currency, indicator_key) pairs stale — not the 3 the raw-frame guard
    used to report. If the real parquet has moved on (nightly `econ-refresh`
    CI) and this no longer holds, that is a genuine discrepancy to
    investigate and report, NOT something to adjust this pin to match.

    AUD `import_prices` severity changed from DEAD to STALE relative to the
    first version of this pin: its age_scored (187d) alone cleared the
    2-missed-periods bar, but its age_raw (5d — the most recent RAW row,
    2026-07-30, is genuinely fresh, just quarantined) does not. Grading
    severity on age_raw when reason == QUARANTINED (Task 2) fixes the
    mislabel. JPY capital_expenditure (also QUARANTINED, age_raw=65d) does
    not change — 65d was already under the DEAD bar either way.
    """
    import yaml
    from scripts.check_calendar_freshness import (
        _load_scored_calendar, _load_raw_calendar_with_indicator_key, INDICATORS_YAML,
    )

    as_of = pd.Timestamp("2026-08-04")
    with open(INDICATORS_YAML) as f:
        ind_cfg = yaml.safe_load(f)

    cal_scored = _load_scored_calendar()
    cal_raw = _load_raw_calendar_with_indicator_key(ind_cfg)
    out = per_currency_indicator_freshness(cal_scored, ind_cfg, as_of, raw_calendar_df=cal_raw)
    stale = out[out["status"] == "stale"].set_index(["currency", "indicator_key"])

    expected = {
        ("AUD", "core_cpi"):            {"age_scored": 279, "age_raw": 279, "threshold_days": 110, "severity": "DEAD",  "reason": "NO_ROW"},
        ("AUD", "import_prices"):       {"age_scored": 187, "age_raw": 5,   "threshold_days": 110, "severity": "STALE", "reason": "QUARANTINED"},
        ("GBP", "ppi_yoy"):             {"age_scored": 48,  "age_raw": 13,  "threshold_days": 45,  "severity": "STALE", "reason": "NO_ROW"},
        ("JPY", "capital_expenditure"): {"age_scored": 155, "age_raw": 65,  "threshold_days": 110, "severity": "STALE", "reason": "QUARANTINED"},
        ("USD", "core_cpi"):            {"age_scored": 55,  "age_raw": 21,  "threshold_days": 45,  "severity": "STALE", "reason": "NO_ROW"},
    }

    assert set(stale.index) == set(expected), (
        f"stale pairs changed since docs/diag-aud-inflation-round1.md Q5 — "
        f"got {sorted(stale.index)}, expected {sorted(expected)}. STOP and "
        f"report this discrepancy rather than editing the pin."
    )
    for key, exp in expected.items():
        for field, val in exp.items():
            actual = stale.loc[key, field]
            assert actual == val, f"{key} {field}: expected {val}, got {actual}"


# ---------------------------------------------------------------------------
# Task 2 — severity must not mislabel a live-but-quarantined series as DEAD.
# ---------------------------------------------------------------------------

def test_quarantined_pair_with_fresh_raw_row_is_not_mislabeled_dead():
    # The exact mislabel this fix targets: age_scored alone (200d, on an
    # indicator whose 2x-threshold/periods-missed bar it clears) would say
    # DEAD. But the raw feed has a much more recent row (10d old) sitting
    # quarantined -- the series is alive, just blocked. Must NOT be DEAD.
    scored = pd.DataFrame([_row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=200))])
    raw = pd.DataFrame([
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=200)),
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=10), actual=0.0),  # quarantined, fresh
    ])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["AUD"],
                                           raw_calendar_df=raw)
    row = out[out["indicator_key"] == "core_cpi"].iloc[0]
    assert row["status"] == "stale"          # scoring genuinely has no current number -- correct
    assert row["reason"] == "QUARANTINED"
    assert row["age_scored"] == 200
    assert row["age_raw"] == 10
    assert row["severity"] != "DEAD"         # THE mislabel this test guards against
    assert row["severity"] == "STALE"


def test_quarantined_pair_with_old_raw_row_too_is_still_dead():
    # Contrast case: reason is QUARANTINED (raw has a *slightly* newer row
    # than scored), but that raw row is ALSO old/skipped enough periods --
    # must still be DEAD. Confirms the fix grades on age_raw, not "any
    # QUARANTINED reason auto-downgrades to STALE".
    scored = pd.DataFrame([_row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=400))])
    raw = pd.DataFrame([
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=400)),
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=250), actual=0.0),  # still 2.5+ periods old
    ])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["AUD"],
                                           raw_calendar_df=raw)
    row = out[out["indicator_key"] == "core_cpi"].iloc[0]
    assert row["reason"] == "QUARANTINED"
    assert row["age_raw"] == 250
    assert row["severity"] == "DEAD"


def test_no_row_reason_severity_still_grades_on_age_scored():
    # reason == NO_ROW (not QUARANTINED) -> severity must grade on
    # age_scored even when age_raw is present AND smaller. Raw has a row
    # 20d ago, but its actual is NULL (a scheduled-but-unreleased event, not
    # a quarantined one) -- that's still NO_ROW (nothing VALID more recent
    # than scored), yet it DOES pull age_raw down to 20 (age_raw counts any
    # raw row, null actual included -- module docstring). If severity used
    # age_raw here it would read STALE (20d, well under a 45d monthly
    # threshold); grading on age_scored (300d) correctly reads DEAD. This is
    # the real GBP ppi_yoy / USD core_cpi shape from the anchor test above
    # (both NO_ROW with age_raw < age_scored) -- confirms severity isn't
    # naively "use age_raw whenever raw_calendar_df is supplied".
    scored = pd.DataFrame([_row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=300))])
    raw = pd.DataFrame([
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=300)),
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=20), actual=float("nan")),
    ])
    out = per_currency_indicator_freshness(scored, IND_CFG, AS_OF, currencies=["USD"],
                                           raw_calendar_df=raw)
    row = out.iloc[0]
    assert row["reason"] == "NO_ROW"
    assert row["age_scored"] == 300
    assert row["age_raw"] == 20
    assert row["severity"] == "DEAD"


def test_jb_raw_check_distinguishes_available_vs_absent():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.econ_calendar_ff import parse_jblanked_range
    from src.ff_scoring import build_matcher

    raw_dir = Path(__file__).resolve().parents[1] / "data" / "jb_raw"
    if not raw_dir.exists() or not list(raw_dir.glob("jb_range_*.json")):
        pytest.skip("no retained jb_raw payloads in this checkout")

    stale_rows = pd.DataFrame([
        {"currency": "USD", "indicator_key": "core_cpi"},
        {"currency": "USD", "indicator_key": "nonexistent_indicator_key"},
    ])
    parquet_last_dates = {
        ("USD", "core_cpi"): pd.Timestamp("2026-06-10"),
        ("USD", "nonexistent_indicator_key"): pd.Timestamp("2026-06-10"),
    }
    res = check_pending_actuals_in_jb_raw(raw_dir, parse_jblanked_range, build_matcher,
                                          stale_rows, parquet_last_dates)
    # a made-up indicator_key can never match anything -> always "no_newer_data"
    assert res[("USD", "nonexistent_indicator_key")]["status"] == "no_newer_data"
    assert res[("USD", "core_cpi")]["status"] in {"actual_available_not_ingested", "no_newer_data"}
