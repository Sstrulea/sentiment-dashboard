"""§5 pre-registered measurements (M-1..M-7) for D1 in {C, D, E, NULL},
each combined with D2-c on both folds (sentiment w_s=0.125, trend
macro_weight_target=4.371794871794871).

READ-ONLY. No adoption, no code/config change. M-5 not applicable (CN-1
adopted -> A/B eliminated, no imputation footprint to measure).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import CCYS, DOCS_DIR, FX_PAIRS, load_window  # noqa: E402
from scripts.diag.prereg_m_candidates import (  # noqa: E402
    MACRO_WEIGHT_TARGET, TREND_WEIGHT, W_S, effective_sentiment_weight,
    effective_trend_weight, score_pair,
)

CANDIDATES = ["null", "e", "d", "c"]
DISPLAY = {"null": "NULL", "e": "E", "d": "D", "c": "C"}
PRICE_PATH = ROOT / "data" / "price_history.parquet"
HORIZONS = (5, 10)
RNG_SEED = 20260728  # M-7 placebo, single reproducible permutation


def _bucket_order():
    return ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]


# ---------------------------------------------------------------------------
# M-1, M-2, M-3
# ---------------------------------------------------------------------------

def run_m1_m2_m3(win) -> dict:
    snapshots = win["snapshots"]
    long_rows = []
    for s in snapshots:
        cur_by_sym = {i["symbol"]: i for i in s["instruments"]}
        for sym in FX_PAIRS:
            cur = cur_by_sym.get(sym)
            if cur is None:
                continue
            row = {"as_of": s["as_of"], "symbol": sym,
                  "current_score": cur["score"], "current_bias": cur["bias"]}
            for cand in CANDIDATES:
                r = score_pair(sym, s, cand)
                row[f"{cand}_score"] = r["score"]
                row[f"{cand}_bias"] = r["bias"]
                row[f"{cand}_ok"] = r["coverage_ok"]
            long_rows.append(row)
    df = pd.DataFrame(long_rows)
    df.to_csv(DOCS_DIR / "diag-M5-candidates-long.csv", index=False)

    m1 = {}
    m2 = {}
    m3 = {}
    for cand in CANDIDATES:
        ok = df[f"{cand}_ok"]
        m1[cand] = {"pct_scorable": 100.0 * ok.mean(), "n_scorable_mean_per_day": ok.sum() / df["as_of"].nunique()}

        sub = df[ok]
        flipped = sub["current_bias"] != sub[f"{cand}_bias"]
        flip_matrix = pd.crosstab(sub["current_bias"], sub[f"{cand}_bias"])
        m2[cand] = {"flip_rate_pct": 100.0 * flipped.mean(), "n_flipped": int(flipped.sum()),
                   "n_total": len(sub), "flip_matrix": flip_matrix}

        abs_scores = sub[f"{cand}_score"].abs()
        day_sigma = sub.groupby("as_of")[f"{cand}_score"].apply(lambda x: np.std(x, ddof=0))
        bias_pct = (sub[f"{cand}_bias"].value_counts(normalize=True) * 100).reindex(_bucket_order()).fillna(0)
        m3[cand] = {
            "mean_sigma": day_sigma.mean(),
            "p55": abs_scores.quantile(0.55), "p90": abs_scores.quantile(0.90),
            "p95": abs_scores.quantile(0.95), "max": abs_scores.max(),
            "bias_pct": bias_pct,
        }

    return {"df": df, "m1": m1, "m2": m2, "m3": m3}


# ---------------------------------------------------------------------------
# M-4
# ---------------------------------------------------------------------------

def run_m4(win) -> dict:
    """Effective sentiment weight per currency + effective trend weight per
    pair, under D2-c — variance across currencies/pairs should be exactly 0
    by construction (not a D1 discriminator; validates D2-c's own wiring)."""
    snapshots = win["snapshots"]
    sent_rows, trend_rows = [], []
    for s in snapshots:
        for ccy in CCYS:
            card = s["currencies"].get(ccy, {})
            w = effective_sentiment_weight(card, s["fx_cot_cells"], ccy)
            if w is not None:
                sent_rows.append({"as_of": s["as_of"], "ccy": ccy, "eff_weight": w})
        for sym in FX_PAIRS:
            w = effective_trend_weight(sym, s)
            if w is not None:
                trend_rows.append({"as_of": s["as_of"], "symbol": sym, "eff_weight": w})

    sent_df = pd.DataFrame(sent_rows)
    trend_df = pd.DataFrame(trend_rows)
    sent_var_by_ccy = sent_df.groupby("ccy")["eff_weight"].mean()
    trend_var_by_pair = trend_df.groupby("symbol")["eff_weight"].mean()

    return {
        "sentiment_variance_across_currencies": float(sent_var_by_ccy.var(ddof=0)),
        "sentiment_unique_values": sorted(sent_df["eff_weight"].unique()),
        "trend_variance_across_pairs": float(trend_var_by_pair.var(ddof=0)),
        "trend_unique_values": sorted(trend_df["eff_weight"].unique()),
        "w_s": W_S, "macro_weight_target": MACRO_WEIGHT_TARGET, "trend_weight": TREND_WEIGHT,
    }


# ---------------------------------------------------------------------------
# Price / forward returns
# ---------------------------------------------------------------------------

def load_price_series() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    px = pd.read_parquet(PRICE_PATH)
    px["date"] = pd.to_datetime(px["date"])
    out = {}
    for sym, g in px.groupby("symbol"):
        g = g.drop_duplicates("date", keep="last").sort_values("date")
        out[sym] = (g["date"].to_numpy(), g["close"].to_numpy(dtype=float))
    return out


def forward_return(dates: np.ndarray, closes: np.ndarray, as_of: pd.Timestamp, h: int):
    idx = np.searchsorted(dates, np.datetime64(as_of), side="right") - 1
    if idx < 0 or idx + h >= len(dates):
        return None
    base, fwd = closes[idx], closes[idx + h]
    if base == 0 or not np.isfinite(base) or not np.isfinite(fwd):
        return None
    return fwd / base - 1.0


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _bias_sign(bias: str):
    if bias is None or bias == "Neutral":
        return None
    return 1 if "Bullish" in bias else -1


AFFECTED = {s for s in FX_PAIRS if "NZD" in s or "CHF" in s}
CLASSES = {"affected_NZD_CHF": AFFECTED, "unaffected": set(FX_PAIRS) - AFFECTED, "ALL": set(FX_PAIRS)}


def _hit_rate_table(cand_df: pd.DataFrame, bias_col: str, fwd_col: str) -> pd.DataFrame:
    """corrected hit-rate per (class, horizon): raw hit rate over directional
    calls minus the naive base-rate (majority-direction share) of the SAME
    evaluated sample."""
    rows = []
    for h in HORIZONS:
        sub_h = cand_df[cand_df["horizon"] == h]
        for cls, syms in CLASSES.items():
            sub = sub_h[sub_h["symbol"].isin(syms)]
            called = sub[sub[bias_col].notna()]
            n_called = len(called)
            if n_called == 0:
                rows.append({"class": cls, "horizon": h, "n": 0, "raw_hit_rate": None,
                           "naive_baseline": None, "corrected_hit_rate": None})
                continue
            pred_sign = called[bias_col]
            actual_sign = called[fwd_col].apply(_sign)
            hit = (pred_sign == actual_sign).mean()
            pos_share = (actual_sign > 0).mean()
            neg_share = (actual_sign < 0).mean()
            baseline = max(pos_share, neg_share)
            rows.append({"class": cls, "horizon": h, "n": n_called, "raw_hit_rate": hit,
                       "naive_baseline": baseline, "corrected_hit_rate": hit - baseline})
    return pd.DataFrame(rows)


def run_m6_m7(win) -> dict:
    snapshots = win["snapshots"]
    price = load_price_series()

    base_rows = []
    for s in snapshots:
        cur_by_sym = {i["symbol"]: i for i in s["instruments"]}
        for sym in FX_PAIRS:
            if sym not in price:
                continue
            dates, closes = price[sym]
            cur = cur_by_sym.get(sym)
            cand_biases = {cand: score_pair(sym, s, cand)["bias"] for cand in CANDIDATES}
            for h in HORIZONS:
                fwd = forward_return(dates, closes, s["as_of"], h)
                row = {"as_of": s["as_of"], "symbol": sym, "horizon": h, "forward_return": fwd,
                      "current_bias": cur["bias"] if cur else None}
                for cand in CANDIDATES:
                    row[f"{cand}_bias"] = cand_biases[cand]
                base_rows.append(row)
    df = pd.DataFrame(base_rows)
    df = df[df["forward_return"].notna()].copy()
    df["current_sign"] = df["current_bias"].apply(_bias_sign)
    for cand in CANDIDATES:
        df[f"{cand}_sign"] = df[f"{cand}_bias"].apply(_bias_sign)
    df.to_csv(DOCS_DIR / "diag-M6-forward-returns-long.csv", index=False)

    m6 = {"current": _hit_rate_table(df, "current_sign", "forward_return")}
    for cand in CANDIDATES:
        m6[cand] = _hit_rate_table(df, f"{cand}_sign", "forward_return")

    # M-7 placebo: permute the (as_of -> forward_return) mapping WITHIN each
    # (symbol, horizon) group, breaking temporal alignment; rerun the same battery.
    rng = np.random.default_rng(RNG_SEED)
    placebo = df.copy()
    for (sym, h), idx in placebo.groupby(["symbol", "horizon"]).groups.items():
        idx = list(idx)
        perm = rng.permutation(idx)
        placebo.loc[idx, "forward_return"] = df.loc[perm, "forward_return"].to_numpy()

    m7 = {"current": _hit_rate_table(placebo, "current_sign", "forward_return")}
    for cand in CANDIDATES:
        m7[cand] = _hit_rate_table(placebo, f"{cand}_sign", "forward_return")

    return {"df": df, "m6": m6, "m7": m7, "seed": RNG_SEED}


def run() -> dict:
    win = load_window()
    r123 = run_m1_m2_m3(win)
    r4 = run_m4(win)
    r67 = run_m6_m7(win)
    return {**r123, "m4": r4, "m6": r67["m6"], "m7": r67["m7"], "seed": r67["seed"]}


if __name__ == "__main__":
    res = run()
    print("=== M-1 (coverage) ===")
    for cand in CANDIDATES:
        print(f"  {DISPLAY[cand]:5} {res['m1'][cand]['pct_scorable']:.1f}% scorable "
             f"({res['m1'][cand]['n_scorable_mean_per_day']:.2f}/28 pairs/day avg)")

    print("\n=== M-2 (flip rate vs current production) ===")
    for cand in CANDIDATES:
        m = res["m2"][cand]
        print(f"  {DISPLAY[cand]:5} flip rate {m['flip_rate_pct']:.1f}% ({m['n_flipped']}/{m['n_total']})")

    print("\n=== M-3 (sigma, |score| percentiles, bias split) ===")
    for cand in CANDIDATES:
        m = res["m3"][cand]
        print(f"  {DISPLAY[cand]:5} sigma={m['mean_sigma']:.3f} p55={m['p55']:.2f} p90={m['p90']:.2f} "
             f"p95={m['p95']:.2f} max={m['max']:.2f}")
        print("        bias:", m["bias_pct"].round(1).to_dict())

    print("\n=== M-4 (effective pillar weight variance across currencies/pairs) ===")
    m4 = res["m4"]
    print(f"  sentiment: variance={m4['sentiment_variance_across_currencies']:.3e}, "
         f"unique values={m4['sentiment_unique_values']}")
    print(f"  trend:     variance={m4['trend_variance_across_pairs']:.3e}, "
         f"unique values={m4['trend_unique_values']}")

    print(f"\n=== M-6 (hit-rate, base-rate corrected; H=5,10; seed={res['seed']}) ===")
    print("current production:")
    print(res["m6"]["current"].to_string(index=False))
    for cand in CANDIDATES:
        print(f"\n{DISPLAY[cand]}:")
        print(res["m6"][cand].to_string(index=False))

    print("\n=== M-7 (placebo — permuted forward returns) ===")
    print("current production (placebo):")
    print(res["m7"]["current"].to_string(index=False))
    for cand in CANDIDATES:
        print(f"\n{DISPLAY[cand]} (placebo):")
        print(res["m7"][cand].to_string(index=False))
