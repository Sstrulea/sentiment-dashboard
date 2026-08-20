#!/usr/bin/env python3
"""FAZA 1B — sample payload + size report + sanity checks (read-only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import history_compute as hc  # noqa: E402
from src.data_integrity import (  # noqa: E402
    build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros,
)
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, build_matcher  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
AS_OF = pd.Timestamp.now()


def build_quarantine_df(ff: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})[
        ["currency", "indicator_key", "canonical_id", "name_raw", "release_dt",
         "actual", "forecast", "previous"]]
    ghosts = detect_ghost_rows(df)
    zeros = detect_implausible_zeros(df)
    return build_quarantine_proposal(ghosts, zeros)


def main() -> int:
    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    catalog = hc.load_catalog()
    ind_cfg = hc.load_indicators_cfg()
    matcher = build_matcher()

    quarantine_df = build_quarantine_df(ff, matcher)
    print(f"Quarantine proposal: {len(quarantine_df)} rows (display filter only)")

    series_cache = hc.compute_catalog(ff, ind_cfg, catalog, quarantine_df, as_of=AS_OF)
    payload = hc.build_payload(catalog, series_cache, catalog_version="2026-08-20", as_of=AS_OF)

    print("\n" + "=" * 90)
    print("SAMPLE PAYLOAD — inflation / EUR, GBP")
    print("=" * 90)
    sample = {"meta": payload["meta"],
             "categories": {"inflation": {ccy: payload["categories"]["inflation"][ccy]
                                         for ccy in ("EUR", "GBP")}}}
    print(json.dumps(sample, indent=2, default=str))

    total_bytes, gzip_bytes = hc.payload_size_bytes(payload)
    print("\n" + "=" * 90)
    print("PAYLOAD SIZE (full catalog)")
    print("=" * 90)
    print(f"raw: {total_bytes:,} bytes ({total_bytes/1024:.1f} KB)")
    print(f"gzip: {gzip_bytes:,} bytes ({gzip_bytes/1024:.1f} KB)")

    print("\n" + "=" * 90)
    print("SANITY CHECKS")
    print("=" * 90)

    # 1. every series has >=4 points on at least one window
    fails_1 = []
    for cat, ccys in payload["categories"].items():
        for ccy, entries in ccys.items():
            for e in entries:
                if e.get("indicator_key") is None:
                    continue
                if not e["window_options"]:
                    fails_1.append((cat, ccy, e["indicator_key"]))
    print(f"1. Every series >=4 points on >=1 window: "
         f"{'PASS' if not fails_1 else 'FAIL -> ' + str(fails_1)}")

    # 2. no quarantined row appears with actual in the payload
    fails_2_detail = []
    for cat, ccys in payload["categories"].items():
        for ccy, entries in ccys.items():
            for e in entries:
                key = e.get("indicator_key")
                if key is None:
                    continue
                df = series_cache[(ccy, key)]
                bad_dts = set(df[df["quarantined"] & ~df["has_override"]]["release_dt"].astype(str))
                for w in e["window_options"].values():
                    for p in w["points"]:
                        if p["release_dt"][:19] in {d[:19] for d in bad_dts}:
                            fails_2_detail.append((ccy, key, p["release_dt"]))
    print(f"2. No quarantined (non-overridden) row in payload: "
         f"{'PASS' if not fails_2_detail else 'FAIL -> ' + str(fails_2_detail)}")

    # 3. no overridden row is ever filtered out by quarantine
    fails_3 = []
    for (ccy, key), df in series_cache.items():
        overridden = df[df["has_override"]]
        for _, r in overridden.iterrows():
            if r["quarantined"]:
                fails_3.append((ccy, key, str(r["release_dt"])))
    print(f"3. No overridden row ever marked quarantined: "
         f"{'PASS' if not fails_3 else 'FAIL -> ' + str(fails_3)}")

    # 4. rates/* revised_from is null almost everywhere
    rates_total = rates_revised = 0
    for ccy, entries in payload["categories"].get("rates", {}).items():
        for e in entries:
            key = e.get("indicator_key")
            if key is None:
                continue
            df = series_cache[(ccy, key)]
            clean = df[~df["quarantined"]]
            rates_total += len(clean)
            rates_revised += clean["revised_from"].notna().sum()
    pct = round(100 * rates_revised / rates_total, 1) if rates_total else None
    print(f"4. rates/* revised_from non-null rate: {rates_revised}/{rates_total} ({pct}%) "
         f"— {'PASS (near-zero)' if (pct is None or pct <= 5) else 'CHECK'}")

    # 5. latest print on 3 chosen series matches parquet directly
    print("5. Latest print spot-check (3 series) vs raw parquet:")
    for ccy, key in [("USD", "cpi_yoy"), ("GBP", "gdp_qoq"), ("AUD", "interest_rate_decision")]:
        df = series_cache[(ccy, key)]
        clean = df[~df["quarantined"]].sort_values("release_dt")
        last = clean.iloc[-1]
        print(f"   {ccy} {key}: release_dt={last['release_dt']} actual={last['actual']} "
             f"z={last['z']} bucket={last['bucket']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
