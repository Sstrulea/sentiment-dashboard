"""MEASUREMENT INSTRUMENT — not production code. Read-only. Writes NOTHING
to data/economic_calendar_ff.parquet — this only measures the effect of a
hypothetical purge, per FAZA 2's explicit "STOP before writing" instruction.

Candidate rows: strict Jan/Feb-2023-duplicates-of-Jan/Feb-2024, found
directly on the LIVE parquet (data/economic_calendar_ff.parquet), EXCLUDING
the 3 canonical_ids entangled with the separate 219-row PMI cross-country
contamination (deferred to FAZA 3, which must re-apply this same filter to
the archive's PMI subset before reattributing — see docs/jan2023-duplicate-
purge.md).

For every (currency, indicator_key) touched, reports:
  - row count before -> after
  - trailing-12 surprise n / mean / sigma before -> after
  - the LATEST-print cell score before -> after
Then the full 28-pair + cross-asset bias-flip list, before -> after.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402
from src.economic_compute import build_payload, compute_indicator_score  # noqa: E402
from src.economic_fetch import CompiledMatcher  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
AS_OF = pd.Timestamp("2026-07-31")

PMI_CONTAMINATED_IDS = {
    "gbp_s_p_global_cips_manufacturing_pmi",
    "gbp_s_p_global_cips_services_pmi",
    "cad_s_p_global_manufacturing_pmi",
}


def find_clean_candidates(df: pd.DataFrame) -> pd.DataFrame:
    early = df[(df["datetime_utc"].dt.year == 2023) & (df["datetime_utc"].dt.month.isin([1, 2]))].copy()
    late = df[(df["datetime_utc"].dt.year == 2024) & (df["datetime_utc"].dt.month.isin([1, 2]))].copy()
    early["_key"] = list(zip(early["currency"], early["name_raw"], early["actual"], early["forecast"],
                             early["datetime_utc"].dt.month, early["datetime_utc"].dt.day))
    late["_key"] = list(zip(late["currency"], late["name_raw"], late["actual"], late["forecast"],
                            late["datetime_utc"].dt.month, late["datetime_utc"].dt.day))
    late_keys = set(late["_key"])
    matches = early[early["_key"].isin(late_keys)]
    return matches[~matches["canonical_id"].isin(PMI_CONTAMINATED_IDS)].drop(columns="_key")


def load_configs():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        ind = yaml.safe_load(f)
    with open(ROOT / "data" / "economic_instruments.yaml") as f:
        inst = yaml.safe_load(f)
    return ind, inst


def indicator_surprise_stats(cal: pd.DataFrame, currency: str, key: str, ind_cfg: dict, defaults: dict):
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

    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])

    candidates = find_clean_candidates(df)
    print(f"=== Clean purge candidates (excl. PMI-contaminated canonical_ids): {len(candidates)} rows ===")
    print(candidates[["currency", "name_raw", "canonical_id", "datetime_utc", "actual", "forecast"]]
          .sort_values("datetime_utc").to_string(index=False))

    ind_cfg, inst_cfg = load_configs()
    matcher = build_matcher()
    CCY2COUNTRY = {"USD": "United States", "EUR": "European Union", "GBP": "United Kingdom",
                  "JPY": "Japan", "AUD": "Australia", "NZD": "New Zealand", "CAD": "Canada", "CHF": "Switzerland"}
    matcher_raw = CompiledMatcher(ind_cfg.get("matcher", {}))
    candidates = candidates.copy()
    candidates["indicator_key"] = candidates.apply(
        lambda r: matcher_raw.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)

    unmapped = candidates[candidates["indicator_key"].isna()]
    if not unmapped.empty:
        print()
        print(f"=== {len(unmapped)} candidate row(s) currently UNMAPPED to any indicator_key "
             f"(no scoring impact either way — e.g. CAD 'Core CPI y/y', matcher repointed to "
             f"Median CPI y/y in the prior fix/no-consensus-and-promotions branch) ===")
        print(unmapped[["currency", "name_raw", "name_canonical", "datetime_utc"]].to_string(index=False))

    affected = sorted(set(zip(candidates["currency"], candidates["indicator_key"])) - {(c, None) for c in candidates["currency"]})
    print()
    print(f"=== {len(affected)} (currency, indicator_key) pairs touched (scored only) ===")
    for c, k in affected:
        print("   ", c, k)

    # BEFORE
    cal_before = to_scoring_frame(df, matcher)
    cal_before["release_dt"] = pd.to_datetime(cal_before["release_dt"])
    payload_before = build_payload(cal_before, ind_cfg, inst_cfg, as_of=AS_OF)

    # AFTER: drop the candidate rows from the RAW ff-canonical frame, rebuild scoring frame
    key_cols = ["canonical_id", "datetime_utc"]
    drop_keys = set(zip(candidates["canonical_id"], candidates["datetime_utc"]))
    df_after = df[~df.apply(lambda r: (r["canonical_id"], r["datetime_utc"]) in drop_keys, axis=1)].copy()
    print()
    print(f"row count: before={len(df)} after={len(df_after)} (delta {len(df)-len(df_after)}, expect {len(candidates)})")

    cal_after = to_scoring_frame(df_after, matcher)
    cal_after["release_dt"] = pd.to_datetime(cal_after["release_dt"])
    payload_after = build_payload(cal_after, ind_cfg, inst_cfg, as_of=AS_OF)

    defaults = ind_cfg.get("defaults", {})
    print()
    print("=== Per-(currency,indicator) surprise stats, before -> after ===")
    for c, k in affected:
        ind_c = ind_cfg["indicators"].get(k, {})
        b = indicator_surprise_stats(cal_before, c, k, ind_c, defaults)
        a = indicator_surprise_stats(cal_after, c, k, ind_c, defaults)
        print(f"-- {c} {k}: rows {b['n_rows']}->{a['n_rows']}  pairs {b['n_pairs']}->{a['n_pairs']}  "
             f"mean {b['mean_surprise']}->{a['mean_surprise']}  sigma {b['sigma']}->{a['sigma']}")
        sb = payload_before["currencies"].get(c, {}).get("breakdown", {}).get(k)
        sa = payload_after["currencies"].get(c, {}).get("breakdown", {}).get(k)
        print(f"     latest cell score: {sb.get('score') if sb else None} -> {sa.get('score') if sa else None}  "
             f"(flag {sb.get('flag') if sb else None} -> {sa.get('flag') if sa else None})")

    print()
    print("=== Category-level before -> after, affected currencies ===")
    for c, _ in {(c, None) for c, k in affected}:
        cb = payload_before["currencies"].get(c, {}).get("categories", {})
        ca = payload_after["currencies"].get(c, {}).get("categories", {})
        for cat in cb:
            if cb[cat] != ca.get(cat):
                print(f"   {c} {cat}: {cb[cat]} -> {ca.get(cat)}")

    print()
    print("=== Instrument bias flips, before -> after ===")
    b_by_sym = {i["symbol"]: i for i in payload_before["instruments"]}
    a_by_sym = {i["symbol"]: i for i in payload_after["instruments"]}
    flips = [(s, b_by_sym[s]["bias"], a_by_sym[s]["bias"], b_by_sym[s]["score"], a_by_sym[s]["score"])
            for s in b_by_sym if b_by_sym[s]["bias"] != a_by_sym[s]["bias"]]
    print(f"{len(flips)} pair(s) change bias:")
    for row in flips:
        print("   ", row)

    print()
    moved = [(s, round(b_by_sym[s]["score"], 4), round(a_by_sym[s]["score"], 4))
            for s in b_by_sym if abs(b_by_sym[s]["score"] - a_by_sym[s]["score"]) > 1e-9]
    print(f"=== {len(moved)} instrument(s) change score at all (incl. sub-threshold moves) ===")
    for row in moved:
        print("   ", row)

    candidates.to_csv(ROOT / "docs" / "jan2023-purge-candidates-live-parquet.csv", index=False)


if __name__ == "__main__":
    main()
