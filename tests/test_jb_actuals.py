"""Daily JBlanked actuals pull (Phase 3 structural fix): payload cleaning (A3),
window/state guard, pull orchestration (NO network — fetcher injected), raw
retention, and the actuals-pull freshness badge (A4).

Cleaning tests run on the REAL range payload captured 2026-07-12
(tests/fixtures/jb_range_raw_2026-07-12.json) — the empirically validated
±1h-duplicate / placeholder-0.0 pattern — never on synthetic dups."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import src.jb_actuals as J
from src.econ_calendar_ff import CANON_COLUMNS, parse_jblanked_range

FIXTURE = Path(__file__).parent / "fixtures" / "jb_range_raw_2026-07-12.json"
NOW = pd.Timestamp("2026-07-12 21:05:00")   # evening tick inside the pull window


def _parse_fixture(now_utc=NOW):
    return parse_jblanked_range(str(FIXTURE), now_utc=now_utc)


def _canon_row(cid, ccy, dt, actual, forecast=1.0):
    return {"canonical_id": cid, "currency": ccy, "name_raw": "raw",
            "name_canonical": "X", "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": 0.5,
            "released": pd.Timestamp(dt) < NOW, "source": "ff"}


def _frame(rows):
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


# --- cleaning (A3) — validated dup patterns from the real payload ------------

def test_clean_placeholder_dup_keeps_real_value_either_side():
    """A3: ±1h duplicates where one copy is the 0.0 'unreleased' placeholder →
    ONE row carrying the real value. Which copy is real is NOT consistent:
    CAD Employment Change has the real value on the EARLY copy, JPY Labor Cash
    Earnings on the LATE copy — both must survive."""
    cleaned = J.clean_jblanked_actuals(_parse_fixture())
    cad = cleaned[cleaned["canonical_id"] == "cad_employment_change"]
    assert len(cad) == 1 and cad.iloc[0]["actual"] == pytest.approx(18.2)
    jpy = cleaned[cleaned["canonical_id"] == "jpy_labor_cash_earnings"]
    assert len(jpy) == 1 and jpy.iloc[0]["actual"] == pytest.approx(3.2)
    # no 0.0 placeholder survives anywhere in the cleaned frame
    assert not ((cleaned["actual"] == 0.0) & cleaned["actual"].notna()).any()


def test_clean_both_real_dup_collapses_to_one():
    """A3: ±1h duplicates where BOTH copies carry the real actual → one row."""
    cleaned = J.clean_jblanked_actuals(_parse_fixture())
    rbnz = cleaned[cleaned["canonical_id"] == "nzd_rbnz_interest_rate_decision"]
    assert len(rbnz) == 1 and rbnz.iloc[0]["actual"] == pytest.approx(2.5)
    claims = cleaned[cleaned["canonical_id"] == "usd_initial_jobless_claims"]
    assert len(claims) == 1 and claims.iloc[0]["actual"] == pytest.approx(215.0)
    # one row per (canonical_id, date) across the whole frame
    key = [cleaned["canonical_id"], cleaned["datetime_utc"].dt.date]
    assert not cleaned.groupby(key).size().gt(1).any()


def test_clean_group_without_real_value_survives_as_schedule():
    """A3: a group with NO real actual is not dropped — it survives as a schedule
    row, and a released 0.0 placeholder is NOT ingested as a fake print.
    Mid-release snapshot from the real payload: at 2026-07-06 23:00 UTC the JPY
    Labor Cash Earnings early copy (22:30) is released with placeholder 0.0 and
    the late copy (23:30, the real 3.2) is still gated → the event must show as
    unreleased schedule, to be filled by the next day's pull."""
    cleaned = J.clean_jblanked_actuals(_parse_fixture(now_utc=pd.Timestamp("2026-07-06 23:00")))
    jpy = cleaned[cleaned["canonical_id"] == "jpy_labor_cash_earnings"]
    assert len(jpy) == 1                      # the group survives (schedule)
    assert pd.isna(jpy.iloc[0]["actual"])     # placeholder 0.0 not taken as a print
    # future events likewise survive as one schedule row per group, actual-less
    cad = cleaned[cleaned["canonical_id"] == "cad_employment_change"]   # 2026-07-10
    assert len(cad) == 1 and pd.isna(cad.iloc[0]["actual"])


def test_clean_aligns_timestamp_to_schedule_row():
    """The kept row's datetime is re-aligned to the faireconomy schedule row for
    the same (canonical_id, date) — faireconomy's hour is the correct UTC one —
    so the actual lands ON the schedule row at merge time (no ±1h phantom)."""
    schedule = _frame([
        _canon_row("cad_employment_change", "CAD", "2026-07-10 12:30", float("nan"), forecast=11.2),
        _canon_row("jpy_ppi", "JPY", "2026-07-09 23:50", float("nan"), forecast=6.8),
    ])
    cleaned = J.clean_jblanked_actuals(_parse_fixture(), schedule=schedule)
    cad = cleaned[cleaned["canonical_id"] == "cad_employment_change"].iloc[0]
    # real copy parsed at 11:30 UTC; schedule hour 12:30 wins, value kept
    assert cad["datetime_utc"] == pd.Timestamp("2026-07-10 12:30")
    assert cad["actual"] == pytest.approx(18.2)
    jpy = cleaned[cleaned["canonical_id"] == "jpy_ppi"].iloc[0]
    assert jpy["datetime_utc"] == pd.Timestamp("2026-07-09 23:50")
    assert jpy["actual"] == pytest.approx(7.1)
    # no schedule row for this one → the JBlanked timestamp is kept
    chf = cleaned[cleaned["canonical_id"] == "chf_unemployment_rate"].iloc[0]
    assert chf["datetime_utc"] == pd.Timestamp("2026-07-06 07:00")


def test_clean_empty_frame():
    out = J.clean_jblanked_actuals(pd.DataFrame(columns=CANON_COLUMNS))
    assert out.empty and list(out.columns) == CANON_COLUMNS


# --- window guard + state -----------------------------------------------------

def test_should_pull_window_and_once_per_day():
    assert not J.should_pull(pd.Timestamp("2026-07-13 20:59"), {})     # before window
    assert J.should_pull(pd.Timestamp("2026-07-13 21:01"), {})        # first tick in window
    done = {"last_success_utc_date": "2026-07-13"}
    assert not J.should_pull(pd.Timestamp("2026-07-13 22:01"), done)  # once per UTC day
    assert not J.should_pull(pd.Timestamp("2026-07-13 23:59"), done)
    assert J.should_pull(pd.Timestamp("2026-07-14 21:01"), done)      # next day pulls again


def test_should_pull_retries_after_failed_day():
    # yesterday succeeded, today's earlier attempts failed (state not advanced)
    # → every later tick keeps retrying until success
    st = {"last_success_utc_date": "2026-07-12"}
    for hour in (21, 22, 23):
        assert J.should_pull(pd.Timestamp(f"2026-07-13 {hour}:05"), st)


def test_state_roundtrip_and_corrupt_file(tmp_path):
    p = tmp_path / "state.json"
    assert J.load_state(p) == {}                       # missing → {}
    J.save_state({"last_success_utc_date": "2026-07-12"}, p)
    assert J.load_state(p)["last_success_utc_date"] == "2026-07-12"
    p.write_text("{not json")
    assert J.load_state(p) == {}                       # corrupt → {} (fail-open)


# --- raw persistence ----------------------------------------------------------

def test_save_raw_prunes_to_newest(tmp_path):
    base = pd.Timestamp("2026-07-01 21:05")
    for i in range(16):
        J.save_raw(f'[{i}]', base + pd.Timedelta(days=i), raw_dir=tmp_path, keep=14)
    kept = sorted(tmp_path.glob("jb_range_*.json"))
    assert len(kept) == 14
    assert kept[0].name == "jb_range_2026-07-03T210500Z.json"   # oldest two pruned
    assert kept[-1].read_text() == "[15]"                       # newest intact


# --- pull orchestration (no network; fetcher injected) --------------------------

def _seeded_parquet(tmp_path):
    """Parquet holding faireconomy schedule rows (correct UTC hours, no actuals)."""
    p = tmp_path / "ff.parquet"
    _frame([
        _canon_row("cad_employment_change", "CAD", "2026-07-10 12:30", float("nan"), forecast=11.2),
        _canon_row("usd_cpi", "USD", "2026-07-14 12:30", float("nan"), forecast=3.8),
    ]).to_parquet(p, index=False)
    return p


def test_pull_actuals_end_to_end(tmp_path):
    parquet, state, raw = _seeded_parquet(tmp_path), tmp_path / "s.json", tmp_path / "raw"
    rep = J.pull_actuals(now_utc=NOW, parquet_path=parquet, state_path=state,
                         raw_dir=raw, fetcher=lambda f, t: FIXTURE.read_text(), cfg={})
    assert rep["status"] == "ok"
    assert len(list(raw.glob("jb_range_*.json"))) == 1          # raw saved before parse
    assert json.loads(state.read_text())["last_success_utc_date"] == "2026-07-12"
    out = pd.read_parquet(parquet)
    cad = out[out["canonical_id"] == "cad_employment_change"]
    assert len(cad) == 1                                        # landed ON the schedule row
    assert cad.iloc[0]["datetime_utc"] == pd.Timestamp("2026-07-10 12:30")
    assert cad.iloc[0]["actual"] == pytest.approx(18.2)
    # untouched future schedule row survives
    assert pd.isna(out[out["canonical_id"] == "usd_cpi"].iloc[0]["actual"])

    # same evening, next hourly tick → skipped (one successful pull per UTC day)
    rep2 = J.pull_actuals(now_utc=NOW + pd.Timedelta(hours=1), parquet_path=parquet,
                          state_path=state, raw_dir=raw,
                          fetcher=lambda f, t: (_ for _ in ()).throw(AssertionError("must not fetch")),
                          cfg={})
    assert rep2 == {"status": "skipped", "reason": "already_pulled_today"}


def test_pull_skips_before_window_without_fetching(tmp_path):
    rep = J.pull_actuals(now_utc=pd.Timestamp("2026-07-12 20:05"),
                         parquet_path=tmp_path / "ff.parquet",
                         state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                         fetcher=lambda f, t: (_ for _ in ()).throw(AssertionError("must not fetch")),
                         cfg={})
    assert rep == {"status": "skipped", "reason": "before_window"}


def test_pull_fetch_failure_is_fail_open_and_retryable(tmp_path):
    parquet, state = _seeded_parquet(tmp_path), tmp_path / "s.json"
    before = pd.read_parquet(parquet)
    rep = J.pull_actuals(now_utc=NOW, parquet_path=parquet, state_path=state,
                         raw_dir=tmp_path / "raw",
                         fetcher=lambda f, t: (_ for _ in ()).throw(RuntimeError("HTTP 500")),
                         cfg={})
    assert rep["status"] == "fetch_failed"
    assert not state.exists()                                   # state NOT advanced
    pd.testing.assert_frame_equal(pd.read_parquet(parquet), before)   # last-good kept
    # → the next hourly tick retries
    assert J.should_pull(NOW + pd.Timedelta(hours=1), J.load_state(state))


def test_pull_empty_payload_does_not_advance_state(tmp_path):
    parquet, state, raw = _seeded_parquet(tmp_path), tmp_path / "s.json", tmp_path / "raw"
    before = pd.read_parquet(parquet)
    rep = J.pull_actuals(now_utc=NOW, parquet_path=parquet, state_path=state,
                         raw_dir=raw, fetcher=lambda f, t: "[]", cfg={})
    assert rep["status"] == "empty"
    assert not state.exists()
    assert len(list(raw.glob("jb_range_*.json"))) == 1          # raw still kept (forensics)
    pd.testing.assert_frame_equal(pd.read_parquet(parquet), before)


def test_pull_requests_trailing_7_day_range(tmp_path):
    seen = {}

    def fetcher(from_d, to_d):
        seen["from"], seen["to"] = from_d, to_d
        return FIXTURE.read_text()

    J.pull_actuals(now_utc=NOW, parquet_path=tmp_path / "ff.parquet",
                   state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                   fetcher=fetcher, cfg={})
    assert seen["to"].isoformat() == "2026-07-12"
    assert seen["from"].isoformat() == "2026-07-05"


# --- freshness badges (A4, A5) ---------------------------------------------------

def test_calendar_badge_tracks_active_ff_parquet(tmp_path, monkeypatch):
    """A5: with calendar_source=ff the calendar badge must watch the ACTIVE FF
    parquet (datetime_utc/actual) — not the frozen MT5 file, which would pin the
    badge red forever. Fresh FF actuals (2d old, under the 3d threshold) → not
    stale; last actual older than the threshold (9d, the incident shape) → stale."""
    import src.ff_refresh as FR
    from src.economic_render import _freshness
    ffp = tmp_path / "ff.parquet"
    monkeypatch.setattr(FR, "FF_PARQUET", ffp)
    monkeypatch.setattr(FR, "calendar_source", lambda cfg=None: "ff")
    monkeypatch.setattr(J, "STATE_JSON", tmp_path / "jb_last_pull.json")   # hermetic
    as_of = pd.Timestamp("2026-07-12 21:30")

    # published actual 2 days ago + a future schedule row (NaN) → fresh
    _frame([_canon_row("usd_cpi", "USD", "2026-07-10 12:30", 3.8),
            _canon_row("usd_cpi", "USD", "2026-07-14 12:30", float("nan"))]).to_parquet(ffp, index=False)
    f = _freshness(as_of=as_of)
    assert f["calendar"] == {"last_update": "2026-07-10T12:30:00",
                             "age_days": 2, "stale": False}

    # last published actual 9 days ago (the 2026-07-03..12 freeze shape) → stale
    _frame([_canon_row("usd_cpi", "USD", "2026-07-03 12:30", 3.8)]).to_parquet(ffp, index=False)
    f = _freshness(as_of=as_of)
    assert f["calendar"]["stale"] is True and f["calendar"]["age_days"] == 9
    assert f["any_stale"] is True

def test_actuals_pull_badge_after_two_missed_days(tmp_path, monkeypatch):
    """A4: simulate the pull failing for 2 days → the DISTINCT actuals_pull badge
    turns stale in the render payload (payload['freshness'] is _freshness())."""
    from src.economic_render import _freshness
    sp = tmp_path / "jb_last_pull.json"
    monkeypatch.setattr(J, "STATE_JSON", sp)
    as_of = pd.Timestamp("2026-07-14 22:00")

    # pulled yesterday evening → fresh, no badge
    J.save_state({"last_success_utc_date": "2026-07-13",
                  "last_success_at": "2026-07-13T21:05:00"}, sp)
    f = _freshness(as_of=as_of)
    assert f["actuals_pull"]["stale"] is False and f["actuals_pull"]["age_days"] == 1

    # last success 2 evenings ago (both windows since then failed) → stale badge
    J.save_state({"last_success_utc_date": "2026-07-12",
                  "last_success_at": "2026-07-12T21:05:00"}, sp)
    f = _freshness(as_of=as_of)
    assert f["actuals_pull"]["stale"] is True and f["actuals_pull"]["age_days"] == 2
    assert f["any_stale"] is True

    # no state file at all (pull NEVER succeeded) → fail-visible, not silent
    sp.unlink()
    f = _freshness(as_of=as_of)
    assert f["actuals_pull"] == {"last_update": None, "age_days": None, "stale": True}
