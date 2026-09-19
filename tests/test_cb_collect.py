"""Central Banks collector (phase 1A): append-only store, idempotence, history, zero-is-a-value, cutoff,
failure isolation, conditional GET, raw rows, --status. Fake sources for the rules; the real adapters over the
real cut fixtures (mocked HTTP) for the end-to-end run. No network."""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pyarrow.parquet as pq
from requests.structures import CaseInsensitiveDict

from src import cb_collect as cc
from src.cb_sources.base import FetchResult, MarketSource

FIX = Path(__file__).parent / "fixtures" / "cb"
NOW = datetime(2026, 9, 19, 16, 35, tzinfo=timezone.utc)          # Saturday
TODAY = NOW.date()


def clock(dt=NOW):
    return lambda: dt


def make_fake(sid: str, script, currency: str = "USD"):
    class Fake(MarketSource):
        id = sid
        def _fetch(self, since, state):
            self.seen = {"since": since, "state": dict(state)}
            LAST[sid] = self.seen
            return script(self, since, state)
    Fake.currency = currency
    Fake.__name__ = f"Fake_{sid}"
    return Fake


LAST: dict = {}


def cfg_for(*ids, history="official", tz="UTC", cutoff="20:00"):
    return {"meta": {"backfill_from": "2026-03-01"},
            "sources": {i: {"adapter": f"Fake_{i}", "history": history, "exchange_tz": tz, "eod_cutoff": cutoff,
                            "horizon_months": 36} for i in ids}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Registers fake sources; returns (paths, register)."""
    classes: dict = {}
    monkeypatch.setattr(cc, "CLASSES", classes)
    LAST.clear()

    def register(sid, script, **kw):
        cls = make_fake(sid, script, **kw)
        classes[cls.__name__] = cls
        return cls
    return cc.Paths(tmp_path / "cb"), register


def q(src, asof, value, contract="3M", instrument="x", unit="percent", **kw):
    return src.quote(instrument, contract, "yield", value, unit, asof, tenor_months=3.0, **kw)


def rows(paths):
    """Every stored row, all partitions (sorted for stable comparisons)."""
    return sorted(cc.load_store(paths).values(), key=lambda r: (r["source"], r["instrument"], r["contract"], r["field"], r["asof"]))


def parts(paths) -> dict:
    """{partition file name: sha256} - the byte-level fingerprint of the whole quote store."""
    return {f.name: sha(f) for f in sorted(paths.quotes.glob("*.parquet"))} if paths.quotes.exists() else {}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


D = lambda n: date(2026, 9, n)      # noqa: E731


# ---------------------------------------------------------------------------
# Store rules
# ---------------------------------------------------------------------------

def test_new_rows_land_with_the_stable_columns(env):
    paths, register = env
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(17), 3.5)]))
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock())
    assert (rep.status, rep.merge.new) == ("ok", 1)
    t = pq.read_table(paths.partition("2026-09"))
    assert t.schema.names == ["source", "currency", "instrument", "contract", "field", "ref_start", "ref_end",
                              "tenor_months", "value", "unit", "asof", "asof_inferred", "fetched_at"]
    r = t.to_pylist()[0]
    assert (r["source"], r["currency"], r["asof"], r["value"], r["asof_inferred"]) == ("a", "USD", D(17), 3.5, False)
    assert r["fetched_at"] == NOW


def test_same_key_last_write_wins_but_identical_content_keeps_the_original_row(env):
    paths, register = env
    val = {"v": 3.5}
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(17), val["v"])]))
    cc.run(paths, cfg=cfg_for("a"), now=clock())
    later = NOW + timedelta(hours=2)
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(later))
    assert (rep.merge.new, rep.merge.updated, rep.merge.unchanged) == (0, 0, 1)
    assert rows(paths)[0]["fetched_at"] == NOW                           # untouched: no churn
    val["v"] = 3.6                                                       # the source revised the value
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(later + timedelta(hours=2)))
    assert (rep.merge.new, rep.merge.updated) == (0, 1)
    (r,) = rows(paths)
    assert (r["value"], r["fetched_at"]) == (3.6, later + timedelta(hours=2))        # last write wins, still ONE row


def test_two_runs_the_same_day_are_byte_identical(env):
    paths, register = env
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(16), 3.4), q(s, D(17), 3.5)], state={"etag": "e1"}))
    register("b", lambda s, since, st: FetchResult("ok", [q(s, D(17), 4.0, contract="6M")], raw={"asof": D(17), "text": "r1,r2"}),)
    cfg = cfg_for("a", "b")
    cc.run(paths, cfg=cfg, now=clock())
    first = (parts(paths), sha(paths.state), sha(paths.raw / "b" / "2026-09-17.csv.gz"))
    reps = cc.run(paths, cfg=cfg, now=clock(NOW + timedelta(hours=2)))
    assert (parts(paths), sha(paths.state), sha(paths.raw / "b" / "2026-09-17.csv.gz")) == first
    assert all(r.merge.new == 0 and r.merge.updated == 0 and not r.raw_written for r in reps)
    keys = [(r["source"], r["instrument"], r["contract"], r["field"], r["asof"]) for r in rows(paths)]
    assert len(keys) == len(set(keys)) == 3                              # zero duplicates


def test_history_is_never_deleted(env):
    paths, register = env
    batch = {"days": [D(14), D(15), D(16)]}
    register("a", lambda s, since, st: FetchResult("ok", [q(s, d, 3.0 + d.day / 100) for d in batch["days"]]))
    cc.run(paths, cfg=cfg_for("a"), now=clock())
    batch["days"] = [D(16), D(17)]                                       # the source's window moved on
    cc.run(paths, cfg=cfg_for("a"), now=clock(NOW + timedelta(days=1)))
    assert sorted(r["asof"] for r in rows(paths)) == [D(14), D(15), D(16), D(17)]


def test_zero_is_a_value_not_a_gap(env):
    """CHF 0.00 policy-rate world: a published 0.0 must be stored and read back as 0.0."""
    paths, register = env
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(17), 0.0), q(s, D(17), -0.25, contract="6M")]))
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock())
    assert rep.merge.new == 2
    got = {r["contract"]: r["value"] for r in rows(paths)}
    assert got == {"3M": 0.0, "6M": -0.25} and got["3M"] is not None
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(NOW + timedelta(hours=2)))
    assert rep.merge.unchanged == 2 and rep.merge.updated == 0            # 0.0 == 0.0, not treated as "missing"


def test_a_failed_source_is_logged_and_does_not_stop_the_rest(env):
    paths, register = env

    def down(s, since, st):
        s._parse_fail("layout changed")
        return None

    def explodes(s, since, st):
        raise RuntimeError("bug")
    register("a", down)
    register("b", lambda s, since, st: FetchResult("ok", [q(s, D(17), 1.0)]))
    register("c", explodes)
    reps = cc.run(paths, cfg=cfg_for("a", "b", "c"), now=clock())
    assert [r.status for r in reps] == ["FAILED", "ok", "FAILED"]
    assert "PARSE-FAIL" in reps[0].note and "RuntimeError" in reps[2].note
    assert [r["source"] for r in rows(paths)] == ["b"]
    assert cc.exit_code(reps) == 0                                       # partial failure: exit 0


def test_exit_code_is_nonzero_only_when_every_source_fails(env):
    paths, register = env
    register("a", lambda s, since, st: None)
    register("b", lambda s, since, st: None)
    assert cc.exit_code(cc.run(paths, cfg=cfg_for("a", "b"), now=clock())) == 1
    register("c", lambda s, since, st: FetchResult("not_modified"))
    register("d", lambda s, since, st: FetchResult("skipped", note="intraday"))
    assert cc.exit_code(cc.run(paths, cfg=cfg_for("a", "c"), now=clock())) == 0      # a 304 is not a failure
    assert cc.exit_code(cc.run(paths, cfg=cfg_for("a", "d"), now=clock())) == 0
    assert not paths.quotes.exists()                                    # nothing new -> nothing written


def test_broken_source_never_wipes_existing_history(env):
    paths, register = env
    ok = {"on": True}
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(17), 3.5)]) if ok["on"] else None)
    cc.run(paths, cfg=cfg_for("a"), now=clock())
    h = parts(paths)
    ok["on"] = False
    cc.run(paths, cfg=cfg_for("a"), now=clock(NOW + timedelta(days=1)))
    assert parts(paths) == h


# ---------------------------------------------------------------------------
# eod_cutoff
# ---------------------------------------------------------------------------

def test_current_day_quote_is_recorded_only_after_the_cutoff(env):
    paths, register = env
    today = date(2026, 9, 18)                                            # Friday
    register("a", lambda s, since, st: FetchResult("ok", [q(s, today - timedelta(days=1), 3.4), q(s, today, 3.5)],
                                                   state={"etag": "new"}))
    before = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(before))
    assert (rep.merge.new, rep.dropped) == (1, 1)
    assert [r["asof"] for r in rows(paths)] == [date(2026, 9, 17)]       # yesterday final, today held back
    assert "etag" not in cc.load_state(paths).get("a", {})               # validators NOT advanced -> re-fetched next run
    after = datetime(2026, 9, 18, 20, 30, tzinfo=timezone.utc)
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(after))
    assert (rep.merge.new, rep.dropped, rep.merge.unchanged) == (1, 0, 1)
    assert sorted(r["asof"] for r in rows(paths)) == [date(2026, 9, 17), date(2026, 9, 18)]
    assert cc.load_state(paths)["a"] == {"etag": "new"}


def test_raw_is_not_kept_for_a_day_that_is_not_final_yet(env):
    paths, register = env
    register("a", lambda s, since, st: FetchResult("ok", [q(s, date(2026, 9, 18), 3.5)], raw={"asof": date(2026, 9, 18), "text": "x"}))
    cc.run(paths, cfg=cfg_for("a"), now=clock(datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)))
    assert not (paths.raw / "a").exists()
    cc.run(paths, cfg=cfg_for("a"), now=clock(datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)))
    assert (paths.raw / "a" / "2026-09-18.csv.gz").exists()


# ---------------------------------------------------------------------------
# Conditional GET / state
# ---------------------------------------------------------------------------

def test_stored_validators_are_passed_back_and_304_adds_nothing(env):
    paths, register = env
    reply = {"r": FetchResult("ok", [], state={"etag": "v1", "last_modified": "Fri"})}
    register("a", lambda s, since, st: reply["r"])
    register_rows = lambda s, since, st: FetchResult("ok", [q(s, D(17), 3.5)], state={"etag": "v1"})   # noqa: E731
    register("b", register_rows)
    cc.run(paths, cfg=cfg_for("a", "b"), now=clock())
    assert cc.load_state(paths) == {"a": {"etag": "v1", "last_modified": "Fri"}, "b": {"etag": "v1"}}
    reply["r"] = FetchResult("not_modified", state={"etag": "v1", "last_modified": "Fri"}, note="HTTP 304")
    h = sha(paths.state)
    reps = cc.run(paths, cfg=cfg_for("a", "b"), now=clock(NOW + timedelta(hours=2)))
    assert LAST["a"]["state"] == {"etag": "v1", "last_modified": "Fri"}          # validators handed to the adapter
    assert reps[0].status == "not_modified" and reps[0].merge.new == 0
    assert sha(paths.state) == h


def test_backfill_ignores_validators_and_covers_only_official_history_sources(env):
    paths, register = env
    for sid in ("mpt", "snap"):
        register(sid, lambda s, since, st: FetchResult("ok", [q(s, D(17), 1.0)], state={"etag": "e"}))
    cfg = cfg_for("mpt")
    cfg["sources"]["snap"] = {**cfg_for("snap", history="snapshot")["sources"]["snap"]}
    cc.run(paths, cfg=cfg, now=clock())                                  # normal run: both, stores validators
    LAST.clear()
    reps = cc.run(paths, cfg=cfg, now=clock(), backfill_from=date(2026, 3, 1))
    assert [r.id for r in reps] == ["mpt"] and "snap" not in LAST         # snapshot sources have nothing to backfill
    assert LAST["mpt"] == {"since": date(2026, 3, 1), "state": {}}        # full download, since = backfill start
    LAST.clear()
    cc.run(paths, cfg=cfg, now=clock())
    assert LAST["mpt"]["since"] == TODAY - timedelta(days=10) and LAST["mpt"]["state"] == {"etag": "e"}


def test_unknown_source_is_rejected(env):
    paths, _ = env
    with pytest.raises(SystemExit):
        cc.run(paths, cfg=cfg_for("a"), only=["nope"], now=clock())


# ---------------------------------------------------------------------------
# Raw rows of snapshot sources
# ---------------------------------------------------------------------------

def test_raw_rows_are_gzip_deterministic_and_written_once(env):
    paths, register = env
    txt = {"t": "IBU2026,2026-09-28,95.645,2026-09-18\nIBV2026,2026-10-28,95.435,2026-09-18"}
    register("asx", lambda s, since, st: FetchResult("ok", [q(s, D(18), 95.6)], raw={"asof": D(18), "text": txt["t"]}))
    (rep,) = cc.run(paths, cfg=cfg_for("asx", history="snapshot"), now=clock())
    f = paths.raw / "asx" / "2026-09-18.csv.gz"
    assert rep.raw_written and gzip.decompress(f.read_bytes()).decode() == txt["t"] + "\n"
    assert f.read_bytes()[4:8] == b"\x00\x00\x00\x00"                     # gzip mtime 0 -> byte-deterministic
    (rep,) = cc.run(paths, cfg=cfg_for("asx", history="snapshot"), now=clock(NOW + timedelta(hours=2)))
    assert not rep.raw_written
    txt["t"] = "IBU2026,2026-09-28,95.650,2026-09-18"                     # the snapshot was corrected: last write wins
    (rep,) = cc.run(paths, cfg=cfg_for("asx", history="snapshot"), now=clock(NOW + timedelta(hours=4)))
    assert rep.raw_written and b"95.650" in gzip.decompress(f.read_bytes())


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------

def test_status_reports_asof_lag_and_missing_weekdays(env):
    paths, register = env
    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 16)]   # 9,14,15 missing
    register("a", lambda s, since, st: FetchResult("ok", [q(s, d, 1.0) for d in days], state={"etag": "x"}))
    register("b", lambda s, since, st: None)
    cc.run(paths, cfg=cfg_for("a", "b"), now=clock())
    text, md = cc.status(paths, TODAY, cfg=cfg_for("a", "b"))
    tok = next(ln for ln in text.splitlines() if ln.startswith("a ")).split()
    assert tok[3:5] == ["2026-09-07", "2026-09-16"]                          # first, last as-of
    assert tok[5] == "2" and tok[8] == "3"                                   # lag: Wed -> Fri = 2 business days; 3 missing weekdays
    assert "2026-09-14" in text and "2026-09-15" in text and "2026-09-09" in text
    assert "2026-09-12" not in text                                          # weekends are not "missing"
    b = next(ln for ln in text.splitlines() if ln.startswith("b "))
    assert " 0 " in b                                                        # a source with no rows is listed, zero rows
    assert md.startswith("### cb_collect --status") and "| a |" in md


def test_main_writes_the_github_step_summary(env, tmp_path, monkeypatch, capsys):
    paths, register = env
    register("a", lambda s, since, st: FetchResult("ok", [q(s, D(17), 1.0)]))
    monkeypatch.setattr(cc, "load_sources", lambda: cfg_for("a"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cc.main(["--data-dir", str(paths.dir)]) == 0
    assert cc.main(["--status", "--data-dir", str(paths.dir)]) == 0
    s = summary.read_text()
    assert "### cb_collect " in s and "### cb_collect --status" in s and "| a |" in s
    out = capsys.readouterr().out
    assert "cb_collect" in out and "last as-of" in out


# ---------------------------------------------------------------------------
# End to end: the real adapters over the real cut fixtures (HTTP mocked)
# ---------------------------------------------------------------------------

def _router(monkeypatch):
    def ok(name, headers=None):
        return (FIX / name).read_bytes(), headers or {}

    def get(url, headers=None, timeout=None, **kw):
        h = headers or {}
        table = [
            ("mpt_histdata.xlsx", "mpt_data_cut.xlsx", {"ETag": "e-mpt"}),
            ("latest-yield-curve-data.zip", "boe_latest_cut.zip", {"Last-Modified": "Fri, 18 Sep 2026 12:58:26 GMT"}),
            ("oisddata.zip", "boe_hist_cut.zip", {"Last-Modified": "Thu, 03 Sep 2026 14:52:11 GMT"}),
            ("settlement-price/index.html", "jpx_page_cut.html", {}),
            ("rb_e20260918.csv", "jpx_rb_e20260918_cut.csv", {"Last-Modified": "Fri, 18 Sep 2026 07:44:43 GMT"}),
            ("canadian-interest-rate-expectations", "mx_expectations_cut.html", {}),
            ("interest-rate/IB/futures", "asx_ib_cut.json", {}),
            ("interest-rate/BB/futures", "asx_bb_cut.json", {}),
            ("daily-treasury-rates", "ust_par_curve_cut.csv", {}),
            ("valet/observations", "boc_tbills_cut.json", {}),
            ("data-api.ecb.europa.eu", "ecb_if_cut.csv", {}),
            ("f1-data.csv", "rba_f1_cut.csv", {"Last-Modified": "Thu, 17 Sep 2026 23:00:50 GMT"}),
        ]
        for needle, name, hdrs in table:
            if needle in url:
                inm, ims = h.get("If-None-Match"), h.get("If-Modified-Since")
                if (inm and inm == hdrs.get("ETag")) or (ims and ims == hdrs.get("Last-Modified")):
                    return _Resp(304, b"", {})
                return _Resp(200, (FIX / name).read_bytes(), hdrs)
        raise AssertionError(f"unrouted {url}")
    monkeypatch.setattr("src.rate_sources.requests.get", get)


class _Resp:
    def __init__(self, status, content, headers):
        self.status_code, self.content, self.headers = status, content, CaseInsensitiveDict(headers)

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def test_end_to_end_all_ten_adapters_over_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr("src.rate_sources.time.sleep", lambda s: None)
    _router(monkeypatch)
    paths = cc.Paths(tmp_path / "cb")
    reps = cc.run(paths, now=clock(), lookback_days=45)                   # real config, real adapters
    by = {r.id: r for r in reps}
    assert set(by) == {"atlantafed_mpt", "boe_ois", "jpx_tona", "mx_corra", "asx_ib", "asx_bb", "ust_bills",
                       "boc_tbills", "ecb_aaa_fwd", "rba_bank_bills"}
    assert all(r.status == "ok" for r in reps), {r.id: (r.status, r.note) for r in reps}
    assert by["atlantafed_mpt"].merge.new == 6 and by["jpx_tona"].merge.new == 12 and by["mx_corra"].merge.new == 8
    assert by["boe_ois"].merge.new == 3 * 36 and by["ust_bills"].merge.new == 27 and by["rba_bank_bills"].merge.new == 9
    r = rows(paths)
    keys = [(x["source"], x["instrument"], x["contract"], x["field"], x["asof"]) for x in r]
    assert len(keys) == len(set(keys))
    mx = [x for x in r if x["source"] == "mx_corra"]
    assert mx and all(x["asof_inferred"] and x["asof"] == date(2026, 9, 18) for x in mx)      # Saturday fetch -> Friday
    assert all(not x["asof_inferred"] for x in r if x["source"] != "mx_corra")
    assert {p.name for p in paths.raw.iterdir()} == {"jpx_tona", "mx_corra", "asx_ib", "asx_bb"}    # snapshot sources only
    validators = cc.load_state(paths)
    assert validators["atlantafed_mpt"]["etag"] == "e-mpt" and "latest" in validators["boe_ois"]

    # second run: idempotent, and the conditional GETs come back 304
    before = (parts(paths), sha(paths.state))
    reps2 = cc.run(paths, now=clock(NOW + timedelta(hours=2)), lookback_days=45)
    by2 = {r.id: r for r in reps2}
    assert all(r.merge.new == 0 and r.merge.updated == 0 for r in reps2)
    assert {i for i, r in by2.items() if r.status == "not_modified"} == {"atlantafed_mpt", "boe_ois", "jpx_tona", "rba_bank_bills"}
    assert (parts(paths), sha(paths.state)) == before
    assert cc.exit_code(reps2) == 0


# ---------------------------------------------------------------------------
# Monthly partitions
# ---------------------------------------------------------------------------

def test_rows_are_partitioned_by_the_month_of_the_asof(env):
    paths, register = env
    days = [date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    register("a", lambda s, since, st: FetchResult("ok", [q(s, d, 3.0 + d.day / 100) for d in days]))
    register("b", lambda s, since, st: FetchResult("ok", [q(s, date(2026, 9, 1), 4.0, contract="6M")]))
    cc.run(paths, cfg=cfg_for("a", "b"), now=clock())
    assert sorted(p.name for p in paths.quotes.glob("*")) == ["market_quotes_2026-08.parquet", "market_quotes_2026-09.parquet"]
    assert not (paths.dir / "market_quotes.parquet").exists()                # the single file is gone for good
    aug = pq.read_table(paths.partition("2026-08")).to_pylist()
    sep = pq.read_table(paths.partition("2026-09")).to_pylist()
    assert [r["asof"] for r in aug] == [date(2026, 8, 28), date(2026, 8, 31)]                 # month boundary: 31 Aug / 1 Sep
    assert sorted((r["source"], r["asof"]) for r in sep) == [("a", date(2026, 9, 1)), ("a", date(2026, 9, 2)), ("b", date(2026, 9, 1))]
    assert pq.read_table(paths.partition("2026-08")).schema.names == cc.COLUMNS                # same columns everywhere
    keys = [cc._key(r) for r in rows(paths)]
    assert len(keys) == len(set(keys)) == 5                                                  # the key is unique globally


def test_an_ordinary_run_rewrites_only_the_partition_it_changes(env):
    paths, register = env
    batch = {"days": [date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]}
    register("a", lambda s, since, st: FetchResult("ok", [q(s, d, 3.0 + d.day / 100) for d in batch["days"]]))
    cc.run(paths, cfg=cfg_for("a"), now=clock())
    aug, sep = paths.partition("2026-08"), paths.partition("2026-09")
    aug_before, aug_mtime, sep_before = aug.read_bytes(), aug.stat().st_mtime_ns, sep.read_bytes()
    batch["days"] = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]              # the source's window moves by one day
    (rep,) = cc.run(paths, cfg=cfg_for("a"), now=clock(NOW + timedelta(hours=2)))
    assert (rep.merge.new, rep.merge.months) == (1, {"2026-09"})
    assert aug.read_bytes() == aug_before and aug.stat().st_mtime_ns == aug_mtime          # August was not even touched
    assert sep.read_bytes() != sep_before and len(pq.read_table(sep)) == 2
    m = sep.stat().st_mtime_ns
    cc.run(paths, cfg=cfg_for("a"), now=clock(NOW + timedelta(hours=4)))                  # idle run: nothing rewritten
    assert sep.stat().st_mtime_ns == m and aug.stat().st_mtime_ns == aug_mtime


def test_partition_bytes_depend_only_on_the_content(env, tmp_path):
    paths, register = env
    src = make_fake("z", lambda *a: None)(cfg_for("z")["sources"]["z"], now=clock())
    rs = [cc.quote_row(q(src, D(d), 3.0 + d / 100, contract=c)) for d in (14, 15, 16) for c in ("3M", "6M")]
    a = cc.serialize_partition(rs)
    assert a == cc.serialize_partition(list(reversed(rs))) == cc.serialize_partition(rs[3:] + rs[:3])    # order-independent
    store = {cc._key(r): r for r in rs}
    assert cc.write_store(paths, store) == ["2026-09"]
    mt = paths.partition("2026-09").stat().st_mtime_ns
    assert cc.write_store(paths, store) == []                                            # identical bytes -> no rewrite
    assert paths.partition("2026-09").stat().st_mtime_ns == mt
    store2 = dict(store)
    other = dict(rs[0], asof=date(2026, 10, 1)); store2[cc._key(other)] = other
    assert cc.write_store(paths, store2, months={"2026-10"}) == ["2026-10"]                 # only the requested partition
    assert cc.write_store(paths, store2, months=set()) == []


def test_load_store_reads_everything_or_an_interval(env):
    paths, register = env
    days = [date(2026, m, 15) for m in (6, 7, 8, 9)]
    register("a", lambda s, since, st: FetchResult("ok", [q(s, d, 1.0) for d in days]))
    cc.run(paths, cfg=cfg_for("a"), now=clock(), backfill_from=date(2026, 3, 1))
    assert sorted(r["asof"].month for r in cc.load_store(paths).values()) == [6, 7, 8, 9]
    assert sorted(r["asof"].month for r in cc.load_store(paths, "2026-07", "2026-08").values()) == [7, 8]
    assert sorted(r["asof"].month for r in cc.load_store(paths, start=date(2026, 8, 20)).values()) == [8, 9]
    assert sorted(r["asof"].month for r in cc.load_store(paths, end="2026-06").values()) == [6]
    assert list(cc.list_partitions(paths)) == ["2026-06", "2026-07", "2026-08", "2026-09"]


# ---------------------------------------------------------------------------
# asof_inferred guard (MX): identical to the previous recorded as-of -> not recorded
# ---------------------------------------------------------------------------

def at(y, m, d, h=12):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


FRI, MON, TUE = date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22)


def inferred_source(register, prices):
    """Fake MX: asof comes from prices['asof'], values from prices['v'] (dict contract -> value)."""
    def script(s, since, st):
        qs = [q(s, prices["asof"], v, contract=c, instrument="corra_3m_futures", inferred=True) for c, v in prices["v"].items()]
        return FetchResult("ok", qs, raw={"asof": prices["asof"], "text": "row"})
    register("mx", script, currency="CAD")
    return prices


def test_an_inferred_asof_identical_to_the_previous_one_is_not_recorded(env):
    paths, register = env
    px = inferred_source(register, {"asof": FRI, "v": {"202612": 97.225, "202703": 96.87}})
    cfg = cfg_for("mx", history="snapshot")
    (rep,) = cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19)))                        # Saturday: Friday's page
    assert rep.merge.new == 2 and not rep.skipped and (paths.raw / "mx" / "2026-09-18.csv.gz").exists()

    px["asof"] = MON                                                                     # Monday holiday: same numbers, new date
    (rep,) = cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 22, 5)))
    assert rep.skipped == {MON: FRI} and rep.merge.new == 0
    assert "fără valori noi" in rep.note and "2026-09-21" in rep.note and "2026-09-18" in rep.note
    assert sorted(r["asof"] for r in rows(paths)) == [FRI, FRI]                          # nothing recorded for Monday
    assert not (paths.raw / "mx" / "2026-09-21.csv.gz").exists()                         # nor a raw file
    assert cc.load_state(paths)[cc.NO_NEW] == {"mx": {"2026-09-21": "2026-09-18"}}
    assert cc.exit_code([rep]) == 0

    h = (parts(paths), sha(paths.state))
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 22, 7)))                                # the same skip again: no diff at all
    assert (parts(paths), sha(paths.state)) == h

    text, md = cc.status(paths, date(2026, 9, 22), cfg=cfg)
    line = next(ln for ln in text.splitlines() if ln.startswith("mx "))
    assert "fără valori noi (as-of 2026-09-21 = 2026-09-18)" in line and "fără valori noi" in md


def test_the_next_real_trading_day_is_recorded_and_the_skipped_day_stays_annotated(env):
    paths, register = env
    px = inferred_source(register, {"asof": FRI, "v": {"202612": 97.225, "202703": 96.87}})
    cfg = cfg_for("mx", history="snapshot")
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19)))
    px["asof"] = MON
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 22, 5)))                                # skipped (holiday)
    px.update(asof=TUE, v={"202612": 97.25, "202703": 96.9})
    (rep,) = cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 23, 5)))                       # Tuesday: real new prices
    assert rep.merge.new == 2 and not rep.skipped
    assert sorted({r["asof"] for r in rows(paths)}) == [FRI, TUE]                        # Monday never recorded
    text, _ = cc.status(paths, date(2026, 9, 23), cfg=cfg)
    line = next(ln for ln in text.splitlines() if ln.startswith("mx "))
    assert "fără valori noi (" not in line                                               # no pending skip: Tuesday is recorded
    assert "2026-09-21 (fără valori noi)" in text                                        # the gap is explained, not just "missing"


def test_a_skipped_asof_that_the_page_later_updates_is_recorded_and_the_marker_dropped(env):
    paths, register = env
    px = inferred_source(register, {"asof": FRI, "v": {"202612": 97.225}})
    cfg = cfg_for("mx", history="snapshot")
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19)))
    px["asof"] = MON
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 21, 23)))                               # page not refreshed yet -> skipped
    assert cc.NO_NEW in cc.load_state(paths)
    px["v"] = {"202612": 97.3}                                                           # refreshed a few hours later
    (rep,) = cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 22, 5)))
    assert rep.merge.new == 1 and not rep.skipped
    assert cc.NO_NEW not in cc.load_state(paths)                                         # marker gone: Monday is recorded now


def test_partial_difference_is_recorded_and_new_contracts_count_as_a_difference(env):
    paths, register = env
    px = inferred_source(register, {"asof": FRI, "v": {"202612": 97.225, "202703": 96.87}})
    cfg = cfg_for("mx", history="snapshot")
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19)))
    px.update(asof=MON, v={"202612": 97.225, "202703": 96.875})                          # one contract moved half a tick
    assert cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 22, 5)))[0].merge.new == 2
    px.update(asof=TUE, v={"202612": 97.225, "202703": 96.875, "202706": 96.6})          # a newly listed contract
    assert cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 23, 5)))[0].merge.new == 3


def test_the_guard_does_not_apply_to_sources_with_a_published_asof(env):
    """RBA / BoC / Treasury rates are flat for weeks: identical values on a new date are real data."""
    paths, register = env
    day = {"d": FRI}
    register("boc", lambda s, since, st: FetchResult("ok", [q(s, day["d"], 2.25, contract="1M"), q(s, day["d"], 2.4, contract="3M")]))
    for d in (FRI, MON, TUE):
        day["d"] = d
        (rep,) = cc.run(paths, cfg=cfg_for("boc"), now=clock(at(2026, 9, 23)))
        assert rep.merge.new == 2 and not rep.skipped
    assert len(rows(paths)) == 6 and cc.NO_NEW not in cc.load_state(paths)


def test_refetching_an_already_recorded_inferred_asof_is_a_plain_merge(env):
    paths, register = env
    inferred_source(register, {"asof": FRI, "v": {"202612": 97.225}})
    cfg = cfg_for("mx", history="snapshot")
    cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19)))
    (rep,) = cc.run(paths, cfg=cfg, now=clock(at(2026, 9, 19, 20)))                      # Saturday again, Friday's page
    assert (rep.merge.unchanged, rep.merge.new, rep.skipped) == (1, 0, {})


def test_the_real_mx_adapter_skips_an_unrefreshed_page(tmp_path, monkeypatch):
    """Same guard through the real MxCorra over the real fixture: Friday recorded, Monday (same page) not."""
    monkeypatch.setattr("src.rate_sources.time.sleep", lambda s: None)
    _router(monkeypatch)
    paths = cc.Paths(tmp_path / "cb")
    (rep,) = cc.run(paths, only=["mx_corra"], now=clock(at(2026, 9, 19, 16)))              # Saturday -> Friday
    assert rep.merge.new == 8
    (rep,) = cc.run(paths, only=["mx_corra"], now=clock(at(2026, 9, 21, 23)))              # Monday after the window, same page
    assert rep.skipped == {date(2026, 9, 21): date(2026, 9, 18)} and rep.merge.new == 0
    assert {r["asof"] for r in rows(paths)} == {date(2026, 9, 18)}


# ---------------------------------------------------------------------------
# Lag in --status: business days between the as-of and the last business day <= today
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asof, today, lag", [
    (date(2026, 9, 18), date(2026, 9, 19), 0),      # Saturday, Friday's data (0A convention)
    (date(2026, 9, 17), date(2026, 9, 19), 1),      # Saturday, Thursday's data
    (date(2026, 9, 16), date(2026, 9, 19), 2),
    (date(2026, 9, 18), date(2026, 9, 20), 0),      # Sunday behaves like Saturday
    (date(2026, 9, 17), date(2026, 9, 20), 1),
    (date(2026, 9, 21), date(2026, 9, 21), 0),      # Monday, same-day data
    (date(2026, 9, 18), date(2026, 9, 21), 1),      # Monday, Friday's data
    (date(2026, 9, 17), date(2026, 9, 21), 2),
    (date(2026, 9, 18), date(2026, 9, 22), 2),      # Tuesday, Friday's data: Fri + Mon
    (date(2026, 9, 22), date(2026, 9, 22), 0),
    (date(2026, 9, 23), date(2026, 9, 22), 0),      # an as-of ahead of "today" never gives a negative lag
])
def test_business_day_lag_convention(asof, today, lag):
    assert cc.business_day_lag(asof, today) == lag


def test_status_shows_the_lag_with_the_same_convention(env):
    paths, register = env
    for sid, d in (("a", date(2026, 9, 18)), ("b", date(2026, 9, 17)), ("c", date(2026, 9, 16))):
        register(sid, lambda s, since, st, d=d: FetchResult("ok", [q(s, d, 1.0)]))
    cfg = cfg_for("a", "b", "c")
    cc.run(paths, cfg=cfg, now=clock())
    text, _ = cc.status(paths, date(2026, 9, 19), cfg=cfg)                                # a Saturday
    lag = {ln.split()[0]: ln.split()[5] for ln in text.splitlines() if ln.split() and ln.split()[0] in "abc" and len(ln.split()[0]) == 1}
    assert lag == {"a": "0", "b": "1", "c": "2"}
    reps = cc.run(paths, cfg=cfg, now=clock())
    assert {r.id: r.lag_bd for r in reps} == {"a": 0, "b": 1, "c": 2}                       # the run report uses it too
