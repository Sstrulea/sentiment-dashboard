"""2y source adapters (audit 2026-09-23, points 1.3-1.5) — offline, fixtures only."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import rate_fetch, rate_migrations
from src.rate_sources import (BocValetSource, BoeSource, DbnomicsFedSource, EcbSource,
                              FredSource, MofJgbSource, RbaSource, TenorMismatch)

FIX = Path(__file__).parent / "fixtures" / "rates"


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status
        self.text = content.decode("latin-1")


def _serve(monkeypatch, src, routes: dict[str, bytes | None]):
    """Route src._get(url) to fixture bytes; None -> unreachable."""
    def fake_get(url, extra_headers=None, retries=0):
        body = routes.get(url)
        if body is None:
            src.last_status, src.last_note = "UNREACHABLE", "HTTP 404"
            return None
        return _Resp(body)
    monkeypatch.setattr(src, "_get", fake_get)


# --- 1.3 MoF JGB ------------------------------------------------------------

def test_mof_merges_history_and_current_month(monkeypatch):
    src = MofJgbSource()
    _serve(monkeypatch, src, {
        src.URL_HISTORY: (FIX / "mof_jgbcm_all.csv").read_bytes(),
        src.URL_CURRENT: (FIX / "mof_jgbcm_current.csv").read_bytes(),
    })
    s = src.fetch("JPY")
    assert s is not None, src.last_note
    assert s.latest_date == date(2026, 9, 17) and s.latest_value == 1.868
    d = dict(s.points)
    assert d[date(2026, 8, 31)] == 1.743      # last day of the history file
    assert d[date(2026, 9, 1)] == 1.802       # first day of the current month
    assert len(d) == len(s.points)            # deduped on date


def test_mof_current_month_wins_a_shared_date():
    merged = MofJgbSource.merge([(date(2026, 9, 1), 9.9), (date(2026, 8, 31), 1.7)],
                                [(date(2026, 9, 1), 1.802)])
    assert merged == [(date(2026, 8, 31), 1.7), (date(2026, 9, 1), 1.802)]


def test_mof_without_current_month_ends_at_month_end(monkeypatch):
    src = MofJgbSource()
    _serve(monkeypatch, src, {src.URL_HISTORY: (FIX / "mof_jgbcm_all.csv").read_bytes()})
    s = src.fetch("JPY")
    assert s is not None and s.latest_date == date(2026, 8, 31)


def test_mof_reads_the_2y_column_by_name():
    pts = MofJgbSource().parse_csv((FIX / "mof_jgbcm_current.csv").read_bytes())
    # 1年 is 1.581 and 3年 2.001 on R8.9.17: only 2年 is 1.868
    assert pts[-1] == (date(2026, 9, 17), 1.868)


# --- 1.4 BoE GLC nominal spot 2.0y --------------------------------------------


def _zip(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, payload)
    return buf.getvalue()


def test_boe_reads_2y_spot_from_current_month(monkeypatch):
    src = BoeSource()
    xlsx = (FIX / "boe_glc_nominal_current_month.xlsx").read_bytes()
    _serve(monkeypatch, src, {src.URL_LATEST: _zip(src.MEMBER_LATEST, xlsx)})
    s = src.fetch("GBP")
    assert s is not None, src.last_note
    d = dict(s.points)
    assert round(d[date(2026, 9, 18)], 3) == 4.658     # control values
    assert round(d[date(2026, 9, 21)], 3) == 4.561
    assert s.latest_date == date(2026, 9, 21) and s.source == "boe_glc"


def test_boe_without_2y_maturity_is_refused():
    with pytest.raises(ValueError, match="2.0-year"):
        BoeSource.parse_spot_xlsx((FIX / "boe_glc_nominal_no_2y.xlsx").read_bytes())


def test_incremental_source_is_assessed_with_its_stored_history():
    src = BoeSource()
    new = src.parse_spot_xlsx((FIX / "boe_glc_nominal_current_month.xlsx").read_bytes())
    from src.rate_sources import YieldSeries, assess
    s = YieldSeries("GBP", "boe_glc", "2y", "daily", new)
    days = pd.bdate_range("2023-01-02", "2026-08-28")
    stored = pd.DataFrame({"currency": "GBP", "date": days, "tenor": "2y",
                           "yield_pct": 4.0, "source": "boe_glc"})
    other = stored.assign(source="boe")        # legacy 5y rows never join
    merged = rate_fetch._with_stored_history(s, pd.concat([stored, other]))
    assert merged.n_points == len(days) + len(new)
    assert assess(s, date(2026, 9, 22)).status == "SHORT-HIST"
    assert assess(merged, date(2026, 9, 22)).qualifies


def _rates(gbp_source: str, start: str) -> pd.DataFrame:
    days = pd.bdate_range(start, "2026-09-18")
    gbp = pd.DataFrame({"currency": "GBP", "date": days, "tenor": "2y",
                        "yield_pct": 3.9, "source": gbp_source})
    usd = pd.DataFrame({"currency": "USD", "date": days, "tenor": "2y",
                        "yield_pct": 3.5, "source": "fred"})
    return pd.concat([gbp, usd], ignore_index=True)


def test_gbp_migration_drops_legacy_and_backfills():
    hist = [(d.date(), 4.1) for d in pd.bdate_range("2016-01-04", "2026-08-28")]
    out, rep = rate_migrations.migrate_gbp_2y_glc(_rates("boe", "2018-01-02"),
                                                  lambda since: hist)
    gbp = out[out.currency == "GBP"]
    assert set(gbp.source) == {"boe_glc"} and rep["status"] == "migrated"
    assert gbp.date.min() == pd.Timestamp("2016-01-04")
    assert len(gbp) == len(hist)
    assert len(out[out.currency == "USD"]) == len(_rates("boe", "2018-01-02").query("currency == 'USD'"))
    # idempotent: the second pass is a no-op and never calls the download
    out2, rep2 = rate_migrations.migrate_gbp_2y_glc(out, lambda since: 1 / 0)
    assert rep2["status"] == "noop" and out2.equals(out)


def test_gbp_migration_failure_blocks_gbp_write(tmp_path):
    p = tmp_path / "rates.parquet"
    _rates("boe", "2018-01-02").to_parquet(p, index=False)

    def boom(since):
        raise RuntimeError("download failed")
    assert rate_migrations.ensure_all(p, history=boom) == {"GBP": False}
    assert set(pd.read_parquet(p).query("currency == 'GBP'").source) == {"boe"}  # untouched


# --- 1.5 tenor guard: every 2y adapter reads the maturity from source metadata ---


class _JsonResp(_Resp):
    def json(self):
        return json.loads(self.content)


def _serve_one(monkeypatch, src, body: bytes, as_json: bool = False):
    resp = (_JsonResp if as_json else _Resp)(body)
    monkeypatch.setattr(src, "_get", lambda url, extra_headers=None, retries=0: resp)


FRED = (FIX / "fred_dgs2.csv").read_bytes()
ECB = (FIX / "ecb_sr_2y.csv").read_bytes()
BOC = (FIX / "boc_2yr.json").read_bytes()
DBN = (FIX / "dbnomics_sveny02.json").read_bytes()
RBA = (FIX / "rba_f2_head.csv").read_bytes()   # layout of the published F2 (web.archive.org copy)

CASES = [
    # (adapter, currency, good fixture, json?, same payload saying another tenor)
    (FredSource, "USD", FRED, False, FRED.replace(b"DGS2", b"DGS5")),
    (EcbSource, "EUR", ECB, False, ECB.replace(b"SR_2Y", b"SR_5Y")),
    (BocValetSource, "CAD", BOC, True,
     BOC.replace(b"Benchmark bond yield, 2-year", b"Benchmark bond yield, 5-year")),
    (DbnomicsFedSource, "USD", DBN, True, DBN.replace(b'"SVENY02"', b'"SVENY05"')),
    (RbaSource, "AUD", RBA, False, (FIX / "rba_f2_no_2y.csv").read_bytes()),
]


@pytest.mark.parametrize("cls,ccy,good,as_json,bad", CASES, ids=[c[0].__name__ for c in CASES])
def test_adapter_accepts_its_2y_series(monkeypatch, cls, ccy, good, as_json, bad):
    src = cls()
    _serve_one(monkeypatch, src, good, as_json)
    s = src.fetch(ccy)
    assert s is not None and s.tenor == "2y" and s.n_points > 0, src.last_note


@pytest.mark.parametrize("cls,ccy,good,as_json,bad", CASES, ids=[c[0].__name__ for c in CASES])
def test_adapter_raises_when_source_metadata_is_not_2y(monkeypatch, cls, ccy, good, as_json, bad):
    src = cls()
    _serve_one(monkeypatch, src, bad, as_json)
    with pytest.raises(TenorMismatch):
        src.fetch(ccy)
    assert src.last_status == "TENOR-MISMATCH"


def test_mof_raises_without_2nen_column(monkeypatch):
    src = MofJgbSource()
    bad = (FIX / "mof_jgbcm_current.csv").read_bytes().replace("2年".encode("shift_jis"),
                                                               "5年".encode("shift_jis"), 1)
    _serve(monkeypatch, src, {src.URL_HISTORY: bad})
    with pytest.raises(TenorMismatch):
        src.fetch("JPY")


def test_boe_raises_without_2y_maturity(monkeypatch):
    src = BoeSource()
    xlsx = (FIX / "boe_glc_nominal_no_2y.xlsx").read_bytes()
    _serve(monkeypatch, src, {src.URL_LATEST: _zip(src.MEMBER_LATEST, xlsx)})
    with pytest.raises(TenorMismatch):
        src.fetch("GBP")


def test_tenor_mismatch_is_not_masked_by_a_fallback(monkeypatch):
    """EUR: ECB says SR_5Y -> the currency is not written; Stooq is never tried."""
    ecb, stooq = rate_fetch.ALL_SOURCES["ecb"], rate_fetch.ALL_SOURCES["stooq"]
    _serve_one(monkeypatch, ecb, ECB.replace(b"SR_2Y", b"SR_5Y"))
    monkeypatch.setattr(stooq, "fetch", lambda ccy: pytest.fail("fallback must not run"))
    assert rate_fetch.fetch_currency("EUR", date(2026, 9, 22)) is None


# --- 1.5 freshness.rates ----------------------------------------------------------

def test_rates_freshness_lag_and_any_stale(monkeypatch, tmp_path):
    from src import economic_render as er
    p = tmp_path / "rates.parquet"
    rows = [("USD", "2026-09-21", "fred"), ("JPY", "2026-08-31", "mof_jgb"),
            ("GBP", "2026-09-21", "boe_glc"), ("GBP", "2026-09-30", "boe_glc")]  # future row ignored
    pd.DataFrame([{"currency": c, "date": pd.Timestamp(d), "tenor": "2y",
                   "yield_pct": 1.0, "source": s} for c, d, s in rows]).to_parquet(p)
    monkeypatch.setattr(er, "RATES_PARQUET", p)
    out = er._rates_freshness(pd.Timestamp("2026-09-23T07:06:11"))
    assert out["per_currency"]["USD"] == {"last_update": "2026-09-21", "lag_bd": 2,
                                          "source": "fred", "max_lag_bd": 7, "stale": False}
    assert out["per_currency"]["GBP"]["last_update"] == "2026-09-21"
    assert out["per_currency"]["JPY"]["lag_bd"] == 17 and out["per_currency"]["JPY"]["stale"]
    assert out["stale_currencies"] == ["JPY"] and out["stale"] is True
    assert set(out["missing"]) == {"EUR", "AUD", "NZD", "CAD", "CHF"}

    monkeypatch.setattr(er, "_trend_enabled", lambda cfg=None: False)
    fr = er._freshness(pd.Timestamp("2026-09-23T07:06:11"), trend_enabled=False)
    assert fr["rates"]["stale"] and fr["any_stale"]


# --- 4C per-source threshold: daily 7, RBA F2 (weekly) 10 --------------------------

@pytest.mark.parametrize("ccy,source,last,lag,stale", [
    ("AUD", "rba", "2026-09-10", 9, False),     # weekly F2, one late Friday: still fresh
    ("AUD", "rba", "2026-09-08", 11, True),
    ("USD", "fred", "2026-09-11", 8, True),     # daily source keeps 7
    ("USD", "fred", "2026-09-14", 7, False),
])
def test_rates_threshold_is_per_source_and_shared(monkeypatch, tmp_path, ccy, source, last, lag, stale):
    """freshness.rates and rate_compute read the same per-source threshold."""
    from src import economic_render as er
    from src.rate_compute import compute_rate_scores
    as_of = pd.Timestamp("2026-09-23T07:06:11")
    dates = pd.bdate_range(end=pd.Timestamp(last), periods=300)
    df = pd.DataFrame({"currency": ccy, "date": dates, "tenor": "2y",
                       "yield_pct": np.linspace(1.0, 2.0, len(dates)), "source": source})
    p = tmp_path / "rates.parquet"
    df.to_parquet(p)
    monkeypatch.setattr(er, "RATES_PARQUET", p)
    fr = er._rates_freshness(as_of)["per_currency"][ccy]
    assert (fr["lag_bd"], fr["stale"]) == (lag, stale)
    assert compute_rate_scores(df, as_of=as_of.date())[ccy].stale is stale


# --- F2 BoE month-boundary gap fill -------------------------------------------

from src import rate_gapfill

HIST_ZIP = (FIX / "boe_glcnominalddata_2025_to_present_aug2026.zip").read_bytes()
CUR_ZIP = _zip(BoeSource.MEMBER_LATEST, (FIX / "boe_glc_nominal_current_month.xlsx").read_bytes())


def _stored_gbp(until: str) -> pd.DataFrame:
    days = pd.bdate_range("2023-01-02", until)
    return pd.DataFrame({"currency": "GBP", "date": days, "tenor": "2y",
                         "yield_pct": 4.0, "source": "boe_glc"})


@pytest.fixture
def gap_env(monkeypatch, tmp_path):
    """rate_fetch against fixtures: current month = Sep 2026, history = Aug 2026."""
    state = tmp_path / "gap.json"
    monkeypatch.setattr(rate_fetch, "gap_load_state", lambda: rate_gapfill.load_state(state))
    monkeypatch.setattr(rate_fetch, "gap_save_state", lambda st: rate_gapfill.save_state(st, state))
    boe = rate_fetch.ALL_SOURCES["boe_glc"]
    calls = []

    def fake_get(url, extra_headers=None, retries=0):
        calls.append(url)
        return _Resp(HIST_ZIP if url == boe.URL_HISTORY else CUR_ZIP)
    monkeypatch.setattr(boe, "_get", fake_get)
    return state, calls, boe


def test_gap_at_month_change_is_filled_from_the_archive(gap_env):
    state, calls, boe = gap_env
    s = rate_fetch.fetch_currency("GBP", date(2026, 9, 22), _stored_gbp("2026-08-25"))
    d = dict(s.points)
    assert round(d[date(2026, 8, 26)], 3) == 4.25 and round(d[date(2026, 8, 28)], 3) == 4.319
    assert date(2026, 8, 31) not in d                     # UK bank holiday
    st = rate_gapfill.load_state(state)["boe_glc"]
    assert st["confirmed_no_data"] == ["2026-08-31"]
    assert calls.count(boe.URL_HISTORY) == 1


def test_gap_fill_at_most_once_per_day_and_idempotent(gap_env):
    state, calls, boe = gap_env
    stored = _stored_gbp("2026-08-25")
    rate_fetch.fetch_currency("GBP", date(2026, 9, 22), stored)
    rate_fetch.fetch_currency("GBP", date(2026, 9, 22), stored)      # same day: no 2nd download
    assert calls.count(boe.URL_HISTORY) == 1
    # once filled (stored up to 08-28) only the confirmed holiday is left -> no download ever
    rate_fetch.fetch_currency("GBP", date(2026, 9, 23), _stored_gbp("2026-08-28"))
    assert calls.count(boe.URL_HISTORY) == 1


def test_gap_the_archive_does_not_cover_yet_is_retried_next_day(gap_env):
    """The archive does not cover the gap yet (built 09-03): nothing added,
    nothing confirmed, no retry the same day, filled the next day."""
    state, calls, boe = gap_env
    added, st = rate_gapfill.fill_gap("x", [date(2026, 9, 25)], [(date(2026, 10, 1), 1.0)],
                                      date(2026, 10, 1), {}, lambda since: ([], date(2026, 9, 2)))
    assert added == [] and st["x"]["confirmed_no_data"] == []   # 09-28..09-30 beyond the archive
    again, _ = rate_gapfill.fill_gap("x", [date(2026, 9, 25)], [(date(2026, 10, 1), 1.0)],
                                     date(2026, 10, 1), st, lambda since: 1 / 0)
    assert again == []                                            # not retried the same day
    later, st2 = rate_gapfill.fill_gap("x", [date(2026, 9, 25)], [(date(2026, 10, 1), 1.0)],
                                       date(2026, 10, 2), st,
                                       lambda since: ([(date(2026, 9, 28), 2.0), (date(2026, 9, 30), 2.1)],
                                                      date(2026, 10, 1)))
    assert later == [(date(2026, 9, 28), 2.0), (date(2026, 9, 30), 2.1)]
    assert st2["x"]["confirmed_no_data"] == ["2026-09-29"]
