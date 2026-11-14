"""Stance S0a - official macro series: parsers of the new / generalised adapters, the yearly store, y/y vs the official
values, the rebasing guard, the zero-request gate. Offline: fixtures trimmed from the real official responses
(tests/fixtures/cb_stance/macro/, fetched 2026-11-14, see manifest.json)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src import cb_macro as M
from src import cb_store as cs
from src.cb_compute.macro_yoy import yoy, yoy_series
from src.cb_sources import official as O
from src.official_series import period_shift
from src.official_sources import StatsNzCpi

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "cb_stance" / "macro"
SINCE = date(2015, 1, 1)


def _t(name: str, enc: str = "utf-8") -> str:
    return (FIX / name).read_bytes().decode(enc)


# --------------------------------------------------------------- parsers -----

def test_fred_index_and_official_yoy_pce_september_2026():
    """BEA, Personal Income and Outlays September 2026: PCE +3.6 %, excluding food and energy +3.1 % from one year ago."""
    from src.fred_api import parse_observations                          # FRED API JSON (fixture cut from the real response)
    head = {d.strftime("%Y-%m"): v for d, v in parse_observations(json.loads(_t("fred_pcepi.json"))) if v is not None}
    core = {d.strftime("%Y-%m"): v for d, v in parse_observations(json.loads(_t("fred_pcepilfe.json"))) if v is not None}
    assert yoy(head, "2026-09") == Decimal("3.6")
    assert yoy(core, "2026-09") == Decimal("3.1")


def test_ecb_monthly_period_parse_hicpx():
    rows = O.parse_ecb(_t("ecb_hicp_xef000.csv"), SINCE)
    got = {d.strftime("%Y-%m"): v for d, v in rows}
    assert got["2026-10"] == 2.5 and got["2026-09"] == 2.5                # Eurostat flash 4 Nov 2026: 2.5 (ex energy, food, alcohol & tobacco)
    assert all(d.day == 1 for d, _ in rows)


def test_ecb_daily_parse_unchanged():
    text = "KEY,FREQ,TIME_PERIOD,OBS_VALUE\nFM.D,D,2026-09-16,2.00\nFM.D,D,2026-09-17,1.75\n"
    assert O.parse_ecb(text, date(2026, 9, 17)) == [(date(2026, 9, 17), 1.75)]


def test_ons_json_values_and_release_date():
    rows, rel = O.parse_ons(json.loads(_t("ons_dko8.json")), SINCE)
    got = {d.strftime("%Y-%m"): v for d, v in rows}
    assert got["2026-09"] == 2.8                                          # ONS bulletin: core CPI 2.8 % in the 12 months to September 2026
    assert rel == date(2026, 10, 21)                                      # releaseDate 2026-10-20T23:00Z = 07:00 UK on 21 October


def test_abs_sdmx_csv_trimmed_mean_monthly():
    got = {d.strftime("%Y-%m"): v for d, v in O.parse_abs(_t("abs_cpi_999902.csv"), SINCE)}
    assert got["2026-09"] == 3.6 and got["2026-08"] == 3.5                # ABS Sep 2026: 3.6 %, up from a revised 3.5 %
    assert min(got) == "2025-04"                                          # the complete monthly CPI starts in April 2025


def test_abs_quarterly_period_parse():
    text = "DATAFLOW,TIME_PERIOD,OBS_VALUE\nABS:CPI_Q,2026-Q3,3.7\n"
    assert O.parse_abs(text, SINCE) == [(date(2026, 7, 1), 3.7)]


def test_statjp_csv_three_codes_and_official_yoy():
    """Statistics Bureau of Japan, national CPI September 2026 (released 23 Oct 2026): all items +2.1 %, less fresh food
    +2.1 %, less fresh food and energy +2.4 % - from the 2025-base reference indices (3 decimals)."""
    text = _t("statjp_zmi2025aa.csv", "cp932")
    out = {c: {d.strftime("%Y-%m"): v for d, v in O.parse_stat_jp(text, c, SINCE)} for c in ("0001", "0161", "0178")}
    assert (yoy(out["0001"], "2026-09"), yoy(out["0161"], "2026-09"), yoy(out["0178"], "2026-09")) == (Decimal("2.1"), Decimal("2.1"), Decimal("2.4"))
    with pytest.raises(ValueError):
        O.parse_stat_jp(text, "9999", SINCE)


@pytest.mark.parametrize("table, key, period, value", [("g1", "GCPIOCPMTMYP", date(2026, 9, 30), 3.7), ("g1", "GCPIAGYP", date(2026, 9, 30), 3.9),
                                                       ("h5", "GLFSURSA", date(2026, 9, 30), 4.7)])
def test_rba_table_is_a_parameter(table, key, period, value):
    got = O.parse_rba_table(_t(f"rba_{table}.csv"), [key], SINCE, table=table.upper())
    assert dict(got[key])[period] == value
    assert O.rba_publication_dates(_t(f"rba_{table}.csv"))[key] == (date(2026, 10, 29) if table == "g1" else date(2026, 10, 16))


def test_rba_f1_alias_still_parses_daily_dates():
    text = "F1\nSeries ID,FIRMMCRTD\n16-Sep-2026,3.60\n"
    assert O.parse_rba_f1(text, ["FIRMMCRTD"], SINCE) == {"FIRMMCRTD": [(date(2026, 9, 16), 3.6)]}


def test_snb_cube_monthly_and_publishing_date():
    text = _t("snb_plkoprinfla.csv")
    got = O.parse_snb(text, ["TLK", "K1"], SINCE)
    assert dict(got["TLK"])[date(2026, 9, 1)] == 1.0 and dict(got["K1"])[date(2026, 9, 1)] == 0.5     # FSO 1 Oct 2026: +1.0 %, core 1 +0.5 %
    assert O.snb_publishing_date(text) == date(2026, 10, 21)


def test_valet_core_measures():
    got = O.parse_valet(json.loads(_t("valet_cpi.json")), ["CPI_TRIM", "CPI_MEDIAN", "STATIC_TOTALCPICHANGE"], SINCE)
    sep = {k: dict(v)[date(2026, 9, 1)] for k, v in got.items()}
    assert sep == {"CPI_TRIM": 1.8, "CPI_MEDIAN": 1.9, "STATIC_TOTALCPICHANGE": 3.1}               # The Daily, 19 Oct 2026, table 4


def test_statsnz_series_ref_and_file_are_parameters():
    a = StatsNzCpi(series_ref="CPIQ.SE9NS1450", file_kind="infoshare-data")
    url = a.discover_csv_url(_t("statsnz_page_2026Q3.html"), a.base, "infoshare-data")
    assert url.endswith("-infoshare-data.csv")
    assert a.discover_csv_url(_t("statsnz_page_2025Q3.html"), a.base, "infoshare-data").endswith("-infoshare-data-updated.csv")
    cur, _ = a.parse_index_csv((FIX / "statsnz_infoshare_2026Q3.csv").read_bytes())         # cp1252
    old, _ = a.parse_index_csv((FIX / "statsnz_infoshare_2025Q3.csv").read_bytes())
    assert cur == {"2026Q3": 1343.0}
    assert yoy({**old, **cur}, "2026Q3") == Decimal("2.9")              # Stats NZ, CPI excl. food, household energy and vehicle fuels: 2.9 % annual
    head = StatsNzCpi()
    assert head.series_ref == "CPIQ.SE9A" and head.file_kind == "index-numbers"


# ----------------------------------------------------------------- y/y -------

def test_period_shift_months_and_quarters():
    assert period_shift("2026-01", -12) == "2025-01" and period_shift("2026-01", -1) == "2025-12"
    assert period_shift("2026Q1", -4) == "2025Q1"


def test_yoy_half_up_and_missing_base():
    assert yoy({"2026-09": 100.25, "2025-09": 100.0}, "2026-09", 1) == Decimal("0.3")
    assert yoy({"2026-09": 101.0}, "2026-09") is None
    assert list(yoy_series({"2025-09": 100.0, "2026-09": 102.0})) == ["2026-09"]


# --------------------------------------------------------------- store -------

def _rows(sid="x:A", vals=None, freq="M"):
    vals = vals or {"2025-12": 1.0, "2026-01": 2.0}
    t = datetime(2026, 11, 14, tzinfo=timezone.utc)
    return [{"series_id": sid, "currency": "USD", "period": p, "period_start": M.period_start(p), "freq": freq, "value": v,
             "unit": "percent", "published_as": "yoy", "release_date": None, "fetched_at": t, "source_url": "u"} for p, v in vals.items()]


def test_yearly_partitions_round_trip_and_idempotent(tmp_path):
    store: dict = {}
    st = cs.merge(M.MACRO, store, _rows())
    assert st.months == {"2025", "2026"}
    assert cs.write(M.MACRO, tmp_path, store) == ["2025", "2026"]
    assert sorted(p.name for p in (tmp_path / "macro_series").iterdir()) == ["macro_series_2025.parquet", "macro_series_2026.parquet"]
    back = cs.load(M.MACRO, tmp_path)
    assert back == store
    assert cs.load(M.MACRO, tmp_path, start=date(2026, 3, 1)).keys() == {("x:A", "2026-01")}
    again = cs.merge(M.MACRO, back, _rows())
    assert (again.new, again.updated, again.unchanged) == (0, 0, 2) and cs.write(M.MACRO, tmp_path, back) == []


def test_monthly_datasets_unchanged_by_granularity():
    ds = cs.Dataset("m", "m", M.MACRO_SCHEMA, ("series_id", "period"), "period_start", ("value",))
    assert ds.part_of(date(2026, 9, 3)) == "2026-09" and ds.glob == "m_????-??.parquet"


# --------------------------------------------------------------- guard -------

def test_small_revision_accepted_and_listed():
    g = M.guard({"2026-07": 3.5, "2026-08": 3.6, "2026-09": 3.6}, {"2026-07": 3.5, "2026-08": 3.5, "2026-09": 3.6, "2026-10": 3.7},
                min_changed=6, max_changed_share=0.5)
    assert g.accepted and g.changed == [("2026-08", 3.6, 3.5)]


def test_rebasing_refused():
    old = {f"2025-{m:02d}": 100.0 + m for m in range(1, 13)}
    new = {p: v * 0.9 for p, v in old.items()}                           # the whole history rescaled = a new base
    g = M.guard(old, new, min_changed=6, max_changed_share=0.5)
    assert not g.accepted and "rebasing" in g.reason and len(g.changed) == 12


class _FakeSrc:
    def __init__(self, obs):
        self._obs = obs
        self.calls = 0
        self.last_status = self.last_note = ""

    def fetch(self, since=None, state=None):
        self.calls += 1
        return O.OfficialResult("ok", list(self._obs), released={}, urls={})


def _cfg():
    return {"providers": {"fake": {"url": "https://example/{key}"}},
            "macro": {"meta": {"backfill_from": "2015-01-01", "check_lead_days": 5, "stale_grace_days": 7, "guard": {"min_changed": 6, "max_changed_share": 0.5}},
                      "series": {"fake:A": {"currency": "USD", "role": "headline", "provider": "fake", "key": "A", "freq": "M", "unit": "index",
                                            "published_as": "index", "lag_days": 20}}}}


def _obs(vals):
    t = datetime(2026, 11, 14, tzinfo=timezone.utc)
    return [O.Obs("fake:A", "USD", M.period_start(p), v, "index", t) for p, v in vals.items()]


def test_run_writes_then_refuses_a_rebased_fetch(tmp_path):
    from src import cb_collect as cc
    paths = cc.Paths(tmp_path)
    hist = {f"2025-{m:02d}": 100.0 + m for m in range(1, 13)}
    rep = M.run_macro(paths, date(2026, 1, 25), backfill_from=date(2025, 1, 1), cfg=_cfg(), sources={"fake": _FakeSrc(_obs(hist))})
    assert rep[0].merge.new == 12
    rebased = {p: round(v * 0.9, 3) for p, v in hist.items()}
    rep = M.run_macro(paths, date(2026, 1, 25), backfill_from=date(2025, 1, 1), cfg=_cfg(), sources={"fake": _FakeSrc(_obs(rebased))})
    assert rep[0].refused and rep[0].merge.new == rep[0].merge.updated == 0
    stored = M.by_series(M.load_macro(paths))["fake:A"]
    assert stored["2025-06"]["value"] == 106.0                            # nothing rewritten
    assert "fake:A" in cc.load_state(paths)[M.REFUSED_KEY]
    text, _ = M.macro_report(rep, date(2026, 1, 25))
    assert "REFUSED fake:A" in text


def test_zero_requests_when_no_period_is_due(tmp_path):
    from src import cb_collect as cc
    paths = cc.Paths(tmp_path)
    src = _FakeSrc(_obs({"2026-09": 101.0}))
    M.run_macro(paths, date(2026, 10, 25), backfill_from=date(2026, 9, 1), cfg=_cfg(), sources={"fake": src})
    src2 = _FakeSrc(_obs({"2026-10": 101.5}))
    rep = M.run_macro(paths, date(2026, 11, 14), cfg=_cfg(), sources={"fake": src2})     # October due 2026-11-20, asked from 11-15
    assert src2.calls == 0 and rep[0].status == "skipped" and not rep[0].requested
    rep = M.run_macro(paths, date(2026, 11, 16), cfg=_cfg(), sources={"fake": src2})
    assert src2.calls == 1 and rep[0].merge.new == 1


def test_due_and_stale_dates():
    assert M.due_date("2026-09", 18) == date(2026, 11, 18)
    assert M.due_date("2026Q3", 20) == date(2027, 1, 20)
    assert not M.is_stale("2026-09", 18, date(2026, 11, 25), 7) and M.is_stale("2026-09", 18, date(2026, 11, 26), 7)


def test_macro_config_series_cover_every_mandate_measure():
    from src import cb_collect as cc
    from src import cb_datasets as ds
    ids = ds.macro_series_ids()
    for b, vs in ds.mandate_versions(ds.load_mandates(cc.Paths())).items():
        for v in vs:
            assert {v.headline, *v.underlying, *v.secondary} <= ids, b
    cfg = O.load_official()["macro"]["series"]
    assert {s["currency"] for s in cfg.values()} == {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"}
    assert {sid for sid, s in cfg.items() if s["role"] == "unemployment"} == {"fred:UNRATE", "rba:GLFSURSA"}
