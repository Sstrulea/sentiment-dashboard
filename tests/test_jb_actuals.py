"""Daily JBlanked actuals pull (Phase 3 structural fix): payload cleaning (A3),
window/state guard, pull orchestration (NO network — fetcher injected), raw
retention, and the actuals-pull freshness badge (A4).

Cleaning tests run on the REAL range payload captured 2026-07-12
(tests/fixtures/jb_range_raw_2026-07-12.json) — the empirically validated
±1h-duplicate / placeholder-0.0 pattern — never on synthetic dups."""
from __future__ import annotations

import json
from pathlib import Path

from datetime import date
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


# --- preserve_zero_actuals (fix/ingest-preserve-zeros) ------------------------

def test_clean_preserve_zero_actuals_false_matches_default_on_real_fixture():
    """Default (False) must be bit-identical to explicitly passing False, on the
    real payload used by every other cleaning test — no behavior change for the
    existing archive_backfill caller, which never passes the new kwarg."""
    default = J.clean_jblanked_actuals(_parse_fixture())
    explicit_false = J.clean_jblanked_actuals(_parse_fixture(), preserve_zero_actuals=False)
    pd.testing.assert_frame_equal(default, explicit_false)


def test_clean_preserve_zero_actuals_true_keeps_lone_zero():
    """preserve_zero_actuals=True: a group with ONLY a 0.0 actual (no real value
    anywhere) is no longer nulled — the 0.0 survives as the ingested actual, so
    ff_scoring's can_be_zero widening can judge it instead of never seeing it."""
    jb = _frame([_canon_row("eur_flat_print", "EUR", "2026-07-10 12:30", 0.0)])
    cleaned = J.clean_jblanked_actuals(jb, preserve_zero_actuals=True)
    row = cleaned[cleaned["canonical_id"] == "eur_flat_print"].iloc[0]
    assert row["actual"] == pytest.approx(0.0)

    # default (False) still nulls the same lone-zero group
    cleaned_default = J.clean_jblanked_actuals(jb)
    row_default = cleaned_default[cleaned_default["canonical_id"] == "eur_flat_print"].iloc[0]
    assert pd.isna(row_default["actual"])


def test_clean_preserve_zero_actuals_real_still_beats_zero_in_dst_dup():
    """The 'real beats zero' pick inside a ±1h DST duplicate is untouched by the
    flag either way — only the final all-zero/no-real nulling step changes."""
    for flag in (False, True):
        cleaned = J.clean_jblanked_actuals(_parse_fixture(), preserve_zero_actuals=flag)
        cad = cleaned[cleaned["canonical_id"] == "cad_employment_change"]
        assert len(cad) == 1 and cad.iloc[0]["actual"] == pytest.approx(18.2)


def test_clean_preserve_zero_actuals_aligns_timestamp_to_schedule():
    """Re-alignment to the faireconomy schedule row (step 3) still applies to a
    row whose 0.0 actual was preserved rather than nulled."""
    jb = _frame([_canon_row("eur_flat_print", "EUR", "2026-07-10 12:30", 0.0)])
    schedule = _frame([_canon_row("eur_flat_print", "EUR", "2026-07-10 12:00",
                                  float("nan"), forecast=2.0)])
    cleaned = J.clean_jblanked_actuals(jb, schedule=schedule, preserve_zero_actuals=True)
    row = cleaned.iloc[0]
    assert row["actual"] == pytest.approx(0.0)
    assert row["datetime_utc"] == pd.Timestamp("2026-07-10 12:00")


# --- window guard + state -----------------------------------------------------

def test_should_pull_due_window_and_once_per_window():
    """Due-window guard: pull while the last SUCCESS predates the most recent
    18:00 UTC instant; at most one successful pull per window."""
    assert J.should_pull(pd.Timestamp("2026-07-13 06:59"), {})         # never pulled -> due
    assert J.should_pull(pd.Timestamp("2026-07-13 18:01"), {})         # evening window
    done = {"last_success_at": "2026-07-13T18:30:00"}
    assert not J.should_pull(pd.Timestamp("2026-07-13 22:01"), done)   # window covered
    assert not J.should_pull(pd.Timestamp("2026-07-14 10:00"), done)   # same window (< 18:00)
    assert J.should_pull(pd.Timestamp("2026-07-14 18:01"), done)       # next window


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
    assert rep2 == {"status": "skipped", "reason": "window_covered"}


def test_pull_skips_when_window_covered_without_fetching(tmp_path):
    (tmp_path / "s.json").write_text('{"last_success_at": "2026-07-11T19:00:00"}')
    rep = J.pull_actuals(now_utc=pd.Timestamp("2026-07-12 05:05"),
                         parquet_path=tmp_path / "ff.parquet",
                         state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                         fetcher=lambda f, t: (_ for _ in ()).throw(AssertionError("must not fetch")),
                         cfg={})
    assert rep == {"status": "skipped", "reason": "window_covered"}


def test_pull_fetch_failure_is_fail_open_and_retryable(tmp_path):
    parquet, state = _seeded_parquet(tmp_path), tmp_path / "s.json"
    before = pd.read_parquet(parquet)
    rep = J.pull_actuals(now_utc=NOW, parquet_path=parquet, state_path=state,
                         raw_dir=tmp_path / "raw",
                         fetcher=lambda f, t: (_ for _ in ()).throw(RuntimeError("HTTP 500")),
                         cfg={})
    assert rep["status"] == "fetch_failed"
    st = J.load_state(state)                                    # success NOT advanced
    assert "last_success_at" not in st and st["last_status"] == "fetch_failed"
    pd.testing.assert_frame_equal(pd.read_parquet(parquet), before)   # last-good kept
    # → the next hourly tick retries
    assert J.should_pull(NOW + pd.Timedelta(hours=1), J.load_state(state))


def test_pull_empty_payload_does_not_advance_state(tmp_path):
    parquet, state, raw = _seeded_parquet(tmp_path), tmp_path / "s.json", tmp_path / "raw"
    before = pd.read_parquet(parquet)
    rep = J.pull_actuals(now_utc=NOW, parquet_path=parquet, state_path=state,
                         raw_dir=raw, fetcher=lambda f, t: "[]", cfg={})
    assert rep["status"] == "empty"
    assert "last_success_at" not in J.load_state(state)
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
    assert seen["to"].isoformat() == "2026-07-13"  # JB to este EXCLUSIV -> cere ziua+1
    assert seen["from"].isoformat() == "2026-07-05"


# --- freshness badges (A4, A5) ---------------------------------------------------

def test_calendar_badge_tracks_active_ff_parquet(tmp_path, monkeypatch):
    """A5, UPDATED by fix/calendar-freshness-measures-source: with
    calendar_source=ff the calendar badge now watches ff_refresh's OWN
    last-successful-run state (data/ff_last_refresh.json) — SOURCE, not
    content. This test used to seed the FF parquet directly and assert the
    badge tracked the most recent PUBLISHED actual; that was exactly the
    structural false-positive the fix closes (a quiet window with no new
    prints — weekend, no scheduled releases — read as "stale" regardless of
    whether ff_refresh itself was running fine). Reassigned here to seed
    ff_refresh's state file instead; the fresh/stale transition and
    any_stale rollup this test guards are otherwise unchanged. See
    test_calendar_badge_survives_a_quiet_content_window below for the
    regression case this fix actually targets."""
    import src.ff_refresh as FR
    from src.economic_render import _freshness
    fp = tmp_path / "ff_last_refresh.json"
    monkeypatch.setattr(FR, "calendar_source", lambda cfg=None: "ff")
    monkeypatch.setattr(FR, "STATE_JSON", fp)
    monkeypatch.setattr(J, "STATE_JSON", tmp_path / "jb_last_pull.json")   # hermetic
    as_of = pd.Timestamp("2026-07-12 21:30")

    # last successful ff_refresh 2 days ago → fresh
    FR.save_state({"last_success_at": "2026-07-10T12:30:00"}, fp)
    f = _freshness(as_of=as_of)
    assert f["calendar"] == {"last_update": "2026-07-10T12:30:00",
                             "age_days": 2, "stale": False}

    # last successful run 9 days ago (the source itself stalled) → stale
    FR.save_state({"last_success_at": "2026-07-03T12:30:00"}, fp)
    f = _freshness(as_of=as_of)
    assert f["calendar"]["stale"] is True and f["calendar"]["age_days"] == 9
    assert f["any_stale"] is True


def test_calendar_badge_survives_a_quiet_content_window(tmp_path, monkeypatch):
    """THE regression this fix targets: ff_refresh ran successfully TODAY
    (state file fresh), but the FF parquet's most recent PUBLISHED actual is
    old (a quiet window — weekend, no scheduled releases, exactly like the
    real 2026-08-11 case: last actual 2026-08-07, refresh ran fine at
    08-11T05:10). Under the OLD content-based reading this was a false-
    positive STALE badge; under source-based reading it must be fresh."""
    import src.ff_refresh as FR
    from src.economic_render import _freshness
    fp = tmp_path / "ff_last_refresh.json"
    monkeypatch.setattr(FR, "calendar_source", lambda cfg=None: "ff")
    monkeypatch.setattr(FR, "STATE_JSON", fp)
    monkeypatch.setattr(J, "STATE_JSON", tmp_path / "jb_last_pull.json")
    as_of = pd.Timestamp("2026-08-11 14:00")

    FR.save_state({"last_success_at": "2026-08-11T05:10:39"}, fp)   # ran fine today
    f = _freshness(as_of=as_of)
    assert f["calendar"]["stale"] is False and f["calendar"]["age_days"] == 0


def test_calendar_badge_absent_state_is_never_not_silently_fresh(tmp_path, monkeypatch):
    """No state file at all (ff_refresh never succeeded, or history predates
    this fix) → fail-visible, same contract as actuals_pull's own absent-
    state case — never a silent stale=False."""
    import src.ff_refresh as FR
    from src.economic_render import _freshness
    fp = tmp_path / "ff_last_refresh.json"   # never written
    monkeypatch.setattr(FR, "calendar_source", lambda cfg=None: "ff")
    monkeypatch.setattr(FR, "STATE_JSON", fp)
    monkeypatch.setattr(J, "STATE_JSON", tmp_path / "jb_last_pull.json")
    f = _freshness(as_of=pd.Timestamp("2026-08-11 14:00"))
    assert f["calendar"] == {"last_update": None, "age_days": None, "stale": True}


def test_calendar_badge_mt5_rollback_still_content_based(tmp_path, monkeypatch):
    """Out of scope for this fix, explicitly: the mt5 rollback path (pre-
    Phase-3) has no ff_refresh state to read and keeps the old content-based
    reading over data/economic_calendar.parquet, unchanged."""
    import src.ff_refresh as FR
    from src.economic_render import _freshness
    monkeypatch.setattr(FR, "calendar_source", lambda cfg=None: "mt5")
    monkeypatch.setattr(J, "STATE_JSON", tmp_path / "jb_last_pull.json")
    mt5p = tmp_path / "mt5.parquet"
    monkeypatch.setattr("src.economic_render.PARQUET", mt5p)
    as_of = pd.Timestamp("2026-07-12 21:30")
    pd.DataFrame([{"currency": "USD", "indicator_key": "cpi_yoy",
                  "release_dt": pd.Timestamp("2026-07-10 12:30"), "actual": 3.8,
                  "consensus": 3.7, "previous": 3.6, "source": "mt5"}]).to_parquet(mt5p, index=False)
    f = _freshness(as_of=as_of)
    assert f["calendar"] == {"last_update": "2026-07-10T12:30:00", "age_days": 2, "stale": False}


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


def test_should_pull_catches_up_at_any_hour_after_wakeup():
    """REGRESSION (prod 2026-07-15): the laptop sleeps through 18:00 UTC, so a
    missed window must be recovered at the FIRST tick after wake-up, at any
    hour. The old rule ("morning fallback only if last success != yesterday")
    deadlocked: a morning pull marked the day done and blocked the next morning
    while the evening window was never reached -> actuals landed ~2 days late."""
    behind = {"last_success_at": "2026-07-13T07:51:35"}                # real prod state
    for hour in ("06:43", "09:43", "10:15", "14:28", "17:00"):         # real skipped ticks
        assert J.should_pull(pd.Timestamp(f"2026-07-15 {hour}"), behind), hour
    morning = {"last_success_at": "2026-07-15T10:15:00"}
    assert not J.should_pull(pd.Timestamp("2026-07-15 11:15"), morning)  # no hammering
    assert J.should_pull(pd.Timestamp("2026-07-15 18:05"), morning)      # evening = new window


def test_should_pull_migrates_legacy_date_only_state():
    """B6: the pre-existing state format (date only) is read as that day's 18:00Z."""
    legacy = {"last_success_utc_date": "2026-07-13"}
    assert not J.should_pull(pd.Timestamp("2026-07-13 22:00"), legacy)  # covered
    assert J.should_pull(pd.Timestamp("2026-07-15 10:00"), legacy)      # two windows behind
    assert J.last_success_ts(legacy) == pd.Timestamp("2026-07-13 18:00")


def test_range_span_is_gap_aware(tmp_path, monkeypatch):
    """B5: the range span covers the whole gap (min 7d, capped at 60d)."""
    seen = {}
    def fake(f, t):
        seen["from"], seen["to"] = f, t
        raise RuntimeError("stop after span check")
    J.pull_actuals(now_utc=pd.Timestamp("2026-07-15 10:00"),
                   parquet_path=tmp_path / "ff.parquet",
                   state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                   fetcher=fake, cfg={}, force=True)
    assert seen["from"] == date(2026, 7, 8)                             # no state -> 7d floor
    (tmp_path / "s.json").write_text('{"last_success_at": "2026-07-03T18:00:00"}')
    J.pull_actuals(now_utc=pd.Timestamp("2026-07-15 10:00"),
                   parquet_path=tmp_path / "ff.parquet",
                   state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                   fetcher=fake, cfg={}, force=True)
    assert seen["from"] == date(2026, 7, 2)                             # 12d gap -> 13d span
    (tmp_path / "s.json").write_text('{"last_success_at": "2025-01-01T18:00:00"}')
    J.pull_actuals(now_utc=pd.Timestamp("2026-07-15 10:00"),
                   parquet_path=tmp_path / "ff.parquet",
                   state_path=tmp_path / "s.json", raw_dir=tmp_path / "raw",
                   fetcher=fake, cfg={}, force=True)
    assert seen["from"] == date(2026, 5, 16)                            # capped at 60d


# --- flagged_bad quality lookup (fix/can-be-zero-transform, 2026-08 audit) --

def test_build_flagged_bad_lookup_whitelist_on_real_fixture():
    # real capture, not synthetic -- zero raises expected against known vocab
    lookup = J.build_flagged_bad_lookup(raw_dir=Path("/nonexistent"), archive_path=FIXTURE)
    assert len(lookup) > 0
    assert all(isinstance(v, bool) for v in lookup.values())


def test_build_flagged_bad_lookup_raises_on_unrecognized_quality(tmp_path):
    # a value present but in NEITHER QUALITY_ACCEPTED nor QUALITY_FLAGGED must
    # crash -- whitelist discipline, not "anything not explicitly bad is fine".
    # Deliberately NOT added to either set (that's the whole point of the test).
    bogus = tmp_path / "jb_range_bogus.json"
    bogus.write_text(json.dumps([{
        "Name": "CPI m/m", "Currency": "CHF", "Date": "2026.07.02 09:30:00",
        "Actual": 0.0, "Forecast": 0.1, "Previous": 0.2,
        "Quality": "Revised Data", "Strength": "Strong Data",
    }]))
    with pytest.raises(ValueError, match="unrecognized Quality 'Revised Data'"):
        J.build_flagged_bad_lookup(raw_dir=tmp_path, archive_path=None)


def test_build_flagged_bad_lookup_raises_on_unrecognized_strength(tmp_path):
    bogus = tmp_path / "jb_range_bogus.json"
    bogus.write_text(json.dumps([{
        "Name": "CPI m/m", "Currency": "CHF", "Date": "2026.07.02 09:30:00",
        "Actual": 0.0, "Forecast": 0.1, "Previous": 0.2,
        "Quality": "Good Data", "Strength": "Moderate Data",
    }]))
    with pytest.raises(ValueError, match="unrecognized Strength 'Moderate Data'"):
        J.build_flagged_bad_lookup(raw_dir=tmp_path, archive_path=None)


def test_build_flagged_bad_lookup_absent_field_is_not_raise_and_not_clean(tmp_path):
    # field ABSENT (key missing on the raw dict entirely) is a THIRD state,
    # distinct from "present but unrecognized" (raises) and "present, known"
    # (accepted/flagged) -- no signal, no raise, no lookup entry created.
    absent = tmp_path / "jb_range_absent.json"
    absent.write_text(json.dumps([{
        "Name": "CPI m/m", "Currency": "CHF", "Date": "2026.07.02 09:30:00",
        "Actual": 0.0, "Forecast": 0.1, "Previous": 0.2,
        # no "Quality", no "Strength" key at all
    }]))
    lookup = J.build_flagged_bad_lookup(raw_dir=tmp_path, archive_path=None)
    assert ("CHF", "CPI m/m", date(2026, 7, 2)) not in lookup




# --- audit F5: observability of every real call (no behavior change) -------------

class _FakeResp:
    def __init__(self, status, text, headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


def _patch_requests(monkeypatch, resp):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)


def test_401_body_and_rate_limit_headers_are_persisted_without_the_key(tmp_path, monkeypatch):
    key = "k" * 32
    body = '{"detail": "Daily request limit reached for key ' + key + '"}' + " " * 400
    _patch_requests(monkeypatch, _FakeResp(401, body, {"X-RateLimit-Remaining": "0",
                                                        "Retry-After": "3600",
                                                        "Content-Type": "application/json"}))
    monkeypatch.setenv("JBLANKED_API_KEY", key)
    state = tmp_path / "s.json"
    (state).write_text('{"last_success_at": "2026-07-10T19:00:00", "rows": 5}')
    rep = J.pull_actuals(now_utc=NOW, parquet_path=_seeded_parquet(tmp_path),
                         state_path=state, raw_dir=tmp_path / "raw", cfg={})
    assert rep == {"status": "fetch_failed"}
    st = J.load_state(state)
    assert st["last_success_at"] == "2026-07-10T19:00:00" and st["rows"] == 5   # untouched
    assert st["last_attempt_at"] == NOW.isoformat() and st["last_http_status"] == 401
    assert st["last_status"] == "fetch_failed"
    assert key not in state.read_text() and "***" in st["last_error"]
    assert len(st["last_error"]) <= J.ERROR_BODY_CHARS
    assert st["last_rate_limit_headers"] == {"X-RateLimit-Remaining": "0", "Retry-After": "3600"}
    # behavior unchanged: still due, the next tick retries
    assert J.should_pull(NOW + pd.Timedelta(hours=1), st)


def test_success_records_the_attempt_too(tmp_path, monkeypatch):
    _patch_requests(monkeypatch, _FakeResp(200, FIXTURE.read_text(), {"X-RateLimit-Limit": "1"}))
    monkeypatch.setenv("JBLANKED_API_KEY", "k" * 32)
    state = tmp_path / "s.json"
    rep = J.pull_actuals(now_utc=NOW, parquet_path=_seeded_parquet(tmp_path),
                         state_path=state, raw_dir=tmp_path / "raw", cfg={})
    st = J.load_state(state)
    assert rep["status"] == "ok" and st["last_status"] == "ok" and st["last_error"] is None
    assert st["last_http_status"] == 200 and st["last_rate_limit_headers"] == {"X-RateLimit-Limit": "1"}
    assert st["last_success_at"] == st["last_attempt_at"] == NOW.isoformat()


def test_skipped_tick_writes_nothing(tmp_path):
    state = tmp_path / "s.json"
    state.write_text('{"last_success_at": "2026-07-11T19:00:00"}')
    J.pull_actuals(now_utc=pd.Timestamp("2026-07-12 05:05"), parquet_path=tmp_path / "ff.parquet",
                   state_path=state, raw_dir=tmp_path / "raw", cfg={},
                   fetcher=lambda f, t: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert state.read_text() == '{"last_success_at": "2026-07-11T19:00:00"}'
