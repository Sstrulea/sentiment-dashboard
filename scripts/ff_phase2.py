"""PHASE 2 driver (isolated, read-only over production data).

Task 1: build the historical FF parquet from the raw cache + continuity report.
Task 3: before/after — MT5 (production calendar) vs FF, per currency × category,
        with per-indicator drill-down where |delta| >= 1.
Task 4: sentinel reconciliation (FF vs official vs MT5).
Writes docs/phase2-before-after.md + docs/phase2-before-after.csv (+ per-indicator CSV).
Nothing is committed; no production file is modified.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.econ_calendar_ff import flash_final_revisions, parse_jblanked_range
from src.ff_scoring import (CADENCE_THRESHOLD, NON_ZSCORED_INDICATORS, build_matcher,
                            detect_cadence, load_configs, score_calendar, to_scoring_frame)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ff_calendar_range.json"
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
MT5_PARQUET = ROOT / "data" / "economic_calendar.parquet"
DOCS = ROOT / "docs"
AS_OF = pd.Timestamp("2026-07-05")
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
CATS = ["growth", "inflation", "labour", "monetary"]


def task1_backfill() -> pd.DataFrame:
    ff = parse_jblanked_range(RAW, now_utc=AS_OF)
    ff.to_parquet(FF_PARQUET, index=False)
    lines = ["## TASK 1 — FF historical parquet\n",
             f"- file: `data/economic_calendar_ff.parquet`  ·  **{len(ff)} rows**, "
             f"**{ff['canonical_id'].nunique()} series**, "
             f"{ff['datetime_utc'].min().date()} → {ff['datetime_utc'].max().date()}",
             f"- currencies: {sorted(ff['currency'].unique())}\n",
             "Continuity — monthly-ish series with a gap > 60 days (sanity):\n"]
    gaps = []
    for cid, g in ff.sort_values("datetime_utc").groupby("canonical_id"):
        d = g["datetime_utc"].drop_duplicates().sort_values()
        if len(d) < 6:
            continue
        step = d.diff().dt.days.dropna()
        if step.median() <= 45:  # monthly-ish
            big = step[step > 60]
            if len(big):
                gaps.append((cid, len(big), int(big.max())))
    if gaps:
        lines.append("| series | #gaps>60d | max gap (d) |")
        lines.append("|---|---|---|")
        for cid, n, mx in sorted(gaps, key=lambda x: -x[2]):
            lines.append(f"| {cid} | {n} | {mx} |")
    else:
        lines.append("- none (all monthly series continuous within 60d).")
    (DOCS / "_phase2_task1.md").write_text("\n".join(lines))
    return ff


def _cat(payload, ccy, cat):
    c = (payload.get("currencies", {}).get(ccy, {}).get("categories", {}) or {}).get(cat)
    if not c:
        return None, 0
    return float(c.get("score_precise", 0.0)), int(c.get("coverage", 0))


def _breakdown(payload, ccy):
    return (payload.get("currencies", {}).get(ccy, {}).get("breakdown", {}) or {})


def main() -> int:
    DOCS.mkdir(exist_ok=True)
    ff = task1_backfill()

    # rate_scores (FRED, unchanged) — same for both runs so MonPol delta == 0.
    rate_scores = None
    try:
        from src.rate_compute import compute_rate_scores
        rates_df = pd.read_parquet(ROOT / "data" / "rates.parquet")
        rate_scores = compute_rate_scores(rates_df, as_of=AS_OF.date())
    except Exception as e:  # noqa: BLE001
        print(f"(rate_scores unavailable: {e}; MonPol shown as N/A)")

    # --- Task 2: cadence-aware z-history classification (per-series median interval) ---
    ff_cal_full = to_scoring_frame(ff, build_matcher())
    fc = ff_cal_full.copy()
    fc["a"] = pd.to_numeric(fc.actual, errors="coerce")
    fc["c"] = pd.to_numeric(fc.consensus, errors="coerce")
    full = fc[(fc.a.notna()) & (fc.c.notna())]
    below = []
    for (ccy, key), g in full.groupby(["currency", "indicator_key"]):
        if key in NON_ZSCORED_INDICATORS:      # display-only (rate decisions) — not z-scored
            continue
        cad = detect_cadence(g["release_dt"])
        thr = CADENCE_THRESHOLD[cad]
        n = len(g)
        if n < thr:
            below.append({"currency": ccy, "indicator": key, "cadence": cad, "n": n, "threshold": thr})
    below_df = pd.DataFrame(below).sort_values(["cadence", "n"]) if below else pd.DataFrame(
        columns=["currency", "indicator", "cadence", "n", "threshold"])
    below_df.to_csv(DOCS / "phase2-below-threshold.csv", index=False)

    # --- flash→final revision telemetry (INFO only; never scoring) ---
    rev = flash_final_revisions(RAW)
    rev.to_csv(DOCS / "phase2-flash-final-revisions.csv", index=False)

    # --- score both sources through the SAME build_payload ---
    mt5_cal = pd.read_parquet(MT5_PARQUET)
    ff_cal = ff_cal_full
    mt5 = score_calendar(mt5_cal, AS_OF, rate_scores)
    ffp = score_calendar(ff_cal, AS_OF, rate_scores)

    # indicator_key -> category (for the drill-down)
    ind_cfg, _ = load_configs()
    key_cat = {k: v.get("category") for k, v in ind_cfg.get("indicators", {}).items()}

    rows = []          # category-level before/after
    drill = []         # per-indicator where |delta| >= 1
    for ccy in CCYS:
        for cat in CATS:
            m, mc = _cat(mt5, ccy, cat)
            f, fc = _cat(ffp, ccy, cat)
            if m is None and f is None:
                continue
            mv = 0.0 if m is None else m
            fv = 0.0 if f is None else f
            rows.append({"currency": ccy, "category": cat, "score_mt5": round(mv, 3),
                         "score_ff": round(fv, 3), "delta": round(fv - mv, 3),
                         "cov_mt5": mc, "cov_ff": fc})
            if abs(fv - mv) >= 1.0:
                bm, bf = _breakdown(mt5, ccy), _breakdown(ffp, ccy)
                keys = {k for k, c in key_cat.items() if c == cat} & (set(bm) | set(bf))
                for k in sorted(keys):
                    em, ef = bm.get(k), bf.get(k)
                    drill.append({
                        "currency": ccy, "category": cat, "indicator": k,
                        "mt5_actual": None if not em else em.get("actual"),
                        "mt5_cons": None if not em else em.get("consensus"),
                        "mt5_score": None if not em else em.get("score"),
                        "ff_actual": None if not ef else ef.get("actual"),
                        "ff_cons": None if not ef else ef.get("consensus"),
                        "ff_score": None if not ef else ef.get("score"),
                    })

    cat_df = pd.DataFrame(rows)
    drill_df = pd.DataFrame(drill)
    cat_df.to_csv(DOCS / "phase2-before-after.csv", index=False)
    drill_df.to_csv(DOCS / "phase2-before-after-indicators.csv", index=False)

    # --- console summary ---
    print("\n=== before/after category deltas (|delta|>=1 flagged) ===")
    print(cat_df.to_string(index=False))
    print(f"\nCSV: docs/phase2-before-after.csv ({len(cat_df)} rows), "
          f"indicators drill: {len(drill_df)} rows")
    big = cat_df[cat_df["delta"].abs() >= 2]
    print(f"\n|delta| >= 2 cells: {len(big)}")
    print(big.to_string(index=False) if len(big) else "  (none)")

    print(f"\n=== Task 2: below z-history threshold (cadence-aware): {len(below_df)} series ===")
    print(below_df.to_string(index=False) if len(below_df) else "  (none)")
    print(f"\nflash→final revision telemetry: {len(rev)} pairs -> docs/phase2-flash-final-revisions.csv")

    return cat_df, drill_df, below_df, rev, ff


if __name__ == "__main__":
    main()
