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
from src.jb_actuals import build_flagged_bad_lookup

ROOT = Path(__file__).resolve().parents[1]
CAN_BE_ZERO_TRANSFORM_FIXTURE = ROOT / "tests" / "fixtures" / "jb_can_be_zero_transform_2026-08.json"

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

def _ff_row(ccy, name_canonical, actual, forecast, dt="2026-06-01"):
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


def test_quarantine_zero_actual_and_consensus():
    # unemployment_rate (can_be_zero=False): actual==0.0 AND consensus==0.0 → NaN
    #
    # fix/can-be-zero-transform (2026-08): this test calls to_scoring_frame()
    # WITHOUT the new `flagged_bad` parameter, which is deliberate, not an
    # oversight — flagged_bad=None disables the suffix-widening path entirely,
    # so this test still exercises and codifies EXACTLY the pre-fix contract
    # (config-only quarantine). "Unemployment Rate" has no period-transform
    # suffix anyway (extract_period_suffix -> "none"), so it would be
    # unaffected by the new rule regardless — see test_can_be_zero_transform_*
    # below for the NEW contract (suffix + Bad-Data guard), which needs a real
    # m/m-fed row and an explicit flagged_bad lookup to exercise.
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


def _load_can_be_zero_transform_fixture():
    """Real capture (frozen 2026-08 from data/archive/ + data/jb_raw/, NOT
    synthetic): CHF 'CPI m/m' 2026-07-02 (Good Data, the classic ±1h dup
    zero-print case investigated in docs/faza1-chf-cpi-zero-placeholder-
    inventory.md), GBP 'CPI y/y' 2024-01-15 (a real y/y zero print), USD
    'Core CPI m/m' 2026-07-14 (Bad Data — the contra-example that motivates
    the flagged_bad guard, per config/alert_exceptions.yaml's exception note
    for USD core_cpi)."""
    parsed = parse_jblanked_range(str(CAN_BE_ZERO_TRANSFORM_FIXTURE), now_utc=pd.Timestamp("2026-08-02"))
    lookup = build_flagged_bad_lookup(raw_dir=None, archive_path=CAN_BE_ZERO_TRANSFORM_FIXTURE)
    return parsed, lookup


def test_can_be_zero_transform_default_disabled_matches_old_contract():
    # flagged_bad=None (the default, unchanged call signature) must reproduce
    # EXACTLY today's config-only quarantine — all three real zeros nulled,
    # regardless of their real transform or Quality/Strength.
    parsed, _lookup = _load_can_be_zero_transform_fixture()
    out = to_scoring_frame(parsed, build_matcher())
    assert out["actual"].notna().sum() == 0
    assert len(out) == 4                                     # all 4 rows still resolve/score


def test_can_be_zero_transform_chf_recovered_gbp_still_quarantined():
    # same indicator_key ("cpi_yoy") on BOTH sides — the whole point of the
    # fix is that the verdict now depends on the row's REAL transform, not a
    # single global config flag: CHF is fed by "CPI m/m" (real, clean, Good
    # Data) -> recovered; GBP is fed by "CPI y/y" (genuinely y/y, and this
    # specific real print happens to ALSO be Bad Data) -> suffix alone
    # already excludes it, before flagged_bad is even consulted.
    #
    # FAZA 1c (2026-08 audit): CHF's ±1h dup is BOTH widened to 0.0 (identical)
    # — the new-duplicate guard collapses that to exactly ONE valid row, not
    # two (see test_new_duplicate_guard_*). This test no longer asserts BOTH
    # rows are 0.0 — that assertion codified the double-counting bug the guard
    # exists to fix. It now asserts recovery happened (>=1 valid, non-NaN,
    # ==0.0) without asserting how many copies survive — that contract is
    # test_new_duplicate_guard_identical_collapses_divergent_excludes_both's job.
    parsed, lookup = _load_can_be_zero_transform_fixture()
    out = to_scoring_frame(parsed, build_matcher(), flagged_bad=lookup)

    chf = out[(out["currency"] == "CHF") & (out["indicator_key"] == "cpi_yoy")]
    assert len(chf) == 2                                      # the ±1h dup, both rows present
    valid = chf["actual"].dropna()
    assert len(valid) == 1                                     # collapsed, not double-counted
    assert (valid == 0.0).all()                                # recovered, not NaN

    gbp = out[(out["currency"] == "GBP") & (out["indicator_key"] == "cpi_yoy")]
    assert len(gbp) == 1
    assert gbp["actual"].isna().all()                          # still quarantined


def test_can_be_zero_transform_bad_data_guard_rejects_despite_suffix_match():
    # USD core_cpi, 2026-07-14, real fixture (NOT synthetic): "Core CPI m/m"
    # actual=0.0, suffix=m/m (would qualify for widening on transform alone),
    # but Quality=='Bad Data' -> the flagged_bad guard rejects it. Without the
    # guard (config-only OR suffix, no AND NOT flagged_bad) this print would
    # be silently recovered as if real.
    parsed, lookup = _load_can_be_zero_transform_fixture()
    assert lookup[("USD", "Core CPI m/m", pd.Timestamp("2026-07-14").date())] is True

    out = to_scoring_frame(parsed, build_matcher(), flagged_bad=lookup)
    usd = out[(out["currency"] == "USD") & (out["indicator_key"] == "core_cpi")]
    assert len(usd) == 1
    assert usd["actual"].isna().all()                          # rejected by the Bad-Data guard


# --- pinned snapshot: derived can_be_zero verdict, every (currency, indicator_key) ---
# Replaces the visibility a materialized config used to give: any future alias
# remap, new indicator, or transform drift changes a row here, and shows up as
# an explicit diff in review instead of a silent behavior change. Regenerate by
# rerunning the derivation (config[key] OR extract_period_suffix(name_raw) in
# {"m/m","q/q"}) over data/economic_calendar_ff.parquet — see
# scripts/measure/transform_uniformity_and_zero_effect.py's V3 for the
# reusable form — and re-pin deliberately, not to make a failing test pass.
PINNED_DERIVED_CAN_BE_ZERO = {
    ("AUD", "capital_expenditure"): True, ("AUD", "company_operating_profits_qoq"): True,
    ("AUD", "core_cpi"): True, ("AUD", "cpi_monthly"): True, ("AUD", "cpi_yoy"): False,
    ("AUD", "employment_change"): True, ("AUD", "gdp_qoq"): True, ("AUD", "import_prices"): True,
    ("AUD", "interest_rate_decision"): True, ("AUD", "manufacturing_pmi"): False,
    ("AUD", "ppi_yoy"): True, ("AUD", "retail_sales"): True, ("AUD", "services_pmi"): False,
    ("AUD", "trimmed_mean_cpi_monthly"): True, ("AUD", "unemployment_rate"): False,
    ("AUD", "wage_growth"): True,
    ("CAD", "common_cpi_yoy"): False, ("CAD", "core_cpi"): False, ("CAD", "cpi_yoy"): True,
    ("CAD", "employment_change"): True, ("CAD", "gdp_qoq"): True,
    ("CAD", "interest_rate_decision"): True, ("CAD", "manufacturing_pmi"): False,
    ("CAD", "ppi_yoy"): True, ("CAD", "retail_sales"): True, ("CAD", "trimmed_cpi_yoy"): False,
    ("CAD", "unemployment_rate"): False,
    ("CHF", "cpi_yoy"): True, ("CHF", "gdp_qoq"): True, ("CHF", "interest_rate_decision"): True,
    ("CHF", "manufacturing_pmi"): False, ("CHF", "ppi_yoy"): True, ("CHF", "retail_sales"): True,
    ("CHF", "unemployment_rate"): False,
    ("EUR", "core_cpi"): False, ("EUR", "cpi_yoy"): False, ("EUR", "employment_change"): True,
    ("EUR", "gdp_qoq"): True, ("EUR", "interest_rate_decision"): True,
    ("EUR", "manufacturing_pmi"): False, ("EUR", "ppi_yoy"): True, ("EUR", "retail_sales"): True,
    ("EUR", "services_pmi"): False, ("EUR", "unemployment_rate"): False,
    ("GBP", "core_cpi"): False, ("GBP", "cpi_yoy"): False, ("GBP", "employment_change"): True,
    ("GBP", "gdp_qoq"): True, ("GBP", "industrial_production_mm"): True,
    ("GBP", "interest_rate_decision"): True, ("GBP", "manufacturing_pmi"): False,
    ("GBP", "ppi_yoy"): True, ("GBP", "retail_sales"): True, ("GBP", "services_pmi"): False,
    ("GBP", "unemployment_rate"): False, ("GBP", "wage_growth"): False,
    ("JPY", "capital_expenditure"): False, ("JPY", "core_cpi"): False,
    ("JPY", "core_machinery_orders_mm"): True, ("JPY", "cpi_yoy"): False,
    ("JPY", "gdp_price_index"): False, ("JPY", "gdp_qoq"): True,
    ("JPY", "industrial_production_mm"): True, ("JPY", "interest_rate_decision"): True,
    ("JPY", "manufacturing_pmi"): False,
    # ("JPY", "ppi_yoy") added feat/board-slot-cleanup-and-cad-promotion:
    # Japan's matcher gained a ppi_yoy rule (was unmodeled, dropped before
    # this pin existed). Raw name "PPI y/y" is already y/y (suffix "y/y",
    # not in {"m/m","q/q"}) and ppi_yoy carries no explicit can_be_zero ->
    # False, same derivation every other row here uses.
    ("JPY", "ppi_yoy"): False,
    ("JPY", "retail_sales"): True, ("JPY", "sppi_yoy"): False,
    ("JPY", "tokyo_core_cpi_yoy"): False, ("JPY", "unemployment_rate"): False,
    ("JPY", "wage_growth"): False,
    ("NZD", "cpi_yoy"): True, ("NZD", "employment_change"): True, ("NZD", "gdp_qoq"): True,
    ("NZD", "interest_rate_decision"): True, ("NZD", "manufacturing_pmi"): False,
    ("NZD", "ppi_yoy"): True, ("NZD", "retail_sales"): True, ("NZD", "services_pmi"): False,
    ("NZD", "unemployment_rate"): False, ("NZD", "wage_growth"): True,
    ("USD", "adp"): False, ("USD", "core_cpi"): True, ("USD", "core_pce"): True,
    ("USD", "cpi_yoy"): False, ("USD", "durable_goods_orders_mm"): True,
    ("USD", "employment_change"): True, ("USD", "gdp_price_index"): True, ("USD", "gdp_qoq"): True,
    ("USD", "import_prices"): True, ("USD", "industrial_production_mm"): True,
    ("USD", "interest_rate_decision"): True, ("USD", "jobless_claims"): False,
    ("USD", "jolts"): False, ("USD", "manufacturing_pmi"): False,
    ("USD", "personal_income_mm"): True, ("USD", "personal_spending_mm"): True,
    ("USD", "ppi_yoy"): True, ("USD", "retail_sales"): True, ("USD", "services_pmi"): False,
    ("USD", "unemployment_rate"): False, ("USD", "unit_labor_costs_qoq"): True,
    ("USD", "wage_growth"): True,
}


def test_can_be_zero_transform_derived_verdict_pinned_snapshot():
    ind_cfg = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}))
    cbz = load_can_be_zero(ind_cfg)

    df = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    df["indicator_key"] = [matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
                           for r in df.itertuples(index=False)]
    scored = df[df["indicator_key"].notna()].copy()
    scored["suffix"] = scored["name_raw"].apply(extract_period_suffix)

    actual: dict[tuple[str, str], bool] = {}
    for (ccy, key), g in scored.groupby(["currency", "indicator_key"], sort=True):
        suffixes = set(g["suffix"])
        assert len(suffixes) == 1, f"MIXED suffix for {ccy}/{key}: {suffixes} — H-xf broke, stop"
        suffix = suffixes.pop()
        actual[(ccy, key)] = (key in cbz) or (suffix in ("m/m", "q/q"))

    missing = set(PINNED_DERIVED_CAN_BE_ZERO) - set(actual)     # pinned pair no longer scored
    added = set(actual) - set(PINNED_DERIVED_CAN_BE_ZERO)        # new pair, not yet pinned
    changed = {k for k in set(actual) & set(PINNED_DERIVED_CAN_BE_ZERO)
              if actual[k] != PINNED_DERIVED_CAN_BE_ZERO[k]}
    assert not missing and not added and not changed, (
        f"derived can_be_zero verdict drifted from the pinned snapshot — "
        f"missing={missing} added={added} changed={ {k: (PINNED_DERIVED_CAN_BE_ZERO[k], actual[k]) for k in changed} }"
    )


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
    lookup = build_flagged_bad_lookup(raw_dir=None, archive_path=NEW_DUPLICATE_GUARD_FIXTURE)
    out = to_scoring_frame(parsed, build_matcher(), flagged_bad=lookup)

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
        _ff_row("USD", "Unemployment Rate", 4.2, 4.3, dt="2026-06-01 12:00"),
        _ff_row("USD", "Unemployment Rate", 4.3, 4.3, dt="2026-06-01 13:00"),
    ]
    ff = pd.DataFrame(rows, columns=CANON_COLUMNS)
    ff["canonical_id"] = "usd_unemployment_rate"
    out = to_scoring_frame(ff, build_matcher(), flagged_bad={})
    unemp = out[out["indicator_key"] == "unemployment_rate"]
    assert unemp["actual"].notna().sum() == 2               # both kept, untouched


# --- fix/cbz-flagged-bad-guard (2026-08): flagged_bad as a UNIVERSAL gate ----
# Closes the hole where `if key not in cbz:` gated the ENTIRE zero-quarantine
# block, so a can_be_zero indicator's 0.0 never reached the flagged_bad check
# at all -- a JBlanked 'Data Not Loaded'/'Bad Data' placeholder on
# employment_change/household_spending/interest_rate_decision/retail_sales
# sailed through unconditionally. Real case: AUD Cash Rate 2026-08-11, raw
# Quality=Strength='Data Not Loaded', would have shown 0.00% instead of 4.35%.

def _gate_row(ccy, name_canonical, name_raw, actual, dt="2026-06-01 12:00", canonical_id=None):
    return {"canonical_id": canonical_id or f"{ccy.lower()}_gate_x", "currency": ccy,
            "name_raw": name_raw, "name_canonical": name_canonical,
            "datetime_utc": pd.Timestamp(dt), "actual": actual, "forecast": 1.0,
            "previous": 1.0, "released": True, "source": "ff"}


def _frame(rows):
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


def test_gate_cbz_zero_kept_when_flagged_bad_clean():
    # (1) cbz + flagged_bad curat -> 0.0 PĂSTRAT.
    ff = _frame([_gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.0)])
    lookup = {("USD", "Federal Funds Rate", pd.Timestamp("2026-06-01").date()): False}  # not bad
    out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
    row = out[out["indicator_key"] == "interest_rate_decision"].iloc[0]
    assert row["actual"] == 0.0


def test_gate_cbz_zero_nulled_when_flagged_bad_true():
    # (2) cbz + flagged_bad=True ('Bad Data') -> 0.0 NULIFICAT.
    ff = _frame([_gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.0)])
    lookup = {("USD", "Federal Funds Rate", pd.Timestamp("2026-06-01").date()): True}   # bad
    out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
    row = out[out["indicator_key"] == "interest_rate_decision"].iloc[0]
    assert np.isnan(row["actual"])


def test_gate_cbz_zero_nulled_when_flagged_bad_key_absent():
    # (3) cbz + cheie ABSENTĂ din flagged_bad -> NULIFICAT (default blocat,
    # niciodată presupus curat).
    ff = _frame([_gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.0)])
    out = to_scoring_frame(ff, build_matcher(), flagged_bad={})   # key never seen
    row = out[out["indicator_key"] == "interest_rate_decision"].iloc[0]
    assert np.isnan(row["actual"])


def test_gate_cbz_zero_kept_when_flagged_bad_none():
    # (4) cbz + flagged_bad=None -> 0.0 PĂSTRAT (comportament vechi intact —
    # callerii care nu pasează flagged_bad nu văd nicio diferență).
    ff = _frame([_gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.0)])
    out = to_scoring_frame(ff, build_matcher())   # flagged_bad=None (default)
    row = out[out["indicator_key"] == "interest_rate_decision"].iloc[0]
    assert row["actual"] == 0.0


def test_gate_non_cbz_suffix_mm_clean_still_widened_unchanged():
    # (5) non-cbz cu sufix m/m + flagged curat -> păstrat (neschimbat).
    ff = _frame([_gate_row("USD", "CPI y/y", "CPI m/m", 0.0)])
    lookup = {("USD", "CPI m/m", pd.Timestamp("2026-06-01").date()): False}
    out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
    row = out[out["indicator_key"] == "cpi_yoy"].iloc[0]
    assert row["actual"] == 0.0


def test_gate_non_cbz_no_short_suffix_nulled_unchanged():
    # (6) non-cbz fără sufix scurt (name_raw e chiar y/y) -> nulificat
    # (neschimbat), indiferent de flagged_bad.
    ff = _frame([_gate_row("USD", "CPI y/y", "CPI y/y", 0.0)])
    lookup = {("USD", "CPI y/y", pd.Timestamp("2026-06-01").date()): False}
    out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
    row = out[out["indicator_key"] == "cpi_yoy"].iloc[0]
    assert np.isnan(row["actual"])


def test_gate_regression_aud_cash_rate_data_not_loaded():
    # (7) Regresie AUD Cash Rate: interest_rate_decision, actual 0.0,
    # Quality='Data Not Loaded' -> NULIFICAT. Mirrors the real dry-run row
    # (2026-08-11 04:30 UTC) that motivated this fix.
    ff = _frame([_gate_row("AUD", "RBA Interest Rate Decision", "Cash Rate", 0.0,
                           dt="2026-08-11 04:30")])
    lookup = {("AUD", "Cash Rate", pd.Timestamp("2026-08-11").date()): True}   # Data Not Loaded -> bad
    out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
    row = out[out["indicator_key"] == "interest_rate_decision"].iloc[0]
    assert np.isnan(row["actual"])


def test_gate_duplicate_guard_silent_when_new_gate_only_reduces_valid_count(caplog):
    # (8) New-duplicate guard must NOT fire when the new gate REDUCES a
    # group's valid count -- it only ever acts on an INCREASE crossing the
    # <2 -> >=2 threshold (widening recovering a duplicate). Two same-group
    # cbz rows, both old_valid=True (valid_before=2, since old_valid ignores
    # cbz zeros entirely). Three sub-cases, all a decrease or flat from
    # valid_before=2, never an increase -- the guard's `valid_before >= 2`
    # skip must hold every time.
    import logging
    date = "2026-06-01"

    def _pair(flagged):
        ff = _frame([
            _gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.0,
                      dt=f"{date} 12:00", canonical_id="usd_fed_funds"),
            _gate_row("USD", "Fed Interest Rate Decision", "Federal Funds Rate", 0.1,
                      dt=f"{date} 13:00", canonical_id="usd_fed_funds"),
        ])
        lookup = {("USD", "Federal Funds Rate", pd.Timestamp(date).date()): flagged}
        with caplog.at_level(logging.INFO):
            out = to_scoring_frame(ff, build_matcher(), flagged_bad=lookup)
        caplog.clear()
        return out[out["indicator_key"] == "interest_rate_decision"].sort_values("release_dt")

    # flagged bad -> the 0.0 row is blocked (valid_after drops from 2 to 1),
    # but the 0.1 row (never touched by the gate at all -- it isn't 0.0) must
    # SURVIVE untouched. If the guard wrongly treated this post-gate 1-valid
    # state as cause to act, it could sweep the surviving 0.1 row away too.
    asym = _pair(True)
    assert list(asym["actual"].isna()) == [True, False]      # 0.0-row blocked, 0.1-row untouched
    assert asym["actual"].dropna().iloc[0] == pytest.approx(0.1)

    # flagged clean -> both kept, INCLUDING the 0.1 row -- if the guard had
    # wrongly fired here (treating [0.0, 0.1] as a "new" divergent dup), it
    # would null the second row too. It must not: this group was already
    # >=2 valid before any gate logic ran (interest_rate_decision is cbz, so
    # old_valid=True for both rows regardless of the 0.0/0.1 split) -- a
    # pre-existing duplicate, out of the guard's scope entirely.
    both_clean = _pair(False)
    assert both_clean["actual"].notna().sum() == 2
    assert list(both_clean["actual"]) == [0.0, 0.1]
    assert not any("new-duplicate group" in r.message for r in caplog.records)
