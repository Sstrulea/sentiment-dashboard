"""Validate George's calls (data/registry/apeluri_george.csv, valute_george.csv).

A row is ACCEPTED only when it is well formed AND the commit that first added it to
the file is dated before the start of its outcome window (checked from git):

  apeluri (central banks)  window start = the bank's 2Y close on the first business day
                           after the call date (a Friday / Saturday / Sunday call starts
                           at Monday's close); the window lasts 20 business days;
                           hawkish = 2Y higher at the end of the window than at its start;
                           an unchanged 2Y is excluded (pre-registered, phase 5 stage 0)
  valute (currencies)      start = the first H.10 noon New York fixing after the call date;
                           outcomes at 1 / 2 / 4 weeks vs the basket of the 8

Columns
  apeluri: data (YYYY-MM-DD), banca {Fed, ECB, BoE, BoJ, BoC, RBA, RBNZ, SNB},
           directie {hawkish, dovish, fara_opinie}, probabilitate integer 50-95
           (fara_opinie -> 50), motiv {munca, inflatie, crestere, comunicare, risc, pricing, altul}, nota
  valute:  data, tari (2 codes of USD EUR GBP JPY CAD AUD NZD CHF, separated by space / ; / |),
           slabe (2 codes, disjoint from tari), nota

    python scripts/registry/validate_calls.py [--json]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CALLS = ROOT / "data" / "registry" / "apeluri_george.csv"
FXCALLS = ROOT / "data" / "registry" / "valute_george.csv"
BANKS = {"Fed": "USD", "ECB": "EUR", "BoE": "GBP", "BoJ": "JPY", "BoC": "CAD", "RBA": "AUD", "RBNZ": "NZD", "SNB": "CHF"}
DIRECTIONS = {"hawkish", "dovish", "fara_opinie"}
REASONS = {"munca", "inflatie", "crestere", "comunicare", "risc", "pricing", "altul"}
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
CLOSES = {"USD": ("15:30", "America/New_York"), "EUR": ("17:30", "Europe/Berlin"),
          "GBP": ("16:30", "Europe/London"), "JPY": ("15:00", "Asia/Tokyo"),
          "CAD": ("16:00", "America/Toronto"), "AUD": ("16:30", "Australia/Sydney"),
          "NZD": ("16:30", "Pacific/Auckland"), "CHF": ("17:00", "Europe/Zurich")}
H10 = ("12:00", "America/New_York")
WINDOW_BD = 20


def next_business_day(d: date) -> date:
    t = pd.Timestamp(d) + pd.offsets.BDay(1)
    return t.date()


def local_instant(d: date, hhmm: str, tz: str) -> pd.Timestamp:
    h, m = (int(x) for x in hhmm.split(":"))
    return (pd.Timestamp(d) + pd.Timedelta(hours=h, minutes=m)).tz_localize(tz).tz_convert("UTC")


def bank_window(call_date: date, bank: str) -> tuple[pd.Timestamp, date, date]:
    """(start instant UTC, start day, end day) of the 2Y window."""
    start = next_business_day(call_date)
    end = (pd.Timestamp(start) + pd.offsets.BDay(WINDOW_BD)).date()
    return local_instant(start, *CLOSES[BANKS[bank]]), start, end


def fx_start(call_date: date) -> pd.Timestamp:
    return local_instant(next_business_day(call_date), *H10)


def first_commit_time(path: Path, line: str, root: Path = ROOT) -> pd.Timestamp | None:
    """Committer time of the oldest commit whose version of `path` contains `line`."""
    rel = str(path.relative_to(root))
    shas = subprocess.run(["git", "log", "--format=%H %cI", "--reverse", "--", rel], cwd=root,
                          capture_output=True, text=True).stdout.split("\n")
    for row in shas:
        if not row.strip():
            continue
        sha, when = row.split(" ", 1)
        blob = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=root, capture_output=True, text=True).stdout
        if line in blob.splitlines():
            return pd.Timestamp(when).tz_convert("UTC")
    return None


def _codes(s: str) -> list[str]:
    return [x for x in re.split(r"[\s;|/]+", (s or "").strip().upper()) if x]


def check_call(row: dict) -> str | None:
    try:
        d = date.fromisoformat(row["data"].strip())
    except Exception:
        return "data is not YYYY-MM-DD"
    if row["banca"] not in BANKS:
        return f"banca {row['banca']!r} not in {sorted(BANKS)}"
    if row["directie"] not in DIRECTIONS:
        return f"directie {row['directie']!r} not in {sorted(DIRECTIONS)}"
    try:
        p = int(row["probabilitate"])
    except Exception:
        return "probabilitate is not an integer"
    if str(p) != row["probabilitate"].strip() or not 50 <= p <= 95:
        return "probabilitate must be an integer 50-95"
    if row["directie"] == "fara_opinie" and p != 50:
        return "fara_opinie requires probabilitate 50"
    if row["motiv"] not in REASONS:
        return f"motiv {row['motiv']!r} not in {sorted(REASONS)}"
    del d
    return None


def check_fx(row: dict) -> str | None:
    try:
        date.fromisoformat(row["data"].strip())
    except Exception:
        return "data is not YYYY-MM-DD"
    t, s = _codes(row["tari"]), _codes(row["slabe"])
    if len(t) != 2 or len(s) != 2 or set(t) & set(s) or not set(t + s) <= set(CCYS):
        return "tari / slabe must be 2 + 2 distinct codes of the 8 currencies"
    return None


def validate(path: Path, kind: str, root: Path = ROOT, now: pd.Timestamp | None = None) -> list[dict]:
    out = []
    if not path.exists():
        return out
    lines = path.read_text().splitlines()
    for i, row in enumerate(csv.DictReader(lines), start=2):
        raw = lines[i - 1]
        rec = {"file": path.name, "line": i, **row}
        err = check_call(row) if kind == "bank" else check_fx(row)
        if err:
            out.append({**rec, "status": "rejected", "reason": err})
            continue
        d = date.fromisoformat(row["data"].strip())
        if kind == "bank":
            start, s_day, e_day = bank_window(d, row["banca"])
            rec.update(window_start=str(start), window_start_day=str(s_day), window_end_day=str(e_day))
        else:
            start = fx_start(d)
            rec.update(window_start=str(start))
        committed = first_commit_time(path, raw, root)
        if committed is None:
            out.append({**rec, "status": "rejected", "reason": "not committed (line not found in any commit)"})
        elif committed >= start:
            out.append({**rec, "status": "rejected", "committed": str(committed),
                        "reason": f"committed {committed} at or after the window start {start}"})
        else:
            out.append({**rec, "status": "accepted", "committed": str(committed)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = validate(CALLS, "bank") + validate(FXCALLS, "fx")
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for r in res:
            print(f"{r['file']}:{r['line']} {r['status']}" + (f" — {r['reason']}" if r.get("reason") else "")
                  + (f" (window start {r['window_start']})" if r.get("window_start") else ""))
        print(f"{sum(r['status'] == 'accepted' for r in res)} accepted, "
              f"{sum(r['status'] == 'rejected' for r in res)} rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
