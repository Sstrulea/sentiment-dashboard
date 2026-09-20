"""Evidence for config `effective_rule`: every rate change since 2025-09 in the official / BIS series against the rule.

    python scripts/cb_check_effective_dates.py [--since 2025-09-01]

1. BIS vs the official series: BIS WS_CBPOL is dated by the EFFECTIVE date for the banks that have an official series
   (change dates identical) - so BIS is read exactly like the official series (no offset).
2. Each bank's effective_rule against the change dates of its series (official level series; BIS for JPY and NZD).
   Decision days = the bank-local dates of the Forex Factory decision rows (placeholders included: only their DATE is used).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import cb_collect as cc                                   # noqa: E402
from src import cb_datasets as ds                                  # noqa: E402
from src.cb_calendar import load_calendars                          # noqa: E402
from src.cb_compute.effective_check import check_rule, compare_change_dates, local_days, series_changes  # noqa: E402
from src.cb_sources.official import parse_bis                       # noqa: E402

AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "CHF": "CH"}
OFFICIAL = {"USD": "fred:DFEDTARU", "EUR": "ecb:DFR", "GBP": "boe:IUDBEDR", "CAD": "boc:V39079", "AUD": "rba:FIRMMCRTD",
            "CHF": "snb:LZ"}
# Decision days the FF calendar does not carry (its BoJ rows of Dec 2025 are missing): the official page proves them.
EXTRA_DECISION_DAYS = {"JPY": [date(2025, 12, 19)]}      # https://www.boj.or.jp/en/mopo/mpmdeci/state_2025/index.htm ("Dec. 19, 2025")
BIS_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.{areas}?startPeriod={since}&format=csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=date.fromisoformat, default=date(2025, 9, 1))
    a = ap.parse_args()
    banks, cals = ds.load_banks(), load_calendars()
    bis = parse_bis(requests.get(BIS_URL.format(areas="+".join(AREA.values()), since=a.since.replace(day=1)), timeout=90).text, a.since)
    store = cc.load_official_store(cc.Paths())
    series = lambda sid: [(r["date"], r["value"]) for r in store.values() if r["series_id"] == sid]      # noqa: E731
    ff = pd.read_parquet(ds.FF_PARQUET)

    print("== 1. BIS vs the official series: days between the change dates (0 = BIS is dated by the effective date)")
    for cur, sid in OFFICIAL.items():
        off = series_changes(series(sid), a.since)
        last = max((d for d, _ in bis.get(AREA[cur], [])), default=None)
        pairs = compare_change_dates(off, series_changes(bis.get(AREA[cur], []), a.since))
        print(f"  {cur} {sid}: " + (", ".join(f"{c.date} " + (f"BIS {d:+d}d" if last and c.date <= last else f"BIS not there yet (last obs {last})")
                                          for c, o, d in pairs) or "no change in the window"))

    print("\n== 2. effective_rule vs the change dates")
    for cur, cfg in banks.items():
        rule, cal = cfg["effective_rule"], cals[cfg["calendar_id"]]
        name = cfg["policy_rate"].get("ff_name")
        rows = ff[(ff["currency"] == cur) & (ff["name_raw"] == name) & (ff["datetime_utc"] >= "2025-08-01")]
        days = sorted(set(local_days([t.to_pydatetime() for t in rows["datetime_utc"]], cfg["tz"])) | set(EXTRA_DECISION_DAYS.get(cur, [])))
        src = OFFICIAL.get(cur) if cur not in ("JPY", "NZD") else f"bis:{AREA[cur][:2]}"
        pts = series(OFFICIAL[cur]) if cur in OFFICIAL else [(d, v) for d, v in bis.get(AREA[cur], [])]
        checks = check_rule(rule, cal, series_changes(pts, a.since), days)
        ok = sum(c.ok for c in checks)
        print(f"  {cur} rule {rule['kind']} {rule.get('days', '')} ({src}): {ok}/{len(checks)} changes match")
        for c in checks:
            print(f"      {c.change.date} {c.change.before:g}->{c.change.after:g}  decision {c.decision}  {'OK' if c.ok else 'MISMATCH (rule -> ' + str(c.predicted) + ')'}")


if __name__ == "__main__":
    main()
