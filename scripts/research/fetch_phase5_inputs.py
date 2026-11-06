"""Phase 5 inputs besides the OECD editions (network; raw files cached in ~/.cache/macro-research,
small derived panels written to data/research/). Official, free sources only.

  IMF CTOT 5.0.1 (data.imf.org, IMF.RES)   CEMPI_CTOTXM_GDP, R_RW_IX, monthly: commodity net export
                                            price index weighted by net exports / GDP, time-varying
                                            weights. No euro-area aggregate -> GDP-2015-weighted mean of
                                            DEU FRA ITA ESP NLD (World Bank NY.GDP.MKTP.CD, 2015).
  OECD BCI (DF_CLI 4.1, BCICP, AA)          current vintage, 15 economies
  ECB eurofxref-hist                        daily reference rates (14:15 CET), per USD
  BIS WS_CBPOL                              daily policy rates, 15 areas (src.cb_sources.official.parse_bis)

    python scripts/research/fetch_phase5_inputs.py
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.cb_sources.official import parse_bis  # noqa: E402

CACHE = Path.home() / ".cache" / "macro-research"
OUT = ROOT / "data" / "research"
UA = "Mozilla/5.0 (macro-data-analysis research; +https://github.com/Sstrulea/macro-data-analysis)"
CCY_ISO = {"USD": "USA", "EUR": "EA", "GBP": "GBR", "JPY": "JPN", "CAD": "CAN", "AUD": "AUS", "NZD": "NZL",
           "CHF": "CHE", "NOK": "NOR", "SEK": "SWE", "MXN": "MEX", "KRW": "KOR", "PLN": "POL", "CZK": "CZE",
           "HUF": "HUN"}
EA_MEMBERS = ["DEU", "FRA", "ITA", "ESP", "NLD"]
BIS_AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "CHF": "CH",
            "NOK": "NO", "SEK": "SE", "MXN": "MX", "KRW": "KR", "PLN": "PL", "CZK": "CZ", "HUF": "HU"}


def _get(url: str, headers: dict | None = None, timeout: int = 300) -> requests.Response:
    for i in range(4):
        r = requests.get(url, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout)
        if r.status_code == 200:
            return r
        time.sleep(10 * (i + 1))
    r.raise_for_status()
    return r


def ctot() -> tuple[pd.DataFrame, dict]:
    iso = [v for v in CCY_ISO.values() if v != "EA"] + EA_MEMBERS
    url = ("https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/CTOT/5.0.1/"
           f"{'+'.join(iso)}.CEMPI_CTOTXM_GDP.R_RW_IX.M?c%5BTIME_PERIOD%5D=ge:1985-01")
    r = _get(url, {"Accept": "application/vnd.sdmx.data+csv;version=1.0.0"})
    (CACHE / "imf").mkdir(parents=True, exist_ok=True)
    (CACHE / "imf" / "ctot.csv").write_text(r.text)
    d = pd.read_csv(io.StringIO(r.text), dtype=str)
    d = pd.DataFrame({"iso": d["COUNTRY"], "period": pd.PeriodIndex(d["TIME_PERIOD"].str.replace("-M", "-"), freq="M"),
                      "value": pd.to_numeric(d["OBS_VALUE"], errors="coerce")}).dropna()
    wb = _get("https://api.worldbank.org/v2/country/" + ";".join(EA_MEMBERS) +
              "/indicator/NY.GDP.MKTP.CD?date=2015&format=json").json()[1]
    w = {x["countryiso3code"]: float(x["value"]) for x in wb}
    wide = d[d["iso"].isin(EA_MEMBERS)].pivot(index="period", columns="iso", values="value")
    wsum = sum(w.values())
    ea = sum(wide[c] * w[c] / wsum for c in EA_MEMBERS)
    ea = ea.dropna()
    out = pd.concat([d[~d["iso"].isin(EA_MEMBERS)],
                     pd.DataFrame({"iso": "EA", "period": ea.index, "value": ea.to_numpy()})])
    inv = {v: k for k, v in CCY_ISO.items()}
    out["ccy"] = out["iso"].map(inv)
    out["period"] = out["period"].dt.to_timestamp(how="end").dt.normalize()
    return out[["ccy", "iso", "period", "value"]].sort_values(["ccy", "period"]), {"ea_gdp2015_usd": w}


def bci() -> pd.DataFrame:
    areas = [v if v != "EA" else "EA20" for v in CCY_ISO.values()]
    url = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/"
           f"{'+'.join(areas)}.M.BCICP.IX._Z.AA.IX._Z.H?startPeriod=1985-01&format=csvfile")
    r = _get(url)
    (CACHE / "oecd_bci.csv").write_text(r.text)
    d = pd.read_csv(io.StringIO(r.text), dtype=str)
    inv = {(v if v != "EA" else "EA20"): k for k, v in CCY_ISO.items()}
    return pd.DataFrame({"ccy": d["REF_AREA"].map(inv), "period": pd.PeriodIndex(d["TIME_PERIOD"], freq="M")
                         .to_timestamp(how="end").normalize(), "value": pd.to_numeric(d["OBS_VALUE"], errors="coerce"),
                         "code": "OECD:DF_CLI(4.1)." + d["REF_AREA"] + ".M.BCICP.IX._Z.AA.IX._Z.H"}).dropna()


def ecb_fx() -> pd.DataFrame:
    r = _get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip")
    (CACHE / "eurofxref-hist.zip").write_bytes(r.content)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    d = pd.read_csv(z.open(z.namelist()[0]))
    d = d.loc[:, ~d.columns.str.startswith("Unnamed")]
    d["Date"] = pd.to_datetime(d["Date"])
    d = d.set_index("Date").sort_index().replace("N/A", np.nan).apply(pd.to_numeric, errors="coerce")
    usd = d["USD"]
    out = {"EUR": np.log(usd)}                                   # value of 1 EUR in USD
    for c in CCY_ISO:
        if c in ("USD", "EUR"):
            continue
        out[c] = np.log(usd / d[c])                              # USD per unit of c
    lv = pd.DataFrame(out)
    lv.insert(0, "USD", 0.0)
    lv = lv[list(CCY_ISO)]
    long = lv.stack(future_stack=True).rename("log_usd_value").reset_index()
    long.columns = ["date", "ccy", "log_usd_value"]
    return long.dropna()


def bis_policy() -> pd.DataFrame:
    url = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D."
           f"{'+'.join(BIS_AREA.values())}?startPeriod=1995-01-01&format=csv")
    r = _get(url)
    got = parse_bis(r.text, date(1995, 1, 1))
    inv = {v: k for k, v in BIS_AREA.items()}
    rows = [(inv[a], d, v) for a, pts in got.items() for d, v in pts if a in inv]
    return pd.DataFrame(rows, columns=["ccy", "date", "rate"]).assign(date=lambda x: pd.to_datetime(x["date"]))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"fetched_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": {}}
    for name, job in (("ctot", ctot), ("bci", bci), ("ecb_fx", ecb_fx), ("bis_policy", bis_policy)):
        try:
            res = job()
        except Exception as e:
            meta["notes"][name] = f"FAILED {type(e).__name__}: {e}"[:300]
            print(f"  FAIL {name}: {meta['notes'][name]}")
            continue
        if isinstance(res, tuple):
            res, extra = res
            meta["notes"][name] = extra
        res.to_parquet(OUT / f"p5_{name}.parquet", index=False)
        dcol = "period" if "period" in res.columns else "date"
        print(f"  ok {name}: {len(res)} rows")
        print(res.groupby("ccy")[dcol].agg(["min", "max", "size"]).to_string())
    (OUT / "p5_inputs.meta.json").write_text(json.dumps(meta, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
