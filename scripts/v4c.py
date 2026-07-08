"""V4C — (A) confirmation battery for the provisional cross-asset long-horizon edge,
(B) head-to-head V4.0 (prod) vs V4.3 (sign). READ-ONLY; nothing adopted.
Reads two full step=1 fundamental-only replays (with r15/r21/r30) from SCRATCH.
Run: SCRATCH=<dir> python -m scripts.v4c"""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get("SCRATCH", ROOT / "data" / ".v4c_cache"))
CSV = ROOT / "data" / "v4c_headtohead.csv"
HZ = [15, 21, 30]
BUCKETS = ["Very Bearish", "Bearish", "Bullish", "Very Bullish"]
BULL, BEAR = {"Bullish", "Very Bullish"}, {"Very Bearish", "Bearish"}
PROFILE_REP = ["SP500", "DAX", "NIKKEI", "FTSE100", "GOLD"]


def base_bull(df, H):
    d = df[df[f"r{H}"].notna()]
    return {s: (g[f"r{H}"] > 0).mean() for s, g in d.groupby("symbol")}


def nn_of(df):
    return df[df["bias"].isin(BUCKETS)].copy()


def agg_excess(sub, bb, H):
    sub = sub[sub[f"r{H}"].notna()]
    if len(sub) < 20:
        return np.nan, len(sub)
    up = sub["bias"].isin(BULL)
    hit = np.where(up, sub[f"r{H}"] > 0, sub[f"r{H}"] < 0)
    bench = np.where(up, sub["symbol"].map(lambda s: bb[H].get(s, .5)),
                     sub["symbol"].map(lambda s: 1 - bb[H].get(s, .5)))
    return 100 * (hit.mean() - bench.mean()), len(sub)


def spread_S(sub, H):
    sub = sub[sub[f"r{H}"].notna()]
    bull = sub[sub["bias"].isin(BULL)][f"r{H}"]
    bear = sub[sub["bias"].isin(BEAR)][f"r{H}"]
    if len(bull) < 5 or len(bear) < 5:
        return np.nan
    return 1e4 * (bull.mean() - bear.mean())


def disjoint(df, H):
    aos = np.sort(df["as_of"].unique())
    return df[df["as_of"].isin(set(aos[::H]))]


# ============================ (A) CONFIRMATION BATTERY ============================

def battery(v40):
    nn = nn_of(v40)
    ca = nn[nn["cls"] == "cross-asset"]
    bb = {H: base_bull(v40, H) for H in HZ}
    print("\n" + "#" * 84 + "\n# (A) CONFIRMATION BATTERY — V4.0 cross-asset long-horizon edge\n" + "#" * 84)

    # A1 disjoint windows (step=H) → excess + S
    print("\n[A1] DISJOINT WINDOWS (as-of step = H, independent obs)")
    print(f"  {'H':>4}{'n':>7}{'S_full(bps)':>13}{'S_disjoint':>12}{'excess_full':>13}{'excess_disj':>13}")
    a1 = {}
    for H in [15, 30]:
        dj = disjoint(v40, H); dj_ca = nn_of(dj)[nn_of(dj)["cls"] == "cross-asset"]
        bbd = {H: base_bull(dj, H)}
        Sf, Sd = spread_S(ca, H), spread_S(dj_ca, H)
        ef, _ = agg_excess(ca, bb, H); ed, nd = agg_excess(dj_ca, bbd, H)
        a1[H] = (Sd, ed)
        print(f"  {H:>4}{nd:>7}{Sf:>13.0f}{Sd:>12.0f}{ef:>13.1f}{ed:>13.1f}")

    # A2 dedup profiles
    print("\n[A2] DEDUP PROFILES (only PROFILE_REP: SP500/DAX/NIKKEI/FTSE100/GOLD)")
    rep = ca[ca["symbol"].isin(PROFILE_REP)]
    print(f"  {'H':>4}{'n':>7}{'S_all(bps)':>12}{'S_rep':>10}  monotone_rep(VBear<Bear<Bull<VBull)")
    a2 = {}
    for H in [15, 30]:
        means = {b: rep[(rep.bias == b) & rep[f"r{H}"].notna()][f"r{H}"].mean() for b in BUCKETS}
        mono = (means["Very Bearish"] < means["Bearish"] < means["Bullish"] < means["Very Bullish"])
        Sr = spread_S(rep, H); a2[H] = Sr
        print(f"  {H:>4}{len(rep[rep[f'r{H}'].notna()]):>7}{spread_S(ca,H):>12.0f}{Sr:>10.0f}  {mono}")

    # A3 leave-top-out episodes
    print("\n[A3] LEAVE-TOP-OUT (episodes = consecutive same instrument+bias-group; drop top-3 by contrib)")
    print(f"  {'H':>4}{'n_epi':>8}{'S_full(bps)':>13}{'S_loo':>10}")
    a3 = {}
    for H in [15, 30]:
        sub = ca[ca[f"r{H}"].notna()].sort_values(["symbol", "as_of"]).copy()
        sub["grp"] = np.where(sub["bias"].isin(BULL), 1, -1)
        chg = (sub["symbol"] != sub["symbol"].shift()) | (sub["grp"] != sub["grp"].shift())
        sub["epi"] = chg.cumsum()
        contrib = sub.groupby("epi").apply(lambda g: g["grp"].iloc[0] * g[f"r{H}"].sum())
        top3 = contrib.sort_values(ascending=False).head(3).index
        loo = sub[~sub["epi"].isin(top3)]
        Sloo = spread_S(loo, H); a3[H] = Sloo
        print(f"  {H:>4}{sub['epi'].nunique():>8}{spread_S(ca,H):>13.0f}{Sloo:>10.0f}")

    # A4 placebo ×2
    print("\n[A4] PLACEBO (should give S≈0; |S_placebo| < 25% of S_real to pass)")
    rng = np.random.default_rng(42)
    print(f"  {'H':>4}{'S_real':>9}{'S_permute':>11}{'S_lag60':>10}{'pass?':>8}")
    a4 = {}
    for H in [15, 30]:
        # time-permute bias within each symbol (returns fixed at their as_of)
        perm = ca.copy()
        perm["bias"] = perm.groupby("symbol")["bias"].transform(lambda s: rng.permutation(s.values))
        # lag score/bias by 60 as-of positions within symbol
        lag = ca.sort_values(["symbol", "as_of"]).copy()
        lag["bias"] = lag.groupby("symbol")["bias"].shift(60)
        Sr = spread_S(ca, H); Sp = spread_S(nn_of(perm), H); Sl = spread_S(nn_of(lag.dropna(subset=["bias"])), H)
        ok = abs(Sp) < 0.25 * abs(Sr) and abs(Sl) < 0.25 * abs(Sr)
        a4[H] = (Sp, Sl, ok)
        print(f"  {H:>4}{Sr:>9.0f}{Sp:>11.0f}{Sl:>10.0f}{('OK' if ok else 'FLAG'):>8}")

    # A5 FX-2024 diagnostic
    print("\n[A5] FX-2024 DIAGNOSTIC — S per year, carry (JPY/CHF leg) vs non-carry")
    fx = nn[nn["cls"] == "fx"].copy()
    fx["carry"] = fx["symbol"].str.contains("JPY|CHF")
    fx["yr"] = fx["as_of"].dt.year
    for H in [15, 30]:
        print(f"  H={H}: " + " | ".join(
            f"{g}: " + ",".join(f"{y}={spread_S(fx[(fx.carry==c)&(fx.yr==y)],H):+.0f}" for y in [2024,2025,2026])
            for g, c in [("carry", True), ("non-carry", False)]))

    # verdict A
    print("\n[A-VERDICT] CONFIRM iff: S_disjoint>0 @15&30, S_loo>0, both placebo pass, monotone on dedup")
    i = a1[15][0] > 0 and a1[30][0] > 0
    ii = a3[15] > 0 and a3[30] > 0
    iii = a4[15][2] and a4[30][2]
    iv = spread_S(rep, 15) > 0 and spread_S(rep, 30) > 0
    conf = i and ii and iii and iv
    print(f"  disjoint S>0 @15&30={i} | S_loo>0={ii} | placebo pass={iii} | dedup S>0={iv} "
          f"→ {'CONFIRMED' if conf else 'stays PROVISIONAL / demoted'}")
    return conf


# ============================ (B) HEAD-TO-HEAD ============================

def headtohead(v40, v43):
    print("\n" + "#" * 84 + "\n# (B) HEAD-TO-HEAD  V4.0 (prod) vs V4.3 (sign) — fundamental-only, non-Neutral\n" + "#" * 84)
    n40, n43 = nn_of(v40), nn_of(v43)
    bb = {H: base_bull(v40, H) for H in HZ}   # returns identical across variants

    # selectivity
    print("\n[SELECTIVITY] % non-Neutral per variant × class (V4.3 expected much more directional)")
    print(f"  {'class':12}{'V4.0':>8}{'V4.3':>8}{'Δpp':>8}")
    sel = {}
    for cls in ["fx", "cross-asset"]:
        p0 = 100 * (v40["cls"] == cls).pipe(lambda m: (v40[m]["bias"].isin(BUCKETS)).mean())
        p3 = 100 * (v43["cls"] == cls).pipe(lambda m: (v43[m]["bias"].isin(BUCKETS)).mean())
        sel[cls] = (p0, p3)
        print(f"  {cls:12}{p0:>8.1f}{p3:>8.1f}{p3-p0:>+8.1f}{'  >10pp → equalize' if abs(p3-p0)>10 else ''}")

    print("\n[B-FULL] all as-ofs (step 1; windows overlap)")
    print(f"  {'class':12}{'H':>4}{'exc_V40':>9}{'exc_V43':>9}{'S_V40':>8}{'S_V43':>8}  winnerΔ")
    full = {}
    for cls in ["fx", "cross-asset"]:
        for H in HZ:
            s40 = n40[n40.cls == cls]; s43 = n43[n43.cls == cls]
            e0, _ = agg_excess(s40, bb, H); e3, _ = agg_excess(s43, bb, H)
            S0, S3 = spread_S(s40, H), spread_S(s43, H)
            full[(cls, H)] = (e0, e3, S0, S3)
            print(f"  {cls:12}{H:>4}{e0:>9.1f}{e3:>9.1f}{S0:>8.0f}{S3:>8.0f}   exΔ={e3-e0:+.1f} SΔ={S3-S0:+.0f}")

    # disjoint control
    print("\n[B-DISJOINT] as-of step = H (independent windows)")
    print(f"  {'class':12}{'H':>4}{'exc_V40':>9}{'exc_V43':>9}{'S_V40':>8}{'S_V43':>8}")
    disj = {}
    for H in HZ:
        dj0, dj3 = disjoint(v40, H), disjoint(v43, H)
        n0d, n3d = nn_of(dj0), nn_of(dj3)
        bbd = base_bull(dj0, H)
        for cls in ["fx", "cross-asset"]:
            e0, _ = agg_excess(n0d[n0d.cls == cls], {H: bbd}, H)
            e3, _ = agg_excess(n3d[n3d.cls == cls], {H: bbd}, H)
            S0, S3 = spread_S(n0d[n0d.cls == cls], H), spread_S(n3d[n3d.cls == cls], H)
            disj[(cls, H)] = (e0, e3, S0, S3)
            print(f"  {cls:12}{H:>4}{e0:>9.1f}{e3:>9.1f}{S0:>8.0f}{S3:>8.0f}")

    # equalized selectivity where Δ>10pp
    print("\n[B-EQUALIZED SELECTIVITY] threshold V4.3 |score| so non-Neutral rate = V4.0's")
    for cls in ["fx", "cross-asset"]:
        p0, p3 = sel[cls]
        if abs(p3 - p0) <= 10:
            print(f"  {cls}: Δ={p3-p0:+.1f}pp ≤10 — no equalization needed")
            continue
        frac = p0 / 100.0
        allc = v43[v43.cls == cls]
        thr = allc["score"].abs().quantile(1 - frac)
        eqc = nn_of(allc[allc["score"].abs() >= thr])
        for H in [15, 30]:
            e3, n3 = agg_excess(eqc, {H: base_bull(v43, H)}, H)
            print(f"  {cls} H={H}: V4.3@equal-sel(|score|≥{thr:.2f}) excess={e3:+.1f} (n={n3}) "
                  f"vs V4.0 excess={full[(cls,H)][0]:+.1f}")

    # M3 sub-period consistency of S, both variants
    print("\n[B-M3] spread S sign by sub-period (2024/2025/2026), both variants")
    for name, d in [("V4.0", n40), ("V4.3", n43)]:
        d = d.copy(); d["yr"] = d["as_of"].dt.year
        for cls in ["fx", "cross-asset"]:
            cells = []
            for H in [15, 30]:
                yrs = [spread_S(d[(d.cls == cls) & (d.yr == y)], H) for y in [2024, 2025, 2026]]
                cells.append(f"H{H}[" + ",".join("n/a" if np.isnan(v) else f"{v:+.0f}" for v in yrs) + "]")
            print(f"  {name} {cls:12}: " + "  ".join(cells))

    # verdict B (pre-registered)
    print("\n[B-VERDICT] V4.3 wins iff @H15 AND H30 (DISJOINT): "
          "exc≥V4.0+1.5 OR S≥V4.0+{15 fx/100 xa}, sign-consistent ≥2/3, no other-class deterioration")
    for cls, sbar in [("fx", 15), ("cross-asset", 100)]:
        wins = []
        for H in [15, 30]:
            e0, e3, S0, S3 = disj[(cls, H)]
            win = (e3 >= e0 + 1.5) or (S3 >= S0 + sbar)
            wins.append(win)
        verdict = "V4.3 wins" if all(wins) else "V4.0 stays"
        print(f"  {cls:12}: disjoint H15/H30 improve={wins} → {verdict}")

    # save CSV: non-Neutral obs both variants
    a = n40[["as_of","symbol","cls","score","bias","r15","r21","r30"]].assign(variant="V4.0")
    b = n43[["as_of","symbol","cls","score","bias","r15","r21","r30"]].assign(variant="V4.3")
    pd.concat([a, b]).to_csv(CSV, index=False)
    print(f"\nwrote {CSV} ({len(a)+len(b)} rows)")


def _load_or_gen():
    """Two full step=1 fundamental-only replays (prod + sign) with r15/r21/r30, cached."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    paths = {"prod": SCRATCH / "v4c_v40.parquet", "sign": SCRATCH / "v4c_v43.parquet"}
    if not all(p.exists() for p in paths.values()):
        from src.fundamental_v4_replay import run_replay_fundamental, attach_trading_day_returns
        for variant, p in paths.items():
            df, px = run_replay_fundamental(step=1, scoring_variant=variant)
            attach_trading_day_returns(df, px, HZ).to_parquet(p)
    return pd.read_parquet(paths["prod"]), pd.read_parquet(paths["sign"])


def main():
    v40, v43 = _load_or_gen()
    v40["as_of"] = pd.to_datetime(v40["as_of"]); v43["as_of"] = pd.to_datetime(v43["as_of"])
    print(f"V4.0: {len(v40)} rows; V4.3: {len(v43)} rows; as-ofs {v40.as_of.nunique()}")
    battery(v40)
    headtohead(v40, v43)


if __name__ == "__main__":
    main()
