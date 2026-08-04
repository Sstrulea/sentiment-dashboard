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
    assert pd.isna(gdp_row["age_days"])
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
    # -- this is the real AUD import_prices shape (docs/diag-aud-inflation-
    # round1.md Q5) and must classify as DEAD via the periods-missed clause.
    cal = pd.DataFrame([_row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=187))])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["AUD"])
    row = out[out["indicator_key"] == "core_cpi"].iloc[0]
    assert row["status"] == "stale"
    assert row["age_days"] == 187
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


def test_regression_quarantine_blind_spot_must_be_stale_not_fresh():
    """THE regression this whole fix exists for. On `main`, before this fix,
    `scripts/check_calendar_freshness.py` builds its calendar_df from the RAW
    parquet (`_load_calendar_with_indicator_key`) — actual==0.0 rows the
    scoring engine quarantines to NaN still read as present/fresh actuals to
    the guard. `docs/diag-aud-inflation-round1.md` Q5 measured this LIVE:
    raw-frame run reports 3 stale currencies; the scored-frame run (what the
    dashboard actually aggregates) reports 5 stale pairs across 4 currencies
    — AUD `import_prices` (last SCORED actual 2026-01-29, 187d old) is one of
    the two the raw-frame run silently missed (JPY `capital_expenditure` is
    the other, see the anchor test below).

    `scripts.check_calendar_freshness._load_scored_calendar` does not exist
    on `main` at all (ImportError) — this test fails outright there, which is
    the point: the fix isn't a tweak to the pure per_currency_indicator_
    freshness math (that was always correct given scored input), it's that
    NOTHING in the shipped pipeline ever called it with scored input.
    """
    import yaml
    from scripts.check_calendar_freshness import _load_scored_calendar, INDICATORS_YAML

    as_of = pd.Timestamp("2026-08-04")
    with open(INDICATORS_YAML) as f:
        ind_cfg = yaml.safe_load(f)

    cal_scored = _load_scored_calendar()
    out = per_currency_indicator_freshness(cal_scored, ind_cfg, as_of, currencies=["AUD"])
    row = out[out["indicator_key"] == "import_prices"].iloc[0]
    assert row["status"] == "stale", (
        f"AUD import_prices must be STALE once fed the SCORED frame — got "
        f"{row['status']!r} (last_date={row['last_date']}, age_days={row['age_days']})"
    )
    assert row["last_date"] == pd.Timestamp("2026-01-29 00:30:00")
    assert row["age_days"] == 187


def test_anchor_five_scored_stale_pairs_at_as_of_2026_08_04():
    """Numeric anchor, docs/diag-aud-inflation-round1.md Q5: running the
    (now scored-aware) guard against the real parquet at as_of=2026-08-04
    must report EXACTLY these 5 (currency, indicator_key) pairs stale — not
    the 3 the raw-frame guard used to report. If the real parquet has moved
    on (nightly `econ-refresh` CI) and this no longer holds, that is a
    genuine discrepancy to investigate and report, NOT something to
    adjust this pin to match.
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
        ("AUD", "core_cpi"):            {"age_days": 279, "threshold_days": 110, "severity": "DEAD",  "reason": "NO_ROW"},
        ("AUD", "import_prices"):       {"age_days": 187, "threshold_days": 110, "severity": "DEAD",  "reason": "QUARANTINED"},
        ("GBP", "ppi_yoy"):             {"age_days": 48,  "threshold_days": 45,  "severity": "STALE", "reason": "NO_ROW"},
        ("JPY", "capital_expenditure"): {"age_days": 155, "threshold_days": 110, "severity": "STALE", "reason": "QUARANTINED"},
        ("USD", "core_cpi"):            {"age_days": 55,  "threshold_days": 45,  "severity": "STALE", "reason": "NO_ROW"},
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
