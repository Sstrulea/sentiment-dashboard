"""V4 long-horizon extension (READ-ONLY). V4.0 fundamental-ONLY score, non-Neutral
readings only, forward returns at H ∈ {15,21,30} TRADING days, base-rate-corrected.
Answers: does the fundamental pillar have long-horizon directional edge?
Run: python -m scripts.v4_horizon   (writes data/fundamental_v4_h30.csv)"""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd

from src.fundamental_v4_replay import run_replay_fundamental, attach_trading_day_returns

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "fundamental_v4_h30.csv"
HZ = [15, 21, 30]
BUCKETS = ["Very Bearish", "Bearish", "Bullish", "Very Bullish"]
BULL, BEAR = {"Bullish", "Very Bullish"}, {"Very Bearish", "Bearish"}
CLASSES = ["fx", "cross-asset"]


def base_bull(df: pd.DataFrame, H: int) -> dict:
    """Per-symbol unconditional up-rate: %(r_H>0) over ALL as-ofs (incl. Neutral)."""
    d = df[df[f"r{H}"].notna()]
    return {s: (g[f"r{H}"] > 0).mean() for s, g in d.groupby("symbol")}


def main():
    df, px = run_replay_fundamental(step=1)
    df = attach_trading_day_returns(df, px, HZ)
    df["yr"] = df["as_of"].dt.year
    bb = {H: base_bull(df, H) for H in HZ}

    nn = df[df["bias"].isin(BUCKETS)].copy()          # non-Neutral filter
    # save deliverable: non-Neutral obs with r15/r21/r30
    nn[["as_of", "symbol", "cls", "score", "bias", "r15", "r21", "r30"]].to_csv(CSV, index=False)
    print(f"replay: {df['as_of'].nunique()} as-ofs, {len(nn)} non-Neutral obs "
          f"({df['as_of'].min().date()}→{df['as_of'].max().date()}); wrote {CSV.name}")

    # r_H availability (tail exclusion visible as shrinking n at larger H)
    print("\ntail-exclusion — non-Neutral obs WITH r_H available:")
    for H in HZ:
        print(f"  H={H}: {nn[f'r{H}'].notna().sum()} / {len(nn)}")

    # ---- M1: hit-rate vs per-instrument base rate ----
    print("\n" + "=" * 84 + "\n[M1] HIT-RATE vs BASE RATE (excess), per bucket × class × H")
    for H in HZ:
        print(f"\n  H={H} bd | benchmark = base_bull (bull buckets) / 1−base_bull (bear buckets)")
        print(f"  {'class':12}{'bucket':14}{'n':>6}{'hit%':>8}{'bench%':>8}{'excess':>8}")
        for cls in CLASSES:
            for b in BUCKETS:
                sub = nn[(nn.cls == cls) & (nn.bias == b) & nn[f"r{H}"].notna()]
                if len(sub) == 0:
                    continue
                up = b in BULL
                hit = ((sub[f"r{H}"] > 0) if up else (sub[f"r{H}"] < 0)).mean()
                bench = sub["symbol"].map(lambda s: bb[H].get(s, 0.5) if up else 1 - bb[H].get(s, 0.5)).mean()
                thin = " thin" if len(sub) < 100 else ""
                print(f"  {cls:12}{b:14}{len(sub):>6}{100*hit:>8.1f}{100*bench:>8.1f}{100*(hit-bench):>+8.1f}{thin}")

    # aggregate excess per class × H (criterion iii)
    print("\n  AGGREGATE excess hit-rate (all non-Neutral, per class × H):")
    agg_excess = {}
    for cls in CLASSES:
        line = []
        for H in HZ:
            sub = nn[(nn.cls == cls) & nn[f"r{H}"].notna()].copy()
            up = sub["bias"].isin(BULL)
            hit = np.where(up, sub[f"r{H}"] > 0, sub[f"r{H}"] < 0)
            bench = np.where(up, sub["symbol"].map(lambda s: bb[H].get(s, .5)),
                             sub["symbol"].map(lambda s: 1 - bb[H].get(s, .5)))
            ex = 100 * (hit.mean() - bench.mean())
            agg_excess[(cls, H)] = ex
            line.append(f"H{H}={ex:+.1f}")
        print(f"    {cls:12} " + "  ".join(line))

    # ---- M2: mean forward return + spread S + monotonicity ----
    print("\n" + "=" * 84 + "\n[M2] MEAN FORWARD RETURN (bps) per bucket × class × H, + spread S")
    S = {}
    for H in HZ:
        print(f"\n  H={H} bd    " + "".join(f"{b:>14}" for b in BUCKETS) + f"{'S(bull−bear)':>15}")
        for cls in CLASSES:
            means = {}
            for b in BUCKETS:
                sub = nn[(nn.cls == cls) & (nn.bias == b) & nn[f"r{H}"].notna()]
                means[b] = 1e4 * sub[f"r{H}"].mean() if len(sub) else np.nan
            bull = nn[(nn.cls == cls) & nn.bias.isin(BULL) & nn[f"r{H}"].notna()][f"r{H}"]
            bear = nn[(nn.cls == cls) & nn.bias.isin(BEAR) & nn[f"r{H}"].notna()][f"r{H}"]
            s = 1e4 * (bull.mean() - bear.mean())
            S[(cls, H)] = s
            mono = "✓" if (means["Very Bearish"] < means["Bearish"] < means["Bullish"] < means["Very Bullish"]) else "✗"
            print(f"  {cls:10}" + "".join(f"{means[b]:>14.0f}" for b in BUCKETS) + f"{s:>13.0f} {mono}")
    print("  (monotonicity ✓ = VBear < Bear < Bull < VBull on mean return)")

    # ---- M3: sign consistency of S across sub-periods ----
    print("\n" + "=" * 84 + "\n[M3] SPREAD S SIGN by sub-period (2024/2025/2026), per class × H")
    consistency = {}
    for H in HZ:
        print(f"\n  H={H} bd    {'class':12}{'2024':>10}{'2025':>10}{'2026':>10}   sign-consistent?")
        for cls in CLASSES:
            signs, cells = [], []
            for yr in [2024, 2025, 2026]:
                sub = nn[(nn.cls == cls) & (nn.yr == yr) & nn[f"r{H}"].notna()]
                bull = sub[sub.bias.isin(BULL)][f"r{H}"]; bear = sub[sub.bias.isin(BEAR)][f"r{H}"]
                if len(bull) < 10 or len(bear) < 10:
                    cells.append("  n/a"); signs.append(None); continue
                s = 1e4 * (bull.mean() - bear.mean()); cells.append(f"{s:+.0f}"); signs.append(np.sign(s))
            full = np.sign(S[(cls, H)])
            agree = sum(1 for x in signs if x is not None and x == full)
            valid = sum(1 for x in signs if x is not None)
            consistency[(cls, H)] = (agree, valid)
            print(f"           {cls:12}" + "".join(f"{c:>10}" for c in cells) +
                  f"   {agree}/{valid} match full-sample sign ({'+' if full > 0 else '−'})")

    # ---- verdict ----
    print("\n" + "=" * 84 + "\n[VERDICT] edge exists per class iff (i) S>0 @H15 AND H30, "
          "(ii) S sign consistent ≥2/3 sub-periods, (iii) aggregate excess>0")
    for cls in CLASSES:
        i = (S[(cls, 15)] > 0) and (S[(cls, 30)] > 0)
        ii = all(consistency[(cls, H)][0] >= 2 for H in [15, 30])
        iii = agg_excess[(cls, 15)] > 0 and agg_excess[(cls, 30)] > 0
        verdict = "EDGE" if (i and ii and iii) else "NULL confirmed"
        print(f"  {cls:12}: (i) S>0@15&30={i}  (ii) sign-consistent={ii}  (iii) excess>0={iii}  → {verdict}")


if __name__ == "__main__":
    main()
