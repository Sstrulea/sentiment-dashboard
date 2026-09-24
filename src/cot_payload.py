"""COT data payload for the COT page (audit 10A) — compute only, no rendering.

Writes, from data/history.parquet:
  public/data/cot/index.json          the weeks available + meta + instrument list
  public/data/cot/<YYYY-MM-DD>.json   one file per report week (Tuesday as_of)
  public/data/cot/series.json         net spec, net comm and open interest per
                                      instrument over EVERY report date, once

A week is published when >= 90% of the contracts in data/contracts.yaml have a
3-year percentile (the 156-report trailing window) at that date.

Series are written ONCE (series.json) instead of 156 points x 3 series x 43
instruments in every weekly file: the weekly files would repeat ~98% of the
same numbers (a week adds one point per series), ~25x the bytes. A client takes
the last 156 points up to the week it shows.

Everything is point in time: a week's numbers use only reports dated <= that
week. Reuses src.compute.compute_metrics and src.cot_score.cot_cell /
flow_score read-only (neither is modified).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .compute import compute_metrics, load_meta
from .cot_score import cot_cell, flow_score

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "history.parquet"
META_FILE = ROOT / "data" / "cot_meta.json"
OUT_DIR = ROOT / "public" / "data" / "cot"

DATE = "report_date_as_yyyy_mm_dd"
CODE = "cftc_contract_market_code"
MIN_P3_SHARE = 0.90
SERIES_POINTS = 156
# What /economic scores (the SENTIMENT column and the cross-asset metals).
IN_MODEL = ("EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "DXY", "GOLD", "SILVER")
SOURCE = ("CFTC Commitments of Traders, Legacy, Futures and Options Combined "
          "(publicreporting.cftc.gov, dataset jun7-fc8e)")
NY = ZoneInfo("America/New_York")
EXCHANGES = {"CHICAGO MERCANTILE EXCHANGE": "CME", "CHICAGO BOARD OF TRADE": "CBOT",
             "NEW YORK MERCANTILE EXCHANGE": "NYMEX", "COMMODITY EXCHANGE INC.": "COMEX",
             "ICE FUTURES U.S.": "ICE US", "CBOE FUTURES EXCHANGE": "CFE"}


def _exchange(name) -> str | None:
    """Short exchange name from CFTC's market_and_exchange_names."""
    if not isinstance(name, str) or " - " not in name:
        return None
    full = name.rsplit(" - ", 1)[1].strip()
    return EXCHANGES.get(full, full.title())


def _num(v):
    """JSON-safe number: NaN/inf -> None; numpy scalars -> python."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f) if f.is_integer() and abs(f) < 2**53 and not isinstance(v, float) else f


def _flip(cur, prev) -> str | None:
    """The rule of compute.build_latest_snapshot._flipped, with its direction:
    the 3y percentile is extreme now (>= 0.95 high / <= 0.05 low) and was
    normal (0.05 < prev < 0.95) at the contract's previous report."""
    if cur is None or pd.isna(cur) or prev is None or pd.isna(prev):
        return None
    if not (0.05 < prev < 0.95):
        return None
    if cur >= 0.95:
        return "high"
    if cur <= 0.05:
        return "low"
    return None


def release_times(as_of: pd.Timestamp) -> tuple[str, str]:
    """(released, next_release), ESTIMATED: the CFTC publishes on the Friday
    after the Tuesday as_of at 15:30 New York time (holiday delays ignored)."""
    d = pd.Timestamp(as_of).date() + timedelta(days=3)
    rel = datetime.combine(d, time(15, 30), NY).astimezone(timezone.utc)
    nxt = datetime.combine(d + timedelta(days=7), time(15, 30), NY).astimezone(timezone.utc)
    return rel.strftime("%Y-%m-%dT%H:%M:%SZ"), nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _side(g: pd.DataFrame, i: int, side: str) -> dict:
    """spec/comm block for the row at position i of one contract's history g."""
    row = g.iloc[i]
    prev = g.iloc[i - 1] if i > 0 else None
    net = row[f"{side}_net"]
    oi = row.get("open_interest_all")
    _flow, fm = flow_score(g[f"{side}_net"].iloc[: i + 1])
    return {
        "net": _num(net),
        "d1w": _num(net - prev[f"{side}_net"]) if prev is not None else None,
        "pct_oi": _num(net / oi) if oi and not pd.isna(oi) else None,
        "p6": _num(row[f"{side}_extreme_6m"]),
        "p3": _num(row[f"{side}_extreme_3y"]),
        "long_share": _num(row[f"{side}_exp_long"]),
        "chg4": _num(fm["chg4"]),
        "z4w": _num(fm["z"]),
        "flip3y": _flip(row[f"{side}_extreme_3y"],
                        prev[f"{side}_extreme_3y"] if prev is not None else None),
    }


def _instrument(g: pd.DataFrame, i: int, info: dict) -> dict:
    row = g.iloc[i]
    prev = g.iloc[i - 1] if i > 0 else None
    cell, m = cot_cell(row["spec_extreme_6m"], row["spec_extreme_3y"], g["spec_net"].iloc[: i + 1])
    oi = row.get("open_interest_all")
    return {
        **info,
        "spec": _side(g, i, "spec"),
        "comm": _side(g, i, "comm"),
        "oi": _num(oi),
        "oi_d1w": _num(oi - prev["open_interest_all"]) if prev is not None else None,
        "score": {"cell": _num(cell), "level": _num(m["level"]), "flow": _num(m["flow"]),
                  "blend": _num(m["blend"]), "z": _num(m["z"]), "chg4": _num(m["chg4"])},
    }


def build(history: pd.DataFrame) -> dict:
    """{"index": {...}, "weeks": {date: payload}, "series": {...}} (pure)."""
    meta = load_meta()
    enriched = compute_metrics(history)
    enriched = enriched[enriched[CODE].isin(meta)].copy()
    enriched[DATE] = pd.to_datetime(enriched[DATE])
    by_code = {c: g.sort_values(DATE).reset_index(drop=True) for c, g in enriched.groupby(CODE)}
    exch = {c: _exchange(g["market_and_exchange_names"].dropna().iloc[-1])
            for c, g in by_code.items()
            if "market_and_exchange_names" in g and g["market_and_exchange_names"].notna().any()}
    infos = {c: {"symbol": m["symbol"], "name": m["name"], "category": m["category"],
                 "category_label": m["category_label"], "in_model": m["symbol"] in IN_MODEL,
                 "exchange": exch.get(c),
                 # currency futures are quoted vs USD (the dollar index is not)
                 "quote": "USD" if m["category"] == "fx" and m["symbol"] != "DXY" else None}
             for c, m in meta.items()}

    dates = sorted(enriched[DATE].unique())
    p3 = enriched.groupby(DATE)["spec_extreme_3y"].apply(lambda s: s.notna().sum())
    weeks = [pd.Timestamp(d) for d in dates if p3.get(d, 0) >= MIN_P3_SHARE * len(meta)]

    out_weeks: dict = {}
    for wk in weeks:
        released, next_release = release_times(wk)
        instruments = []
        for code, info in infos.items():
            g = by_code.get(code)
            hit = g.index[g[DATE] == wk] if g is not None else []
            if len(hit) == 0:
                last = g[g[DATE] <= wk][DATE].max() if g is not None else None
                instruments.append({**info, "missing": True,
                                    "last_report": None if last is None or pd.isna(last)
                                    else pd.Timestamp(last).strftime("%Y-%m-%d")})
                continue
            instruments.append(_instrument(g, int(hit[0]), info))
        out_weeks[wk.strftime("%Y-%m-%d")] = {
            "as_of": wk.strftime("%Y-%m-%d"), "released": released,
            "next_release": next_release, "instruments": instruments,
        }

    all_dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates]
    series = {"dates": all_dates, "points_per_chart": SERIES_POINTS, "instruments": {}}
    for code, info in infos.items():
        g = by_code.get(code)
        pos = {} if g is None else {pd.Timestamp(d).strftime("%Y-%m-%d"): k for k, d in enumerate(g[DATE])}
        col = lambda c: [(_num(g[c].iloc[pos[d]]) if d in pos else None) for d in all_dates]  # noqa: E731
        series["instruments"][info["symbol"]] = (
            {"spec_net": [None] * len(all_dates), "comm_net": [None] * len(all_dates),
             "oi": [None] * len(all_dates)} if g is None else
            {"spec_net": col("spec_net"), "comm_net": col("comm_net"), "oi": col("open_interest_all")})

    index = {"weeks": sorted(out_weeks, reverse=True),
             "latest": max(out_weeks) if out_weeks else None,
             "instruments": list(infos.values()), "in_model": list(IN_MODEL),
             "min_p3_share": MIN_P3_SHARE, "series_file": "series.json"}
    return {"index": index, "weeks": out_weeks, "series": series}


def write(history_path: Path = HISTORY_FILE, out_dir: Path = OUT_DIR,
          meta_path: Path = META_FILE) -> dict:
    """Build from the parquet and write the files; returns a small summary."""
    history = pd.read_parquet(history_path)
    built = build(history)
    try:
        fetch_meta = json.loads(Path(meta_path).read_text())
    except (OSError, ValueError):
        fetch_meta = {}
    latest = built["index"]["latest"]
    rel, nxt = release_times(pd.Timestamp(latest)) if latest else (None, None)
    built["index"]["meta"] = {"as_of": latest, "released": rel, "next_release": nxt,
                              "released_is_estimate": True,
                              "fetched_at": fetch_meta.get("fetched_at"), "source": SOURCE}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = set(built["weeks"]) | {"index", "series"}
    for old in out_dir.glob("*.json"):              # a week that no longer qualifies
        if old.stem not in keep:
            old.unlink()
    dump = lambda obj: json.dumps(obj, separators=(",", ":"), allow_nan=False)  # noqa: E731
    for wk, payload in built["weeks"].items():
        payload["fetched_at"] = fetch_meta.get("fetched_at") if wk == latest else None
        payload["source"] = SOURCE
        (out_dir / f"{wk}.json").write_text(dump(payload))
    (out_dir / "series.json").write_text(dump(built["series"]))
    (out_dir / "index.json").write_text(dump(built["index"]))
    size = sum(p.stat().st_size for p in out_dir.glob("*.json"))
    log.info("COT payload: %d weeks, %d bytes in %s", len(built["weeks"]), size, out_dir)
    return {"weeks": len(built["weeks"]), "bytes": size, "latest": latest}
