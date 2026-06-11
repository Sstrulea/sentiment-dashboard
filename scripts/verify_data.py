"""3-level health check for the Economic Dashboard data chain.

Verifies that what the dashboard shows is the latest, correct data from MT5:

    MT5 economic_calendar.csv  →  economic_calendar.parquet  →  economic.json  →  page

    python -m scripts.verify_data            # sample of key indicators
    python -m scripts.verify_data --all      # every indicator in the payload

Checks:
  1. FRESHNESS   — is economic.json newer than the MT5 CSV (dashboard not behind)?
  2. VALUES      — last published `actual` identical across CSV ↔ parquet ↔ JSON?
  3. STALE FLAGS — which indicators are greyed/excluded (silent staleness made visible).

Exit code 0 if all PASS (stale flags are informational), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.economic_fetch import (
    CompiledMatcher,
    _csv_path,
    _load_indicators_cfg,
    PARQUET,
    load_raw,
    normalize,
)

ROOT = Path(__file__).resolve().parents[1]
ECON_JSON = ROOT / "public" / "data" / "economic.json"

# (currency, indicator_key) sample for the default value check.
SAMPLE = [
    ("USD", "employment_change"), ("USD", "cpi_yoy"), ("USD", "jobless_claims"),
    ("EUR", "cpi_yoy"), ("EUR", "core_cpi"), ("GBP", "gdp_qoq"),
    ("GBP", "services_pmi"), ("JPY", "cpi_yoy"), ("CAD", "unemployment_rate"),
]


def _latest_published(df: pd.DataFrame):
    """(release_dt.date, actual) of the most recent row with a non-null actual."""
    pub = df[df["actual"].notna()]
    if pub.empty:
        return None
    row = pub.sort_values("release_dt").iloc[-1]
    return pd.Timestamp(row["release_dt"]).date(), float(row["actual"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Economic Dashboard data health check.")
    ap.add_argument("--all", action="store_true", help="check every indicator, not just a sample")
    args = ap.parse_args(argv)

    failures = 0
    print("=" * 78)
    print("ECONOMIC DASHBOARD — DATA HEALTH CHECK")
    print("=" * 78)

    # ---- load the three layers ------------------------------------------
    csv_path = _csv_path()
    csv_mtime = datetime.fromtimestamp(os.path.getmtime(csv_path), tz=timezone.utc)
    pq = pd.read_parquet(PARQUET)
    pq["release_dt"] = pd.to_datetime(pq["release_dt"])
    payload = json.loads(ECON_JSON.read_text())
    gen = payload.get("generated_at")
    gen_dt = pd.Timestamp(gen).to_pydatetime() if gen else None
    if gen_dt and gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=timezone.utc)

    # CSV → normalized (what fetch WOULD produce right now)
    matcher = CompiledMatcher(_load_indicators_cfg().get("matcher", {}))
    csv_norm = normalize(load_raw(), matcher)

    # ---- Check 1: freshness ---------------------------------------------
    print("\n[1] FRESHNESS")
    print(f"  MT5 csv written     : {csv_mtime:%Y-%m-%d %H:%M UTC}")
    print(f"  dashboard generated : {gen_dt:%Y-%m-%d %H:%M UTC}" if gen_dt else "  dashboard generated : —")
    if gen_dt and gen_dt >= csv_mtime:
        print("  PASS — dashboard rendered after the latest MT5 write.")
    else:
        print("  FAIL — dashboard is OLDER than the MT5 CSV → run `python -m src.main --mode economic`.")
        failures += 1

    # ---- Check 2: value consistency CSV ↔ parquet ↔ JSON ----------------
    print("\n[2] VALUES — last published actual across the three layers")
    pairs = []
    if args.all:
        for ccy, card in payload.get("currencies", {}).items():
            for key in (card.get("breakdown") or {}):
                if key != "rate_expectations":
                    pairs.append((ccy, key))
    else:
        pairs = SAMPLE
    print(f"  {'ccy/indicator':26}{'CSV':>16}{'parquet':>16}{'dashboard':>16}  ok")
    mism = 0
    for ccy, key in pairs:
        c = _latest_published(csv_norm[(csv_norm.currency == ccy) & (csv_norm.indicator_key == key)]) if not csv_norm.empty else None
        p = _latest_published(pq[(pq.currency == ccy) & (pq.indicator_key == key)])
        e = (payload.get("currencies", {}).get(ccy, {}).get("breakdown", {}) or {}).get(key)
        j = (pd.Timestamp(e["release_dt"]).date(), float(e["actual"])) if e and e.get("actual") is not None else None
        def fmt(t):
            return f"{t[1]:g}@{t[0]}" if t else "—"
        # values must agree; the date may differ by ≤1 day (tz: CSV server vs UTC).
        vals = [x[1] for x in (c, p, j) if x]
        val_ok = len(vals) >= 2 and max(vals) - min(vals) < 1e-9
        ok = "✅" if val_ok else "⚠️"
        if not val_ok:
            mism += 1
        print(f"  {ccy} {key:21}{fmt(c):>16}{fmt(p):>16}{fmt(j):>16}  {ok}")
    if mism == 0:
        print("  PASS — values match across CSV, parquet and dashboard.")
    else:
        print(f"  FAIL — {mism} indicator(s) differ (fetch/render behind, or a mapping/dedup issue).")
        failures += 1

    # ---- Check 3: stale flags -------------------------------------------
    print("\n[3] STALE / GREYED (excluded from score — informational)")
    stale = []
    for ccy, card in payload.get("currencies", {}).items():
        for key, e in (card.get("breakdown") or {}).items():
            if e.get("stale"):
                stale.append((ccy, key, e.get("age_days")))
    if stale:
        for ccy, key, age in sorted(stale):
            age_str = f"{age}d" if age is not None else "n/a"
            print(f"  {ccy} {key:22} age={age_str}")
    else:
        print("  none — every scored indicator is within its recency window.")

    print("\n" + "=" * 78)
    print("RESULT:", "✅ ALL PASS" if failures == 0 else f"❌ {failures} CHECK(S) FAILED")
    print("=" * 78)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
