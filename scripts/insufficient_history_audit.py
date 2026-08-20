#!/usr/bin/env python3
"""FAZA 1D Part 2 — insufficient_history breakdown + counterfactual (read-only,
in-memory only; never writes data/economic_indicators.yaml).

IMPORTANT CORRECTION (see report): the scored/insufficient_history gate is
`defaults.fallback_min_prints` (default 6), NOT `defaults.surprise_window_k`
(default 12) — confirmed against economic_compute.py's own code AND against
existing project docs (docs/proposal-staleness-gate.md, docs/usd-gdp-shutdown-
gap.md) that already made this exact correction previously. window_k only
caps the MAXIMUM trailing sample once a series has already cleared
fallback_min_prints — changing it cannot move a single row from
insufficient_history to scored. Both counterfactuals are shown below.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import history_compute as hc  # noqa: E402
from src.data_integrity import build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros  # noqa: E402
from src.economic_compute import compute_indicator_score  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, build_matcher  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
AS_OF = pd.Timestamp.now()


def build_quarantine_df(ff, matcher):
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})[
        ["currency", "indicator_key", "canonical_id", "name_raw", "release_dt",
         "actual", "forecast", "previous"]]
    return build_quarantine_proposal(detect_ghost_rows(df), detect_implausible_zeros(df))


def part_2_1(series_cache: dict, ind_cfg: dict) -> list[dict]:
    from src.ff_scoring import detect_cadence
    rows = []
    for (ccy, key), df in series_cache.items():
        printed = df[df["actual"].notna()].sort_values("release_dt")
        if printed.empty:
            continue
        cadence = detect_cadence(printed["release_dt"]) if len(printed) >= 2 else "unknown"
        n_scored = int((printed["score_status"] == "scored").sum())
        n_insuff = int((printed["score_status"] == "insufficient_history").sum())
        n_no_actual = int((printed["score_status"] == "no_actual").sum())  # should be 0 among printed
        first_scored = printed[printed["score_status"] == "scored"]
        first_scored_dt = first_scored.iloc[0]["release_dt"] if len(first_scored) else None
        rows.append({
            "ccy": ccy, "key": key, "cadence": cadence, "n_total": len(printed),
            "n_scored": n_scored, "n_insufficient": n_insuff, "n_no_actual_flag": n_no_actual,
            "first_scored_release_dt": str(first_scored_dt) if first_scored_dt is not None else None,
            "first_release_dt": str(printed.iloc[0]["release_dt"]),
        })
    return rows


def _recompute_with_params(full_frame: pd.DataFrame, ccy: str, key: str, ind_cfg: dict,
                           window_k: int | None, fallback_min_prints: int | None) -> dict:
    """Re-run ONLY the scored-vs-fallback classification with modified
    defaults, in-memory — never touches data/economic_indicators.yaml.
    Reuses compute_indicator_score itself (not re-derived), just fed a
    different `defaults` dict per counterfactual point in time, same as
    history_compute.py's own per-row backtest pattern."""
    defaults = dict(ind_cfg.get("defaults", {}) or {})
    if window_k is not None:
        defaults["surprise_window_k"] = window_k
    if fallback_min_prints is not None:
        defaults["fallback_min_prints"] = fallback_min_prints
    cfg = (ind_cfg.get("indicators", {}) or {}).get(key, {})

    sub = full_frame[(full_frame["currency"] == ccy) & (full_frame["indicator_key"] == key)]
    sub = sub.sort_values("release_dt").reset_index(drop=True)
    printed = sub[sub["actual"].notna()]

    n_scored = n_insuff = 0
    sigmas = []
    for _, row in printed.iterrows():
        upto = sub[sub["release_dt"] <= row["release_dt"]]
        res = compute_indicator_score(upto, cfg, defaults, row["release_dt"], allow_stale=True,
                                      currency=ccy, indicator_key=key)
        if res is None:
            continue
        if res["flag"] is None and res["z"] is not None:
            n_scored += 1
            if res.get("surprise") and res["z"]:
                sigmas.append(abs(res["surprise"] / res["z"]))
        elif res["flag"] != "no_consensus" and res["flag"] != "direction_mismatch":
            n_insuff += 1
    return {"n_scored": n_scored, "n_insufficient": n_insuff,
            "sigma_last": sigmas[-1] if sigmas else None,
            "sigma_median": float(pd.Series(sigmas).median()) if sigmas else None}


def part_2_2(full_frame: pd.DataFrame, ind_cfg: dict, quarterly_keys: list[tuple]) -> dict:
    out = {}
    for ccy, key in quarterly_keys:
        base = _recompute_with_params(full_frame, ccy, key, ind_cfg, None, None)
        variants = {}
        for label, wk, fmp in [
            ("window_k=8 (fallback_min_prints unchanged=6)", 8, None),
            ("window_k=6 (fallback_min_prints unchanged=6)", 6, None),
            ("fallback_min_prints=4 (window_k unchanged=12)", None, 4),
            ("fallback_min_prints=3 (window_k unchanged=12)", None, 3),
        ]:
            variants[label] = _recompute_with_params(full_frame, ccy, key, ind_cfg, wk, fmp)
        out[f"{ccy}/{key}"] = {"base": base, "variants": variants}
    return out


def main() -> int:
    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    ind_cfg = hc.load_indicators_cfg()
    matcher = build_matcher()
    catalog = hc.load_catalog()
    quarantine_df = build_quarantine_df(ff, matcher)
    series_cache = hc.compute_catalog(ff, ind_cfg, catalog, quarantine_df, as_of=AS_OF)
    full_frame = hc.build_full_frame(ff, matcher, hc.load_can_be_zero(ind_cfg),
                                     hc.build_flagged_bad_lookup(), hc.load_overrides(hc.MANUAL_ACTUALS_OVERRIDES), AS_OF)

    print("=" * 100)
    print("2.1 — per-series n_scored / n_insufficient_history / first scored release")
    print("=" * 100)
    rows = part_2_1(series_cache, ind_cfg)
    rows.sort(key=lambda r: (r["cadence"], r["ccy"], r["key"]))
    print(f"{'CADENCE':<11}{'CCY':<5}{'KEY':<28}{'N':>4}{'SCORED':>8}{'INSUFF':>8}{'FIRST_SCORED':>15}")
    for r in rows:
        print(f"{r['cadence']:<11}{r['ccy']:<5}{r['key']:<28}{r['n_total']:>4}{r['n_scored']:>8}"
             f"{r['n_insufficient']:>8}   {r['first_scored_release_dt']}")

    by_cadence = {}
    for r in rows:
        c = by_cadence.setdefault(r["cadence"], {"scored": 0, "insuff": 0, "total": 0})
        c["scored"] += r["n_scored"]; c["insuff"] += r["n_insufficient"]; c["total"] += r["n_total"]
    print("\nAggregate insufficient_history %% by cadence:")
    for cad, c in by_cadence.items():
        pct = round(100 * c["insuff"] / c["total"], 1) if c["total"] else None
        print(f"  {cad:<11} insufficient={c['insuff']}/{c['total']} ({pct}%)")

    print()
    print("=" * 100)
    print("2.2 — counterfactual (in-memory only, nothing written to economic_indicators.yaml)")
    print("=" * 100)
    quarterly = [(r["ccy"], r["key"]) for r in rows if r["cadence"] == "quarterly"]
    result = part_2_2(full_frame, ind_cfg, quarterly)
    for series, d in result.items():
        b = d["base"]
        print(f"\n{series}  base(window_k=12,fallback_min_prints=6): "
             f"scored={b['n_scored']} insufficient={b['n_insufficient']} "
             f"sigma_last={b['sigma_last']}")
        for label, v in d["variants"].items():
            print(f"    {label:<48} scored={v['n_scored']:>3} insufficient={v['n_insufficient']:>3} "
                 f"sigma_last={v['sigma_last']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
