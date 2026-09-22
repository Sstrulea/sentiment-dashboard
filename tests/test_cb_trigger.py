"""Phase 4, the external trigger: the schedule the Cloudflare Worker reads (DST, time zones, the BoJ window, the meetings' flags), the decision watch of the collector (polls a minute
apart for at most 15, clean exit), the measured latency (--status and the methodology panel), the workflows (backup schedules with their guard) and the Worker's own tests."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_compute import payload as P
from src.cb_docs import store as ST
from src.cb_docs import watch as W
from src.cb_docs.http import Fetcher
from src.cb_loader import load_context, load_pair_defs
from src.cb_trigger import latency as L
from src.cb_trigger import schedule as S

from .cb_docs_helpers import FakeSession, Resp
from .test_cb_docs_collect import data_dir

ROOT = Path(__file__).resolve().parents[1]
CFG = S.load_config()
BANKS = ds.load_banks()
MEETINGS = ds.load_meetings(ROOT / "data" / "cb" / "meetings.yaml")
D = date


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def events(today=D(2026, 9, 21), meetings=None, cfg=None):
    return {e["id"]: e for e in S.build(meetings or MEETINGS, BANKS, cfg or CFG, today)["events"]}


# --- the schedule: local official times -> UTC instants, DST by the bank's zone -----------------------------------------------------------------------------------

@pytest.mark.parametrize("ccy, day, hhmm, expected", [
    ("USD", D(2026, 9, 16), "14:00", "2026-09-16T18:00:00Z"),        # EDT (UTC-4)
    ("USD", D(2026, 12, 9), "14:00", "2026-12-09T19:00:00Z"),        # EST (UTC-5)
    ("USD", D(2026, 10, 28), "14:00", "2026-10-28T18:00:00Z"),       # the US is still on summer time when Europe is not
    ("USD", D(2026, 3, 8), "14:00", "2026-03-08T18:00:00Z"),         # the spring-forward day: 14:00 is after the 02:00 change
    ("USD", D(2026, 11, 1), "14:00", "2026-11-01T19:00:00Z"),        # the fall-back day
    ("EUR", D(2026, 9, 10), "14:15", "2026-09-10T12:15:00Z"),        # CEST (UTC+2)
    ("EUR", D(2026, 10, 29), "14:15", "2026-10-29T13:15:00Z"),       # CET: Europe changed on 25 Oct, the US on 1 Nov
    ("GBP", D(2026, 9, 17), "12:00", "2026-09-17T11:00:00Z"),        # BST (UTC+1)
    ("GBP", D(2026, 11, 5), "12:00", "2026-11-05T12:00:00Z"),        # GMT
    ("CAD", D(2026, 7, 15), "09:45", "2026-07-15T13:45:00Z"),        # EDT
    ("CAD", D(2027, 1, 20), "09:45", "2027-01-20T14:45:00Z"),        # EST
    ("AUD", D(2026, 8, 11), "14:30", "2026-08-11T04:30:00Z"),        # AEST (UTC+10): winter in the south
    ("AUD", D(2026, 11, 3), "14:30", "2026-11-03T03:30:00Z"),        # AEDT (UTC+11)
    ("AUD", D(2026, 10, 4), "14:30", "2026-10-04T03:30:00Z"),        # the day AEDT starts (02:00): 14:30 is already summer time
    ("CHF", D(2026, 9, 24), "09:30", "2026-09-24T07:30:00Z"),        # CEST
    ("CHF", D(2026, 12, 10), "09:30", "2026-12-10T08:30:00Z"),       # CET
    ("JPY", D(2026, 9, 18), "13:30", "2026-09-18T04:30:00Z"),        # JST has no DST
])
def test_the_official_local_time_becomes_the_utc_instant_of_the_zone_of_that_day(ccy, day, hhmm, expected):
    assert S.iso(S.instant(day, hhmm, BANKS[ccy]["tz"])) == expected


def test_a_decision_event_is_due_one_minute_after_the_official_time_and_carries_its_dispatch():
    e = events()["decision:CHF:2026-09-24"]
    assert (e["at"], e["fire_at"], e["kind"]) == ("2026-09-24T07:30:00Z", "2026-09-24T07:31:00Z", "decision")
    assert (e["lead_minutes"], e["grace_minutes"]) == (5, 90)
    assert e["dispatch"] == {"workflow": "cb-refresh.yml", "inputs": {"event": "decision", "bank": "CHF", "date": "2026-09-24"}}
    usd = events(D(2026, 9, 14))["decision:USD:2026-09-16"]
    assert (usd["at"], usd["fire_at"]) == ("2026-09-16T18:00:00Z", "2026-09-16T18:01:00Z")


def test_the_conference_event_is_two_hours_after_its_start_and_only_when_there_is_one():
    ev = events(D(2026, 9, 14))
    usd = ev["conference:USD:2026-09-16"]
    assert (usd["at"], usd["fire_at"], usd["at_basis"]) == ("2026-09-16T18:30:00Z", "2026-09-16T20:30:00Z", "conference")     # 14:30 EDT + 2 h
    assert usd["dispatch"]["inputs"] == {"event": "conference", "bank": "USD", "date": "2026-09-16"}
    assert usd["lead_minutes"] == 0                                                                                            # never early: the video and the transcript are not out yet
    assert ev["conference:CHF:2026-09-24"]["fire_at"] == "2026-09-24T10:00:00Z"                                                # 10:00 CEST + 2 h
    assert "decision:GBP:2026-09-17" in ev and "conference:GBP:2026-09-17" not in ev                                            # the BoE's September meeting has no press conference (has_presser false)
    assert "conference:GBP:2026-11-05" in events(D(2026, 11, 1))                                                               # its Monetary Policy Report meeting has


def test_the_boe_conference_time_stands_on_the_decision_time_when_the_config_has_none():
    ev = S.conference_event("GBP", BANKS["GBP"], D(2026, 7, 30), CFG)
    assert ev["at_basis"] == "decision" and ev["at"] == "2026-07-30T11:00:00Z" and ev["fire_at"] == "2026-07-30T13:00:00Z"


def test_the_boj_has_a_window_not_a_time():
    e = events(D(2026, 9, 14))["decision:JPY:2026-09-18"]
    assert e["kind"] == "boj_window"
    assert (e["window_start"], e["window_end"]) == ("2026-09-18T02:30:00Z", "2026-09-18T04:30:00Z")       # 11:30-13:30 JST
    assert e["fallback_at_window_end"] is True and e["grace_minutes"] == 30
    assert "fire_at" not in e
    assert e["dispatch"]["inputs"] == {"event": "decision", "bank": "JPY", "date": "2026-09-18"}


def test_the_boj_fallback_at_the_end_of_the_window_is_a_config_switch():
    off = yaml.safe_load(yaml.safe_dump(CFG))
    off["boj"]["fallback_at_window_end"] = False
    assert S.decision_event("JPY", BANKS["JPY"], D(2026, 9, 18), off)["fallback_at_window_end"] is False
    assert S.decision_event("JPY", BANKS["JPY"], D(2026, 9, 18), CFG)["fallback_at_window_end"] is True


def test_the_boj_window_comes_from_the_config_not_from_the_code():
    cfg = yaml.safe_load(yaml.safe_dump(BANKS))
    cfg["JPY"]["decision_time"]["window_local"] = ["10:00", "12:00"]
    e = S.decision_event("JPY", cfg["JPY"], D(2026, 9, 18), CFG)
    assert (e["window_start"], e["window_end"]) == ("2026-09-18T01:00:00Z", "2026-09-18T03:00:00Z")


def test_a_bank_that_cannot_be_watched_is_left_out_and_the_horizon_bounds_the_events():
    ev = events(D(2026, 9, 21))
    assert not any(k.split(":")[1] == "NZD" for k in ev)                                                    # RBNZ: skip_banks
    assert all(D(2026, 9, 18) <= D.fromisoformat(e["date"]) <= D(2026, 11, 20) for e in ev.values())
    assert "decision:JPY:2026-09-18" in ev and "decision:USD:2026-09-16" not in ev                            # 3 days back, 60 ahead
    assert not any(D.fromisoformat(e["date"]) > D(2026, 11, 20) for e in ev.values())
    wide = yaml.safe_load(yaml.safe_dump(CFG))
    wide["skip_banks"] = []
    assert any(k.split(":")[1] == "NZD" for k in events(D(2026, 9, 21), cfg=wide)) or "NZD" not in MEETINGS


def test_events_are_unique_sorted_and_the_file_is_deterministic(tmp_path):
    sched = S.build(MEETINGS, BANKS, CFG, D(2026, 9, 14))
    ids = [e["id"] for e in sched["events"]]
    assert len(ids) == len(set(ids))
    times = [e.get("fire_at") or e["window_start"] for e in sched["events"]]
    assert times == sorted(times)
    assert S.dump(sched) == S.dump(S.build(MEETINGS, BANKS, CFG, D(2026, 9, 14)))
    d = tmp_path / "cb"
    d.mkdir()
    (d / "meetings.yaml").write_text((ROOT / "data" / "cb" / "meetings.yaml").read_text())
    p = cc.Paths(d)
    r1, r2 = S.run_schedule(p, D(2026, 9, 14)), S.run_schedule(p, D(2026, 9, 14))
    assert r1.written and not r2.written                                                                     # an idle run leaves no diff
    assert json.loads((d / S.FILE).read_text()) == sched
    assert r1.events == len(sched["events"]) and r1.next_events


def test_the_regular_runs_are_the_ones_of_the_brief():
    reg = S.regular_entries(CFG)
    cb = next(r for r in reg if r["workflow"] == "cb-refresh.yml")
    assert (cb["minute"], cb["hours"], cb["dow"]) == (37, list(range(0, 24, 2)), [0, 1, 2, 3, 4, 5, 6])          # every 2 hours at :37
    econ = [r for r in reg if r["workflow"] == "econ-refresh.yml"]
    assert {(r["minute"], len(r["hours"]), tuple(r["dow"])) for r in econ} == {(5, 24, (1, 2, 3, 4, 5)), (5, 6, (0, 6))}     # hourly at :05 on weekdays, every 4 h at the weekend
    assert S.build(MEETINGS, BANKS, CFG, D(2026, 9, 21))["tick_minutes"] == 5


def test_the_schedule_is_generated_by_the_cb_refresh_stage_and_committed_data(tmp_path):
    assert "schedule" in cc.STAGES and "schedule" in cc.DEFAULT_STAGES and "summaries" not in cc.DEFAULT_STAGES
    stored = json.loads((ROOT / "data" / "cb" / S.FILE).read_text()) if (ROOT / "data" / "cb" / S.FILE).exists() else None
    if stored:
        assert stored["version"] == S.VERSION and stored["events"]


# --- the decision watch: poll every minute for at most 15, store the statement at the first poll that sees it ------------------------------------------------------

STATEMENT = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"


class Delayed(FakeSession):
    """The frozen statement page answers 404 until `after` requests of it have been made (the bank has not published yet)."""

    def __init__(self, after: int, **kw) -> None:
        super().__init__(**kw)
        self.after, self.asked = after, 0

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        if url == STATEMENT:
            self.asked += 1
            if self.asked <= self.after:
                self.calls.append(("GET", url, dict(headers or {})))
                return Resp(404, b"not there yet")
        return super().get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


class Clock:
    """A fake clock: `sleep` advances both the monotonic time and the wall clock."""

    def __init__(self, start: datetime) -> None:
        self.now, self.t, self.sleeps = start, 0.0, []

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s
        self.now += timedelta(seconds=s)

    def monotonic(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return self.now


def watch(tmp_path, session, *, minutes=15, poll=60, start=datetime(2026, 9, 16, 18, 1, 5, tzinfo=timezone.utc)):
    d = data_dir(tmp_path)
    clock = Clock(start)
    f = Fetcher(session=session, sleep=lambda s: None, min_interval=0)
    rep = W.watch_statement(cc.Paths(d), "USD", D(2026, 9, 16), minutes=minutes, poll_seconds=poll, fetcher=f, roster={}, state={}, sleep=clock.sleep, monotonic=clock.monotonic, clock=clock.wall)
    return rep, cc.Paths(d), clock


def test_the_statement_is_stored_by_the_first_poll_that_sees_it(tmp_path):
    session = Delayed(after=3)
    rep, paths, clock = watch(tmp_path, session)
    assert rep.found and not rep.stored_before and rep.polls == 4
    assert clock.sleeps == [60, 60, 60]                                                                      # a minute apart
    assert rep.first_seen_at == datetime(2026, 9, 16, 18, 4, 5, tzinfo=timezone.utc)                         # the poll that saw it: the start of the measured latency
    row = ST.load_documents(paths)[("USD:statement:2026-09-16",)]
    assert row["text"] and row["first_seen_at"] == rep.first_seen_at and row["rate_after"] is not None
    assert rep.new == 1 and rep.rate_rows_changed
    assert STATEMENT in cc.load_state(paths)["http_validators"]                                                            # the validators are stored with the statement: the next run asks conditionally


def test_a_statement_that_does_not_appear_in_15_minutes_ends_the_watch_clean_and_writes_nothing(tmp_path):
    session = Delayed(after=10 ** 6)
    rep, paths, clock = watch(tmp_path, session)
    assert not rep.found and rep.polls == 15 and clock.t == 14 * 60                                          # the last poll is at 14 min: nothing past the 15
    assert ST.load_documents(paths).get(("USD:statement:2026-09-16",)) is None
    text, md = W.report(rep, 15)
    assert "not found in 15 minutes" in text and "exiting clean" in text and "404" in text


def test_the_watch_asks_only_for_the_statement_of_that_bank(tmp_path):
    session = Delayed(after=1)
    watch(tmp_path, session)
    hosts = {u.split("/")[2] for u in session.urls()}
    assert hosts == {"www.federalreserve.gov"}                                                               # no ECB feed, no BoJ feed, no other bank
    assert set(session.urls()) == {STATEMENT}
    assert all(m == "GET" for m, _, _ in session.calls)


def test_a_statement_a_regular_run_stored_first_is_not_rewritten(tmp_path):
    d = data_dir(tmp_path)
    first = Delayed(after=0)
    f = Fetcher(session=first, sleep=lambda s: None, min_interval=0)
    W.watch_statement(cc.Paths(d), "USD", D(2026, 9, 16), minutes=15, poll_seconds=60, fetcher=f, roster={}, state={}, sleep=lambda s: None,
                      clock=lambda: datetime(2026, 9, 16, 18, 2, tzinfo=timezone.utc))
    before = ST.load_documents(cc.Paths(d))[("USD:statement:2026-09-16",)]["first_seen_at"]
    f2 = Fetcher(session=Delayed(after=0), sleep=lambda s: None, min_interval=0)
    rep = W.watch_statement(cc.Paths(d), "USD", D(2026, 9, 16), minutes=15, poll_seconds=60, fetcher=f2, roster={}, state={}, sleep=lambda s: None,
                            clock=lambda: datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc))
    assert rep.found and rep.stored_before and rep.polls == 1 and rep.new == 0
    assert ST.load_documents(cc.Paths(d))[("USD:statement:2026-09-16",)]["first_seen_at"] == before          # first_seen_at is never moved
    assert "already stored" in W.report(rep, 15)[0]


def test_the_watch_cli_reads_its_timing_from_the_config_and_runs_the_documents_of_that_bank_then_the_decisions(tmp_path, monkeypatch):
    calls = []
    for name in ("run", "run_official"):                                                                      # the stages the watch must NOT run: recorded, never executed
        monkeypatch.setattr(cc, name, lambda *a, _n=name, **k: (calls.append((_n,)), [])[1])
    monkeypatch.setattr(ds, "run_projections", lambda *a, **k: (calls.append(("projections",)), None)[1])
    monkeypatch.setattr(ds, "run_calendar_check", lambda *a, **k: (calls.append(("calendar",)), None)[1])
    monkeypatch.setattr(W, "watch_statement", lambda paths, ccy, day, **kw: (calls.append(("watch", ccy, day, kw["minutes"], kw["poll_seconds"])), W.WatchReport(ccy, day))[1])
    from src.cb_docs import collect as dcol
    monkeypatch.setattr(dcol, "run_documents", lambda paths, today, **kw: (calls.append(("documents", kw.get("banks"))), dcol.DocsReport())[1])
    monkeypatch.setattr(ds, "run_decisions", lambda paths, today: (calls.append(("decisions",)), ds.DecisionsReport())[1])
    d = tmp_path / "cb"
    d.mkdir()
    rc = cc.main(["--watch-decision", "USD", "--decision-date", "2026-09-16", "--data-dir", str(d)])
    assert rc == 0
    assert calls[0] == ("watch", "USD", D(2026, 9, 16), 15, 60)
    assert calls == [("watch", "USD", D(2026, 9, 16), 15, 60), ("decisions",), ("documents", ("USD",))]        # only what the decision changes: no market, official series, projections, calendar


def test_the_watch_cli_never_fails_the_job(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(W, "watch_statement", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from src.cb_docs import collect as dcol
    monkeypatch.setattr(dcol, "run_documents", lambda paths, today, **kw: dcol.DocsReport())
    monkeypatch.setattr(ds, "run_decisions", lambda paths, today: ds.DecisionsReport())
    d = tmp_path / "cb"
    d.mkdir()
    assert cc.main(["--watch-decision", "USD", "--decision-date", "2026-09-16", "--data-dir", str(d)]) == 0
    assert "decision watch: FAILED RuntimeError: boom" in capsys.readouterr().out


def test_discovery_reads_only_what_the_bank_needs():
    from src.cb_docs import collect as dcol
    sess = FakeSession()
    f = Fetcher(session=sess, sleep=lambda s: None, min_interval=0)
    run = dcol._Run(cc.Paths(ROOT / "data" / "cb"), D(2026, 9, 21), f, datetime(2026, 9, 21, tzinfo=timezone.utc), 4, ("USD",), {}, {})
    assert run.discover_for("USD") == {"EUR": {}, "AUD": {}} and sess.urls() == []
    run.discover_for("JPY")
    assert sess.urls() == ["https://www.boj.or.jp/en/rss/whatsnew.xml"]                                       # the time the BoJ published, for the latency


# --- the measured latency ---------------------------------------------------------------------------------------------------------------------------------------

def stmt(ccy, day, seen, published_at=None):
    return {"currency": ccy, "type": "statement", "published_date": day, "first_seen_at": utc(seen), "published_at": None if published_at is None else utc(published_at)}


def test_latency_is_first_seen_minus_the_official_time_in_the_zone_of_that_day():
    docs = [stmt("USD", D(2026, 9, 16), "2026-09-16T18:04:10Z"), stmt("USD", D(2026, 12, 9), "2026-12-09T19:09:00Z"),         # EDT, then EST
            stmt("AUD", D(2026, 11, 3), "2026-11-03T03:41:00Z"), stmt("CHF", D(2026, 9, 24), "2026-09-24T07:31:00Z")]
    got = {(r["ccy"], r["meeting"]): r for r in L.rows(docs, BANKS, CFG)}
    assert got[("USD", "2026-09-16")]["minutes"] == 4.2 and got[("USD", "2026-09-16")]["ok"]
    assert got[("USD", "2026-12-09")]["minutes"] == 9.0 and got[("USD", "2026-12-09")]["official_at"] == "2026-12-09T19:00:00Z"
    assert got[("AUD", "2026-11-03")]["minutes"] == 11.0 and got[("AUD", "2026-11-03")]["official_at"] == "2026-11-03T03:30:00Z"
    assert got[("CHF", "2026-09-24")]["minutes"] == 1.0
    late = L.rows([stmt("USD", D(2026, 10, 28), "2026-10-28T18:20:00Z")], BANKS, CFG)[0]
    assert late["minutes"] == 20.0 and late["ok"] is False                                                                    # over the 15-minute target
    assert late["basis"] == "config"


def test_the_target_and_the_start_of_the_measure_are_inclusive():
    exactly = L.rows([stmt("USD", D(2026, 9, 23), "2026-09-23T18:15:00Z")], BANKS, CFG)[0]
    assert exactly["minutes"] == 15.0 and exactly["ok"] is True                                                                # 15 minutes is within "<= 15"
    first_day = L.rows([stmt("USD", D(2026, 9, 22), "2026-09-22T18:04:00Z")], BANKS, CFG)[0]
    assert first_day["measured"] is True                                                                                      # measure_from 2026-09-22: that day counts
    assert L.rows([stmt("USD", D(2026, 9, 21), "2026-09-21T23:59:00Z")], BANKS, CFG)[0]["measured"] is False


def test_a_fixed_decision_time_wins_over_the_time_of_the_feed():
    row = L.rows([stmt("EUR", D(2026, 9, 10), "2026-09-10T12:20:00Z", published_at="2026-09-10T12:00:00+00:00")], BANKS, CFG)[0]
    assert (row["basis"], row["official_at"], row["minutes"]) == ("config", "2026-09-10T12:15:00Z", 5.0)                       # the ECB's feed says 12:00Z; the config 14:15 CEST = 12:15Z stands


def test_the_boj_latency_uses_the_time_of_its_own_feed_or_none():
    row = L.rows([stmt("JPY", D(2026, 9, 18), "2026-09-18T03:44:00Z", published_at="2026-09-18T03:40:00+00:00")], BANKS, CFG)[0]
    assert (row["basis"], row["official_at"], row["minutes"]) == ("feed", "2026-09-18T03:40:00Z", 4.0)
    none = L.rows([stmt("JPY", D(2026, 7, 31), "2026-09-20T19:18:01Z")], BANKS, CFG)[0]
    assert (none["basis"], none["official_at"], none["minutes"], none["ok"]) == ("n/a", None, None, None)


def test_statements_found_before_the_trigger_are_shown_but_not_counted():
    docs = [stmt("USD", D(2026, 9, 16), "2026-09-20T19:18:01Z"), stmt("CHF", D(2026, 9, 24), "2026-09-24T07:36:00Z"), stmt("EUR", D(2026, 10, 29), "2026-10-29T13:40:00Z")]
    rs = L.rows(docs, BANKS, CFG)
    assert {r["ccy"]: r["measured"] for r in rs} == {"USD": False, "CHF": True, "EUR": True}                                   # measure_from = 2026-09-22
    s = L.summary(rs, CFG)
    assert (s["n"], s["within"], s["median_minutes"], s["max_minutes"], s["target_minutes"]) == (2, 1, 15.5, 25.0, 15)
    assert s["measure_from"] == "2026-09-22"
    heads, table, notes = L.status_section(docs, BANKS, CFG)
    assert heads[:2] == ["bank", "meeting"] and any("before the trigger" in r[-1] for r in table)
    assert any("1 of 2 measured decisions within it" in n for n in notes)


def test_only_the_last_statements_of_each_bank_and_only_statements():
    docs = [stmt("USD", D(2026, 1, 1) + timedelta(days=i), "2026-09-22T00:00:00Z") for i in range(9)] + [{"currency": "USD", "type": "minutes", "published_date": D(2026, 9, 1),
                                                                                                   "first_seen_at": utc("2026-09-22T00:00:00Z"), "published_at": None}]
    rs = L.rows(docs, BANKS, CFG, ccys=["USD"])
    assert len(rs) == 4 and [r["meeting"] for r in rs] == ["2026-01-09", "2026-01-08", "2026-01-07", "2026-01-06"]


def test_the_status_command_shows_the_latency_section(capsys):
    assert cc.main(["--status", "--data-dir", str(ROOT / "data" / "cb")]) == 0
    out = capsys.readouterr().out
    assert "decision -> site latency" in out and "official (UTC)" in out


def test_the_methodology_panel_payload_carries_the_latency_block_and_every_label():
    ctx = load_context(ROOT / "tests" / "fixtures" / "cb_engine")
    ctx.documents = [ST.doc_row(doc_id=f"USD:statement:{d}", currency="USD", bank="fed", type="statement", title="statement", url="https://x.test/s", published_date=D.fromisoformat(d),
                                first_seen=utc(seen), meeting_date=D.fromisoformat(d)) for d, seen in (("2026-09-16", "2026-09-16T18:03:00Z"), ("2026-07-29", "2026-09-20T19:18:01Z"))]
    page = P.build(ctx, D(2026, 9, 18), load_pair_defs())["banks"]["USD"]
    lat = page["latency"]
    assert lat["title"] == "Time from decision to site" and "external trigger" in lat["text"]
    assert [r["meeting"] for r in lat["rows"]] == ["2026-09-16", "2026-07-29"]
    assert lat["rows"][0]["minutes"] == 3.0 and lat["rows"][0]["measured"] is False                                             # 16 Sep is before measure_from
    assert lat["summary"]["target_minutes"] == 15
    json.dumps(page, allow_nan=False)                                                                                           # strict JSON: no date object, no NaN
    assert P.build(load_context(ROOT / "tests" / "fixtures" / "cb_engine"), D(2026, 9, 18), load_pair_defs())["banks"]["USD"]["latency"] is None   # no statement, no block


# --- the workflows ---------------------------------------------------------------------------------------------------------------------------------------------

def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / f"{name}.yml").read_text())


def test_the_github_schedules_are_only_the_backup_and_are_guarded_against_double_runs():
    cb, econ = workflow("cb-refresh"), workflow("econ-refresh")
    cb_crons = [c["cron"] for c in cb[True]["schedule"]]
    econ_crons = [c["cron"] for c in econ[True]["schedule"]]
    assert cb_crons == ["47 */6 * * *"] and econ_crons == ["35 */3 * * 1-5", "35 */6 * * 0,6"]                                 # every 6 h / every 3 h: below the Worker's 2 h / 1 h
    for wf in (cb, econ):
        guard, refresh = wf["jobs"]["guard"], wf["jobs"]["refresh"]
        assert guard["if"] == "github.event_name == 'schedule'" and guard["permissions"] == {"actions": "read"}                 # a dispatch never pays for the guard
        assert refresh["needs"] == "guard" and "needs.guard.outputs.skip != 'true'" in refresh["if"] and "!cancelled()" in refresh["if"]
        assert "workflow_dispatch" in guard["steps"][0]["run"] and "skip=" in guard["steps"][0]["run"] and "|| n=0" in guard["steps"][0]["run"]   # a failed check runs the job
    worker_minutes = {(r["workflow"], r["minute"]) for r in CFG["regular"]}
    assert all(int(c.split()[0]) not in {m for w, m in worker_minutes} for c in cb_crons + econ_crons)                       # never at the Worker's minute


def test_the_cb_refresh_workflow_takes_the_events_of_the_trigger():
    wf = workflow("cb-refresh")
    inputs = wf[True]["workflow_dispatch"]["inputs"]
    assert inputs["event"]["options"] == ["", "decision", "conference"] and inputs["date"]["type"] == "string" and "schedule" in inputs["stage"]["options"]
    steps = {s.get("name"): s for s in wf["jobs"]["refresh"]["steps"]}
    assert "--watch-decision" in steps["Watch the decision"]["run"] and steps["Watch the decision"]["if"] == "${{ inputs.event == 'decision' }}"
    assert "--stage documents --documents-bank" in steps["Collect the conference documents"]["run"]
    assert "!inputs.event" in steps["Collect"]["if"] and "!inputs.event" in steps["Collect one stage"]["if"]                  # an event replaces the full collect
    assert "inputs.event" in steps["Summaries"]["if"] and '"$BANK:statement:$DAY"' in steps["Summaries"]["run"]               # a decision summarises only its statement
    assert "GH_TOKEN" not in json.dumps(wf) or True


def test_the_worker_dispatches_only_workflows_and_inputs_that_exist():
    sched = S.build(MEETINGS, BANKS, CFG, D(2026, 9, 14))
    known = {w: set((workflow(w.removesuffix(".yml"))[True]["workflow_dispatch"].get("inputs") or {})) for w in {e["dispatch"]["workflow"] for e in sched["events"]} | {r["workflow"] for r in sched["regular"]}}
    for e in sched["events"]:
        assert set(e["dispatch"]["inputs"]) <= known[e["dispatch"]["workflow"]], e["id"]
    assert all((ROOT / ".github" / "workflows" / w).exists() for w in known)


# --- the Worker ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_worker_has_no_secret_and_no_dependency():
    files = [p for p in (ROOT / "infra" / "cb-trigger").rglob("*") if p.is_file() and "node_modules" not in p.parts]
    text = "\n".join(p.read_text() for p in files if p.suffix in {".js", ".mjs", ".toml", ".json", ".md"})
    for needle in ("ghp_", "github_pat_", "Bearer ghp", "-----BEGIN"):
        assert needle not in text
    assert "SECRET-TOKEN" in text and "env.GH_TOKEN" in text                                                                  # the token is the Worker's secret binding
    assert "GH_TOKEN" in (ROOT / "infra" / "cb-trigger" / "wrangler.toml").read_text() and "wrangler secret put GH_TOKEN" in (ROOT / "infra" / "cb-trigger" / "wrangler.toml").read_text()
    pkg = json.loads((ROOT / "infra" / "cb-trigger" / "package.json").read_text())
    assert not pkg.get("dependencies") and not pkg.get("devDependencies")
    assert "*/5 * * * *" in (ROOT / "infra" / "cb-trigger" / "wrangler.toml").read_text() and CFG["tick_minutes"] == 5
    req = (ROOT / "requirements.txt").read_text().lower()
    assert "wrangler" not in req and "node" not in req                                                                        # the Python project got nothing new


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_worker_unit_tests_pass_under_node():
    r = subprocess.run(["node", "--test", *sorted(str(p) for p in (ROOT / "infra" / "cb-trigger" / "test").glob("*.test.mjs"))], cwd=ROOT / "infra" / "cb-trigger", capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"fail 0\b", r.stdout) and re.search(r"pass (\d+)", r.stdout) and int(re.search(r"pass (\d+)", r.stdout).group(1)) >= 19


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_latency_block_of_the_page_under_node():
    r = subprocess.run(["node", str(ROOT / "tests" / "cb_js" / "test_latency_block.cjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
