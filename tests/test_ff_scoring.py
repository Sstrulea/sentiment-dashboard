"""Phase 2 — FF scoring bridge + baseline PROVENANCE tests.

Guarantees no series ever mixes MT5 and FF prints: the FF scoring frame is built
exclusively from FF rows, so a rebuilt z-score baseline for any series (especially
the # xf transform-delta series) is 100% source='ff'.
"""
from __future__ import annotations

import pandas as pd

import numpy as np

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_scoring import detect_cadence, load_can_be_zero, to_scoring_frame

# (currency, name_canonical) for the # xf series in config/ff_aliases.yaml, with the
# indicator_key they must resolve to via the matcher.
XF_SERIES = [
    ("USD", "Core CPI y/y", "core_cpi"), ("USD", "PPI y/y", "ppi_yoy"),
    ("USD", "Core PCE Price Index y/y", "core_pce"),
    ("USD", "Average Hourly Earnings y/y", "wage_growth"),
    ("EUR", "PPI y/y", "ppi_yoy"), ("GBP", "PPI Output y/y", "ppi_yoy"),
    ("JPY", "Retail Sales m/m", "retail_sales"),
    ("NZD", "CPI y/y", "cpi_yoy"), ("CAD", "CPI y/y", "cpi_yoy"),
    ("CAD", "GDP q/q", "gdp_qoq"), ("CHF", "CPI y/y", "cpi_yoy"),
]


def _ff_row(ccy, canon, actual, dt):
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": "raw",
            "name_canonical": canon, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": actual - 0.1, "previous": actual - 0.2,
            "released": True, "source": "ff"}


def _ff_frame():
    rows = []
    for i, (ccy, canon, _key) in enumerate(XF_SERIES):
        for m in range(1, 5):
            rows.append(_ff_row(ccy, canon, 1.0 + 0.1 * m, f"2026-0{m}-1{i%9}"))
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


def test_scoring_frame_is_100pct_ff():
    frame = to_scoring_frame(_ff_frame())
    assert not frame.empty
    assert (frame["source"] == "ff").all()          # never any MT5 row
    assert set(frame["source"].unique()) == {"ff"}


def test_xf_series_resolve_and_are_ff_only():
    frame = to_scoring_frame(_ff_frame())
    for ccy, _canon, key in XF_SERIES:
        sub = frame[(frame["currency"] == ccy) & (frame["indicator_key"] == key)]
        assert len(sub) >= 1, f"{ccy}/{key} did not resolve"
        # baseline provenance: every print for this series is FF-sourced
        assert (sub["source"] == "ff").all()


def test_unmodeled_series_dropped_for_parity():
    # JPY PPI is not in the MT5 matcher (JPY has no PPI pattern) -> dropped in both
    # sources, preserving parity. Its rows never reach the scoring frame.
    ff = pd.DataFrame([_ff_row("JPY", "PPI y/y", 2.0, "2026-06-01")], columns=CANON_COLUMNS)
    frame = to_scoring_frame(ff)
    assert frame.empty


def test_detect_cadence_classifies_by_median_interval():
    assert detect_cadence(pd.date_range("2024-01-01", periods=12, freq="MS")) == "monthly"
    assert detect_cadence(pd.date_range("2024-01-01", periods=8, freq="QS")) == "quarterly"
    assert detect_cadence(pd.date_range("2024-01-01", periods=20, freq="W")) == "weekly"
    # quarterly AU/NZ CPI with 13 prints is ABOVE the quarterly threshold (8), not below —
    # the bug the cadence-aware reclassification fixes.
    from src.ff_scoring import CADENCE_THRESHOLD
    assert 13 >= CADENCE_THRESHOLD[detect_cadence(pd.date_range("2023-01-01", periods=13, freq="QS"))]


def test_mt5_and_ff_frames_are_disjoint_in_source():
    ff_frame = to_scoring_frame(_ff_frame())
    mt5_like = pd.DataFrame([{"currency": "USD", "indicator_key": "cpi_yoy",
                              "release_dt": pd.Timestamp("2026-06-10"), "actual": 4.2,
                              "consensus": 4.2, "previous": 4.0, "source": "mt5"}])
    assert set(ff_frame["source"].unique()).isdisjoint(set(mt5_like["source"].unique()))


# --- zero-placeholder quarantine gate ---------------------------------------

def _ff_row(ccy, name_canonical, actual, forecast, dt="2026-06-01"):
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": "raw",
            "name_canonical": name_canonical, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": 1.0,
            "released": True, "source": "ff"}


def test_config_can_be_zero_set():
    cbz = load_can_be_zero()
    assert "employment_change" in cbz and "retail_sales" in cbz  # net change / m/m growth
    assert "unemployment_rate" not in cbz and "manufacturing_pmi" not in cbz  # levels/indices
    assert "cpi_yoy" not in cbz


def test_quarantine_zero_actual_and_consensus():
    # unemployment_rate (can_be_zero=False): actual==0.0 AND consensus==0.0 → NaN
    ff = pd.DataFrame([
        _ff_row("USD", "Unemployment Rate", 0.0, 4.3),      # placeholder actual
        _ff_row("USD", "Unemployment Rate", 4.2, 0.0),      # placeholder consensus
        _ff_row("USD", "Unemployment Rate", 4.2, 4.3),      # clean
    ], columns=CANON_COLUMNS)
    out = to_scoring_frame(ff)
    unemp = out[out["indicator_key"] == "unemployment_rate"].reset_index(drop=True)
    assert np.isnan(unemp.loc[0, "actual"])                 # 0.0 actual quarantined
    assert np.isnan(unemp.loc[1, "consensus"])              # 0.0 consensus quarantined
    assert unemp.loc[2, "actual"] == 4.2                    # clean row untouched


def test_can_be_zero_true_keeps_zero():
    # employment_change (can_be_zero=True): a real 0 net-jobs print is KEPT
    ff = pd.DataFrame([_ff_row("USD", "Nonfarm Payrolls", 0.0, 50.0)], columns=CANON_COLUMNS)
    out = to_scoring_frame(ff)
    emp = out[out["indicator_key"] == "employment_change"].iloc[0]
    assert emp["actual"] == 0.0                             # NOT quarantined


def test_can_be_zero_false_ghost_zero_already_nan_before_dedup():
    # docs/dedup-latest-wins-bug.md: the _keep_latest_published fix (nonzero
    # beats zero within a dedup cluster) only matters for can_be_zero: true
    # indicators. For unemployment_rate (can_be_zero=False), a 0.0 published
    # AFTER the real value -- the exact ordering that broke JPY retail_sales
    # -- must already be NaN by the time _dedup_flash_final runs, so the fix
    # never has anything to do here: the NaN is excluded by the `published`
    # filter regardless of which _keep_latest_published version is in use.
    from src.economic_compute import _dedup_flash_final
    ff = pd.DataFrame([
        _ff_row("USD", "Unemployment Rate", 4.2, 4.3, dt="2026-06-01 12:00"),  # real, first
        _ff_row("USD", "Unemployment Rate", 0.0, 4.3, dt="2026-06-01 13:00"),  # ghost, later
    ], columns=CANON_COLUMNS)
    out = to_scoring_frame(ff)
    unemp = out[out["indicator_key"] == "unemployment_rate"].sort_values("release_dt")
    # the later, ghost row is already NaN here -- before dedup ever runs
    assert unemp["actual"].iloc[0] == 4.2
    assert np.isnan(unemp["actual"].iloc[1])

    dd = _dedup_flash_final(unemp.reset_index(drop=True), 18)
    assert len(dd) == 1
    assert dd["actual"].iloc[0] == 4.2                      # real value kept, untouched by the fix


def test_historical_placeholder_excluded_from_baseline():
    # a 0.0 placeholder among real prints must not enter the (actual,consensus) pairs
    rows = [_ff_row("USD", "CPI y/y", v, c, dt=f"2026-0{m}-01")
            for m, (v, c) in enumerate([(3.0, 3.0), (0.0, 3.0), (3.1, 3.0)], start=1)]
    out = to_scoring_frame(pd.DataFrame(rows, columns=CANON_COLUMNS))
    cpi = out[out["indicator_key"] == "cpi_yoy"]
    both = cpi[cpi["actual"].notna() & cpi["consensus"].notna()]
    assert len(both) == 2                                   # the 0.0 placeholder dropped
    assert 0.0 not in set(both["actual"])
