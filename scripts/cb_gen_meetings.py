"""Generate data/cb/meetings.yaml from the official calendar pages.

    python scripts/cb_gen_meetings.py [--evidence-date YYYY-MM-DD] [--out data/cb/meetings.yaml] [--check]

Fetches the Fed, ECB, BoE, BoJ, BoC, RBA and SNB calendar pages (parsers: src/cb_sources/calendar.py), reads the ECB
meetings already held from the FF decision rows (the ECB page lists only the future) and the RBNZ published calendar
from data/cb/manual/rbnz.yaml; rules in src/cb_sources/meetings.py. `--check` writes nothing: it exits 1 and prints a
diff when the file on disk differs from what the pages say today (the weekly check in --status warns the same way).
"""
from __future__ import annotations

import argparse
import difflib
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import cb_datasets as ds                                        # noqa: E402
from src.cb_compute.effective_check import local_days                    # noqa: E402
from src.cb_sources.calendar import PARSERS, CalendarPages              # noqa: E402
from src.cb_sources.meetings import build                                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--out", default=str(ROOT / "data" / "cb" / "meetings.yaml"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    banks = ds.load_banks()
    src = CalendarPages()
    parsed = {}
    for bank in tuple(PARSERS) + ("CHF",):
        page = src.meetings(bank, banks[bank]["calendar_url"])
        if page is None:
            sys.exit(f"{bank}: calendar page unavailable ({src.last_status} {src.last_note}) - nothing written")
        parsed[bank] = page
    ff = pd.read_parquet(ds.FF_PARQUET)
    ecb = ff[(ff["currency"] == "EUR") & (ff["name_raw"] == banks["EUR"]["policy_rate"]["ff_name"]) & ff["actual"].notna()]
    ecb_days = local_days([t.to_pydatetime() for t in ecb["datetime_utc"]], banks["EUR"]["tz"])
    rbnz = ds.load_rbnz(ROOT / "data" / "cb" / "manual" / "rbnz.yaml")["published_calendar"]["meetings"]
    text = build(parsed, banks, a.evidence_date, ecb_days, rbnz)
    out = Path(a.out)
    if a.check:
        cur = out.read_text() if out.exists() else ""
        if cur == text:
            print(f"{out}: identical to what the official pages say")
            return 0
        sys.stdout.writelines(difflib.unified_diff(cur.splitlines(True), text.splitlines(True), str(out), "regenerated"))
        return 1
    out.write_text(text)
    print(f"wrote {out} ({sum(1 for ln in text.splitlines() if ln.startswith('    - '))} meetings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
