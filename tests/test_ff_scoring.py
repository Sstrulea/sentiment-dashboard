"""Phase 2 — FF scoring bridge + baseline PROVENANCE tests.

Guarantees no series ever mixes MT5 and FF prints: the FF scoring frame is built
exclusively from FF rows, so a rebuilt z-score baseline for any series (especially
the # xf transform-delta series) is 100% source='ff'.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

import numpy as np

from src.econ_calendar_ff import CANON_COLUMNS, extract_period_suffix, parse_jblanked_range
from src.economic_fetch import CompiledMatcher
from src.ff_scoring import CCY2COUNTRY, build_matcher, detect_cadence, load_can_be_zero, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]

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
    # GBP "PPI Input m/m" has no matcher rule under any indicator_key (it
    # measures producer input costs, a different concept from the output-
    # price ppi_yoy slot GBP's "PPI Output m/m" already feeds — deliberately
    # left unrouted, docs/empty-slot-routing-audit.md Part B) -> dropped in
    # both sources, preserving parity. Its rows never reach the scoring
    # frame. (Was JPY "PPI y/y" until feat/board-slot-cleanup-and-cad-
    # promotion added Japan's missing ppi_yoy matcher rule — that series is
    # no longer unmodeled, so this pins a still-genuinely-unrouted one.)
    ff = pd.DataFrame([_ff_row("GBP", "PPI Input m/m", 2.0, "2026-06-01")], columns=CANON_COLUMNS)
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

def _zrow(ccy, name_canonical, actual, forecast, dt="2026-06-01"):
    # was a second `_ff_row` that silently shadowed the helper above (so
    # _ff_frame put the date string in `forecast`); renamed (audit 2026-09-23).
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": "raw",
            "name_canonical": name_canonical, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": 1.0,
            "released": True, "source": "ff"}


def test_config_can_be_zero_set():
    # fix/can-be-zero-transform (2026-08): load_can_be_zero() is now only ONE
    # of two orthogonal reasons to_scoring_frame may keep a 0.0 actual — the
    # OTHER is the row's real transform (extract_period_suffix on name_raw)
    # being m/m or q/q, gated by a Bad-Data guard (see test_can_be_zero_
    # transform_* below). This SET's contract is unchanged: it is still the
    # CONFIG-only membership, so "cpi_yoy" stays absent here for every
    # currency even though CHF/CAD/... cpi_yoy zeros are now recoverable via
    # the OTHER path — this assertion is about the config flag, not the full
    # can_be_zero decision.
    cbz = load_can_be_zero()
    assert "employment_change" in cbz and "retail_sales" in cbz  # net change / m/m growth
    assert "unemployment_rate" not in cbz and "manufacturing_pmi" not in cbz  # levels/indices
    assert "cpi_yoy" not in cbz


# --- fix/can-be-zero-transform (2026-08): suffix parser + union rule --------

def test_extract_period_suffix_known_and_none():
    assert extract_period_suffix("CPI m/m") == "m/m"
    assert extract_period_suffix("GDP q/q") == "q/q"
    assert extract_period_suffix("Core CPI y/y") == "y/y"
    assert extract_period_suffix("Average Earnings Index 3m/y") == "3m/y"
    assert extract_period_suffix("Employment Change") == "none"     # net change, no suffix
    assert extract_period_suffix("Federal Funds Rate") == "none"    # rate decision, no suffix
    assert extract_period_suffix("ISM Manufacturing PMI") == "none"  # NOT confused with "MoM"-shaped


def test_extract_period_suffix_raises_on_unrecognized():
    # a future feed name using an unmodeled slash convention must crash the
    # can_be_zero decision that depends on it, not silently fall back to
    # "none" (which would silently re-quarantine a genuinely-transformed
    # series with no visible signal that the vocabulary drifted).
    with pytest.raises(ValueError, match="unrecognized period suffix"):
        extract_period_suffix("Retail Sales 6m/y")


# --- new-duplicate guard (fix/can-be-zero-transform, 2026-08 audit, FAZA 1c) -

NEW_DUPLICATE_GUARD_FIXTURE = ROOT / "tests" / "fixtures" / "jb_new_duplicate_guard_2026-08.json"


def test_new_duplicate_guard_identical_collapses_divergent_excludes_both():
    # real archive rows (NOT synthetic): CHF cpi_yoy 2026-07-02 -- the ±1h dup,
    # BOTH copies widened to actual=0.0 (IDENTICAL) -- must collapse to ONE
    # valid row, not two. CHF ppi_yoy 2025-04-14 -- one copy already valid
    # (0.1, never touched by can_be_zero at all) plus one newly-widened 0.0
    # (DIVERGENT) -- no tiebreak, so BOTH must be excluded, including the
    # originally-valid 0.1 (keeping it would be guessing which copy is real).
    parsed = parse_jblanked_range(str(NEW_DUPLICATE_GUARD_FIXTURE), now_utc=pd.Timestamp("2026-08-02"))
    out = to_scoring_frame(parsed, build_matcher())

    chf_cpi = out[(out["currency"] == "CHF") & (out["indicator_key"] == "cpi_yoy")]
    assert len(chf_cpi) == 2                              # both rows still present in the frame
    assert chf_cpi["actual"].notna().sum() == 1            # but only ONE counts as valid
    assert (chf_cpi.loc[chf_cpi["actual"].notna(), "actual"] == 0.0).all()

    chf_ppi = out[(out["currency"] == "CHF") & (out["indicator_key"] == "ppi_yoy")]
    assert len(chf_ppi) == 2
    assert chf_ppi["actual"].notna().sum() == 0             # BOTH excluded, including the 0.1


def test_new_duplicate_guard_does_not_touch_pre_existing_duplicates():
    # a group already >=2 valid BEFORE widening (no can_be_zero involved at
    # all -- two ordinary non-zero prints on the same day) must be completely
    # untouched by this guard, regardless of flagged_bad being provided.
    rows = [
        _zrow("USD", "Unemployment Rate", 4.2, 4.3, dt="2026-06-01 12:00"),
        _zrow("USD", "Unemployment Rate", 4.3, 4.3, dt="2026-06-01 13:00"),
    ]
    ff = pd.DataFrame(rows, columns=CANON_COLUMNS)
    ff["canonical_id"] = "usd_unemployment_rate"
    out = to_scoring_frame(ff, build_matcher())
    unemp = out[out["indicator_key"] == "unemployment_rate"]
    assert unemp["actual"].notna().sum() == 2               # both kept, untouched


# --- audit 2026-09-23 phase 2: provenance recorded at ingest -----------------------
# 2.2 consensus = effective_consensus(forecast, forecast_origin)
# 2.3 actual 0.0 is a placeholder iff jb_status == "Data Not Loaded"

from src.ff_scoring import actual_is_placeholder, effective_consensus  # noqa: E402


def _prow(forecast, origin, actual=1.0, jb_status=None, name="Trade Balance",
          ccy="CAD", canon="Trade Balance", dt="2026-06-01 12:30"):
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": name,
            "name_canonical": canon, "datetime_utc": pd.Timestamp(dt), "actual": actual,
            "forecast": forecast, "previous": 1.0, "released": True, "source": "ff",
            "forecast_origin": origin, "jb_status": jb_status}


@pytest.mark.parametrize("forecast,origin,expected", [
    (0.0, "ff", 0.0),            # FF "0.0%": a real consensus, kept
    (0.3, "ff", 0.3),
    (np.nan, "ff_blank", np.nan),  # FF "": no consensus
    (0.0, "jb", np.nan),         # JB 0.0: its no-forecast placeholder
    (0.3, "jb", 0.3),            # JB value (pre-archive history): kept
    (0.0, None, np.nan),         # unknown provenance = not FF-confirmed
    (0.0, "manual", 0.0),
])
def test_effective_consensus(forecast, origin, expected):
    got = effective_consensus(forecast, origin)
    assert (np.isnan(got) and np.isnan(expected)) or got == expected


@pytest.mark.parametrize("actual,status,placeholder", [
    (0.0, "Data Not Loaded", True),
    (0.0, "Good Data", False),   # Good/Bad Data is a direction tag, ignored (C1)
    (0.0, "Bad Data", False),
    (0.0, None, False),
    (0.4, "Data Not Loaded", False),
    (np.nan, "Data Not Loaded", False),
])
def test_actual_is_placeholder(actual, status, placeholder):
    assert actual_is_placeholder(actual, status) is placeholder


def _cpi(forecast, origin, actual=0.4, jb_status="Bad Data", dt="2026-06-01"):
    return _prow(forecast, origin, actual, jb_status, name="CPI m/m", ccy="CHF", canon="CPI y/y", dt=dt)


def test_scoring_frame_applies_the_consensus_rule():
    ff = pd.DataFrame([_cpi(0.0, "ff", dt="2026-09-03"), _cpi(np.nan, "ff_blank", dt="2026-08-03"),
                       _cpi(0.0, "jb", dt="2026-07-03")], columns=CANON_COLUMNS)
    ff["datetime_utc"] = pd.to_datetime(["2026-09-03", "2026-08-03", "2026-07-03"])
    out = to_scoring_frame(ff, build_matcher()).sort_values("release_dt")
    assert out["consensus"].tolist()[2] == 0.0            # CHF CPI 09-03: FF "0.0%" scored
    assert out["consensus"].iloc[:2].isna().all()           # ff_blank + jb 0.0


def test_scoring_frame_applies_the_placeholder_rule_only():
    rows = [_cpi(0.1, "ff", actual=0.0, jb_status="Data Not Loaded"),
            _cpi(0.1, "ff", actual=0.0, jb_status="Bad Data"),
            _cpi(0.1, "ff", actual=0.0, jb_status=None)]
    ff = pd.DataFrame(rows, columns=CANON_COLUMNS)
    ff["datetime_utc"] = pd.to_datetime(["2026-07-03", "2026-08-03", "2026-09-03"])
    ff["canonical_id"] = ["a", "b", "c"]
    out = to_scoring_frame(ff, build_matcher()).sort_values("release_dt")
    assert out["actual"].isna().tolist() == [True, False, False]


def test_scoring_frame_without_provenance_columns_is_conservative():
    """A frame from before 2.1 (no columns): consensus 0.0 is not FF-confirmed."""
    row = {k: v for k, v in _cpi(0.0, "ff").items() if k not in ("forecast_origin", "jb_status")}
    ff = pd.DataFrame([row])
    out = to_scoring_frame(ff, build_matcher())
    assert out["consensus"].isna().all() and out["actual"].tolist() == [0.4]
