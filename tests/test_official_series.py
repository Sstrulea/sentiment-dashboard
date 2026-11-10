"""Official-series layer (P2): Stats NZ CPI -> NZD CPI y/y, display-only.

Offline: fixtures are trimmed verbatim from the real P1 files
(tests/fixtures/statsnz/, Sep-2026 quarter release, fetched 2026-11-10).
No network anywhere — the fetcher is driven through injected getters.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src import history_compute as hc
from src import official_fetch as of
from src import official_series as osr
from src.official_sources import FetchResult, StatsNzCpi

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "statsnz"
PAGE = (FIX / "cpi_sep2026_release_page_trimmed.html").read_text()
CSV = (FIX / "cpi_sep2026_index_numbers_trimmed.csv").read_bytes()
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"

QUARTERS = ["2022Q4", "2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2", "2024Q3",
            "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]
YOY = ["7.2", "6.7", "6.0", "5.6", "4.7", "4.0", "3.3", "2.2",
       "2.2", "2.5", "2.7", "3.0", "3.1", "3.1", "4.1", "3.7"]
QOQ_FF = [1.4, 1.2, 1.1, 1.8, 0.5, 0.6, 0.4, 0.6, 0.5, 0.9, 0.5, 1.0, 0.6, 0.9, 1.5, 0.7]
RELEASES = ["2023-01-24 21:45", "2023-04-19 22:45", "2023-07-18 22:45", "2023-10-16 21:45",
            "2024-01-23 21:45", "2024-04-16 22:45", "2024-07-16 22:45", "2024-10-15 21:45",
            "2025-01-21 21:45", "2025-04-16 22:45", "2025-07-20 22:45", "2025-10-19 21:45",
            "2026-01-22 21:45", "2026-04-20 22:45", "2026-07-20 22:45", "2026-10-21 21:45"]
CSV_URL = ("https://www.stats.govt.nz/assets/Uploads/Consumers-price-index/"
           "Consumers-price-index-September-2026-quarter/Download-data/"
           "consumers-price-index-september-2026-quarter-index-numbers.csv")
ADAPTER = StatsNzCpi()


@pytest.fixture(autouse=True)
def _logging_enabled():
    """Some scripts imported by other tests call logging.disable(...) process-
    wide (scripts/measure/tournament/*); the guard-log assertions here need
    logging on. Restored afterwards."""
    import logging
    prev = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    yield
    logging.disable(prev)


def _index():
    return ADAPTER.parse_index_csv(CSV)[0]


# ------------------------------------------------------------ parsing -------

def test_document_link_discovered_from_entity_encoded_json():
    assert 'href="' not in PAGE.split("data-value")[1][:2000]   # links are not hrefs
    assert ADAPTER.discover_csv_url(PAGE) == CSV_URL


def test_csv_parse_series_and_period_format():
    index, raw = ADAPTER.parse_index_csv(CSV)
    assert ADAPTER.period_to_quarter("2026.09") == "2026Q3"
    assert ADAPTER.period_to_quarter("2026.03") == "2026Q1"
    assert index["2026Q3"] == 1368.0 and raw["2026Q3"] == "1368"
    assert "1914Q3" not in index            # NA row skipped
    assert index["1914Q2"] == pytest.approx(12.8696737357259)
    assert index["2026Q2"] == 1359.0        # not SE901 (Food, 2026.06 = other value)


def test_release_page_url_per_quarter():
    assert ADAPTER.release_page_url("2026Q3").endswith("consumers-price-index-september-2026-quarter/")
    assert ADAPTER.release_page_url("2026Q4").endswith("consumers-price-index-december-2026-quarter/")
    assert ADAPTER.release_page_url("2027Q1").endswith("consumers-price-index-march-2027-quarter/")


def test_reference_quarter_is_calendar_quarter_before_release():
    assert [osr.reference_quarter(r) for r in RELEASES] == QUARTERS


# ------------------------------------------ permanent published values -------

def test_permanent_yoy_and_qoq_match_published_16_of_16():
    index = _index()
    assert [str(osr.transform(index, q, "YoY")) for q in QUARTERS] == YOY
    assert [str(osr.transform(index, q, "QoQ")) for q in QUARTERS] == [f"{v:.1f}" for v in QOQ_FF]


def test_rounding_is_half_up_not_bankers():
    assert str(osr.pct_change(1002.5, 1000)) == "0.3"     # 0.25 -> 0.3 (round() gives 0.2)
    assert str(osr.pct_change(1000.5, 1000)) == "0.1"     # 0.05 -> 0.1


# ------------------------------------------------------------ fetcher -------

class _Resp:
    def __init__(self, status, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content or text.encode()


def _getter(page_status=200, page=PAGE, csv=CSV):
    calls = []

    def get(url):
        calls.append(url)
        if url.endswith(".csv"):
            return _Resp(200, content=csv)
        return _Resp(page_status, text=page if page_status == 200 else "<html>Page not found</html>")
    get.calls = calls
    return get


def _anchors(published=None):
    published = published or QOQ_FF
    return pd.DataFrame({"release_dt": pd.to_datetime(RELEASES), "period": QUARTERS,
                         "published": published, "forecast": [None] * 16})


def _run(tmp_path, *, get=None, anchors=None, dry_run=False, force=False, store=None):
    store = store or tmp_path / "official_series.parquet"
    get = get or _getter()
    return of.run_series("statsnz_cpi", dry_run=dry_run, force=force,
                         as_of=pd.Timestamp("2026-11-10"), store_path=store,
                         anchors=anchors if anchors is not None else _anchors(),
                         fetch=lambda q: ADAPTER.fetch(q, get=get)), store


def test_backfill_writes_full_series_after_g1_on_all_16(tmp_path, caplog):
    caplog.set_level("INFO", logger="official_fetch")
    out, store = _run(tmp_path)
    assert out["status"] == "written" and out["target"] == "2026Q3"
    s = pd.read_parquet(store)
    assert list(s.columns) == osr.STORE_COLUMNS
    assert set(s["series_id"]) == {"statsnz_cpi"} and set(s["value_kind"]) == {"index"}
    assert s.set_index("period").loc["2026Q3", "value"] == 1368.0
    assert set(s["source_url"]) == {CSV_URL}
    assert sum("G1 " in r.message and "-> ok" in r.message for r in caplog.records) == 16


def test_up_to_date_makes_zero_requests(tmp_path):
    _run(tmp_path)

    def boom(_q):
        raise AssertionError("network must not be touched")
    out = of.run_series("statsnz_cpi", dry_run=False, force=False, as_of=pd.Timestamp("2026-11-10"),
                        store_path=tmp_path / "official_series.parquet", anchors=_anchors(), fetch=boom)
    assert out == {"status": "up_to_date", "written": 0, "requests": False}


@pytest.mark.parametrize("case", ["page_404", "no_document_link", "quarter_absent"])
def test_not_yet_published_writes_nothing(tmp_path, case):
    anchors = _anchors()
    if case == "page_404":
        get = _getter(page_status=404)
    elif case == "no_document_link":
        get = _getter(page="<html><body>Consumers price index — coming soon</body></html>")
    else:   # a new 2026Q4 anchor, but the CSV still ends at 2026Q3
        get = _getter()
        anchors = pd.concat([anchors, pd.DataFrame({"release_dt": [pd.Timestamp("2027-01-21 21:45")],
                                                    "period": ["2026Q4"], "published": [0.5],
                                                    "forecast": [None]})], ignore_index=True)
    out, store = _run(tmp_path, get=get, anchors=anchors)
    assert out["status"] == "not_yet" and out["written"] == 0
    assert not store.exists()


def test_g1_mismatch_refuses_and_writes_nothing(tmp_path, caplog):
    bad = list(QOQ_FF)
    bad[5] = 0.7                       # 2024Q1 official QoQ is 0.6
    out, store = _run(tmp_path, anchors=_anchors(bad))
    assert out["status"] == "refused" and out["g1"] == ["2024Q1: official QoQ=0.6 != anchor 0.7"]
    assert not store.exists()
    assert any("REFUSED" in r.message for r in caplog.records)


def test_g2_mismatch_refuses_and_leaves_store_untouched(tmp_path):
    _, store = _run(tmp_path)
    s = pd.read_parquet(store)
    s.loc[s["period"] == "2025Q1", "value"] = 1298.0          # a "revision" vs the CSV's 1299
    s.to_parquet(store, index=False)
    before = store.read_bytes()
    out, _ = _run(tmp_path, force=True)
    assert out["status"] == "refused" and out["g2"] == ["2025Q1: CSV=1299.0 != store=1298.0"]
    assert store.read_bytes() == before


def test_g2_only_looks_at_last_5_years(tmp_path):
    _, store = _run(tmp_path)
    s = pd.read_parquet(store)
    s.loc[s["period"] == "2021Q3", "value"] = 1.0             # outside (2021Q3, 2026Q3]
    s.to_parquet(store, index=False)
    assert of.check_g2(_index(), osr.index_map(s, "statsnz_cpi"), "2026Q3") == []


def test_dry_run_force_verifies_last_quarter_without_writing(tmp_path):
    _, store = _run(tmp_path)
    before = store.read_bytes()
    out, _ = _run(tmp_path, dry_run=True, force=True)
    assert out["status"] == "dry_run_ok" and out["target"] == "2026Q3"
    assert store.read_bytes() == before


def test_dry_run_on_empty_store_writes_nothing(tmp_path):
    out, store = _run(tmp_path, dry_run=True)
    assert out["status"] == "dry_run_ok" and not store.exists()


def test_store_is_append_only(tmp_path):
    store = tmp_path / "s.parquet"
    row = {"series_id": "statsnz_cpi", "value_kind": "index", "source_url": "u", "fetched_at_utc": "t"}
    assert osr.append_rows(pd.DataFrame([{**row, "period": "2026Q3", "value": 1368.0}]), store) == 1
    n = osr.append_rows(pd.DataFrame([{**row, "period": "2026Q3", "value": 9999.0},
                                      {**row, "period": "2026Q4", "value": 1375.0}]), store)
    assert n == 1
    s = pd.read_parquet(store).set_index("period")["value"].to_dict()
    assert s == {"2026Q3": 1368.0, "2026Q4": 1375.0}


def test_fetch_error_and_main_never_fail(tmp_path, monkeypatch):
    def raising_get(_url):
        raise ConnectionError("down")
    assert ADAPTER.fetch("2026Q3", get=raising_get).status == "error"

    def explode(*a, **k):
        raise RuntimeError("anything")
    monkeypatch.setattr(of, "run_series", explode)
    assert of.main([]) == 0


# ------------------------------------------------------------ payload -------

def _nzd_catalog():
    return {"categories": {"inflation": {"NZD": hc.load_catalog()["categories"]["inflation"]["NZD"]}}}


def _store_from_fixture(drop: tuple[str, ...] = ()):
    idx = _index()
    return pd.DataFrame([{"series_id": "statsnz_cpi", "period": p, "value": v, "value_kind": "index",
                          "source_url": CSV_URL, "fetched_at_utc": "2026-11-10T00:00:00Z"}
                         for p, v in idx.items() if p not in drop])


def _payload(as_of, store):
    from src.history_render import _build_quarantine_df
    from src.ff_corrections import read_ff_parquet
    from src.ff_scoring import build_matcher
    ff = read_ff_parquet(FF_PARQUET, overrides_path=hc.MANUAL_ACTUALS_OVERRIDES)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    ff = ff[ff["datetime_utc"] <= as_of]
    ind_cfg = hc.load_indicators_cfg()
    cat = _nzd_catalog()
    cache = hc.compute_catalog(ff, ind_cfg, cat, _build_quarantine_df(ff, build_matcher()),
                               as_of=as_of, official_store=store)
    return hc.build_payload(cat, cache, catalog_version="t", ind_cfg=ind_cfg, as_of=as_of), cache, ff, ind_cfg


needs_ff = pytest.mark.skipif(not FF_PARQUET.exists(), reason="requires local data/economic_calendar_ff.parquet")


@needs_ff
def test_payload_nzd_inflation_official_rank1_qoq_rank2_linked():
    as_of = pd.Timestamp("2026-11-10 12:00")
    payload, cache, ff, ind_cfg = _payload(as_of, _store_from_fixture())
    nzd = payload["categories"]["inflation"]["NZD"]
    off, qoq = nzd
    assert (off["rank"], off["role"], off["indicator_key"]) == (1, "official", "cpi_yoy_official")
    assert off["source"] == "official" and off["scored"] is False and off["official_series"] == "statsnz_cpi"
    assert off["comparable_with_target"] is True and off["display_label"] == "CPI (YoY)"
    assert (off["target"]["kind"], off["target"]["low"], off["target"]["high"]) == ("band", 1.0, 3.0)
    assert off["has_data"] and not off["stale"]
    pts = off["window_options"]["max"]["points"]
    assert len(pts) == 16
    assert [p["period"] for p in pts] == QUARTERS
    assert [f"{p['actual']:.1f}" for p in pts] == YOY
    assert [f"{p['previous']:.1f}" for p in pts[1:]] == YOY[:-1]
    assert f"{pts[0]['previous']:.1f}" == "7.2"          # 2022Q3 y/y
    assert all(p["forecast"] is None and p["z"] is None and p["bucket"] is None
               and p["score_status"] == "official" and p["source_ref"] == "stats.govt.nz" for p in pts)

    # rank 2: QoQ entry identical to a payload built WITHOUT the official entry (except rank)
    assert (qoq["rank"], qoq["indicator_key"], qoq["display_label"]) == (2, "cpi_yoy", "CPI (QoQ)")
    cat_wo = {"categories": {"inflation": {"NZD": [e for e in _nzd_catalog()["categories"]["inflation"]["NZD"]
                                                     if not e.get("official_series")]}}}
    alone = hc.build_payload(cat_wo, cache, catalog_version="t", ind_cfg=ind_cfg, as_of=as_of)
    alone_qoq = alone["categories"]["inflation"]["NZD"][0]
    assert {**alone_qoq, "rank": 2} == qoq

    # linked == the QoQ point of the same publication (as published)
    qpts = {p["release_dt"]: p for p in qoq["window_options"]["max"]["points"]}
    for p in pts:
        q = qpts[p["release_dt"]]
        as_pub = q["revised_from"] if q["revised_from"] is not None else q["actual"]
        assert p["linked"] == {"indicator_key": "cpi_yoy", "label": "CPI (QoQ)",
                               "actual": as_pub, "forecast": q["forecast"]}
    assert [p["linked"]["actual"] for p in pts] == QOQ_FF
    assert payload["health"]["official_pending"] == []


@needs_ff
def test_official_pending_only_after_48h():
    store = _store_from_fixture(drop=("2026Q3",))
    rel = pd.Timestamp("2026-10-21 21:45")
    p47, _, _, _ = _payload(rel + pd.Timedelta(hours=47), store)
    p49, _, _, _ = _payload(rel + pd.Timedelta(hours=49), store)
    assert p47["health"]["official_pending"] == []
    pend = p49["health"]["official_pending"]
    assert len(pend) == 1 and pend[0]["period"] == "2026Q3" and pend[0]["indicator_key"] == "cpi_yoy_official"
    pts = p49["categories"]["inflation"]["NZD"][0]["window_options"]["max"]["points"]
    assert len(pts) == 15 and pts[-1]["period"] == "2026Q2"


# ---------------------------------------------------------- isolation -------

def test_scoring_modules_do_not_import_official_layer():
    code = ("import sys, src.economic_compute, src.economic_render, src.ff_scoring;"
            "bad=[m for m in ('src.official_series','src.official_sources','src.official_fetch') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for mod in ("economic_compute", "economic_render", "ff_scoring"):
        src = (ROOT / "src" / f"{mod}.py").read_text()
        assert "official_series" not in src and "official_sources" not in src and "official_fetch" not in src
