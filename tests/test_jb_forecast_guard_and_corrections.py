"""fix/jb-duplicate-forecast-guard (2026-10): the JB forecast guard in
clean_jblanked_actuals and the CORRECTION override state.

Guard tests run on the REAL range payload of 2026-10-03 20:05 UTC
(tests/fixtures/jb_range_raw_2026-10-03.json): its 2026-09-30 "Core PCE Price
Index m/m" appears twice — 11:30 UTC 0.2/0.3/0.1 (the real print) and 12:30 UTC
0.9/0.8/0.1 (Personal Spending's numbers) — and "last real row" ingested 0.9.
The schedule rows mirror data/economic_calendar_ff.parquet (FF consensus 0.3)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

import src.jb_actuals as J
from src import economic_render as er
from src import ff_refresh
from src.econ_calendar_ff import CANON_COLUMNS, parse_jblanked_range
from src.ff_corrections import CORRECTION, apply_corrections, read_ff_parquet
from src.manual_actuals import apply_overrides

FIXTURE = Path(__file__).parent / "fixtures" / "jb_range_raw_2026-10-03.json"
NOW = pd.Timestamp("2026-10-03 20:05:51")
PCE, NFP, ADP = "usd_core_pce_price_index", "usd_nonfarm_payrolls", "usd_adp_nonfarm_employment_change"
PCE_DT = pd.Timestamp("2026-09-30 12:30")
BEA_NOTE = ("BEA Personal Income and Outlays, August 2026: core PCE +0.2% m/m — "
            "https://www.bea.gov/news/2026/personal-income-and-outlays-august-2026")


def _jb():
    return parse_jblanked_range(str(FIXTURE), now_utc=NOW)


def _sched(cid, name_raw, name_canonical, dt, forecast, origin="ff", actual=float("nan")):
    return {"canonical_id": cid, "currency": "USD", "name_raw": name_raw,
            "name_canonical": name_canonical, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": 0.1, "released": True,
            "source": "ff", "forecast_origin": origin, "jb_status": None}


def _schedule(pce_fc=0.3, nfp_fc=89.0, adp_fc=73.0, origin="ff", pce_actual=float("nan")):
    return pd.DataFrame([
        _sched(PCE, "Core PCE Price Index m/m", "Core PCE Price Index y/y", PCE_DT, pce_fc, origin,
               pce_actual),
        _sched(NFP, "Non-Farm Employment Change", "Nonfarm Payrolls", "2026-10-02 12:30", nfp_fc, origin),
        _sched(ADP, "ADP Non-Farm Employment Change", "ADP Nonfarm Employment Change",
               "2026-09-30 12:15", adp_fc, origin),
    ], columns=CANON_COLUMNS)


def _one(cleaned, cid):
    rows = cleaned[cleaned["canonical_id"] == cid]
    assert len(rows) == 1
    return rows.iloc[0]


# --- forecast guard -----------------------------------------------------------

def test_fixture_carries_the_conflicting_core_pce_pair():
    jb = _jb()
    pce = jb[jb["canonical_id"] == PCE].sort_values("datetime_utc")
    assert pce[["actual", "forecast"]].values.tolist() == [[0.2, 0.3], [0.9, 0.8]]


def test_guard_core_pce_keeps_the_row_matching_the_ff_forecast():
    row = _one(J.clean_jblanked_actuals(_jb(), schedule=_schedule(), preserve_zero_actuals=True), PCE)
    assert row["actual"] == pytest.approx(0.2)
    assert row["datetime_utc"] == PCE_DT                    # still re-aligned to the FF hour


def test_without_schedule_the_old_pick_is_unchanged():
    assert _one(J.clean_jblanked_actuals(_jb(), preserve_zero_actuals=True), PCE)["actual"] == 0.9


def test_guard_nfp_single_real_row_with_matching_forecast():
    """NFP 2026-10-02: one real row (29, forecast 89) beside a 0.0 placeholder."""
    row = _one(J.clean_jblanked_actuals(_jb(), schedule=_schedule(), preserve_zero_actuals=True), NFP)
    assert row["actual"] == pytest.approx(29.0)


def test_guard_identical_duplicates_unchanged_even_when_forecast_differs():
    """ADP 2026-09-30: two copies, both 90 — rule c, whatever FF says."""
    for adp_fc in (73.0, 99.0):
        row = _one(J.clean_jblanked_actuals(_jb(), schedule=_schedule(adp_fc=adp_fc),
                                            preserve_zero_actuals=True), ADP)
        assert row["actual"] == pytest.approx(90.0)


def test_guard_conflict_without_a_matching_forecast_ingests_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="src.jb_actuals"):
        cleaned = J.clean_jblanked_actuals(_jb(), schedule=_schedule(pce_fc=0.5),
                                           preserve_zero_actuals=True)
    assert pd.isna(_one(cleaned, PCE)["actual"])
    assert any("JB pull: conflicting duplicates" in r.message and PCE in r.message
               for r in caplog.records)


def test_guard_single_row_with_a_different_forecast_ingests_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="src.jb_actuals"):
        cleaned = J.clean_jblanked_actuals(_jb(), schedule=_schedule(nfp_fc=90.0),
                                           preserve_zero_actuals=True)
    assert pd.isna(_one(cleaned, NFP)["actual"])
    assert any("forecast mismatch" in r.message and NFP in r.message for r in caplog.records)


def test_guard_needs_an_ff_forecast():
    """forecast_origin other than "ff", or a NaN FF forecast → old behaviour."""
    for sched in (_schedule(origin="jb"), _schedule(pce_fc=float("nan"), nfp_fc=float("nan"))):
        cleaned = J.clean_jblanked_actuals(_jb(), schedule=sched, preserve_zero_actuals=True)
        assert _one(cleaned, PCE)["actual"] == 0.9
        assert _one(cleaned, NFP)["actual"] == 29.0


def test_reingest_through_pull_actuals_overwrites_the_wrong_value(tmp_path):
    """C3 (guard half): a parquet holding the bad 0.9, re-ingesting the 2026-10-03
    payload, ends at 0.2."""
    parquet = tmp_path / "ff.parquet"
    _schedule(pce_actual=0.9).to_parquet(parquet, index=False)
    rep = J.pull_actuals_range(now_utc=NOW, parquet_path=parquet, state_path=tmp_path / "s.json",
                         raw_dir=tmp_path / "raw", fetcher=lambda f, t: FIXTURE.read_text(),
                         cfg={}, force=True)
    assert rep["status"] == "ok"
    out = pd.read_parquet(parquet)
    assert out.loc[(out["canonical_id"] == PCE) & (out["datetime_utc"] == PCE_DT), "actual"].tolist() == [0.2]


# --- CORRECTION ------------------------------------------------------------------

def _correction(actual=0.2, note=BEA_NOTE, dt="2026-09-30 12:30:00", state=CORRECTION):
    return {"canonical_id": PCE, "currency": "USD", "indicator_key": "core_pce",
            "datetime_utc": dt, "actual": actual, "state_resolved": state,
            "entered_by": "audit-1004", "entered_at": "2026-10-06T00:00:00+00:00", "note": note}


@pytest.mark.parametrize("current", [0.9, 0.0, float("nan")])
def test_correction_beats_any_current_actual(current):
    ff = _schedule(pce_actual=current)
    out = apply_corrections(ff, [_correction()])
    assert out.loc[out["canonical_id"] == PCE, "actual"].tolist() == [0.2]
    assert ff.loc[ff["canonical_id"] == PCE, "actual"].equals(
        _schedule(pce_actual=current).loc[ff["canonical_id"] == PCE, "actual"])   # input not mutated


@pytest.mark.parametrize("note", ["", "BEA says 0.2", "www.bea.gov/x", "ftp://bea.gov/x"])
def test_correction_without_source_url_is_ignored(note, caplog):
    with caplog.at_level(logging.WARNING, logger="src.ff_corrections"):
        out = apply_corrections(_schedule(pce_actual=0.9), [_correction(note=note)])
    assert out.loc[out["canonical_id"] == PCE, "actual"].tolist() == [0.9]
    assert any("no http(s) source URL" in r.message for r in caplog.records)


def test_correction_for_a_missing_row_is_ignored():
    out = apply_corrections(_schedule(pce_actual=0.9), [_correction(dt="2026-09-30 11:30:00")])
    assert out.loc[out["canonical_id"] == PCE, "actual"].tolist() == [0.9]


def test_other_states_are_not_corrections():
    out = apply_corrections(_schedule(pce_actual=0.9), [_correction(state="MISSING")])
    assert out.loc[out["canonical_id"] == PCE, "actual"].tolist() == [0.9]


def test_correction_survives_a_jb_reingest(tmp_path):
    """C3 (CORRECTION half): the guard cannot help here (no FF consensus on file),
    so the re-delivery writes 0.9 back into the parquet — the read still says 0.2."""
    parquet, overrides = tmp_path / "ff.parquet", tmp_path / "ov.json"
    _schedule(origin="jb", pce_actual=0.2).to_parquet(parquet, index=False)
    overrides.write_text(json.dumps([_correction()], indent=1) + "\n")
    J.pull_actuals_range(now_utc=NOW, parquet_path=parquet, state_path=tmp_path / "s.json",
                   raw_dir=tmp_path / "raw", fetcher=lambda f, t: FIXTURE.read_text(),
                   cfg={}, force=True)
    raw = pd.read_parquet(parquet)
    assert raw.loc[raw["canonical_id"] == PCE, "actual"].tolist() == [0.9]
    ff = read_ff_parquet(parquet, overrides_path=overrides)
    assert ff.loc[ff["canonical_id"] == PCE, "actual"].tolist() == [0.2]


def test_apply_overrides_never_turns_a_correction_into_a_manual_row():
    ff = _schedule(pce_actual=float("nan"))
    manual, _ = apply_overrides(ff, [_correction()], now_utc=NOW)
    assert manual.empty


def test_scoring_reads_the_corrected_value(tmp_path, monkeypatch):
    parquet, overrides = tmp_path / "ff.parquet", tmp_path / "ov.json"
    _schedule(pce_actual=0.9).to_parquet(parquet, index=False)
    monkeypatch.setattr(ff_refresh, "FF_PARQUET", parquet)
    monkeypatch.setattr(ff_refresh, "calendar_source", lambda cfg=None: "ff")
    monkeypatch.setattr(er, "MANUAL_ACTUALS_OVERRIDES", overrides)
    monkeypatch.setattr(er, "ROOT", tmp_path)                 # no FRED quarantine file

    def pce_actual():
        cal = er._load_calendar_frame(NOW)
        return cal.loc[(cal["currency"] == "USD") & (cal["indicator_key"] == "core_pce"), "actual"].tolist()

    overrides.write_text("[]")
    assert pce_actual() == [0.9]
    overrides.write_text(json.dumps([_correction()]))
    assert pce_actual() == [0.2]
    overrides.write_text(json.dumps([_correction(note="no url")]))
    assert pce_actual() == [0.9]


def test_repo_overrides_correct_core_pce_2026_09_30():
    """The committed entry (audit-1004) is a valid CORRECTION for the bad print."""
    entries = json.loads((Path(__file__).resolve().parents[1] / "data" /
                          "manual_actuals_overrides.json").read_text())
    hit = [e for e in entries if e["canonical_id"] == PCE and e["state_resolved"] == CORRECTION]
    assert len(hit) == 1 and hit[0]["actual"] == 0.2 and hit[0]["datetime_utc"] == "2026-09-30 12:30:00"
    out = apply_corrections(_schedule(pce_actual=0.9), hit)
    assert out.loc[out["canonical_id"] == PCE, "actual"].tolist() == [0.2]
