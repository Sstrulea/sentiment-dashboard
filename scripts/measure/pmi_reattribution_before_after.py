"""MEASUREMENT INSTRUMENT — not production code. Read-only. Writes NOTHING
to data/economic_calendar_ff.parquet.

FAZA 3 — reattribute the 219 rows purged on 2026-07-30 (commit 8223035,
cross-country PMI contamination) to their real country, by matching the
LOCAL HOUR of each purged cluster against EUR/JPY/CHF's own correctly-
labeled 2026 copies (data-confirmed, not guessed — see the printed
validation table). Applies FAZA 2's exact Jan/Feb-2023-duplicate filter to
this 219-row subset FIRST (per the task's explicit dependency), excluding
4 rows that are themselves year-shift duplicates, not genuine history.

76 of the 219 rows (a distinct hour cluster at 13:45/14:45 UTC, plus a
handful of singleton outliers) do NOT match any of EUR/JPY/CHF/GBP/CAD's
own known release-hour patterns and are left UNIDENTIFIED — not
reattributed, not guessed. See docs/pmi-reattribution-before-after.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402
from src.economic_compute import build_payload  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
AS_OF = pd.Timestamp("2026-07-31")

REATTRIBUTION_TARGET = {
    ("CHF", "manufacturing_pmi"): ("CHF", "procure.ch Manufacturing PMI", "Manufacturing PMI"),
    ("JPY", "manufacturing_pmi"): ("JPY", "au Jibun Bank Manufacturing PMI", "Flash Manufacturing PMI"),
    ("EUR", "manufacturing_pmi"): ("EUR", "S&P Global Manufacturing PMI", "Flash Manufacturing PMI"),
    ("EUR", "services_pmi"): ("EUR", "S&P Global Services PMI", "Flash Services PMI"),
}


def load_configs():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        ind = yaml.safe_load(f)
    with open(ROOT / "data" / "economic_instruments.yaml") as f:
        inst = yaml.safe_load(f)
    return ind, inst


def indicator_surprise_stats(cal: pd.DataFrame, currency: str, key: str, defaults: dict):
    sub = cal[(cal["currency"] == currency) & (cal["indicator_key"] == key)]
    pairs = sub[sub["actual"].notna() & sub["consensus"].notna()].sort_values("release_dt")
    diffs = (pairs["actual"] - pairs["consensus"]).tail(int(defaults.get("surprise_window_k", 12)))
    n = len(diffs)
    return {
        "n_rows": int(sub["actual"].notna().sum()),
        "n_pairs": n,
        "mean_surprise": float(diffs.mean()) if n else None,
        "sigma": float(diffs.std(ddof=1)) if n >= 2 else None,
    }


def main() -> None:
    pd.set_option("display.max_rows", None, "display.width", 160)

    candidates = pd.read_csv(ROOT / "docs" / "pmi-reattribution-candidates.csv", parse_dates=["datetime_utc"])
    print(f"=== {len(candidates)} clean reattribution rows (post Jan-2023-dup filter) ===")
    print(candidates.groupby(["reatt_ccy", "reatt_key"]).size().to_string())

    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])

    ind_cfg, inst_cfg = load_configs()
    matcher = build_matcher()
    defaults = ind_cfg.get("defaults", {})

    cal_before = to_scoring_frame(df, matcher)
    cal_before["release_dt"] = pd.to_datetime(cal_before["release_dt"])
    payload_before = build_payload(cal_before, ind_cfg, inst_cfg, as_of=AS_OF)

    # AFTER: these 139 rows were REMOVED from the live parquet by the 2026-07-30
    # PMI purge (commit 8223035) — they don't exist in `df` anymore. Relabel
    # them (from `candidates`, which already carries the original archived
    # row data) to their true currency/name_canonical, then ADD (concat) them
    # to the current live frame — this is an additive reattribution, not an
    # in-place edit.
    df_after = df.copy()
    added_parts = []
    for (ccy, key), grp in candidates.groupby(["reatt_ccy", "reatt_key"]):
        target_ccy, target_canonical, target_raw = REATTRIBUTION_TARGET[(ccy, key)]
        relabeled = grp.copy()
        relabeled["currency"] = target_ccy
        relabeled["name_canonical"] = target_canonical
        relabeled["canonical_id"] = f"{target_ccy.lower()}_{target_canonical.lower().replace(' ', '_').replace('.', '')}"
        added_parts.append(relabeled[["canonical_id", "currency", "name_raw", "name_canonical",
                                      "datetime_utc", "actual", "forecast", "previous", "released", "source"]])
    df_after = pd.concat([df_after] + added_parts, ignore_index=True)

    cal_after = to_scoring_frame(df_after, matcher)
    cal_after["release_dt"] = pd.to_datetime(cal_after["release_dt"])
    payload_after = build_payload(cal_after, ind_cfg, inst_cfg, as_of=AS_OF)

    print()
    print("=== Per-series surprise stats, before -> after ===")
    for ccy, key in REATTRIBUTION_TARGET:
        b = indicator_surprise_stats(cal_before, ccy, key, defaults)
        a = indicator_surprise_stats(cal_after, ccy, key, defaults)
        print(f"-- {ccy} {key}: rows {b['n_rows']}->{a['n_rows']}  pairs {b['n_pairs']}->{a['n_pairs']}  "
             f"mean {b['mean_surprise']}->{a['mean_surprise']}  sigma {b['sigma']}->{a['sigma']}")
        sb = payload_before["currencies"].get(ccy, {}).get("breakdown", {}).get(key)
        sa = payload_after["currencies"].get(ccy, {}).get("breakdown", {}).get(key)
        print(f"     latest cell: score {sb.get('score') if sb else None}->{sa.get('score') if sa else None}  "
             f"flag {sb.get('flag') if sb else None}->{sa.get('flag') if sa else None}")

    print()
    print("=== Category-level before -> after, affected currencies (CHF/JPY/EUR) ===")
    for c in ["CHF", "JPY", "EUR"]:
        cb = payload_before["currencies"].get(c, {}).get("categories", {})
        ca = payload_after["currencies"].get(c, {}).get("categories", {})
        for cat in cb:
            if cb[cat] != ca.get(cat):
                print(f"   {c} {cat}: {cb[cat]} -> {ca.get(cat)}")

    print()
    print("=== Instrument bias flips, before -> after ===")
    b_by_sym = {i["symbol"]: i for i in payload_before["instruments"]}
    a_by_sym = {i["symbol"]: i for i in payload_after["instruments"]}
    flips = [(s, b_by_sym[s]["bias"], a_by_sym[s]["bias"], round(b_by_sym[s]["score"], 4), round(a_by_sym[s]["score"], 4))
            for s in b_by_sym if b_by_sym[s]["bias"] != a_by_sym[s]["bias"]]
    print(f"{len(flips)} pair(s) change bias:")
    for row in flips:
        print("   ", row)

    moved = [(s, round(b_by_sym[s]["score"], 4), round(a_by_sym[s]["score"], 4))
            for s in b_by_sym if abs(b_by_sym[s]["score"] - a_by_sym[s]["score"]) > 1e-9]
    print(f"\n=== {len(moved)} instrument(s) change score at all ===")
    for row in moved:
        print("   ", row)


if __name__ == "__main__":
    main()
