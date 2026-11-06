"""Fetch the phase-3 market inputs (network only; not wired to cron).

  data/regime_inputs.parquet  FRED keyless: VIXCLS VXVCLS BAA10Y NASDAQCOM SP500 DGS10
                              (long format series/date/avail/value, avail = date + 1 day;
                              same layout as data/tree_inputs.parquet, read by src.tree.data.Inputs)
  data/ecb_exr.parquet        ECB euro foreign exchange reference rates (EXR, daily, 14:15 CET
                              concertation): USD GBP JPY CAD AUD NZD CHF per EUR, from 1999,
                              stored as the log value of one unit of each currency in EUR (EUR = 0)
  data/bis_neer.parquet       BIS effective exchange rates (WS_EER), daily nominal broad index,
                              8 economies (used only by stage H)

    python scripts/fetch_phase3_market.py
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rate_sources import FredSeriesSource  # noqa: E402

FRED = ["VIXCLS", "VXVCLS", "BAA10Y", "NASDAQCOM", "SP500", "DGS10"]
ECB_CCY = ["USD", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/D.{c}.EUR.SP00.A"
           "?format=csvdata&startPeriod=1999-01-01")
BIS_AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ",
            "CHF": "CH"}
BIS_URL = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_EER/1.0/D.N.B.{areas}"
           "?startPeriod=1999-01-01&format=csv")
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"


def fred() -> pd.DataFrame:
    out = []
    for s in FRED:
        src = FredSeriesSource(s)
        df = src.fetch_series()
        if df is None:
            raise RuntimeError(f"FRED {s}: {src.last_status} {src.last_note}")
        out.append(pd.DataFrame({"series": s, "date": df["date"], "avail": df["date"] + pd.Timedelta(days=1),
                                 "value": df["value"].astype(float), "source": f"fred:{s}"}))
        print(f"  ok {s:10s} {len(df):6d} {df['date'].min().date()} -> {df['date'].max().date()}")
    return pd.concat(out, ignore_index=True)


def ecb() -> pd.DataFrame:
    r = requests.get(ECB_URL.format(c="+".join(ECB_CCY)), headers={"User-Agent": UA}, timeout=300)
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text))
    d = d[d["CURRENCY_DENOM"] == "EUR"]
    out = pd.DataFrame({"ccy": d["CURRENCY"], "date": pd.to_datetime(d["TIME_PERIOD"]),
                        "per_eur": pd.to_numeric(d["OBS_VALUE"], errors="coerce")}).dropna()
    out["value"] = -np.log(out["per_eur"])          # log value of one unit of the currency in EUR
    out["code"] = "ECB:EXR.D." + out["ccy"] + ".EUR.SP00.A"
    print(out.groupby("ccy")["date"].agg(["min", "max", "size"]).to_string())
    return out


def bis_neer() -> pd.DataFrame:
    r = requests.get(BIS_URL.format(areas="+".join(BIS_AREA.values())), headers={"User-Agent": UA}, timeout=300)
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text))
    inv = {v: k for k, v in BIS_AREA.items()}
    out = pd.DataFrame({"ccy": d["REF_AREA"].map(inv), "date": pd.to_datetime(d["TIME_PERIOD"], errors="coerce"),
                        "value": pd.to_numeric(d["OBS_VALUE"], errors="coerce")}).dropna()
    out["code"] = "BIS:WS_EER.D.N.B." + out["ccy"].map(BIS_AREA)
    print(out.groupby("ccy")["date"].agg(["min", "max", "size"]).to_string())
    return out


def main() -> int:
    notes = {}
    for name, job, path in (("fred", fred, "regime_inputs.parquet"), ("ecb", ecb, "ecb_exr.parquet"),
                            ("bis_neer", bis_neer, "bis_neer.parquet")):
        try:
            df = job()
        except Exception as e:
            notes[name] = f"FAILED {type(e).__name__}: {e}"[:300]
            print(f"  FAIL {name}: {notes[name]}")
            continue
        for c in ("date", "avail"):
            if c in df:
                df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
        df.to_parquet(ROOT / "data" / path, index=False)
    (ROOT / "data" / "phase3_market.meta.json").write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes}, indent=1) + "\n")
    print("notes:", notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
