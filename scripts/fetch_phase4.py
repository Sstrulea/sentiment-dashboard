"""Fetch the phase-4 inputs (network only; not wired to cron). Official, keyless:

  data/cftc_tff.parquet     CFTC Traders in Financial Futures, futures + options combined
                            (publicreporting.cftc.gov dataset yw9f-hn96 — the same reports as the
                            yearly FinFut files), from 2006: asset managers and leveraged funds,
                            long / short, open interest
  data/cftc_legacy.parquet  CFTC Legacy combined (dataset jun7-fc8e, src.fetch._fetch_batch), full
                            history from 1995, non-commercial long / short, open interest
  data/bis_reer.parquet     BIS effective exchange rates WS_EER, monthly, real, broad (M.R.B)
  data/fomc_dates.parquet   FOMC meeting decision days 2000 -> 2027 from federalreserve.gov
                            (fomchistoricalYYYY.htm for 2000-2020, fomccalendars.htm after)

Contracts: EUR 099741, GBP 096742, JPY 097741, CAD 090741, AUD 232741, NZD 112741, CHF 092741,
USD = ICE U.S. Dollar Index 098662.

    python scripts/fetch_phase4.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fetch import _fetch_batch  # noqa: E402

CODES = {"EUR": "099741", "GBP": "096742", "JPY": "097741", "CAD": "090741", "AUD": "232741",
         "NZD": "112741", "CHF": "092741", "USD": "098662"}
TFF_URL = "https://publicreporting.cftc.gov/resource/yw9f-hn96.json"
TFF_COLS = ["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "market_and_exchange_names",
            "open_interest_all", "asset_mgr_positions_long", "asset_mgr_positions_short",
            "lev_money_positions_long", "lev_money_positions_short",
            "dealer_positions_long_all", "dealer_positions_short_all"]
BIS_AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ",
            "CHF": "CH"}
REER_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_EER/1.0/M.R.B.{a}?startPeriod=1990-01&format=csv"
FED = "https://www.federalreserve.gov/monetarypolicy/"
UA = "Mozilla/5.0 (macro-data-analysis research; +https://github.com/Sstrulea/macro-data-analysis)"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def tff() -> pd.DataFrame:
    inv = {v: k for k, v in CODES.items()}
    codes = ",".join(f"'{c}'" for c in CODES.values())
    r = requests.get(TFF_URL, params={"$select": ",".join(TFF_COLS),
                                      "$where": f"cftc_contract_market_code in ({codes})",
                                      "$order": "report_date_as_yyyy_mm_dd", "$limit": 50000}, timeout=180)
    r.raise_for_status()
    d = pd.DataFrame(r.json())
    for c in TFF_COLS[3:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["date"] = pd.to_datetime(d["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None)
    d["ccy"] = d["cftc_contract_market_code"].map(inv)
    d["avail"] = d["date"] + pd.Timedelta(days=6)
    return d.drop(columns=["report_date_as_yyyy_mm_dd"]).sort_values(["ccy", "date"]).reset_index(drop=True)


def legacy() -> pd.DataFrame:
    inv = {v: k for k, v in CODES.items()}
    d = _fetch_batch(list(CODES.values()), "1990-01-01T00:00:00.000")
    d = d.rename(columns={"report_date_as_yyyy_mm_dd": "date"})
    d["ccy"] = d["cftc_contract_market_code"].map(inv)
    d["avail"] = d["date"] + pd.Timedelta(days=6)
    return d.sort_values(["ccy", "date"]).drop_duplicates(["ccy", "date"]).reset_index(drop=True)


def reer() -> pd.DataFrame:
    r = requests.get(REER_URL.format(a="+".join(BIS_AREA.values())), headers={"User-Agent": UA}, timeout=180)
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text))
    inv = {v: k for k, v in BIS_AREA.items()}
    p = pd.PeriodIndex(d["TIME_PERIOD"], freq="M")
    out = pd.DataFrame({"ccy": d["REF_AREA"].map(inv), "period": p.to_timestamp(how="end").normalize(),
                        "value": pd.to_numeric(d["OBS_VALUE"], errors="coerce"),
                        "code": "BIS:WS_EER.M.R.B." + d["REF_AREA"]}).dropna()
    # BIS publishes month M around mid-M+1; pre-registered availability = period end + 15 days + 10
    out["avail"] = out["period"] + pd.Timedelta(days=25)
    return out.sort_values(["ccy", "period"]).reset_index(drop=True)


def _get(url: str) -> str:
    for i in range(3):
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        if r.status_code == 200:
            return r.text
        time.sleep(3 * (i + 1))
    r.raise_for_status()
    return ""


def fomc() -> pd.DataFrame:
    rows = []
    for yr in range(2000, 2021):
        html = _get(f"{FED}fomchistorical{yr}.htm")
        for m in re.finditer(rf"({MONTHS})\s+(\d{{1,2}})(?:\s*-\s*(?:({MONTHS})\s+)?(\d{{1,2}}))?\s+"
                             r"(Meeting|Conference Call|\(unscheduled\))", html):
            mon1, d1, mon2, d2, kind = m.groups()
            mon, day = (mon2 or mon1, d2) if d2 else (mon1, d1)
            rows.append({"date": pd.Timestamp(f"{day} {mon} {yr}"), "kind": kind, "source": f"fomchistorical{yr}.htm"})
        time.sleep(1)
    html = _get(f"{FED}fomccalendars.htm")
    for block in re.split(r'<h4><a id="\d+">', html)[1:]:
        ym = re.match(r"(\d{4}) FOMC Meetings", block)
        if not ym:
            continue
        yr = int(ym.group(1))
        for m in re.finditer(r'fomc-meeting__month[^>]*><strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>([0-9\-]+)',
                             block, re.S):
            mon, days = m.group(1), m.group(2)
            mon = mon.split("/")[-1]
            day = days.split("-")[-1]
            rows.append({"date": pd.Timestamp(f"{day} {mon} {yr}"), "kind": "Meeting", "source": "fomccalendars.htm"})
    d = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return d


def main() -> int:
    notes = {}
    for name, job, path in (("tff", tff, "cftc_tff.parquet"), ("legacy", legacy, "cftc_legacy.parquet"),
                            ("reer", reer, "bis_reer.parquet"), ("fomc", fomc, "fomc_dates.parquet")):
        try:
            df = job()
        except Exception as e:
            notes[name] = f"FAILED {type(e).__name__}: {e}"[:300]
            print(f"  FAIL {name}: {notes[name]}")
            continue
        df.to_parquet(ROOT / "data" / path, index=False)
        key = "ccy" if "ccy" in df.columns else "kind"
        dcol = "date" if "date" in df.columns else "period"
        print(f"  ok {name}: {len(df)} rows")
        print(df.groupby(key)[dcol].agg(["min", "max", "size"]).to_string())
    (ROOT / "data" / "phase4.meta.json").write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
