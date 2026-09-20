"""Official rate series (phase 1B-1): deterministic parsers over real cut fixtures, provider adapters with mocked
HTTP, the monthly official_series store (key (series_id, date)), idempotence and failure isolation. No network."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from requests.structures import CaseInsensitiveDict

from src import cb_collect as cc
from src.cb_sources.official import (PROVIDERS, BisSeries, BocSeries, BoeSeries, BojSeries, EcbSeries, FredSeries,
                                     RbaSeries, SnbSeries, load_official, parse_bis, parse_boe, parse_boj,
                                     parse_ecb, parse_fred, parse_rba_f1, parse_snb, parse_valet)

FIX = Path(__file__).parent / "fixtures" / "cb"
NOW = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
D = date
ALL = D(2000, 1, 1)


def text(name: str) -> str:
    return (FIX / name).read_text()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_the_18_requested_series_are_configured_with_their_roles():
    cfg = load_official()
    ids = set(cfg["series"])
    assert ids == {"fred:DFEDTARL", "fred:DFEDTARU", "fred:EFFR", "fred:SOFR", "ecb:DFR", "ecb:MRO", "ecb:ESTR",
                   "boe:IUDBEDR", "boe:IUDSOIA", "boj:STRDCLUCON", "bis:JP", "boc:V39079", "boc:AVG.INTWO",
                   "rba:FIRMMCRTD", "rba:FIRMMCRID", "bis:NZ", "snb:LZ", "snb:SARON"}
    assert cfg["meta"]["backfill_from"] == D(2025, 9, 1)
    assert set(cfg["providers"]) == set(PROVIDERS) == {"fred", "ecb", "boe", "boj", "boc", "rba", "bis", "snb"}
    assert {s["provider"] for s in cfg["series"].values()} == set(cfg["providers"])
    assert cfg["series"]["bis:JP"]["role"] == "policy_bis" and cfg["series"]["bis:NZ"]["role"] == "policy_bis"
    assert cfg["series"]["ecb:MRO"]["role"] == "policy_mro"
    assert not any("FIRMMOIS" in json.dumps(s) for s in cfg["series"].values())        # RBA OIS stopped in 2022-12
    for sid, s in cfg["series"].items():
        assert sid.startswith(s["provider"] + ":") and s["unit"] == "percent", sid


def test_banks_reference_configured_series():
    import yaml
    banks = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "central_banks.yaml").read_text())["banks"]
    series = load_official()["series"]
    for b, c in banks.items():
        for role, sid in c["policy_rate"]["official"].items():
            assert sid in series and series[sid]["currency"] == b, (b, sid)
        bench = (c["overnight_benchmark"] or {}).get("series_id")
        if bench:
            assert series[bench]["currency"] == b and series[bench]["role"] == "overnight", (b, bench)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_fred_upper_limit_and_missing_observations():
    assert parse_fred(text("fred_dfedtaru_cut.csv"), ALL) == [
        (D(2026, 9, 14), 3.75), (D(2026, 9, 15), 3.75), (D(2026, 9, 16), 3.75), (D(2026, 9, 17), 4.0),
        (D(2026, 9, 18), 4.0), (D(2026, 9, 19), 4.0)]
    sofr = parse_fred(text("fred_sofr_cut.csv"), ALL)
    assert D(2026, 9, 7) not in [d for d, _ in sofr] and len(sofr) == 5             # Labor Day: FRED's empty cell is skipped
    assert parse_fred(text("fred_dfedtaru_cut.csv"), D(2026, 9, 18)) == [(D(2026, 9, 18), 4.0), (D(2026, 9, 19), 4.0)]


def test_ecb_dfr_and_the_2024_spread_change():
    assert parse_ecb(text("ecb_dfr_cut.csv"), ALL) == [
        (D(2026, 9, 14), 2.25), (D(2026, 9, 15), 2.25), (D(2026, 9, 16), 2.5), (D(2026, 9, 17), 2.5), (D(2026, 9, 18), 2.5)]
    dfr, mro = dict(parse_ecb(text("ecb_dfr_2024_cut.csv"), ALL)), dict(parse_ecb(text("ecb_mro_2024_cut.csv"), ALL))
    assert round(mro[D(2024, 9, 17)] - dfr[D(2024, 9, 17)], 2) == 0.50 and round(mro[D(2024, 9, 18)] - dfr[D(2024, 9, 18)], 2) == 0.15


def test_boe_reads_two_columns_from_one_csv():
    got = parse_boe(text("boe_iadb_cut.csv"), ALL)
    assert set(got) == {"IUDBEDR", "IUDSOIA"} and [v for _, v in got["IUDBEDR"]] == [3.75] * 6
    assert got["IUDSOIA"][2] == (D(2026, 9, 14), 3.7312) and got["IUDBEDR"][0][0] == D(2026, 9, 10)


def test_boj_call_rate_skips_weekend_nulls():
    got = parse_boj(json.loads(text("boj_call_cut.json")), ALL)
    assert got == [(D(2026, 9, 11), 0.977), (D(2026, 9, 14), 0.977), (D(2026, 9, 15), 0.978), (D(2026, 9, 16), 0.977)]


def test_valet_policy_and_corra():
    got = parse_valet(json.loads(text("valet_policy_cut.json")), ["V39079", "AVG.INTWO"], ALL)
    assert got["V39079"] == [(D(2026, 9, d), 2.25) for d in (14, 15, 16, 17)]
    assert got["AVG.INTWO"] == [(D(2026, 9, 14), 2.28), (D(2026, 9, 15), 2.29), (D(2026, 9, 16), 2.29), (D(2026, 9, 17), 2.29)]


def test_rba_f1_target_and_overnight():
    got = parse_rba_f1(text("rba_f1_cut.csv"), ["FIRMMCRTD", "FIRMMCRID"], ALL)
    assert got["FIRMMCRTD"][-1] == (D(2026, 9, 17), 4.35) and got["FIRMMCRID"][-1] == (D(2026, 9, 17), 4.35)
    assert max(d for d, _ in got["FIRMMCRTD"]) == D(2026, 9, 17)                        # the still-empty 18 Sep row is skipped
    with pytest.raises(ValueError, match="F1 ids missing"):
        parse_rba_f1("Series ID,A\n", ["FIRMMOIS1D"], ALL)                                # the OIS columns are not collected


def test_bis_shows_the_boj_hike_on_the_effective_date_and_the_ocr_hike_a_day_late():
    got = parse_bis(text("bis_cbpol_cut.csv"), ALL)
    assert got["JP"] == [(D(2026, 6, 15), 0.75), (D(2026, 6, 16), 0.75), (D(2026, 6, 17), 1.0), (D(2026, 9, 14), 1.0), (D(2026, 9, 15), 1.0)]
    # RBNZ decided on 2 Sep 2026; BIS books the new OCR on the 3rd (its own convention)
    assert got["NZ"][:3] == [(D(2026, 9, 1), 2.5), (D(2026, 9, 2), 2.5), (D(2026, 9, 3), 2.75)]


def test_snb_zero_policy_rate_is_a_value():
    got = parse_snb(text("snb_cube_cut.csv"), ["LZ", "SARON"], ALL)
    assert got["LZ"] == [(D(2026, 6, 17), 0.0), (D(2026, 6, 18), 0.0), (D(2026, 6, 19), 0.0), (D(2026, 9, 10), 0.0), (D(2026, 9, 11), 0.0)]
    assert got["SARON"][0] == (D(2026, 6, 17), -0.04)
    with pytest.raises(ValueError, match="no Date header"):
        parse_snb("garbage", ["LZ"], ALL)


# ---------------------------------------------------------------------------
# Adapters over mocked HTTP
# ---------------------------------------------------------------------------

class Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content if isinstance(content, bytes) else content.encode(), CaseInsensitiveDict(headers or {})

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("src.rate_sources.time.sleep", lambda s: None)


def router(monkeypatch, overrides=None):
    """Serve every provider from the fixtures; `overrides` maps a URL needle to a Resp / callable."""
    calls = []
    table = [
        ("id=DFEDTARU", "fred_dfedtaru_cut.csv", {}), ("id=DFEDTARL", "fred_dfedtaru_cut.csv", {}),
        ("id=EFFR", "fred_sofr_cut.csv", {}), ("id=SOFR", "fred_sofr_cut.csv", {}),
        ("KR.DFR.LEV", "ecb_dfr_cut.csv", {}), ("KR.MRR_FR.LEV", "ecb_dfr_cut.csv", {}), ("EST/B.EU000A2X2A25.WT", "ecb_dfr_cut.csv", {}),
        ("fromshowcolumns.asp", "boe_iadb_cut.csv", {}), ("getDataCode", "boj_call_cut.json", {}),
        ("valet/observations", "valet_policy_cut.json", {}), ("f1-data.csv", "rba_f1_cut.csv", {"Last-Modified": "Thu, 17 Sep 2026 23:00:50 GMT"}),
        ("WS_CBPOL", "bis_cbpol_cut.csv", {}), ("snbgwdzid", "snb_cube_cut.csv", {}),
    ]

    def get(url, headers=None, timeout=None, **kw):
        h = headers or {}
        calls.append((url, dict(h)))
        for needle, resp in (overrides or {}).items():
            if needle in url:
                return resp(h) if callable(resp) else resp
        for needle, name, hdrs in table:
            if needle in url:
                if h.get("If-Modified-Since") and h["If-Modified-Since"] == hdrs.get("Last-Modified"):
                    return Resp(304)
                return Resp(200, (FIX / name).read_bytes(), hdrs)
        raise AssertionError(f"unrouted {url}")
    monkeypatch.setattr("src.rate_sources.requests.get", get)
    return calls


def mk(cls, **kw):
    return cls(now=lambda: NOW, **kw)


def test_fred_adapter_keeps_a_failed_series_out_of_the_way_of_the_others(monkeypatch):
    router(monkeypatch, {"id=EFFR": Resp(503), "id=DFEDTARL": Resp(200, "<html>Just a moment... requires JavaScript</html>")})
    s = mk(FredSeries)
    res = s.fetch(since=D(2026, 9, 1))
    assert {o.series_id for o in res.obs} == {"fred:DFEDTARU", "fred:SOFR"}
    assert set(res.failed) == {"fred:EFFR", "fred:DFEDTARL"} and "503" in res.failed["fred:EFFR"] and "BOT-WALL" in res.failed["fred:DFEDTARL"]
    assert all(o.currency == "USD" and o.unit == "percent" and o.fetched_at == NOW for o in res.obs)


def test_a_provider_that_is_down_returns_none_with_a_status(monkeypatch):
    router(monkeypatch, {"WS_CBPOL": Resp(500)})
    s = mk(BisSeries)
    assert s.fetch(since=D(2026, 9, 1)) is None and s.last_status == "UNREACHABLE" and "500" in s.last_note


def test_adapter_never_raises_on_a_layout_change(monkeypatch):
    router(monkeypatch, {"snbgwdzid": Resp(200, "<no cube here>\nfoo;bar\n")})
    s = mk(SnbSeries)
    assert s.fetch(since=D(2026, 9, 1)) is None and s.last_status in ("UNREACHABLE", "PARSE-FAIL")


def test_rba_uses_a_non_browser_ua_and_last_modified(monkeypatch):
    calls = router(monkeypatch)
    s = mk(RbaSeries)
    res = s.fetch(since=D(2026, 9, 1))
    assert res.status == "ok" and res.state["last_modified"].startswith("Thu, 17 Sep") and "Mozilla" not in calls[0][1]["User-Agent"]
    again = mk(RbaSeries).fetch(since=D(2026, 9, 1), state=res.state)
    assert again.status == "not_modified" and again.obs == [] and calls[-1][1]["If-Modified-Since"] == res.state["last_modified"]


def test_one_request_serves_several_series(monkeypatch):
    calls = router(monkeypatch)
    for cls, n_series in ((BoeSeries, 2), (BocSeries, 2), (BisSeries, 2), (SnbSeries, 2), (RbaSeries, 2)):
        calls.clear()
        res = mk(cls).fetch(since=D(2026, 1, 1))
        assert len(calls) == 1 and len({o.series_id for o in res.obs}) == n_series, cls.__name__
    assert len(mk(EcbSeries).fetch(since=D(2026, 1, 1)).obs) == 15 and len(calls) == 4         # 3 series, 5 rows each
    assert mk(BojSeries).fetch(since=D(2026, 9, 15)).obs[0].date == D(2026, 9, 15)


def test_since_is_passed_to_the_provider_urls(monkeypatch):
    calls = router(monkeypatch)
    mk(FredSeries).fetch(since=D(2025, 9, 1))
    mk(BoeSeries).fetch(since=D(2025, 9, 1))
    mk(BojSeries).fetch(since=D(2025, 9, 1))
    urls = [u for u, _ in calls]
    assert any("cosd=2025-09-01" in u for u in urls) and any("Datefrom=01/Sep/2025" in u for u in urls) and any("startDate=202509" in u for u in urls)


# ---------------------------------------------------------------------------
# Collector: official_series monthly store
# ---------------------------------------------------------------------------

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def official_rows(paths):
    return sorted(cc.load_official_store(paths).values(), key=lambda r: (r["series_id"], r["date"]))


def fingerprint(paths):
    d = paths.dir / "official_series"
    return {f.name: sha(f) for f in sorted(d.glob("*.parquet"))} if d.exists() else {}


def test_run_official_writes_monthly_partitions_keyed_by_series_and_date(tmp_path, monkeypatch):
    router(monkeypatch)
    paths = cc.Paths(tmp_path / "cb")
    reps = cc.run_official(paths, now=lambda: NOW, backfill_from=D(2025, 9, 1))
    assert {r.id for r in reps} == {f"official:{p}" for p in PROVIDERS} and all(r.status == "ok" for r in reps), [(r.id, r.status, r.note) for r in reps]
    files = sorted(f.name for f in (paths.dir / "official_series").glob("*"))
    assert files == ["official_series_2026-06.parquet", "official_series_2026-09.parquet"] and not (paths.dir / "official_series.parquet").exists()
    t = pq.read_table(paths.dir / "official_series" / "official_series_2026-09.parquet")
    assert t.schema.names == ["series_id", "currency", "date", "value", "unit", "fetched_at"]
    rows = official_rows(paths)
    keys = [(r["series_id"], r["date"]) for r in rows]
    assert len(keys) == len(set(keys))                                                    # one row per (series_id, date)
    assert {r["date"].strftime("%Y-%m") for r in rows} == {"2026-06", "2026-09"}          # partition = month of the date
    by = {(r["series_id"], r["date"]): r["value"] for r in rows}
    assert by[("snb:LZ", D(2026, 9, 11))] == 0.0                                          # CHF 0.00 kept as a value
    assert by[("bis:NZ", D(2026, 9, 3))] == 2.75 and by[("bis:JP", D(2026, 6, 17))] == 1.0
    assert {r.id: r.asof_max for r in reps}["official:snb"] == D(2026, 9, 11)


def test_two_official_runs_are_idempotent_and_byte_identical(tmp_path, monkeypatch):
    router(monkeypatch)
    paths = cc.Paths(tmp_path / "cb")
    cc.run_official(paths, now=lambda: NOW, backfill_from=D(2025, 9, 1))
    first = (fingerprint(paths), sha(paths.state))
    mt = {f.name: f.stat().st_mtime_ns for f in (paths.dir / "official_series").glob("*")}
    reps = cc.run_official(paths, now=lambda: NOW + timedelta(hours=2), lookback_days=10 ** 4)
    assert all(r.merge.new == 0 and r.merge.updated == 0 for r in reps)
    assert {r.id for r in reps if r.status == "not_modified"} == {"official:rba"}          # the conditional GET came back 304
    assert (fingerprint(paths), sha(paths.state)) == first
    assert {f.name: f.stat().st_mtime_ns for f in (paths.dir / "official_series").glob("*")} == mt


def test_a_revised_value_is_a_last_write_wins_update_and_history_survives(tmp_path, monkeypatch):
    router(monkeypatch)
    paths = cc.Paths(tmp_path / "cb")
    cc.run_official(paths, now=lambda: NOW, backfill_from=D(2025, 9, 1), only=["fred"])
    router(monkeypatch, {"id=SOFR": Resp(200, "observation_date,SOFR\n2026-09-10,3.65\n")})       # SOFR revised, window moved
    (rep,) = cc.run_official(paths, now=lambda: NOW + timedelta(days=1), only=["fred"], backfill_from=D(2026, 9, 10))
    assert rep.merge.updated == 1
    by = {(r["series_id"], r["date"]): r["value"] for r in official_rows(paths)}
    assert by[("fred:SOFR", D(2026, 9, 10))] == 3.65 and by[("fred:SOFR", D(2026, 9, 3))] == 3.66      # older rows untouched


def test_a_failed_provider_does_not_stop_the_rest_and_exit_code_follows_the_market_rule(tmp_path, monkeypatch):
    router(monkeypatch, {"WS_CBPOL": Resp(503), "data-api.ecb.europa.eu": Resp(200, "<html>challenge</html>")})
    paths = cc.Paths(tmp_path / "cb")
    reps = cc.run_official(paths, now=lambda: NOW, backfill_from=D(2025, 9, 1))
    st = {r.id: r.status for r in reps}
    assert st["official:bis"] == st["official:ecb"] == "FAILED" and st["official:fred"] == "ok" and cc.exit_code(reps) == 0
    assert {r["series_id"].split(":")[0] for r in official_rows(paths)} == {"fred", "boe", "boj", "boc", "rba", "snb"}


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        cc.run_official(cc.Paths(tmp_path / "cb"), only=["nope"], now=lambda: NOW)


def test_committed_official_series_partitions_are_well_formed():
    d = Path(__file__).resolve().parents[1] / "data" / "cb" / "official_series"
    files = sorted(d.glob("official_series_????-??.parquet"))
    assert files, "official_series seed missing"
    known = set(load_official()["series"])
    seen = set()
    for f in files:
        month = f.stem.removeprefix("official_series_")
        for r in pq.read_table(f).to_pylist():
            assert r["date"].strftime("%Y-%m") == month and r["series_id"] in known, (f.name, r)
            assert (r["series_id"], r["date"]) not in seen
            seen.add((r["series_id"], r["date"]))
    assert min(d_ for _, d_ in seen) >= D(2025, 9, 1)
