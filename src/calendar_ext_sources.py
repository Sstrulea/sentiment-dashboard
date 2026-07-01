"""STRAT 3 — external national-statistics fallback for MT5 calendar gaps.

Same guard as the FRED fallback (src.calendar_fred_fallback): fill a genuine MT5
gap ONLY when the external series CONTINUES the MT5 series within 0.1pp on the
overlapping published periods; otherwise REFUSE and leave the gap (accept-
degradation). Never overrides an MT5 actual, never fabricates.

Sources (each a graceful fetcher returning {period_start -> value in MT5 units}):
  - AUD retail m/m  -> ABS Data API (SDMX-JSON/CSV)           [_fetch_abs]
  - JPY GDP q/q     -> e-Stat as-released (API key required)  [_fetch_estat]
  - CHF GDP q/q     -> SECO/BFS PXWeb (sporting-event-adj.)   [_fetch_seco_pxweb]

IPv4 is pinned process-wide by importing rate_sources. Each fetcher fails fast &
quiet (short timeout, graceful) so the engine simply REFUSES when an endpoint or
key is not resolved — no bad data can enter.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

import src.rate_sources  # noqa: F401 — import pins IPv4 (allowed_gai_family) process-wide
from .calendar_fred_fallback import (
    PARQUET, Candidate, Target, _gap_periods, _to_period_value,
    evaluate_candidate, mt5_ground_truth, CALENDAR_COLUMNS,
)

log = logging.getLogger(__name__)

UA = {"User-Agent": "macro-data-analysis/1.0 (+github.com/Sstrulea/sentiment-dashboard)"}
HTTP_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Source fetchers — each returns a tidy (date, value) frame, or None (graceful)
# ---------------------------------------------------------------------------

def _tidy(dates, values) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": pd.to_numeric(values, errors="coerce")})


def _fetch_abs(dataflow_key: str, *, start: str = "2023") -> Optional[pd.DataFrame]:
    """ABS Data API (data.api.abs.gov.au) CSV. `dataflow_key` = 'DATAFLOW/DATAKEY'.
    Returns monthly levels (caller transforms to m/m %). None on any failure."""
    url = f"https://data.api.abs.gov.au/rest/data/{dataflow_key}?startPeriod={start}&format=csv"
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
        if r.status_code != 200 or "," not in r.text[:2000]:
            log.warning("ABS %s -> HTTP %s (dataflow/key needs confirmation)", dataflow_key, r.status_code)
            return None
        import io
        df = pd.read_csv(io.StringIO(r.text))
        tcol = next((c for c in df.columns if c.upper() in ("TIME_PERIOD", "TIME")), None)
        vcol = next((c for c in df.columns if c.upper() in ("OBS_VALUE", "VALUE")), None)
        if not tcol or not vcol:
            log.warning("ABS %s -> unexpected columns %s", dataflow_key, list(df.columns))
            return None
        return _tidy(df[tcol], df[vcol])
    except Exception as e:  # noqa: BLE001
        log.warning("ABS %s unreachable: %s", dataflow_key, str(e)[:80])
        return None


def _fetch_estat(stats_data_id: str) -> Optional[pd.DataFrame]:
    """e-Stat (Japan) as-released. Requires an API key in $ESTAT_APP_ID; without it
    we log and skip (never guess)."""
    app_id = os.environ.get("ESTAT_APP_ID")
    if not app_id:
        log.warning("JPY e-Stat skipped — necesită cheie e-Stat ($ESTAT_APP_ID not set).")
        return None
    url = ("https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
           f"?appId={app_id}&statsDataId={stats_data_id}")
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            log.warning("e-Stat %s -> HTTP %s", stats_data_id, r.status_code)
            return None
        # e-Stat JSON shape varies by table; parsing is table-specific and only
        # wired once a real statsDataId + key are provided.
        log.warning("e-Stat %s fetched but no table mapping configured — refusing.", stats_data_id)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("e-Stat %s unreachable: %s", stats_data_id, str(e)[:80])
        return None


def _fetch_seco_pxweb(px_table: str, query: dict) -> Optional[pd.DataFrame]:
    """SECO/BFS PXWeb: POST a JSON query to a px table, parse the JSON-stat response.
    None on any failure (PXWeb needs the exact table id + query dimensions)."""
    url = f"https://www.pxweb.bfs.admin.ch/api/v1/en/{px_table}"
    try:
        r = requests.post(url, json=query, headers=UA, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            log.warning("SECO PXWeb %s -> HTTP %s (table/query needs confirmation)", px_table, r.status_code)
            return None
        js = r.json()
        vals = js.get("value")
        times = (js.get("dimension", {}).get("Quartal", {}).get("category", {}).get("index"))
        if not vals or not times:
            log.warning("SECO PXWeb %s -> unexpected JSON-stat shape", px_table)
            return None
        dates = sorted(times, key=lambda k: times[k])
        return _tidy(pd.to_datetime([d.replace("q", "-") for d in dates], errors="coerce"), vals)
    except Exception as e:  # noqa: BLE001
        log.warning("SECO PXWeb %s unreachable: %s", px_table, str(e)[:80])
        return None


# ---------------------------------------------------------------------------
# Targets (external). Each candidate carries its own fetcher via `series_id` that
# encodes the source-specific locator; the transform is applied after fetch.
# ---------------------------------------------------------------------------

def _dispatch(cand: "ExtCandidate") -> Optional[pd.DataFrame]:
    if cand.source == "abs":
        return _fetch_abs(cand.locator)
    if cand.source == "estat":
        return _fetch_estat(cand.locator)
    if cand.source == "seco":
        return _fetch_seco_pxweb(cand.locator, cand.query or {})
    return None


class ExtCandidate(Candidate):
    def __init__(self, source: str, locator: str, transform: str, label: str = "", query: dict | None = None):
        super().__init__(series_id=f"{source}:{locator}", transform=transform, label=label)
        self.source = source
        self.locator = locator
        self.query = query


EXT_TARGETS: list[Target] = [
    Target("AUD", "retail_sales", [
        ExtCandidate("abs", "RT/1.20.0.10.M", "level_mom", "ABS Retail Trade turnover SA")],
        note="AUD Retail m/m — ABS Data API (confirm dataflow id/key)"),
    Target("JPY", "gdp_qoq", [
        ExtCandidate("estat", "0003109741", "growth_as_is", "e-Stat Real GDP QoQ")],
        note="JPY GDP q/q — e-Stat (needs $ESTAT_APP_ID + statsDataId)"),
    Target("CHF", "gdp_qoq", [
        ExtCandidate("seco", "px-x-0402000000_101/px-x-0402000000_101.px", "growth_as_is",
                     "SECO GDP QoQ sporting-event-adjusted", query={})],
        note="CHF GDP q/q sporting-event-adjusted — SECO/BFS PXWeb (confirm table/query)"),
]


def apply_ext_fallback(parquet_path: Path = PARQUET, targets: list[Target] = EXT_TARGETS) -> dict:
    """Same guarded engine as FRED, but with external national-source fetchers."""
    if not parquet_path.exists():
        return {"targets": [], "filled": 0}
    df = pd.read_parquet(parquet_path)
    df["release_dt"] = pd.to_datetime(df["release_dt"])
    new_rows: list[dict] = []
    report: list[dict] = []

    for t in targets:
        sub = df[(df["currency"] == t.currency) & (df["indicator_key"] == t.indicator_key)]
        mt5 = mt5_ground_truth(sub)
        gaps = _gap_periods(sub, mt5)
        entry = {"currency": t.currency, "indicator": t.indicator_key, "mt5_points": len(mt5),
                 "gaps": [str(p.date()) for p in sorted(gaps)], "candidates": [],
                 "decision": "REFUSED", "chosen": None, "filled": 0, "note": t.note}
        accepted = []
        for c in t.candidates:
            raw = _dispatch(c)
            if raw is None or len(raw) == 0:
                entry["candidates"].append(f"{c.series_id} ({c.label}): unavailable")
                continue
            fred = _to_period_value(raw, c.transform)
            ev = evaluate_candidate(mt5, fred)
            entry["candidates"].append(f"{c.series_id} ({c.label}): {ev['reason']}")
            if ev["accepted"]:
                accepted.append((c, ev, fred))
        if accepted:
            c, ev, fred = min(accepted, key=lambda x: x[1]["max_diff"])
            entry["decision"] = "ACCEPTED"; entry["chosen"] = f"{c.series_id} ({c.label})"
            for per, sched in gaps.items():
                if per in fred:
                    row = {k: sched.get(k) for k in CALENDAR_COLUMNS}
                    row["actual"] = round(float(fred[per]), 6)
                    row["source"] = c.source
                    row["release_dt"] = pd.Timestamp(sched["release_dt"])
                    new_rows.append(row); entry["filled"] += 1
        else:
            log.warning("EXT fallback REFUSED %s %s — no source continues the MT5 series.",
                        t.currency, t.indicator_key)
        report.append(entry)

    if new_rows:
        merged = pd.concat([df, pd.DataFrame(new_rows, columns=CALENDAR_COLUMNS)], ignore_index=True)
        merged = (merged.sort_values(["currency", "indicator_key", "release_dt"])
                  .drop_duplicates(["currency", "indicator_key", "release_dt"], keep="last")
                  .reset_index(drop=True))
        merged.to_parquet(parquet_path, index=False)
    return {"targets": report, "filled": len(new_rows)}


def main(argv=None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    argparse.ArgumentParser(description="Guarded external-source fallback for MT5 calendar gaps").parse_args(argv)
    rep = apply_ext_fallback()
    print(f"External fallback — {rep['filled']} gap row(s) filled\n")
    for e in rep["targets"]:
        print(f"{e['currency']} {e['indicator']}: {e['decision']} "
              f"(gaps={e['gaps'] or '—'}, filled={e['filled']})")
        for c in e["candidates"]:
            print(f"    - {c}")
        if e["decision"] == "REFUSED":
            print(f"    → REFUSED/kept empty: {e['note']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
