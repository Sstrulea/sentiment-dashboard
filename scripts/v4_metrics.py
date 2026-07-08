"""V4 metrics — reads data/fundamental_v4_backfill.csv (the replay) + the live
calendar, prints every pre-registered table (C-A..C-D, selectivity, flips, zero-rate,
|z|-dist under mad, today grid). READ-ONLY. Run: python -m scripts.v4_metrics"""
from __future__ import annotations
import copy
from pathlib import Path
import numpy as np, pandas as pd, yaml

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "fundamental_v4_backfill.csv"
HORIZONS = [5, 10]
VARIANTS = ["V4.0", "V4.1", "V4.2"]


def _hit(df, H, sel_thresh=None):
    d = df[(df["bias"] != "Neutral") & df[f"r{H}"].notna() & (df["score"].abs() > 0)].copy()
    if sel_thresh is not None:
        d = d[d["score"].abs() >= sel_thresh]
    d["yr"] = pd.to_datetime(d["as_of"]).dt.year
    d["hit"] = (np.sign(d["score"]) == np.sign(d[f"r{H}"])).astype(float)
    return d


def cA(df):
    print("\n" + "=" * 78 + "\n[C-A] DIRECTIONAL HIT-RATE (non-Neutral, |score|>0) — per variant×H×class×period")
    for H in HORIZONS:
        d = _hit(df, H)
        print(f"\n  H={H} business days")
        print(f"  {'class':12}{'period':>8}" + "".join(f"{v:>9}" for v in VARIANTS) + f"{'Δ2-0':>8}{'n(V0)':>8}")
        for cls in ["fx", "cross-asset"]:
            for per in ["all", 2024, 2025, 2026]:
                sub = d[d["cls"] == cls] if per == "all" else d[(d["cls"] == cls) & (d["yr"] == per)]
                if len(sub[sub.variant == "V4.0"]) < 20:
                    continue
                hr = {v: 100 * sub[sub.variant == v]["hit"].mean() for v in VARIANTS}
                n0 = len(sub[sub.variant == "V4.0"])
                d20 = hr["V4.2"] - hr["V4.0"]
                print(f"  {cls:12}{str(per):>8}" + "".join(f"{hr[v]:>9.1f}" for v in VARIANTS) + f"{d20:>+8.1f}{n0:>8}")


def selectivity(df):
    print("\n" + "=" * 78 + "\n[SELECTIVITY GUARD] % non-Neutral per variant×class (|score|>0 & bias≠Neutral)")
    print(f"  {'class':12}" + "".join(f"{v:>9}" for v in VARIANTS))
    for cls in ["fx", "cross-asset"]:
        c = df[df["cls"] == cls]
        pct = {v: 100 * ((c[c.variant == v]["bias"] != "Neutral") & (c[c.variant == v]["score"].abs() > 0)).mean()
               for v in VARIANTS}
        flag = "  <<>10pp" if any(abs(pct[v] - pct["V4.0"]) > 10 for v in VARIANTS) else ""
        print(f"  {cls:12}" + "".join(f"{pct[v]:>9.1f}" for v in VARIANTS) + flag)
    # equalized-selectivity hit-rate: threshold on the V4.0 |score| quantile matching
    # V4.0 non-Neutral share, applied to all variants.
    print("\n  equalized-selectivity hit-rate (threshold = V4.0 |score| p-cut, H=5):")
    for cls in ["fx", "cross-asset"]:
        v0 = df[(df.cls == cls) & (df.variant == "V4.0")]
        share = ((v0["bias"] != "Neutral") & (v0["score"].abs() > 0)).mean()
        thr = df[(df.cls == cls)]["score"].abs().quantile(1 - share)
        hr = {}
        for v in VARIANTS:
            d = _hit(df[df.cls == cls], 5, sel_thresh=thr)
            dv = d[d.variant == v]
            hr[v] = 100 * dv["hit"].mean() if len(dv) >= 20 else float("nan")
        print(f"    {cls:12} thr|score|≥{thr:.2f}: " + "  ".join(f"{v}={hr[v]:.1f}" for v in VARIANTS))


def cB_flips(df):
    print("\n" + "=" * 78 + "\n[C-B] BIAS FLIPS per instrument (consecutive as-ofs) — median/yr, vs V4.0")
    res = {}
    for v in VARIANTS:
        rates = []
        for sym, g in df[df.variant == v].sort_values("as_of").groupby("symbol"):
            b = g["bias"].values
            if len(b) > 1:
                rates.append(252.0 * (b[1:] != b[:-1]).sum() / (len(b) - 1))
        res[v] = np.median(rates) if rates else float("nan")
    base = res["V4.0"]
    for v in VARIANTS:
        d = 100 * (res[v] - base) / base if base else 0
        print(f"  {v}: median {res[v]:.0f} flips/yr   ({d:+.0f}% vs V4.0)  {'FAIL >+20%' if d > 20 else 'ok'}")


def zero_rate(df):
    print("\n" + "=" * 78 + "\n[ZERO-RATE] % Neutral bias per variant×class (reported, not a target)")
    print(f"  {'class':12}" + "".join(f"{v:>9}" for v in VARIANTS))
    for cls in ["fx", "cross-asset"]:
        c = df[df["cls"] == cls]
        z = {v: 100 * (c[c.variant == v]["bias"] == "Neutral").mean() for v in VARIANTS}
        print(f"  {cls:12}" + "".join(f"{z[v]:>9.1f}" for v in VARIANTS))


def calendar_metrics():
    import src.economic_render as ER
    from src.economic_compute import compute_indicator_score
    cal = ER._load_calendar_frame(); cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    cfg = yaml.safe_load(open(ROOT / "data" / "economic_indicators.yaml"))
    inds, dfl = cfg["indicators"], cfg["defaults"]
    # C-C: |z| under mad (descriptive; forbidden to re-derive thresholds)
    K = 12
    zs = {"std": [], "mad": []}
    for (ccy, key), g in cal.groupby(["currency", "indicator_key"]):
        g = g.sort_values("release_dt"); a = pd.to_numeric(g.actual, "coerce"); c = pd.to_numeric(g.consensus, "coerce")
        p = pd.DataFrame({"d": a - c}).dropna().reset_index(drop=True)
        floor = inds.get(key, {}).get("sigma_floor", 0.0)
        for t in range(len(p)):
            tr = p["d"].iloc[max(0, t - K + 1):t + 1]
            if len(tr) < 6:
                continue
            std = tr.std(ddof=1); med = tr.median(); mad = max(1.4826 * (tr - med).abs().median(), floor)
            if std > 0:
                zs["std"].append(abs(p["d"].iloc[t] / std))
            if mad > 0:
                zs["mad"].append(abs(p["d"].iloc[t] / mad))
    print("\n" + "=" * 78 + "\n[C-C] |z| DISTRIBUTION std vs mad (DESCRIPTIVE — thresholds NOT re-derived)")
    print(f"  {'pctile':8}{'std':>9}{'mad':>9}")
    for q in [50, 60, 75, 85, 87.5, 90, 95]:
        print(f"  p{q:<7}{np.percentile(zs['std'], q):>9.3f}{np.percentile(zs['mad'], q):>9.3f}")
    print(f"  n: std={len(zs['std'])} mad={len(zs['mad'])}")
    # C-D coverage: non-fallback indicators today, V4.0 vs V4.1
    AS = pd.Timestamp("2026-07-08")
    cov = {"std": 0, "mad": 0, "tot": 0}
    for (ccy, key), g in cal.groupby(["currency", "indicator_key"]):
        if key not in inds:
            continue
        cov["tot"] += 1
        for m in ["std", "mad"]:
            d = dict(dfl); d["sigma_method"] = m
            r = compute_indicator_score(g, inds[key], d, AS, allow_stale=True, currency=ccy)
            if r and r.get("flag") is None and not r.get("stale"):
                cov[m] += 1
    print("\n" + "=" * 78 + f"\n[C-D] COVERAGE (non-fallback, non-stale) today: V4.0(std)={cov['std']} "
          f"V4.1(mad)={cov['mad']} / {cov['tot']} series")


def main():
    df = pd.read_csv(CSV)
    print(f"replay: {len(df)} rows, {df['as_of'].nunique()} as-ofs "
          f"{df['as_of'].min()}→{df['as_of'].max()}, {df['symbol'].nunique()} symbols")
    e = df[(df.symbol == "EURUSD") & (df.variant == "V4.0") & df.r5.notna() & (df.score.abs() > 0)]
    print(f"SIGN SENTINEL EURUSD: corr(sign score, sign r5)={np.sign(e.score).corr(np.sign(e.r5)):+.2f} "
          f"(convention: score>0 = base strength = pair up)")
    cA(df); selectivity(df); cB_flips(df); zero_rate(df); calendar_metrics()


if __name__ == "__main__":
    main()
