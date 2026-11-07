"""Fetch monthly macro for the 8 economies from the OECD SDMX API -> data/macro_monthly.parquet.

Official, keyless (sdmx.oecd.org). Not wired to cron. One request per dataflow
(all areas in one key), raw CSVs cached under ~/.cache/macro-phase3/oecd so a
re-run does not hit the API (OECD rate-limits). --refresh re-downloads.

Areas: USD USA, EUR EA20, GBP GBR, JPY JPN, CAD CAN, AUD AUS, NZD NZL, CHF CHE.
Series (monthly; quarterly where the area has no monthly series, e.g. NZ):
  cpi        CPI all items, index          DF_PRICES_C2018_ALL (COICOP 2018) spliced
             with DF_PRICES_ALL (COICOP 1999) on the YoY: the newer flow wins a month
  cpi_core   CPI all items less food and energy (_TXCP01_NRG), same two flows
  unemp      harmonised unemployment rate, s.a. (DF_IALFS_UNE_M)
  emp        employment, persons, 15+, s.a. (DF_IALFS_INDIC, EMP / PS)
  ip         industrial production (BTE), volume index, s.a. (DF_INDSERV 4.3)
  bci        business confidence, amplitude adjusted (DF_CLI 4.1, BCICP)
  cli        composite leading indicator, amplitude adjusted (DF_CLI 4.1, LI) —
             heavily revised series, used only as a variant

Long format: ccy, area, series, freq, period (month/quarter END date), value,
yoy (for cpi / cpi_core / ip, computed within one flow), source, avail.
avail (pre-registered, prereg_phase3.yml): period end + lag:
  cpi, cpi_core +28 days; unemp, emp, ip, cli +42 days; bci +14 days.
Values are the CURRENT vintage (revisions are not real-time) — noted in the report.

    python scripts/fetch_oecd_macro.py [--refresh]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / ".cache" / "macro-phase3" / "oecd"
OUT = ROOT / "data" / "macro_monthly.parquet"
BASE = "https://sdmx.oecd.org/public/rest/data"
AREAS = {"USD": "USA", "EUR": "EA20", "GBP": "GBR", "JPY": "JPN", "CAD": "CAN", "AUD": "AUS",
         "NZD": "NZL", "CHF": "CHE"}
A = "+".join(AREAS.values())
A_LFS = A.replace("EA20", "EA")          # the LFS flows code the euro area as EA
LAG_DAYS = {"cpi": 28, "cpi_core": 28, "unemp": 42, "emp": 42, "ip": 42, "cli": 42, "bci": 14}
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"

REQUESTS = {
    "prices_c2018": f"OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,1.0/{A}+EA.M+Q.N.CPI.IX._T+_TXCP01_NRG.N._Z",
    "prices_c1999": f"OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/{A}.M+Q.N.CPI.IX._T+_TXCP01_NRG.N._Z",
    "unemp": f"OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0/{A_LFS}..._Z.Y._T.Y_GE15..M+Q",
    "emp": f"OECD.SDD.TPS,DSD_LFS@DF_IALFS_INDIC,1.0/{A_LFS}.EMP.PS._Z.Y._T.Y_GE15._Z.M+Q",
    "ip": f"OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3/{A}.M+Q.PRVM.IX.BTE.Y._Z._Z.",
    "cli": f"OECD.SDD.STES,DSD_STES@DF_CLI,4.1/{A}.M.LI+BCICP.IX._Z.AA.IX._Z.H",
}


def get(name: str, refresh: bool) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}.csv"
    if refresh or not f.exists():
        url = f"{BASE}/{REQUESTS[name]}?startPeriod=1990-01&format=csvfile"
        for attempt in range(3):
            r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
            if r.status_code == 200:
                break
            if r.status_code == 404:
                raise RuntimeError(f"{name}: 404 {r.text[:120]}")
            time.sleep(20 * (attempt + 1))
        r.raise_for_status()
        f.write_text(r.text)
        time.sleep(3)
    return pd.read_csv(f, dtype=str)


def period_end(p: pd.Series, freq: pd.Series) -> pd.Series:
    out = []
    for x, fr in zip(p, freq):
        per = pd.Period(x.replace("-Q", "Q"), freq="Q" if fr == "Q" else "M")
        out.append(per.end_time.normalize())
    return pd.Series(out, index=p.index)


def tidy(df: pd.DataFrame, series: str, source: str, ccy_col="REF_AREA") -> pd.DataFrame:
    inv = {v: k for k, v in AREAS.items()} | {"EA": "EUR"}
    out = pd.DataFrame({"ccy": df[ccy_col].map(inv), "area": df[ccy_col], "series": series,
                        "freq": df["FREQ"], "period": period_end(df["TIME_PERIOD"], df["FREQ"]),
                        "value": pd.to_numeric(df["OBS_VALUE"], errors="coerce"), "source": source})
    return out.dropna(subset=["ccy", "value"])


def monthly_first(df: pd.DataFrame) -> pd.DataFrame:
    """Keep monthly rows where an area has them; quarterly only for areas without."""
    has_m = set(df.loc[df["freq"] == "M", "area"])
    return df[(df["freq"] == "M") | ~df["area"].isin(has_m)]


def with_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """YoY (%) by date: value / value of the same period one year earlier - 1
    (a missing month, e.g. US CPI 2025-10, leaves only its own YoY empty)."""
    parts = []
    for _, g in df.groupby(["area", "series", "freq", "source"]):
        g = g.sort_values("period").copy()
        prev = g["period"] - pd.offsets.DateOffset(years=1)
        prev = prev + pd.offsets.MonthEnd(0)
        lookup = dict(zip(g["period"], g["value"]))
        base = prev.map(lookup)
        g["yoy"] = (g["value"] / base - 1) * 100
        parts.append(g)
    return pd.concat(parts)


def prices(refresh: bool) -> pd.DataFrame:
    frames = []
    for name in ("prices_c2018", "prices_c1999"):
        d = get(name, refresh)
        d = d[d["UNIT_MEASURE"] == "IX"]
        for exp, s in (("_T", "cpi"), ("_TXCP01_NRG", "cpi_core")):
            x = d[d["EXPENDITURE"] == exp]
            if "BASE_PER" in x.columns:      # one base per area/freq (the most recent)
                x = x.sort_values("BASE_PER").groupby(["REF_AREA", "FREQ"], group_keys=False).apply(
                    lambda g: g[g["BASE_PER"] == g["BASE_PER"].dropna().max()] if g["BASE_PER"].notna().any() else g)
            frames.append(with_yoy(monthly_first(tidy(x, s, f"oecd:{name}"))))
    allp = pd.concat(frames)
    # splice: per (area, series), COICOP 2018 wins a period; COICOP 1999 fills the rest
    allp["rank"] = allp["source"].map({"oecd:prices_c2018": 0, "oecd:prices_c1999": 1})
    allp = allp.sort_values("rank").drop_duplicates(["ccy", "series", "period"]).drop(columns="rank")
    return allp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    frames, notes = [], {}
    try:
        frames.append(prices(a.refresh))
    except Exception as e:
        notes["prices"] = f"FAILED {e}"
    for name, series, filt in (("unemp", "unemp", None), ("emp", "emp", None), ("ip", "ip", None)):
        try:
            d = get(name, a.refresh)
            t = monthly_first(tidy(d, series, f"oecd:{name}"))
            frames.append(with_yoy(t) if series == "ip" else t.assign(yoy=float("nan")))
        except Exception as e:
            notes[name] = f"FAILED {e}"
    try:
        d = get("cli", a.refresh)
        for m, s in (("BCICP", "bci"), ("LI", "cli")):
            frames.append(tidy(d[d["MEASURE"] == m], s, "oecd:cli").assign(yoy=float("nan")))
    except Exception as e:
        notes["cli"] = f"FAILED {e}"
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(["ccy", "series", "period"]).sort_values(["ccy", "series", "period"])
    out["avail"] = out["period"] + pd.to_timedelta(out["series"].map(LAG_DAYS), unit="D")
    for c in ("period", "avail"):
        out[c] = pd.to_datetime(out[c]).astype("datetime64[ns]")
    out = out[["ccy", "area", "series", "freq", "period", "value", "yoy", "source", "avail"]].reset_index(drop=True)
    out.to_parquet(OUT, index=False)
    meta = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes,
            "requests": REQUESTS}
    OUT.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    cov = out.groupby(["series", "ccy"]).agg(freq=("freq", "first"), first=("period", "min"),
                                             last=("period", "max"), n=("value", "size"))
    print(cov.to_string())
    print(f"-> {OUT} ({len(out)} rows); notes {notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
