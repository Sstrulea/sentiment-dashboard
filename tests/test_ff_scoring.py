"""Phase 2 — FF scoring bridge + baseline PROVENANCE tests.

Guarantees no series ever mixes MT5 and FF prints: the FF scoring frame is built
exclusively from FF rows, so a rebuilt z-score baseline for any series (especially
the # xf transform-delta series) is 100% source='ff'.
"""
from __future__ import annotations

import pandas as pd

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_scoring import detect_cadence, to_scoring_frame

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
