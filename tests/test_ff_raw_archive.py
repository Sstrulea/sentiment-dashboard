"""fix/alert-noise-and-ff-archive — FAZA 2: faireconomy weekly archive.

All file-system tests use `tmp_path` — never touches the real
data/ff_raw/. Network is never exercised (save_weekly_payload takes raw
text directly); fetch_and_archive_weekly's own network call is not
unit-tested here (it's a one-line requests.get, same pattern already used
elsewhere in this codebase).
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.ff_raw_archive import save_weekly_payload

CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


def _sample_payload(ccys=CCYS, title_suffix=""):
    events = [
        {"title": f"Sample Event{title_suffix}", "country": ccy,
         "date": "2026-08-01T12:00:00-04:00", "forecast": "1.0", "previous": "0.9"}
        for ccy in ccys
    ]
    return json.dumps(events)


def test_saved_payload_is_parsable_and_covers_all_8_currencies(tmp_path):
    payload = _sample_payload()
    report = save_weekly_payload(payload, as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    assert report["status"] == "saved"
    saved = json.loads((tmp_path / "ff_weekly_2026-08-01.json").read_text())
    countries = {e["country"] for e in saved}
    assert countries == set(CCYS)


def test_one_file_per_calendar_day_cadence_gate(tmp_path):
    day = pd.Timestamp("2026-08-01")
    r1 = save_weekly_payload(_sample_payload(), as_of=day, raw_dir=tmp_path)
    assert r1["status"] == "saved"
    # Second attempt SAME day, even with different content -> gated by cadence, not dedup.
    r2 = save_weekly_payload(_sample_payload(title_suffix=" v2"), as_of=day, raw_dir=tmp_path)
    assert r2["status"] == "already_saved_today"
    assert len(list(tmp_path.glob("ff_weekly_*.json"))) == 1


def test_dedup_skips_identical_content_on_a_new_day(tmp_path):
    payload = _sample_payload()
    save_weekly_payload(payload, as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    # A new day, but byte-identical content -> dedup, no new file.
    r2 = save_weekly_payload(payload, as_of=pd.Timestamp("2026-08-02"), raw_dir=tmp_path)
    assert r2["status"] == "unchanged"
    assert len(list(tmp_path.glob("ff_weekly_*.json"))) == 1


def test_dedup_does_not_skip_changed_content(tmp_path):
    save_weekly_payload(_sample_payload(), as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    r2 = save_weekly_payload(_sample_payload(title_suffix=" v2"),
                             as_of=pd.Timestamp("2026-08-02"), raw_dir=tmp_path)
    assert r2["status"] == "saved"
    assert len(list(tmp_path.glob("ff_weekly_*.json"))) == 2


def test_rotation_bounds_retained_files(tmp_path):
    base = pd.Timestamp("2026-01-01")
    for i in range(5):
        day = base + pd.Timedelta(days=i)
        save_weekly_payload(_sample_payload(title_suffix=f" day{i}"), as_of=day,
                           raw_dir=tmp_path, retain=3)
    files = sorted(tmp_path.glob("ff_weekly_*.json"))
    assert len(files) == 3
    # the 3 newest days survive
    assert [f.name for f in files] == [
        "ff_weekly_2026-01-03.json", "ff_weekly_2026-01-04.json", "ff_weekly_2026-01-05.json",
    ]


def test_rotation_reports_what_was_removed(tmp_path):
    base = pd.Timestamp("2026-01-01")
    for i in range(3):
        save_weekly_payload(_sample_payload(title_suffix=f" day{i}"), as_of=base + pd.Timedelta(days=i),
                           raw_dir=tmp_path, retain=2)
    r = save_weekly_payload(_sample_payload(title_suffix=" day3"), as_of=base + pd.Timedelta(days=3),
                           raw_dir=tmp_path, retain=2)
    assert r["status"] == "saved"
    assert r["rotated_out"] == ["ff_weekly_2026-01-02.json"]


def test_failed_ingest_never_saves_empty_payload(tmp_path):
    r = save_weekly_payload("", as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    assert r["status"] == "invalid"
    assert list(tmp_path.glob("*")) == []


def test_failed_ingest_never_saves_garbled_json(tmp_path):
    r = save_weekly_payload("{not valid json...", as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    assert r["status"] == "invalid"
    assert list(tmp_path.glob("*")) == []


def test_non_array_payload_rejected(tmp_path):
    r = save_weekly_payload(json.dumps({"not": "a list"}), as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    assert r["status"] == "invalid"
    assert list(tmp_path.glob("*")) == []


def test_empty_array_rejected_as_invalid_not_saved(tmp_path):
    r = save_weekly_payload("[]", as_of=pd.Timestamp("2026-08-01"), raw_dir=tmp_path)
    assert r["status"] == "invalid"
    assert list(tmp_path.glob("*")) == []
