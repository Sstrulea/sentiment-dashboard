"""Fetch the rates/FX backbone of the reaction layer into data/backbone_rates.parquet.

Network only here; nodes read the parquet offline. Not wired to cron. Official,
keyless sources only, through the repo's own adapters where they exist:

  2Y government yield (series Y2_<CCY>)
    USD  FRED DGS2                                       (fredgraph CSV)
    EUR  ECB YC B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y (AAA spot 2Y, 2004-09-06 ->)
         + Bundesbank BBSIS D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A before it
    GBP  BoE GLC nominal spot curve, 2.0y (glcnominalddata.zip + latest month)
    JPY  MoF jgbcm_all.csv + jgbcm.csv, column 2年
    CAD  BoC Valet BD.CDN.2YR.DQ.YLD
    AUD  RBA F2 FCMYGBAG2D (live; on a bot wall: the RBA rows data/rates.parquet
         already holds, written by the production rate fetch from the same table)
    NZD  RBNZ B2 hb2-daily(.xlsx) — the site is behind a Cloudflare wall: never bypassed
    CHF  SNB cube rendoblid, D0=2J (daily values published monthly)
  Policy rate (POL_<CCY>): BIS WS_CBPOL daily, areas US XM GB JP CA AU NZ CH
  FX (FX_<CCY>): FRED H.10, log value of one unit of the currency in USD
    (DEXUSEU, DEXUSUK, DEXUSAL, DEXUSNZ: USD per unit -> +log;
     DEXJPUS, DEXCAUS, DEXSZUS: units per USD -> -log); USD = 0 by definition.

Long format: series, ccy, kind, code, source, date, avail, value.
avail (pre-registered in scripts/measure/prereg_reaction_v0.yml):
  daily series: date + 1 day; CHF 2Y: first day of month M+2 + 10 days
  (rendoblid publishes month M around the first working day of M+2).

    python scripts/fetch_backbone.py [--out data/backbone_rates.parquet]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.cb_sources.official import parse_bis, parse_snb  # noqa: E402
from src.rate_sources import (FRED_UA, BocValetSource, BoeSource, FredSeriesSource,  # noqa: E402
                              MofJgbSource, RbaSource, RbnzSource)

CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
BIS_AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU",
            "NZD": "NZ", "CHF": "CH"}
FX = {"EUR": ("DEXUSEU", 1), "GBP": ("DEXUSUK", 1), "AUD": ("DEXUSAL", 1), "NZD": ("DEXUSNZ", 1),
      "JPY": ("DEXJPUS", -1), "CAD": ("DEXCAUS", -1), "CHF": ("DEXSZUS", -1)}
ECB_KEY = "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y"
BBK_KEY = "BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A"
BIS_URL = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.{areas}"
           "?startPeriod={since}&format=csv")
SNB_URL = "https://data.snb.ch/api/cube/rendoblid/data/csv/en"
SINCE = date(2000, 1, 1)
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"


def _frame(series, ccy, kind, code, source, dates, values) -> pd.DataFrame:
    d = pd.to_datetime(pd.Series(list(dates)), errors="coerce")
    v = pd.to_numeric(pd.Series(list(values)), errors="coerce")
    df = pd.DataFrame({"series": series, "ccy": ccy, "kind": kind, "code": code, "source": source,
                       "date": d, "value": v}).dropna(subset=["date", "value"])
    df = df[df["date"] >= pd.Timestamp(SINCE)]
    df["avail"] = df["date"] + pd.Timedelta(days=1)
    return df


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=180, **kw)
    r.raise_for_status()
    return r


# --- 2Y ---------------------------------------------------------------------

def y2_usd():
    s = FredSeriesSource("DGS2")
    df = s.fetch_series()
    if df is None:
        raise RuntimeError(f"{s.last_status} {s.last_note}")
    return _frame("Y2_USD", "USD", "y2", "FRED:DGS2", "fred", df["date"], df["value"])


def y2_eur():
    r = _get(f"https://data-api.ecb.europa.eu/service/data/YC/{ECB_KEY}"
             "?format=csvdata&startPeriod=2004-01-01")
    e = pd.read_csv(io.StringIO(r.text))
    if set(e["DATA_TYPE_FM"].astype(str)) != {"SR_2Y"}:
        raise RuntimeError("ECB: not SR_2Y")
    ecb = _frame("Y2_EUR", "EUR", "y2", f"ECB:YC.{ECB_KEY}", "ecb", e["TIME_PERIOD"], e["OBS_VALUE"])
    r = _get(f"https://api.statistiken.bundesbank.de/rest/download/{BBK_KEY}?format=csv&lang=en")
    lines = r.content.decode("utf-8-sig").splitlines()
    if "residual maturity of 2.0 years" not in "\n".join(lines[:3]):
        raise RuntimeError("Bundesbank: header does not say 2.0 years")
    rows = [ln.split(",") for ln in lines if ln[:4].isdigit()]
    bbk = _frame("Y2_EUR", "EUR", "y2", f"BBK:{BBK_KEY}", "bundesbank",
                 [x[0] for x in rows], [x[1] for x in rows])
    first_ecb = ecb["date"].min()
    return pd.concat([bbk[bbk["date"] < first_ecb], ecb], ignore_index=True)


def y2_gbp():
    src = BoeSource()
    pts, _ = src.fetch_history_with_coverage(date(2010, 1, 1))
    cur = src.fetch("GBP")
    by = dict(pts)
    if cur is not None:
        by.update(dict(cur.points))
    p = sorted(by.items())
    return _frame("Y2_GBP", "GBP", "y2", "BoE:GLC nominal spot 2.0y", "boe_glc",
                  [d for d, _ in p], [v for _, v in p])


def y2_jpy():
    src = MofJgbSource()
    s = src.fetch("JPY")
    if s is None:
        raise RuntimeError(f"{src.last_status} {src.last_note}")
    return _frame("Y2_JPY", "JPY", "y2", "MoF:jgbcm 2年", "mof_jgb",
                  [d for d, _ in s.points], [v for _, v in s.points])


def y2_cad():
    r = _get(f"https://www.bankofcanada.ca/valet/observations/{BocValetSource.SERIES}/json"
             "?start_date=2000-01-01")
    j = r.json()
    label = j["seriesDetail"][BocValetSource.SERIES]["label"]
    if "2-year" not in label:
        raise RuntimeError(f"BoC label {label!r}")
    obs = j["observations"]
    return _frame("Y2_CAD", "CAD", "y2", f"BoC:{BocValetSource.SERIES}", "boc_valet",
                  [o["d"] for o in obs], [(o.get(BocValetSource.SERIES) or {}).get("v") for o in obs])


def y2_aud():
    src = RbaSource()
    s = src.fetch("AUD")
    if s is not None:
        return _frame("Y2_AUD", "AUD", "y2", f"RBA:F2 {RbaSource.SERIES_ID}", "rba",
                      [d for d, _ in s.points], [v for _, v in s.points]), "live"
    note = f"live {src.last_status} {src.last_note}"
    r = pd.read_parquet(ROOT / "data" / "rates.parquet")
    r = r[(r["currency"] == "AUD") & (r["source"] == "rba")]
    if r.empty:
        raise RuntimeError(note + "; no RBA rows in data/rates.parquet")
    return _frame("Y2_AUD", "AUD", "y2", f"RBA:F2 {RbaSource.SERIES_ID} (data/rates.parquet)", "rba",
                  r["date"], r["yield_pct"]), note + " -> data/rates.parquet RBA rows"


def y2_nzd():
    src = RbnzSource()
    try:
        s = src.fetch("NZD")
    except Exception as e:  # TenorMismatch on the wall's HTML page
        raise RuntimeError(f"RBNZ B2: {type(e).__name__}: {e}")
    if s is None:
        raise RuntimeError(f"RBNZ B2: {src.last_status} {src.last_note}")
    return _frame("Y2_NZD", "NZD", "y2", "RBNZ:B2 2y", "rbnz",
                  [d for d, _ in s.points], [v for _, v in s.points])


def y2_chf():
    r = _get(SNB_URL)
    text = r.content.decode("utf-8-sig")
    pub = next((ln for ln in text.splitlines() if ln.startswith('"PublishingDate"')), "")
    got = parse_snb(text, ["2J"], SINCE)["2J"]
    df = _frame("Y2_CHF", "CHF", "y2", "SNB:rendoblid D0=2J", "snb", [d for d, _ in got], [v for _, v in got])
    m2 = (df["date"].dt.to_period("M") + 2).dt.to_timestamp()
    df["avail"] = m2 + pd.Timedelta(days=10)
    return df, pub.split(";")[-1].strip('"')


# --- policy + FX --------------------------------------------------------------

def policy():
    areas = "+".join(BIS_AREA.values())
    r = _get(BIS_URL.format(areas=areas, since=SINCE.isoformat()))
    got = parse_bis(r.text, SINCE)
    out = []
    for ccy, a in BIS_AREA.items():
        pts = got.get(a, [])
        out.append(_frame(f"POL_{ccy}", ccy, "policy", f"BIS:WS_CBPOL D.{a}", "bis",
                          [d for d, _ in pts], [v for _, v in pts]))
    return pd.concat(out, ignore_index=True)


def fx():
    out = []
    for ccy, (sid, sign) in FX.items():
        s = FredSeriesSource(sid)
        df = s.fetch_series()
        if df is None:
            raise RuntimeError(f"FRED {sid}: {s.last_status} {s.last_note}")
        df = df[df["value"] > 0]
        out.append(_frame(f"FX_{ccy}", ccy, "fx", f"FRED:{sid}", "fred", df["date"],
                          sign * np.log(df["value"].astype(float))))
    return pd.concat(out, ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "backbone_rates.parquet")
    a = ap.parse_args()
    frames, notes = [], {}
    jobs = {"Y2_USD": y2_usd, "Y2_EUR": y2_eur, "Y2_GBP": y2_gbp, "Y2_JPY": y2_jpy, "Y2_CAD": y2_cad,
            "Y2_AUD": y2_aud, "Y2_NZD": y2_nzd, "Y2_CHF": y2_chf, "POL": policy, "FX": fx}
    for name, job in jobs.items():
        try:
            res = job()
        except Exception as e:
            notes[name] = f"FAILED {type(e).__name__}: {e}"[:300]
            print(f"  FAIL {name}: {notes[name]}")
            continue
        if isinstance(res, tuple):
            res, notes[name] = res
        frames.append(res)
        for s, g in res.groupby("series"):
            print(f"  ok   {s:8s} {len(g):6d}  {g['date'].min().date()} -> {g['date'].max().date()}"
                  f"  {g['code'].iloc[-1]}")
    out = pd.concat(frames, ignore_index=True).sort_values(["series", "date"])
    out = out.drop_duplicates(["series", "date"], keep="last").reset_index(drop=True)
    for c in ("date", "avail"):
        out[c] = pd.to_datetime(out[c]).astype("datetime64[ns]")
    out = out[["series", "ccy", "kind", "code", "source", "date", "avail", "value"]]
    out.to_parquet(a.out, index=False)
    meta = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes}
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"-> {a.out} ({len(out)} rows); notes: {json.dumps(notes, indent=1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
