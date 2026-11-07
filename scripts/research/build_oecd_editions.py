"""Compact the raw OECD STES-revisions downloads and compute the edition components.

  ~/.cache/macro-research/oecd_rev/<AREA>_core.csv  ->  ~/.cache/macro-research/oecd_editions_core.parquet
  -> data/research/p5_oecd_components.parquet   (ccy, edition, excess_emp, unemp_trend, external, freqs,
                                                  last periods, area code used)
  -> data/research/p5_oecd_inventory.csv        (country x measure: first / last edition, frequency,
                                                  editions missing, last month in the latest edition)

Euro area: for each edition and measure the widest euro-area code present in that edition is used
(EA20 > EA19 > EA18 > ... > EA11).

    python scripts/research/build_oecd_editions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.csv as pcsv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tree.composite import AREA, EA_CODES, edition_components  # noqa: E402

CACHE = Path.home() / ".cache" / "macro-research"
RAW = CACHE / "oecd_rev"
COMPACT = CACHE / "oecd_editions_core.parquet"
OUT = ROOT / "data" / "research"
KEEP_UNITS = {("EMP", "PS"), ("UNEMP", "PT_LF"), ("EX", "XDC"), ("IM", "XDC")}


def period_end(tp: pd.Series) -> pd.Series:
    q = tp.str.contains("Q")
    out = pd.Series(pd.NaT, index=tp.index)
    out[~q] = pd.PeriodIndex(tp[~q], freq="M").to_timestamp(how="end").normalize()
    if q.any():
        out[q] = pd.PeriodIndex(tp[q].str.replace("-Q", "Q"), freq="Q").to_timestamp(how="end").normalize()
    return pd.to_datetime(out)


def compact() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW.glob("*_core.csv")):
        if f.stat().st_size == 0:
            continue
        t = pcsv.read_csv(f, convert_options=pcsv.ConvertOptions(
            include_columns=["REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ACTIVITY", "EDITION", "TIME_PERIOD", "OBS_VALUE"],
            column_types={"EDITION": "string", "TIME_PERIOD": "string"})).to_pandas()
        t = t[[(m, u) in KEEP_UNITS for m, u in zip(t["MEASURE"], t["UNIT_MEASURE"])]]
        t = t[t["ACTIVITY"] == "_T"]
        frames.append(t.drop(columns=["UNIT_MEASURE", "ACTIVITY"]))
        print(f"  {f.name}: {len(t)} rows", flush=True)
    d = pd.concat(frames, ignore_index=True)
    d["period"] = period_end(d["TIME_PERIOD"])
    d["value"] = pd.to_numeric(d["OBS_VALUE"], errors="coerce")
    d = d.dropna(subset=["value", "period"]).drop(columns=["TIME_PERIOD", "OBS_VALUE"])
    d.to_parquet(COMPACT, index=False)
    return d


def euro_area(d: pd.DataFrame) -> pd.DataFrame:
    ea = d[d["REF_AREA"].isin(EA_CODES)]
    rank = {c: i for i, c in enumerate(EA_CODES)}
    ea = ea.assign(rank=ea["REF_AREA"].map(rank))
    best = ea.groupby(["EDITION", "MEASURE"])["rank"].transform("min")
    return ea[ea["rank"] == best].drop(columns="rank")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    d = pd.read_parquet(COMPACT) if COMPACT.exists() else compact()
    ea = euro_area(d)
    nat = d[d["REF_AREA"].isin(AREA)]
    allr = pd.concat([nat.assign(ccy=nat["REF_AREA"].map(AREA)), ea.assign(ccy="EUR")], ignore_index=True)
    rows = []
    for (ccy, ed), g in allr.groupby(["ccy", "EDITION"], sort=True):
        c = edition_components(g)
        rows.append({"ccy": ccy, "edition": ed, "excess_emp": c["excess_emp"], "unemp_trend": c["unemp_trend"],
                     "external": c["external"], "area_codes": ",".join(sorted(g["REF_AREA"].unique())),
                     **{f"freq_{k}": v for k, v in c["freq"].items()},
                     **{f"last_{k}": v for k, v in c["last_period"].items()}})
    comp = pd.DataFrame(rows)
    comp.to_parquet(OUT / "p5_oecd_components.parquet", index=False)
    # inventory
    inv = []
    eds_all = sorted(allr["EDITION"].unique())
    for (ccy, m), g in allr.groupby(["ccy", "MEASURE"]):
        eds = sorted(g["EDITION"].unique())
        span = [e for e in eds_all if eds[0] <= e <= eds[-1]]
        last_ed = g[g["EDITION"] == eds[-1]]
        inv.append({"ccy": ccy, "measure": m, "first_edition": eds[0], "last_edition": eds[-1],
                    "editions": len(eds), "editions_missing_in_span": len(span) - len(eds),
                    "freq_latest": "M" if (last_ed["FREQ"] == "M").any() else "Q",
                    "last_period_latest": str(last_ed["period"].max().date()),
                    "area_codes": ",".join(sorted(g["REF_AREA"].unique()))})
    pd.DataFrame(inv).to_csv(OUT / "p5_oecd_inventory.csv", index=False)
    print(pd.DataFrame(inv).pivot(index="ccy", columns="measure", values="first_edition").to_string())
    print(comp.groupby("ccy")[["excess_emp", "unemp_trend", "external"]].apply(lambda x: x.notna().sum()).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
