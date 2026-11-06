"""Download every OECD STES revisions edition (DF_STES_REVISIONS 4.0) for the phase-5 universe
into ~/.cache/macro-research/oecd_rev/<AREA>_<group>.csv (raw, not committed).

One request per area and measure group (all editions 1999-02 -> latest in one response),
>= 3 s between requests, resumable (an existing non-empty file is skipped unless --refresh).
Groups: core = EMP + UNEMP + EX + IM; extra = CP + PRVM (descriptive).
Areas: USA GBR JPN CAN AUS NZL CHE NOR SWE MEX KOR POL CZE HUN and the euro area under each
code it carried (EA11, EA12, EA13, EA15, EA16, EA17, EA18, EA19, EA20).

    python scripts/research/fetch_oecd_editions.py [--refresh]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

CACHE = Path.home() / ".cache" / "macro-research" / "oecd_rev"
BASE = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0"
AREAS = ["USA", "GBR", "JPN", "CAN", "AUS", "NZL", "CHE", "NOR", "SWE", "MEX", "KOR", "POL", "CZE", "HUN",
         "EA11", "EA12", "EA13", "EA15", "EA16", "EA17", "EA18", "EA19", "EA20"]
GROUPS = {"core": "EMP+UNEMP+EX+IM", "extra": "CP+PRVM"}
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--groups", nargs="*", default=list(GROUPS))
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    for g in a.groups:
        for area in AREAS:
            f = CACHE / f"{area}_{g}.csv"
            if f.exists() and f.stat().st_size > 0 and not a.refresh:
                continue
            url = f"{BASE}/{area}..{GROUPS[g]}...?startPeriod=1985-01&format=csvfile"
            for attempt in range(4):
                try:
                    r = requests.get(url, headers={"User-Agent": UA}, timeout=600)
                except requests.RequestException as e:
                    print(f"  {area} {g}: {type(e).__name__}, retry")
                    time.sleep(30 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    f.write_text(r.text)
                    print(f"  ok {area} {g}: {len(r.content) / 1e6:.1f} MB", flush=True)
                    break
                if r.status_code == 404:          # area not in the flow: empty marker
                    f.write_text("")
                    print(f"  -- {area} {g}: 404 (no data)", flush=True)
                    break
                print(f"  {area} {g}: HTTP {r.status_code}, retry", flush=True)
                time.sleep(60 * (attempt + 1))
            time.sleep(3.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
