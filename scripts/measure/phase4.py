"""Phase 4 measurement (attempt #4, last historical round) against scripts/measure/prereg_phase4.yml.

    python scripts/measure/phase4.py [--out-json data/tree_runs/phase4_metrics.json] [--out-md docs/eval/phase4.md]

Stage A (A1-A6, hard gates), B (MS / MS-lite), C (v4 and its parts), D (comparison gates).
Refuses to run while the pre-registration has uncommitted changes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "measure"))

import reaction_v0 as R  # noqa: E402
from phase3 import grid_stats, weekly_ic  # noqa: E402
from src.tree.data import Inputs  # noqa: E402
from src.tree.regimes import REGIME_INPUTS  # noqa: E402
from src.tree.v4 import CCYS, MIN_TRAIN, P_ABSTAIN, Phase4  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_phase4.yml"
COST = 6.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def fwd_panel(p4: Phase4, h: int) -> pd.DataFrame:
    return pd.DataFrame({c: R.forward(p4.basket[c], p4.fridays, h, 1e4) for c in CCYS})


def window(S: pd.DataFrame, start, end=None) -> pd.DataFrame:
    end = end or S.index[-1]
    return S[(S.index >= pd.Timestamp(start)) & (S.index <= pd.Timestamp(end))]


def first_full(S: pd.DataFrame, n: int = 5) -> pd.Timestamp:
    ok = S.notna().sum(axis=1) >= n
    return S.index[ok][0]


def ls(S: pd.DataFrame, F: pd.DataFrame, h: int, start) -> dict:
    prereg = {"harness": {"halves": {"first": ["2000-01-01", "2000-01-02"], "second": ["2000-01-03", "end"]}}}
    m = R.cs_metrics(S, F, h, pd.Timestamp(start), prereg)
    return {k: m[k] for k in ("ls_bp", "ls_sharpe", "ls_maxdd_bp", "ls_n", "pairs_hit")}


def test_signal(S: pd.DataFrame, fwd: dict, horizons: list[int], start) -> dict:
    """IC / t / halves / L/S per horizon in [start, end]; window halves at the middle date."""
    out = {}
    for h in horizons:
        F = fwd[h]
        Sw, Fw = window(S, start), window(F, start)
        ics = weekly_ic(Sw, Fw)
        g = grid_stats(ics, h, pd.Timestamp(start))
        mid = Sw.index[0] + (Sw.index[-1] - Sw.index[0]) / 2
        h1 = grid_stats(ics[ics.index <= mid], h, pd.Timestamp(start))
        h2 = grid_stats(ics[ics.index > mid], h, pd.Timestamp(start))
        out[h] = {**g, "IC_h1": h1["IC"], "IC_h2": h2["IC"], "mid": str(mid.date()), **ls(Sw, Fw, h, start)}
    return out


def hard_gate(res: dict, horizons: list[int], h: int, extra: dict | None = None) -> dict:
    m = res[h]
    i = horizons.index(h)
    nb = [horizons[j] for j in (i - 1, i + 1) if 0 <= j < len(horizons)]
    crit = {"IC>0 (pre-registered sign)": bool(np.isfinite(m["IC"]) and m["IC"] > 0),
            "t>=3": bool(np.isfinite(m["t"]) and m["t"] >= 3),
            "same_sign_halves": bool(np.isfinite(m["IC_h1"]) and np.isfinite(m["IC_h2"]) and
                                     np.sign(m["IC_h1"]) == np.sign(m["IC_h2"]) == np.sign(m["IC"])),
            "neighbours_same_sign": all(np.isfinite(res[n]["IC"]) and np.sign(res[n]["IC"]) == np.sign(m["IC"]) for n in nb),
            "net_LS>0": bool(np.isfinite(m["ls_bp"]) and m["ls_bp"] > 0)}
    crit.update(extra or {})
    return {"criteria": crit, "pass": all(crit.values())}


def event_stats(x: pd.DataFrame, split, col="net") -> dict:
    if len(x) < 3:
        return {"n": int(len(x)), "mean_net": float(x[col].mean()) if len(x) else None}
    m, t, n, G = R.clustered_mean_t(x[col].to_numpy(), x["date"].to_numpy())
    a, b = x[x["date"] <= split], x[x["date"] > split]
    return {"n": n, "clusters": G, "mean_gross": float(x["gross"].mean()), "mean_net": m, "t": t,
            "net_h1": float(a[col].mean()) if len(a) else None, "net_h2": float(b[col].mean()) if len(b) else None,
            "n_h1": int(len(a)), "n_h2": int(len(b))}


def event_gate(s: dict) -> dict:
    crit = {"mean_net>=15bp": bool(s.get("mean_net") is not None and s["mean_net"] >= 15),
            "t>=3": bool(s.get("t") is not None and np.isfinite(s["t"]) and s["t"] >= 3),
            "both_halves>0": bool((s.get("net_h1") or -1) > 0 and (s.get("net_h2") or -1) > 0)}
    return {"criteria": crit, "pass": all(crit.values())}


def shock_kind(d2y: float, eq: float) -> str | None:
    """policy shock: 2Y and equities move in opposite directions; information: same direction."""
    if not (np.isfinite(d2y) and np.isfinite(eq)) or d2y == 0 or eq == 0:
        return None
    return "policy" if np.sign(d2y) != np.sign(eq) else "information"


def fix_index(s: pd.Series, day: pd.Timestamp, before: bool) -> int:
    """Index of the last fixing strictly before `day` (before=True) or the first after it."""
    i = s.index.searchsorted(day, side="left")
    return i - 1 if before else s.index.searchsorted(day, side="right")


# ---------------------------------------------------------------------------
# stage A
# ---------------------------------------------------------------------------

def a2(p4: Phase4) -> dict:
    ev = p4.events
    d = ev[(ev["indicator_key"] == "interest_rate_decision") & ev["released"] & ev["consensus"].notna() &
           ev["previous"].notna()].copy()
    d["expected"] = np.sign((d["consensus"] - d["previous"]).round(6))
    rows = []
    for r in d.itertuples(index=False):
        s = p4.basket[r.ccy]
        day = r.release_dt.normalize()
        k1 = fix_index(s, day, before=True)
        k0 = k1 - 10
        if k0 < 0:
            continue
        mv = (s.iloc[k1] - s.iloc[k0]) * 1e4
        rows.append({"ccy": r.ccy, "date": day, "expected": r.expected, "move": mv})
    x = pd.DataFrame(rows)
    split = pd.Timestamp("2024-11-30")
    chg = x[x["expected"] != 0].copy()
    chg["gross"] = chg["move"] * chg["expected"]
    chg["net"] = chg["gross"] - COST
    hold = x[x["expected"] == 0].copy()
    hold["gross"] = hold["move"]
    hold["net"] = hold["move"]
    res = {"meetings": int(len(x)), "expected_change": event_stats(chg, split),
           "expected_change_by_ccy": chg.groupby("ccy").size().to_dict(),
           "no_change_unconditional": event_stats(hold, split)}
    # 2000 -> proxy: BIS changes as meetings
    rows = []
    for c in CCYS:
        pol = p4.bb.by[f"POL_{c}"].dropna()
        ch = pol.diff()
        ch = ch[ch.abs() > 1e-9]
        s = p4.basket[c]
        for i, (dt, v) in enumerate(ch.items()):
            prev = ch[(ch.index < dt) & (ch.index >= dt - pd.Timedelta(days=182))]
            if prev.empty:
                continue
            e = np.sign(prev.iloc[-1])
            k1 = fix_index(s, dt, before=True)
            if k1 - 10 < 0:
                continue
            g = (s.iloc[k1] - s.iloc[k1 - 10]) * 1e4 * e
            rows.append({"ccy": c, "date": dt, "gross": g, "net": g - COST})
    px = pd.DataFrame(rows)
    res["proxy_2000_bis_changes"] = event_stats(px, pd.Timestamp("2012-12-31"))
    res["gate"] = event_gate(res["expected_change"])
    return res


def a3(p4: Phase4) -> dict:
    fomc = pd.read_parquet(ROOT / "data" / "fomc_dates.parquet")
    pol = p4.bb.by["POL_USD"].dropna()
    ch = pol.diff()
    chd = ch[ch.abs() > 1e-9].index
    keep = []
    for r in fomc.itertuples(index=False):
        if r.kind == "Meeting" or any(abs((d - r.date).days) <= 2 for d in chd):
            keep.append(r.date)
    ri = Inputs.load(REGIME_INPUTS)
    nas, spx = ri.full("NASDAQCOM"), ri.full("SP500")
    y2 = p4.bb.by["Y2_USD"].dropna()
    s = p4.basket["USD"]

    def day_move(series, d):
        if d not in series.index:
            return np.nan
        i = series.index.get_loc(d)
        return float(series.iloc[i] - series.iloc[i - 1]) if i > 0 else np.nan

    rows = []
    for d in keep:
        if d > s.index[-1] - pd.Timedelta(days=16):
            continue
        dy = day_move(y2, d) * 100
        eq_s = nas if d <= pd.Timestamp("2016-12-31") else spx
        eq = day_move(np.log(eq_s), d) * 100
        kind = shock_kind(dy, eq)
        if kind is None:
            continue
        k = fix_index(s, d, before=False)            # first fixing after the decision day = day +1
        if k + 9 >= len(s):
            continue
        g = (s.iloc[k + 9] - s.iloc[k]) * 1e4 * np.sign(dy)
        rows.append({"date": d, "d2y": dy, "eq": eq, "kind": kind,
                     "gross": g, "net": g - COST})
    x = pd.DataFrame(rows)
    split = pd.Timestamp("2012-12-31")
    res = {"fomc_days": len(keep), "classified": int(len(x)), "by_kind": x["kind"].value_counts().to_dict(),
           "policy": event_stats(x[x["kind"] == "policy"], split),
           "information": event_stats(x[x["kind"] == "information"], split)}
    res["gate"] = event_gate(res["policy"])
    # other banks, FF decisions 2023 -> (descriptive)
    ev = p4.events
    d = ev[(ev["indicator_key"] == "interest_rate_decision") & ev["released"] & ev["d2y"].notna() &
           (ev["ccy"] != "USD") & ev["y2_date"].notna()]
    rows = []
    for r in d.itertuples(index=False):
        eq = day_move(np.log(spx), r.y2_date) * 100 if r.y2_date in spx.index else np.nan
        if not (np.isfinite(eq) and r.d2y != 0 and eq != 0):
            continue
        sb = p4.basket[r.ccy]
        k = fix_index(sb, r.release_dt.normalize(), before=False)
        if k + 9 >= len(sb):
            continue
        g = (sb.iloc[k + 9] - sb.iloc[k]) * 1e4 * np.sign(r.d2y)
        rows.append({"date": r.release_dt.normalize(), "ccy": r.ccy,
                     "kind": "policy" if np.sign(r.d2y) != np.sign(eq) else "information", "gross": g, "net": g - COST})
    o = pd.DataFrame(rows)
    res["other_banks_2023"] = {k: event_stats(o[o["kind"] == k], pd.Timestamp("2024-11-30")) for k in ("policy", "information")} \
        if len(o) else {}
    return res


def a6(p4: Phase4, F1: pd.DataFrame) -> dict:
    rk = p4.carry().rank(axis=1)
    pct = p4.crowding()["pct"]
    out = {}
    for name, filt in (("plain", False), ("filtered", True)):
        prev = pd.Series(0.0, index=CCYS)
        pnl = []
        for t in rk.index:
            r = rk.loc[t].dropna()
            if len(r) < 5 or t not in F1.index or F1.loc[t].isna().all():
                pnl.append(np.nan)
                continue
            w = pd.Series(0.0, index=CCYS)
            o = r.sort_values()
            w[o.index[-2:]] = 0.5
            w[o.index[:2]] = -0.5
            if filt and pd.notna(pct.get(t)) and pct[t] > 70:
                w = w * (1 - pct[t] / 100.0)
            g = float((w * F1.loc[t].fillna(0.0)).sum())
            pnl.append(g - 3.0 * float((w - prev).abs().sum()))
            prev = w
        out[name] = pd.Series(pnl, index=rk.index)

    def stats(x):
        x = x.dropna()
        cum = x.cumsum()
        return {"sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(52)) if len(x) > 2 else np.nan,
                "maxdd_bp": float((cum.cummax() - cum).max()), "mean_bp": float(x.mean()), "n": int(len(x))}
    halves = {"2000-2012": ("2000-01-01", "2012-12-31"), "2013-2026": ("2013-01-01", "2026-12-31")}
    res = {}
    for hn, (a, b) in halves.items():
        res[hn] = {k: stats(v[(v.index >= a) & (v.index <= b)]) for k, v in out.items()}
    res["all"] = {k: stats(v) for k, v in out.items()}
    res["weeks_filtered"] = int((pct > 70).sum())
    crit = {}
    for hn in halves:
        p, f = res[hn]["plain"], res[hn]["filtered"]
        crit[f"{hn} sharpe +0.10"] = bool(f["sharpe"] - p["sharpe"] >= 0.10)
        crit[f"{hn} maxdd <= 0.75x"] = bool(f["maxdd_bp"] <= 0.75 * p["maxdd_bp"])
    res["gate"] = {"criteria": crit, "pass": all(crit.values())}
    return res


# ---------------------------------------------------------------------------
# stage D helpers
# ---------------------------------------------------------------------------

def prob_metrics(P: pd.DataFrame, F: pd.DataFrame) -> dict:
    m = P.notna() & F.notna()
    p, y = P[m].stack(), (F[m] > 0).astype(float).stack()
    y = y.reindex(p.index)
    if len(p) == 0:
        return {}
    brier = float(((p - y) ** 2).mean())
    act = (p < P_ABSTAIN[0]) | (p > P_ABSTAIN[1])
    hit_all = float(((p > 0.5) == (y > 0.5)).mean())
    hit_act = float(((p[act] > 0.5) == (y[act] > 0.5)).mean()) if act.any() else np.nan
    bins = np.arange(0.30, 0.75, 0.05)
    cal = []
    for lo in bins:
        s = (p >= lo) & (p < lo + 0.05)
        if s.sum() >= 10:
            cal.append({"bin": f"{lo:.2f}-{lo + 0.05:.2f}", "mean_P": float(p[s].mean()), "freq": float(y[s].mean()), "n": int(s.sum())})
    return {"brier": brier, "n": int(len(p)), "hit_all": hit_all, "coverage": float(act.mean()),
            "hit_when_acting": hit_act, "calibration": cal}


def abstention_ls(P: pd.DataFrame, F: pd.DataFrame, h: int, start) -> dict:
    g = R.grid_mask(P.index, pd.Timestamp(start), h)
    prev = pd.Series(0.0, index=CCYS)
    pnl = []
    for t in P.index[g]:
        p = P.loc[t].dropna()
        if t not in F.index or p.empty:
            continue
        w = pd.Series(0.0, index=CCYS)
        lo, hi = p[p < P_ABSTAIN[0]].sort_values().index[:2], p[p > P_ABSTAIN[1]].sort_values().index[-2:]
        w[hi] = 0.5
        w[lo] = -0.5
        pnl.append(float((w * F.loc[t].fillna(0.0)).sum()) - 3.0 * float((w - prev).abs().sum()))
        prev = w
    x = pd.Series(pnl)
    cum = x.cumsum()
    return {"ls_bp": float(x.mean()) if len(x) else np.nan,
            "ls_sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(52 / h)) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan,
            "ls_maxdd_bp": float((cum.cummax() - cum).max()) if len(x) else np.nan, "n": int(len(x))}


def compare(p4: Phase4, scores: dict, fwd: dict, start, halve: dict, min_weeks: dict) -> dict:
    out = {}
    for h in (2, 4, 8, 13):
        F = fwd[h]
        out[h] = {}
        Ps = {name: p4.calibrate(S, F, h, min_weeks[name], halve=halve[name]) for name, S in scores.items()}
        common = None
        for name in Ps:
            m = window(Ps[name], start).notna()
            common = m if common is None else (common & m.reindex_like(common).fillna(False))
        for name, S in scores.items():
            Sw, Fw = window(S, start), window(F, start)
            g = grid_stats(weekly_ic(Sw, Fw), h, pd.Timestamp(start))
            Pw = window(Ps[name], start)
            out[h][name] = {"IC": g["IC"], "t": g["t"], "N_ind": g["N_ind"], **ls(Sw, Fw, h, start),
                            "prob": prob_metrics(Pw, Fw), "prob_common": prob_metrics(Pw.where(common), Fw),
                            "abstention_LS": abstention_ls(Pw, Fw, h, start)}
    return out


def gate_recent(c: dict) -> dict:
    m = c[4]
    v4, ms, v3 = m["v4"], m["MS"], m["v3_A1"]
    crit = {"Sharpe>=1.3xMS": bool(v4["ls_sharpe"] >= 1.3 * ms["ls_sharpe"]),
            "Sharpe>=1.0xv3": bool(v4["ls_sharpe"] >= 1.0 * v3["ls_sharpe"]),
            "Sharpe>0": bool(v4["ls_sharpe"] > 0),
            "maxDD<=0.7xMS": bool(v4["ls_maxdd_bp"] <= 0.7 * ms["ls_maxdd_bp"]),
            "Brier<=v3 (common)": bool(v4["prob_common"].get("brier", 9) <= v3["prob_common"].get("brier", -9)),
            "IC>=v3-0.02": bool(v4["IC"] >= v3["IC"] - 0.02)}
    return {"criteria": crit, "pass": all(crit.values())}


def gate_long(c: dict) -> dict:
    m = c[4]
    v4, ms = m["v4_carry"], m["MS_lite"]
    crit = {"Sharpe>=1.3xMS-lite": bool(v4["ls_sharpe"] >= 1.3 * ms["ls_sharpe"]), "Sharpe>0": bool(v4["ls_sharpe"] > 0),
            "maxDD<=0.7xMS-lite": bool(v4["ls_maxdd_bp"] <= 0.7 * ms["ls_maxdd_bp"])}
    return {"criteria": crit, "pass": all(crit.values())}


# ---------------------------------------------------------------------------

def run() -> dict:
    p4 = Phase4()
    fwd = {h: fwd_panel(p4, h) for h in (1, 2, 4, 8, 13, 26)}
    res = {"A": {}, "gates": {}}
    # A1
    am, lev = p4.tff_signal("asset_mgr"), p4.tff_signal("lev_money")
    s0 = first_full(am)
    res["A"]["A1_tff_asset_mgr"] = test_signal(am, fwd, [4, 8, 13], s0)
    res["A"]["A1_tff_lev_money_descriptive"] = test_signal(lev, fwd, [4, 8, 13], s0)
    tff, leg = p4.tff, p4.legacy
    j = tff.merge(leg[["ccy", "date", "open_interest_all"]], on=["ccy", "date"], suffixes=("_tff", "_leg"))
    res["A"]["A1_reconciliation"] = {c: {"weeks": int(len(g)), "oi_equal_share": float((g["open_interest_all_tff"] == g["open_interest_all_leg"]).mean()),
                                         "max_abs_diff": float((g["open_interest_all_tff"] - g["open_interest_all_leg"]).abs().max())}
                                     for c, g in j.groupby("ccy")}
    for h in (4, 8, 13):
        res["gates"][f"A1 TFF asset managers @{h}w"] = hard_gate(res["A"]["A1_tff_asset_mgr"], [4, 8, 13], h)
    # A2, A3
    res["A"]["A2_pre_meeting"] = a2(p4)
    res["gates"]["A2 pre-meeting drift (2023 ->)"] = res["A"]["A2_pre_meeting"]["gate"]
    res["A"]["A3_fed_shock"] = a3(p4)
    res["gates"]["A3 Fed policy shock drift"] = res["A"]["A3_fed_shock"]["gate"]
    # A4
    rv = p4.reer_value()
    res["A"]["A4_reer"] = test_signal(rv, fwd, [8, 13, 26], first_full(rv))
    for h in (8, 13, 26):
        res["gates"][f"A4 REER value @{h}w"] = hard_gate(res["A"]["A4_reer"], [8, 13, 26], h)
    # A5
    ca = p4.carry()
    res["A"]["A5_carry"] = test_signal(ca, fwd, [2, 4, 8, 13, 26], first_full(ca))
    m26 = res["A"]["A5_carry"][26]
    res["gates"]["A5 carry @26w"] = hard_gate(res["A"]["A5_carry"], [2, 4, 8, 13, 26], 26,
                                              {"IC>=0.05": bool(np.isfinite(m26["IC"]) and m26["IC"] >= 0.05)})
    # A6
    res["A"]["A6_crowding"] = a6(p4, fwd[1])
    res["gates"]["A6 crowding filter on carry"] = res["A"]["A6_crowding"]["gate"]

    # passing nodes -> v4 (pre-registered mechanical rule)
    passing = {}
    for k, v in res["gates"].items():
        if v["pass"] and k.startswith(("A1", "A4")):
            passing[k] = am if k.startswith("A1") else rv
    res["v4_definition"] = {"base": "carry_z + macro_z, regime gate", "extra_nodes_passed": sorted(passing),
                            "note": "event-type nodes (A2, A3) and A5 / A6 would enter as described in the prereg; none passed"
                            if not passing else ""}

    # B + C + D
    v4 = p4.v4_raw(macro=True, extra=passing)
    v4c = p4.v4_raw(macro=False)
    v3 = p4.v3_panel("A1")
    ms, msl = p4.ms(), p4.ms(lite=True)
    recent = pd.Timestamp("2023-09-29")
    res["B"] = {"MS": test_signal(ms, fwd, [2, 4, 8, 13], first_full(ms)),
                "MS_lite": test_signal(msl, fwd, [2, 4, 8, 13], first_full(msl)),
                "MS_lite_2016": test_signal(msl, fwd, [2, 4, 8, 13], pd.Timestamp("2016-01-01"))}
    res["C"] = {"v4": test_signal(v4, fwd, [2, 4, 8, 13], recent),
                "v4_carry_2000": test_signal(v4c, fwd, [2, 4, 8, 13], first_full(v4c)),
                "regime_counts": p4.regime().value_counts().to_dict(),
                "crowding_pct_gt70_weeks": int((p4.crowding()["pct"] > 70).sum())}
    conc = p4.concentration(window(v4, recent))
    res["C"]["concentration"] = {"mean": float(conc.mean()), "share_flagged": float((conc >= 0.6).mean()), "n": int(len(conc))}
    # v4.1 descriptive: yearly NNLS on carry_z, macro_z
    from scipy.optimize import nnls
    cz, mz = p4.carry_z(), p4.macro_z()
    w41 = {}
    for yr in (2025, 2026):
        t0 = pd.Timestamp(f"{yr}-01-01")
        X = pd.concat([cz.stack().rename("c"), mz.stack().rename("m"), fwd[4].stack().rename("r")], axis=1)
        X = X[X.index.get_level_values(0) + pd.Timedelta(weeks=4) < t0].dropna()
        if len(X) > 50:
            w, _ = nnls(X[["c", "m"]].to_numpy(), X["r"].to_numpy())
            w41[yr] = {"carry": float(w[0]), "macro": float(w[1]), "n": int(len(X))}
    res["C"]["v4_1_weights"] = w41

    res["D"] = {"recent": compare(p4, {"v4": v4, "v3_A1": v3, "carry": ca, "MS": ms}, fwd, recent,
                                  halve={"v4": True, "v3_A1": False, "carry": False, "MS": False},
                                  min_weeks={"v4": MIN_TRAIN["long"], "v3_A1": MIN_TRAIN["short"], "carry": MIN_TRAIN["long"],
                                             "MS": MIN_TRAIN["short"]}),
                "long": compare(p4, {"v4_carry": v4c, "carry": ca, "MS_lite": msl}, fwd, pd.Timestamp("2016-01-01"),
                                halve={"v4_carry": True, "carry": False, "MS_lite": False},
                                min_weeks={"v4_carry": MIN_TRAIN["long"], "carry": MIN_TRAIN["long"], "MS_lite": MIN_TRAIN["long"]})}
    res["gates"]["D recent: v4 better than the market"] = gate_recent(res["D"]["recent"])
    res["gates"]["D long: v4-carry vs MS-lite"] = gate_long(res["D"]["long"])
    res["fridays"] = [str(p4.fridays[0].date()), str(p4.fridays[-1].date())]
    return res


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def markdown(r: dict) -> str:
    L = [f"# Phase 4 — measurement (pre-registration {r['prereg_commit'][:8]}, attempt #4, last historical round)", ""]

    def table(title, res):
        L.extend([f"### {title}", "", "| h | IC | t | N_ind | IC h1 | IC h2 | L/S net bp | Sharpe | maxDD bp |", "|---|---|---|---|---|---|---|---|---|"])
        for h, m in res.items():
            L.append(f"| {h} | {_f(m['IC'])} | {_f(m['t'], 2)} | {m['N_ind']} | {_f(m['IC_h1'])} | {_f(m['IC_h2'])} | "
                     f"{_f(m['ls_bp'], 1)} | {_f(m['ls_sharpe'], 2)} | {_f(m['ls_maxdd_bp'], 0)} |")
        L.append("")
    L.append("## A")
    for k in ("A1_tff_asset_mgr", "A1_tff_lev_money_descriptive", "A4_reer", "A5_carry"):
        table(k, r["A"][k])
    for k in ("A2_pre_meeting", "A3_fed_shock", "A6_crowding"):
        L += [f"### {k}", "", "```", json.dumps({x: y for x, y in r["A"][k].items() if x != "gate"}, indent=1), "```", ""]
    L.append("## B")
    for k, v in r["B"].items():
        table(k, v)
    L.append("## C")
    table("v4 (2023-09-29 ->)", r["C"]["v4"])
    table("v4 carry-only (2000 ->)", r["C"]["v4_carry_2000"])
    L += ["## D", ""]
    for wn, w in r["D"].items():
        L += [f"### window {wn}", "", "| h | score | IC | t | L/S Sharpe | maxDD | Brier | Brier common | hit all | coverage | hit acting | abst. L/S Sharpe |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h, per in w.items():
            for name, m in per.items():
                p, pc, al = m["prob"], m["prob_common"], m["abstention_LS"]
                L.append(f"| {h} | {name} | {_f(m['IC'])} | {_f(m['t'], 2)} | {_f(m['ls_sharpe'], 2)} | {_f(m['ls_maxdd_bp'], 0)} | "
                         f"{_f(p.get('brier'), 4)} | {_f(pc.get('brier'), 4)} | {_f(p.get('hit_all'))} | {_f(p.get('coverage'))} | "
                         f"{_f(p.get('hit_when_acting'))} | {_f(al['ls_sharpe'], 2)} |")
        L.append("")
    L += ["## Gates", ""]
    for k, v in r["gates"].items():
        failed = [c for c, ok in v["criteria"].items() if not ok]
        L.append(f"- {k}: {'PASS' if v['pass'] else 'fail'}" + (f" (fails: {', '.join(failed)})" if failed else ""))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "phase4_metrics.json")
    ap.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval" / "phase4.md")
    a = ap.parse_args()
    commit = R.prereg_commit(PREREG)
    res = run()
    res["prereg_commit"] = commit
    out = R._clean(res)
    a.out_json.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    a.out_md.write_text(markdown(out))
    print(f"prereg {commit} -> {a.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
