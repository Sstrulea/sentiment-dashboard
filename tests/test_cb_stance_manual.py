"""Stance S0a - the manual files: mandates.yaml (schema 8/8, one current version, overlaps, the CAD 2027 version,
consistency with econ_catalog.yml), neutral.yaml and forecasts.yaml validators (positive and negative cases), the NZD
forecasts = the latest MPS of rbnz.yaml, config/cb_stance.yaml. Offline."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
import yaml

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_compute.stance_inputs import current_version

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 14)
PATHS = cc.Paths()


@pytest.fixture(scope="module")
def mand():
    return ds.load_mandates(PATHS)


@pytest.fixture(scope="module")
def ids():
    return ds.macro_series_ids()


# --------------------------------------------------------------- mandates -----

def test_mandates_valid_8_of_8(mand, ids):
    assert ds.validate_mandates(mand, TODAY, ids) == []
    vs = ds.mandate_versions(mand)
    assert set(vs) == set(ds.STANCE_BANKS)
    assert all(current_version(v, TODAY) is not None for v in vs.values())


def test_cad_has_the_2021_and_the_2027_agreements(mand):
    cad = ds.mandate_versions(mand)["CAD"]
    assert len(cad) == 2
    assert current_version(cad, date(2026, 12, 31)).effective_from == date(2022, 1, 1)
    nxt = current_version(cad, date(2027, 1, 1))
    assert nxt.effective_from == date(2027, 1, 1) and nxt.effective_to == date(2031, 12, 31)
    assert (nxt.target.low, nxt.target.high, nxt.target.midpoint) == (1.0, 3.0, 2.0)
    assert ds.validate_mandates(mand, date(2027, 1, 1), ds.macro_series_ids()) == []


def test_validation_rejects_overlap_and_inverted_dates(mand, ids):
    bad = copy.deepcopy(mand)
    bad["banks"]["CAD"]["versions"][1]["effective_from"] = date(2026, 6, 1)          # overlaps the 2021 agreement
    errs = ds.validate_mandates(bad, TODAY, ids)
    assert any("overlap" in e for e in errs) and any("not exactly one version current" in e for e in errs)
    bad = copy.deepcopy(mand)
    bad["banks"]["USD"]["versions"][0]["effective_to"] = date(2020, 1, 1)
    assert any("effective_to 2020-01-01 < effective_from" in e for e in ds.validate_mandates(bad, TODAY, ids))


def test_validation_rejects_unknown_series_and_missing_bank(mand, ids):
    bad = copy.deepcopy(mand)
    bad["banks"]["GBP"]["versions"][0]["underlying"]["secondary"] = ["ons:XXXX"]
    del bad["banks"]["CHF"]
    errs = ds.validate_mandates(bad, TODAY, ids)
    assert any("ons:XXXX" in e for e in errs) and any("bank CHF missing" in e for e in errs)


def test_validation_rejects_midpoint_without_quote_and_source_without_page(mand, ids):
    bad = copy.deepcopy(mand)
    bad["banks"]["AUD"]["versions"][0]["target"]["midpoint"] = {"value": 2.5}
    bad["banks"]["EUR"]["versions"][0]["sources"][0]["page"] = ""
    errs = ds.validate_mandates(bad, TODAY, ids)
    assert any("AUD" in e and "midpoint" in e for e in errs) and any("EUR" in e and "source.url and source.page" in e for e in errs)


def test_mandate_targets_consistent_with_econ_catalog(mand):
    """No runtime import between the modules: the catalog's chart targets (where they carry a number) equal the current
    mandate targets. CHF is text-only in the catalog: explicit assertion on its note."""
    cat = yaml.safe_load((ROOT / "data" / "econ_catalog.yml").read_text())
    cur = {b: current_version(v, TODAY) for b, v in ds.mandate_versions(mand).items()}
    seen = {}
    for _, ccys in cat["categories"].items():
        for ccy, entries in ccys.items():
            for e in entries:
                t = e.get("target")
                if isinstance(t, dict) and t.get("effective_to") is None:
                    seen.setdefault(ccy, set()).add((t["kind"], t.get("value"), t.get("low"), t.get("high")))
    for ccy, ts in seen.items():
        m = cur[ccy].target
        assert ts == {(m.kind, m.value, m.low, m.high)}, (ccy, ts, m)
    assert set(seen) == {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD"}
    chf_notes = {e.get("target_note") for e in cat["categories"]["inflation"]["CHF"]}
    assert "SNB: 0-2% (target on headline CPI, annual)" in chf_notes
    assert (cur["CHF"].target.kind, cur["CHF"].target.low, cur["CHF"].target.high) == ("band", 0.0, 2.0)


# ---------------------------------------------------------------- neutral -----

def test_neutral_valid_and_statuses(mand):
    doc = ds.load_neutral(PATHS)
    assert ds.validate_neutral(doc, TODAY, mand) == []
    st = {b: e["status"] for b, e in doc["banks"].items()}
    assert "USD" not in st and st["CHF"] == "not_published" and st["NZD"] in ("filled", "pending")
    assert all(st[b] in ("filled", "pending") for b in ("EUR", "GBP", "JPY", "CAD", "AUD"))
    assert all(x.get("member") for x in doc["banks"]["GBP"]["estimates"])


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["banks"]["JPY"]["estimates"][0]["source"].pop("url"), "source.url and source.page"),
    (lambda d: d["banks"]["NZD"].update(status="pending", reason=None), "pending needs reason and next_step"),
    (lambda d: d["banks"]["CAD"]["estimates"][0].update(high=30.0), "outside"),
    (lambda d: d["banks"]["CAD"]["estimates"][0].update(low=3.5, high=3.25), "low 3.5 > high 3.25"),
    (lambda d: d["banks"]["EUR"]["estimates"][0].update(published=date(2027, 1, 1)), "in the future"),
    (lambda d: d["banks"]["CHF"].pop("policy_quote"), "own policy"),
    (lambda d: d["banks"].update(USD={"status": "filled"}), "USD must have no entry"),
    (lambda d: d["banks"]["AUD"].update(flag="BANK_ESTIMATE"), "!= mandates.yaml neutral.flag"),
    (lambda d: d["banks"]["GBP"]["estimates"][0].update(quote=" ".join(["word"] * 31)), "longer than 30 words"),
])
def test_neutral_validator_negative(mand, mutate, needle):
    doc = copy.deepcopy(ds.load_neutral(PATHS))
    mutate(doc)
    assert any(needle in e for e in ds.validate_neutral(doc, TODAY, mand)), needle


def test_load_with_validation_raises(tmp_path, mand):
    (tmp_path / "manual").mkdir()
    doc = copy.deepcopy(ds.load_neutral(PATHS))
    doc["banks"]["CAD"]["estimates"][0]["low"] = 9.0
    (tmp_path / "manual" / "neutral.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ds.StanceDataError):
        ds.load_neutral(cc.Paths(tmp_path), today=TODAY, validate=True, mandates=mand)


# -------------------------------------------------------------- forecasts -----

def test_forecasts_valid_7_manual(mand):
    doc = ds.load_forecasts(PATHS)
    assert ds.validate_forecasts(doc, TODAY, mand, ds.load_rbnz(PATHS.manual / "rbnz.yaml")) == []
    assert set(doc["banks"]) == set(ds.STANCE_BANKS) - {"USD"}
    assert all(e["status"] in ("filled", "pending") for e in doc["banks"].values())
    ra = {b for b, e in doc["banks"].items() for s in e["series"] if s["role"] == "rate_assumption"}
    assert ra == {"EUR", "GBP", "AUD", "CHF"}


def test_nzd_forecasts_are_the_latest_mps_of_rbnz_yaml():
    doc = ds.load_forecasts(PATHS)
    rb = ds.load_rbnz(PATHS.manual / "rbnz.yaml")
    latest = max(m["meeting"] for m in rb["mps"] if m["status"] == "filled")
    assert doc["banks"]["NZD"]["meeting"] == latest
    assert not any(s["role"] == "rate_assumption" for s in doc["banks"]["NZD"]["series"])         # the OCR track is not duplicated


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["banks"]["GBP"]["series"][0].update(role="core"), "unknown role"),
    (lambda d: d["banks"]["GBP"]["series"][0].update(transform="monthly"), "unknown transform"),
    (lambda d: d["banks"]["GBP"]["series"][0]["values"][0].update(horizon="Q4-2026"), "unknown horizon"),
    (lambda d: d["banks"]["EUR"].update(conditioning="own_path"), "!= mandates.yaml"),
    (lambda d: d["banks"]["AUD"]["series"][0]["values"][0].update(value=40.0), "outside"),
    (lambda d: d["banks"]["CAD"]["series"][3]["values"][0].update(low=0.5), "low 0.5 > high -0.25"),
    (lambda d: d["banks"]["CHF"].update(published=date(2026, 12, 10)), "in the future"),
    (lambda d: d["banks"]["CAD"]["series"][2].pop("next_step"), "pending needs reason and next_step"),
    (lambda d: d["banks"]["JPY"].pop("source"), "source.url and source.page"),
    (lambda d: d["banks"]["NZD"].update(meeting=date(2026, 5, 27)), "latest filled MPS"),
    (lambda d: d["banks"]["EUR"]["series"][3].pop("instrument"), "instrument"),
])
def test_forecasts_validator_negative(mand, mutate, needle):
    doc = copy.deepcopy(ds.load_forecasts(PATHS))
    mutate(doc)
    errs = ds.validate_forecasts(doc, TODAY, mand, ds.load_rbnz(PATHS.manual / "rbnz.yaml"))
    assert any(needle in e for e in errs), (needle, errs)


def test_projections_view_merges_sep_and_manual():
    view = ds.projections_view(PATHS)
    assert set(view) == set(ds.STANCE_BANKS)
    assert view["USD"].source == "fed_sep" and view["USD"].conditioning == "own_path"
    assert view["AUD"].cutoff == date(2026, 10, 28) and view["AUD"].source == "manual"


# ------------------------------------------------------------------ config ----

def test_stance_config_frozen_parameters():
    c = ds.load_stance_config()
    assert c["frozen"] == date(2026, 11, 14)
    assert (c["rule"]["inflation_response"], c["rule"]["output_gap_response"], c["rule"]["okun_coefficient"]) == (1.5, 0.5, 2.0)
    assert (c["situations"]["delta_pp"], c["situations"]["epsilon_pp"], c["horizon_months"]) == (0.25, 0.5, 12)
    assert c["staleness"]["neutral_months"] == 15 and len(c["underlying_rule"]) == 3


def test_status_section_lists_pending_and_stale():
    title, heads, rows, notes = ds.stance_status(PATHS, TODAY)
    assert len(rows) == 8
    assert any(n.startswith("WARN neutral EUR") for n in notes)
    assert any(n.startswith("PENDING forecasts CAD underlying") for n in notes)
