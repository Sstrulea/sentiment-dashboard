"""MEASUREMENT INSTRUMENT — not production code. Read-only. Writes nothing.

FAZA 2 (fix/watchdog-per-instrument) — measures the effect of per-currency
`can_be_zero` granularity for the 14 "suspect" series identified in
docs/faza1-chf-cpi-zero-placeholder-inventory.md: what would N, score_precise,
cell score, and pair bias look like if actual==0.0 were treated as a
legitimate reading (not a placeholder) for EACH of these 14 (currency,
indicator_key) pairs specifically — without touching data/economic_indicators.yaml
or any production code.

Reproduces `src/ff_scoring.to_scoring_frame`'s exact logic, parameterized by
a PER-(currency, indicator_key) exception set instead of the current global
per-indicator `can_be_zero` set — this is the simulation of "option 1"
(per-currency override) from docs/proposal-can-be-zero-granularity.md,
without writing the override anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.economic_compute import build_payload  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, load_can_be_zero  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
AS_OF = pd.Timestamp("2026-07-31")

SUSPECT_SERIES = [
    ("CHF", "cpi_yoy"), ("CAD", "gdp_qoq"), ("GBP", "ppi_yoy"), ("CHF", "gdp_qoq"),
    ("EUR", "gdp_qoq"), ("CHF", "ppi_yoy"), ("GBP", "gdp_qoq"), ("CAD", "ppi_yoy"),
    ("CAD", "cpi_yoy"), ("USD", "core_pce"), ("USD", "ppi_yoy"), ("USD", "gdp_qoq"),
    ("AUD", "ppi_yoy"), ("USD", "wage_growth"),
]


def load_configs():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        ind = yaml.safe_load(f)
    with open(ROOT / "data" / "economic_instruments.yaml") as f:
        inst = yaml.safe_load(f)
    return ind, inst


def to_scoring_frame_with_exceptions(ff_df: pd.DataFrame, matcher: CompiledMatcher,
                                     per_currency_exceptions: set[tuple[str, str]]) -> pd.DataFrame:
    """Mirrors ff_scoring.to_scoring_frame's zero-placeholder quarantine
    EXACTLY, except: for (currency, indicator_key) pairs in
    `per_currency_exceptions`, actual==0.0 is treated as legitimate (kept,
    not nulled) — simulating a per-currency can_be_zero override. Consensus
    (forecast) quarantine is UNCHANGED (still global, matching today's
    behavior) — this measurement is scoped to the actual-side question the
    task asks about."""
    cbz = load_can_be_zero()
    nan = float("nan")
    recs: list[dict] = []
    for r in ff_df.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        actual, consensus = r.actual, r.forecast
        exempt = (r.currency, key) in per_currency_exceptions
        if key not in cbz and not exempt:
            if actual == 0.0:
                actual = nan
        if key not in cbz:
            if consensus == 0.0:
                consensus = nan
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
        })
    return pd.DataFrame(recs, columns=["currency", "indicator_key", "release_dt",
                                       "actual", "consensus", "previous", "source"])


def indicator_stats(cal: pd.DataFrame, currency: str, key: str, defaults: dict):
    sub = cal[(cal["currency"] == currency) & (cal["indicator_key"] == key)]
    pairs = sub[sub["actual"].notna() & sub["consensus"].notna()].sort_values("release_dt")
    diffs = (pairs["actual"] - pairs["consensus"]).tail(int(defaults.get("surprise_window_k", 12)))
    n = len(diffs)
    return {
        "n_rows_actual_valid": int(sub["actual"].notna().sum()),
        "n_pairs": n,
        "mean_surprise": float(diffs.mean()) if n else None,
        "sigma": float(diffs.std(ddof=1)) if n >= 2 else None,
    }


def main() -> None:
    pd.set_option("display.max_rows", None, "display.width", 160)

    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    ind_cfg, inst_cfg = load_configs()
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}))
    defaults = ind_cfg.get("defaults", {})

    cal_before = to_scoring_frame_with_exceptions(df, matcher, set())
    cal_before["release_dt"] = pd.to_datetime(cal_before["release_dt"])
    payload_before = build_payload(cal_before, ind_cfg, inst_cfg, as_of=AS_OF)

    print("=== Per-series recovery, isolated (one series exempted at a time) ===")
    for ccy, key in SUSPECT_SERIES:
        cal_iso = to_scoring_frame_with_exceptions(df, matcher, {(ccy, key)})
        cal_iso["release_dt"] = pd.to_datetime(cal_iso["release_dt"])
        payload_iso = build_payload(cal_iso, ind_cfg, inst_cfg, as_of=AS_OF)

        b = indicator_stats(cal_before, ccy, key, defaults)
        a = indicator_stats(cal_iso, ccy, key, defaults)
        recovered_rows = a["n_rows_actual_valid"] - b["n_rows_actual_valid"]

        cat_before = payload_before["currencies"].get(ccy, {}).get("categories", {})
        cat_after = payload_iso["currencies"].get(ccy, {}).get("categories", {})
        bd_before = payload_before["currencies"].get(ccy, {}).get("breakdown", {}).get(key, {})
        bd_after = payload_iso["currencies"].get(ccy, {}).get("breakdown", {}).get(key, {})
        cat_key = bd_before.get("category") or bd_after.get("category")
        cb = cat_before.get(cat_key, {})
        ca = cat_after.get(cat_key, {})

        b_by_sym = {i["symbol"]: i for i in payload_before["instruments"]}
        a_by_sym = {i["symbol"]: i for i in payload_iso["instruments"]}
        flips = [s for s in b_by_sym if b_by_sym[s]["bias"] != a_by_sym[s]["bias"]]

        print(f"-- {ccy} {key}: recovered rows +{recovered_rows}  "
             f"latest flag {bd_before.get('flag')}->{bd_after.get('flag')}  "
             f"latest score {bd_before.get('score')}->{bd_after.get('score')}")
        print(f"     category {cat_key}: N {cb.get('coverage')}->{ca.get('coverage')}  "
             f"precise {cb.get('score_precise')}->{ca.get('score_precise')}  "
             f"cell {cb.get('score_cell')}->{ca.get('score_cell')}")
        print(f"     bias flips: {flips if flips else 'none'}")

    print()
    print("=== Combined scenario: all 14 series exempted at once ===")
    cal_combined = to_scoring_frame_with_exceptions(df, matcher, set(SUSPECT_SERIES))
    cal_combined["release_dt"] = pd.to_datetime(cal_combined["release_dt"])
    payload_combined = build_payload(cal_combined, ind_cfg, inst_cfg, as_of=AS_OF)

    b_by_sym = {i["symbol"]: i for i in payload_before["instruments"]}
    c_by_sym = {i["symbol"]: i for i in payload_combined["instruments"]}
    flips = [(s, b_by_sym[s]["bias"], c_by_sym[s]["bias"]) for s in b_by_sym
            if b_by_sym[s]["bias"] != c_by_sym[s]["bias"]]
    print(f"{len(flips)} pair(s) change bias in the combined scenario:")
    for row in flips:
        print("   ", row)

    for ccy in sorted({c for c, _ in SUSPECT_SERIES}):
        cb = payload_before["currencies"].get(ccy, {}).get("categories", {})
        cc = payload_combined["currencies"].get(ccy, {}).get("categories", {})
        for cat in cb:
            if cb[cat] != cc.get(cat):
                print(f"   {ccy} {cat}: {cb[cat]} -> {cc.get(cat)}")


if __name__ == "__main__":
    main()
