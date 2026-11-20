"""Fetch the raw inputs of the decision trees (src/tree) into data/tree_inputs.parquet.

Network only here; `python -m src.tree.run` reads the parquet offline. Not wired to
cron. Official, keyless sources only, through the fetchers the repo already has:
  - FRED fredgraph CSV  (src.rate_sources.FredSeriesSource)
  - CFTC Socrata, Legacy Combined (src.fetch._fetch_batch), JPY = 097741
  - MoF Japan JGB 2Y   (src.rate_sources.MofJgbSource)

Long format, one row per observation: series, date, avail, value, source.
`avail` is the first day a value may be used by a node (nodes read avail <= asof):
  - daily market series: date + 1 day (close / noon fixing known after the day;
    the walk-forward return starts at the asof-day DEXJPUS fixing, so a node never
    sees the asof day itself)
  - COT: report Tuesday + 6 days (released Friday 15:30 ET, after the asof-Friday
    DEXJPUS noon fixing -> first usable on the following Monday)

    python scripts/fetch_tree_inputs.py [--out data/tree_inputs.parquet]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.fetch import _fetch_batch  # noqa: E402
from src.rate_sources import FredSeriesSource, MofJgbSource  # noqa: E402

FRED_SERIES = ["DGS2", "DGS10", "VIXCLS", "VXVCLS", "BAMLH0A0HYM2", "SP500",
               "DCOILWTICO", "DEXJPUS"]
COT_CODES = {"COT_JPY": "097741"}
DAILY_LAG_DAYS = 1
COT_LAG_DAYS = 6


def fred(series_id: str) -> pd.DataFrame:
    src = FredSeriesSource(series_id)
    df = src.fetch_series()
    if df is None or df.empty:
        raise RuntimeError(f"FRED {series_id}: {src.last_status} {src.last_note}")
    return pd.DataFrame({"series": series_id, "date": df["date"],
                         "avail": df["date"] + pd.Timedelta(days=DAILY_LAG_DAYS),
                         "value": df["value"].astype(float), "source": f"fred:{series_id}"})


def cot(name: str, code: str) -> pd.DataFrame:
    df = _fetch_batch([code], "1990-01-01T00:00:00.000")
    if df.empty:
        raise RuntimeError(f"CFTC {code}: no rows")
    df = df.sort_values("report_date_as_yyyy_mm_dd").drop_duplicates("report_date_as_yyyy_mm_dd")
    d = df["report_date_as_yyyy_mm_dd"]
    net = df["noncomm_positions_long_all"] - df["noncomm_positions_short_all"]
    return pd.DataFrame({"series": name, "date": d, "avail": d + pd.Timedelta(days=COT_LAG_DAYS),
                         "value": net.astype(float), "source": f"cftc:legacy_combined:{code}"})


def jgb2y() -> pd.DataFrame:
    src = MofJgbSource()
    s = src.fetch("JPY")
    if s is None:
        raise RuntimeError(f"MoF JGB: {src.last_status} {src.last_note}")
    d = pd.to_datetime([p[0] for p in s.points])
    return pd.DataFrame({"series": "JGB2Y", "date": d,
                         "avail": d + pd.Timedelta(days=DAILY_LAG_DAYS),
                         "value": [float(p[1]) for p in s.points], "source": "mof:jgbcm:2y"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "tree_inputs.parquet")
    args = ap.parse_args()

    frames, failed = [], {}
    jobs = [(s, lambda s=s: fred(s)) for s in FRED_SERIES]
    jobs += [(n, lambda n=n, c=c: cot(n, c)) for n, c in COT_CODES.items()]
    jobs.append(("JGB2Y", jgb2y))
    for name, job in jobs:
        try:
            df = job()
        except Exception as e:  # one failed source leaves its nodes missing, not the run
            failed[name] = f"{type(e).__name__}: {e}"
            print(f"  FAIL {name}: {failed[name]}")
            continue
        frames.append(df)
        print(f"  ok   {name:13s} {len(df):6d} rows  {df['date'].min().date()} -> {df['date'].max().date()}")

    if not frames:
        print("nothing fetched")
        return 1
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).astype("datetime64[ns]")
    out["avail"] = pd.to_datetime(out["avail"]).astype("datetime64[ns]")
    out = out.sort_values(["series", "date"]).reset_index(drop=True)
    if args.out.exists() and failed:
        # keep the previous copy of a series whose source failed this time
        old = pd.read_parquet(args.out)
        keep = old[old["series"].isin(failed)]
        out = pd.concat([out, keep], ignore_index=True).sort_values(["series", "date"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.out, index=False)
    meta = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "failed": failed, "rows": int(len(out))}
    args.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"-> {args.out} ({len(out)} rows); failed: {failed or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
