"""Registry v1 (phase 5, stage G): prospective snapshots (Friday) and scoring (Tuesday).

Snapshot rows (data/registry/snapshots.parquet): asof (the Friday), source, universe (8 | 15), ccy,
score, rank (1 = strongest), p (v4 only), stale, source_asof, note. Every source is the value known on
Friday 21:00 UTC; outcomes start at the first fixing after it (H.10 noon NY for the 8, ECB 14:15 CET
for the 15), as in stage E of prereg_phase5.yml.

Sources: v3 (production payload, rank of score_v3), candidate_live (composite z + v3 macro-block z,
on the 8), composite and its components on the 15 (labour, confidence, external, tot, carry with the
vol rule), MS (surprises + COT + momentum), TFF asset managers contrarian, macro_momentum and v4
(continuity; their inputs are not refreshed by the registry, so they go stale). George's calls are
scored from data/registry/apeluri_george.csv / valute_george.csv (validated by validate_calls.py)."""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.registry.inputs import DIR as INPUTS, staleness  # noqa: E402
from src.tree.composite import CCYS15, DEVELOPED, P5Inputs, signals_at  # noqa: E402

REG = ROOT / "data" / "registry"
SNAP = REG / "snapshots.parquet"
REALIZED = REG / "realized.parquet"
SUMMARY = REG / "summary.json"
PAYLOAD = ROOT / "public" / "data" / "economic.json"
CCY8 = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
HORIZONS = [1, 2, 4, 8, 13]
COST15 = {c: (2.0 if c in DEVELOPED else 6.0) for c in CCYS15}


def zc(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=1)
    return (s - s.mean()) / sd if s.notna().sum() >= 3 and sd > 0 else s * np.nan


def rolling_pct_last(s: pd.Series, n: int = 156, min_n: int = 104) -> float:
    s = s.dropna()
    w = s.iloc[-n:]
    return float(100 * (w <= w.iloc[-1]).mean()) if len(w) >= min_n else np.nan


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def src_v3(payload: Path = PAYLOAD) -> tuple[pd.Series, pd.Series, str]:
    d = json.loads(payload.read_text())
    v3 = pd.Series({c: d["currencies"].get(c, {}).get("score_v3") for c in CCY8}, dtype=float)
    macro = pd.Series({c: ((d["currencies"].get(c, {}).get("v3") or {}).get("blocks") or {}).get("macro")
                       for c in CCY8}, dtype=float)
    return v3, macro, str(d.get("as_of"))


def src_composite(t: pd.Timestamp) -> dict:
    inp = P5Inputs(research_dir=INPUTS)
    per = t.to_period("M")
    flags = inp.high_vol_flags()
    hv = bool(flags.get(per - 1, False))
    s = signals_at(inp, t, per.strftime("%Y%m"), (per - 1).to_timestamp(how="end").normalize(), hv)
    z = s["z"]
    out = {"composite": s["composite"], "labour": z["labour"], "comp_confidence": z["confidence"],
           "comp_external": z["external"], "comp_tot": z["tot"],
           "carry_vol": z["carry"].where(~pd.Series(hv, index=z.index))}
    return {k: v.reindex(CCYS15) for k, v in out.items()} | {"_high_vol": hv}


def _h10_levels() -> pd.DataFrame:
    h = pd.read_parquet(INPUTS / "h10_fx.parquet").pivot(index="date", columns="ccy", values="log_usd_value")
    h.insert(0, "USD", 0.0)
    return h[CCY8].dropna().sort_index()


def _cot_ratio(df: pd.DataFrame, long: str, short: str) -> dict[str, pd.Series]:
    out = {}
    for c, g in df.groupby("ccy"):
        g = g.sort_values("date")
        out[c] = pd.Series(((g[long] - g[short]) / g["open_interest_all"]).to_numpy(float), index=pd.DatetimeIndex(g["date"]))
    return out


def src_tff(t: pd.Timestamp) -> pd.Series:
    tff = pd.read_parquet(INPUTS / "cftc_tff.parquet")
    r = _cot_ratio(tff, "asset_mgr_positions_long", "asset_mgr_positions_short")
    return pd.Series({c: -rolling_pct_last(s[s.index + pd.Timedelta(days=6) <= t]) for c, s in r.items()}).reindex(CCY8)


def ms_surprises(t: pd.Timestamp) -> pd.Series:
    from scripts.build_reaction_events import catalog_pairs
    from src.economic_compute import effective_direction
    from src.ff_scoring import load_configs, scoring_view, to_scoring_frame
    ff = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    fr = scoring_view(to_scoring_frame(ff))
    fr = fr[pd.to_datetime(fr["release_dt"]) < t + pd.Timedelta(hours=21)]
    ind, _ = load_configs()
    pairs = catalog_pairs()
    out = {}
    for (c, k), g in fr.groupby(["currency", "indicator_key"]):
        if (c, k) not in pairs or k not in ind["indicators"]:
            continue
        g = g[g["actual"].notna() & g["consensus"].notna()].sort_values("release_dt").tail(12)
        if g.empty:
            continue
        d = effective_direction(ind["indicators"][k], ind["defaults"], c)
        out.setdefault(c, []).append(float(np.sign((g["actual"] - g["consensus"]) * d).mean()))
    return pd.Series({c: sum(v) for c, v in out.items()}).reindex(CCY8)


def src_ms(t: pd.Timestamp) -> pd.Series:
    leg = pd.read_parquet(INPUTS / "cftc_legacy.parquet")
    r = _cot_ratio(leg, "noncomm_positions_long_all", "noncomm_positions_short_all")
    cot = pd.Series({c: rolling_pct_last(s[s.index + pd.Timedelta(days=6) <= t]) for c, s in r.items()}).reindex(CCY8)
    lv = _h10_levels()
    a = lv[lv.index <= t].iloc[-1]
    b = lv[lv.index <= t - pd.Timedelta(days=91)].iloc[-1]
    mv = a - b
    mv["USD"] = -mv.drop("USD").mean()
    mom = np.sign(mv)
    parts = [zc(ms_surprises(t)), zc(cot), zc(mom)]
    tot = sum(p.fillna(0) for p in parts)
    return tot.where(sum(p.notna().astype(int) for p in parts) == 3)


def src_macro_momentum(t: pd.Timestamp) -> tuple[pd.Series, bool]:
    from src.tree.macro import MacroInputs
    mi = MacroInputs.load()
    s = pd.Series({c: mi.momentum(c, t)["composite_eq"] for c in CCY8}, dtype=float)
    last = mi.frame["avail"].max()
    return s, bool((t - last).days > 70)


def src_v4(t: pd.Timestamp, live_macro: pd.Series) -> tuple[pd.Series, dict, bool]:
    from src.tree.v4 import Phase4, snapshot as v4s
    fr = pd.date_range("2000-01-07", t, freq="7D")
    if fr[-1] != t:
        fr = fr.append(pd.DatetimeIndex([t]))
    p4 = Phase4(fridays=fr)
    d = v4s(t, p4, live_macro=live_macro.to_dict())
    s = pd.Series({c: e["score"] for c, e in d["currencies"].items()}, dtype=float).reindex(CCY8)
    P = {c: e["by_horizon"].get("2", {}).get("P_beats_basket") for c, e in d["currencies"].items()}
    last_fx = p4.basket.index[-1]
    return s, P, bool((t - last_fx).days > 14)


def snapshot(t: pd.Timestamp, payload: Path = PAYLOAD, out: Path = SNAP) -> pd.DataFrame:
    t = pd.Timestamp(t).normalize()
    st = staleness(t + pd.Timedelta(days=1))
    rows = []

    def add(source, universe, s, stale=False, source_asof=None, p=None, note=None):
        s = s.astype(float)
        rk = s.rank(ascending=False, method="min")
        for c in (CCY8 if universe == "8" else CCYS15):
            rows.append({"asof": t, "source": source, "universe": universe, "ccy": c, "score": s.get(c),
                         "rank": rk.get(c), "p": (p or {}).get(c), "stale": bool(stale),
                         "source_asof": source_asof, "note": note})

    v3, macro, v3_asof = src_v3(payload)
    v3_stale = (t + pd.Timedelta(days=1) - pd.Timestamp(v3_asof[:10])).days > 3
    add("v3", "8", v3, v3_stale, v3_asof)
    comp = src_composite(t)
    stale_c = any(st[k]["stale"] for k in ("p5_oecd_components.parquet", "p5_bci.parquet", "p5_ctot.parquet",
                                           "p5_ecb_fx.parquet", "p5_bis_policy.parquet"))
    note = "high FX vol: carry out" if comp["_high_vol"] else None
    for k in ("composite", "labour", "comp_confidence", "comp_external", "comp_tot", "carry_vol"):
        add(k, "15", comp[k], stale_c, note=note)
        add(k, "8", comp[k].reindex(CCY8), stale_c, note=note)
    add("candidate_live", "8", (zc(comp["composite"].reindex(CCY8)) + zc(macro)) / 2, stale_c or v3_stale)
    add("ms", "8", src_ms(t), st["cftc_legacy.parquet"]["stale"] or st["h10_fx.parquet"]["stale"])
    add("tff_am_contrarian", "8", src_tff(t), st["cftc_tff.parquet"]["stale"])
    mm, mm_stale = src_macro_momentum(t)
    add("macro_momentum", "8", mm, mm_stale, note="inputs not refreshed by the registry")
    v4, P, v4_stale = src_v4(t, macro)
    add("v4", "8", v4, v4_stale, p=P, note="inputs not refreshed by the registry")
    new = pd.DataFrame(rows)
    if out.exists():
        old = pd.read_parquet(out)
        old = old[pd.to_datetime(old["asof"]) != t]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["asof", "source", "universe", "ccy"]).reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(out, index=False)
    return new


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _first_after(idx: pd.DatetimeIndex, t: pd.Timestamp) -> int:
    return int(idx.searchsorted(t + pd.Timedelta(days=1)))


def realized(asofs, h10: pd.DataFrame, ecb: pd.DataFrame, rates_at, horizons=HORIZONS) -> pd.DataFrame:
    """Outcomes per (asof, universe, ccy, h): 8 = H.10 log change vs the basket of the 8 (bp);
    15 = ECB excess return (carry at the start) vs the basket of the other 14 (bp)."""
    rows = []
    b8 = h10.apply(lambda col: col - (h10.sum(axis=1) - col) / (h10.shape[1] - 1))
    for t in sorted(set(pd.to_datetime(asofs))):
        for h in horizons:
            i = _first_after(b8.index, t)
            if i < len(b8.index):
                a = b8.index[i]
                j = b8.index.searchsorted(a + pd.Timedelta(weeks=h), side="right") - 1
                if a + pd.Timedelta(weeks=h) <= b8.index[-1] and b8.index[j] >= a + pd.Timedelta(weeks=h) - pd.Timedelta(days=4):
                    for c in CCY8:
                        rows.append({"asof": t, "universe": "8", "ccy": c, "h": h, "ret_bp": float((b8.iloc[j][c] - b8.loc[a][c]) * 1e4)})
            i = _first_after(ecb.index, t)
            if i < len(ecb.index):
                a = ecb.index[i]
                j = ecb.index.searchsorted(a + pd.Timedelta(weeks=h), side="right") - 1
                if a + pd.Timedelta(weeks=h) <= ecb.index[-1] and ecb.index[j] >= a + pd.Timedelta(weeks=h) - pd.Timedelta(days=4):
                    mv = (ecb.iloc[j] - ecb.loc[a]) * 1e4
                    r = pd.Series({c: rates_at(c, t) for c in CCYS15})
                    ex = (mv + (r - r["USD"]) * h / 52 * 100).dropna()
                    for c in ex.index:
                        rows.append({"asof": t, "universe": "15", "ccy": c, "h": h,
                                     "ret_bp": float(ex[c] - ex.drop(c).mean())})
    return pd.DataFrame(rows, columns=["asof", "universe", "ccy", "h", "ret_bp"])


def summarise(snaps: pd.DataFrame, real: pd.DataFrame) -> list[dict]:
    m = snaps.merge(real, on=["asof", "universe", "ccy"])
    out = []
    for (src, uni, h), g in m.groupby(["source", "universe", "h"]):
        ics, ls, hit, tot = [], [], 0, 0
        k = 2 if uni == "8" else 3
        for _, w in g.groupby("asof"):
            w = w.dropna(subset=["score", "ret_bp"])
            if len(w) < (5 if uni == "8" else 10):
                continue
            if w["score"].nunique() > 1:
                ics.append(spearmanr(w["score"], w["ret_bp"]).correlation)
            o = w.sort_values("score")
            ls.append(o["ret_bp"].tail(k).mean() - o["ret_bp"].head(k).mean())
            for (_, a), (_, b) in combinations(w.iterrows(), 2):
                if a["score"] != b["score"] and a["ret_bp"] != b["ret_bp"]:
                    tot += 1
                    hit += np.sign(a["score"] - b["score"]) == np.sign(a["ret_bp"] - b["ret_bp"])
        ic = pd.Series(ics, dtype=float)
        out.append({"source": src, "universe": uni, "h": int(h), "snapshots": int(len(ics)),
                    "mean_IC": float(ic.mean()) if len(ic) else None,
                    "t": float(ic.mean() / ic.std(ddof=1) * np.sqrt(len(ic))) if len(ic) > 2 and ic.std(ddof=1) > 0 else None,
                    "ls_bp": float(np.mean(ls)) if ls else None, "pairs_hit": hit / tot if tot else None})
    return out


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2)) if len(p) else float("nan")


def score_george_banks(calls: list[dict], y2: dict[str, pd.Series], now: pd.Timestamp) -> dict:
    """calls: accepted rows from validate_calls (with window_start_day / window_end_day)."""
    from scripts.registry.validate_calls import BANKS
    rows = []
    for c in calls:
        ccy = BANKS[c["banca"]]
        s = y2.get(ccy)
        if s is None or s.empty:
            rows.append({**c, "outcome": None, "pending": f"no 2Y for {ccy}"})
            continue
        a, b = pd.Timestamp(c["window_start_day"]), pd.Timestamp(c["window_end_day"])
        if b > s.index[-1] or b > now:
            rows.append({**c, "outcome": None, "pending": "window not finished"})
            continue
        ya, yb = s[s.index <= a].iloc[-1], s[s.index <= b].iloc[-1]
        if ya == yb:
            rows.append({**c, "outcome": None, "pending": "2Y unchanged (excluded)"})
            continue
        prior = s[(s.index < a) & (s.index >= a - pd.Timedelta(days=28))]
        mom = np.sign(prior.iloc[-1] - prior.iloc[0]) if len(prior) > 1 else 0
        hawk = 1.0 if yb > ya else 0.0
        p = int(c["probabilitate"]) / 100
        p_h = p if c["directie"] == "hawkish" else (1 - p if c["directie"] == "dovish" else 0.5)
        rows.append({**c, "outcome": hawk, "p_hawkish": p_h, "p_momentum": 0.55 if mom > 0 else (0.45 if mom < 0 else 0.5)})
    done = [r for r in rows if r.get("outcome") is not None]
    res = {"n_scored": len(done), "n_pending": len(rows) - len(done)}
    if done:
        y = np.array([r["outcome"] for r in done])
        res["brier_george"] = brier([r["p_hawkish"] for r in done], y)
        res["brier_0.5"] = brier(np.full(len(y), 0.5), y)
        res["brier_momentum_2y_4w"] = brier([r["p_momentum"] for r in done], y)
        res["stage_h_model"] = "not reused: the phase-3 stage-H model needs a walk-forward refit, not available live"
        bins = {}
        for r in done:
            q = max(r["p_hawkish"], 1 - r["p_hawkish"])
            key = "50" if q == 0.5 else ("51-64" if q < 0.65 else ("65-79" if q < 0.8 else "80-95"))
            right = (r["p_hawkish"] > 0.5) == (r["outcome"] == 1.0)
            bins.setdefault(key, []).append((q, right))
        res["calibration"] = {k: {"n": len(v), "mean_p": float(np.mean([x for x, _ in v])),
                                  "hit": float(np.mean([y for _, y in v]))} for k, v in bins.items()}
        for key in ("directie", "motiv"):
            res[f"by_{key}"] = {}
            for val in sorted({r[key] for r in done}):
                sub = [r for r in done if r[key] == val]
                res[f"by_{key}"][val] = {"n": len(sub), "brier": brier([r["p_hawkish"] for r in sub], [r["outcome"] for r in sub])}
    res["rows"] = [{k: v for k, v in r.items() if k not in ("nota",)} for r in rows]
    return res


def score_george_fx(calls: list[dict], h10: pd.DataFrame, snaps: pd.DataFrame) -> dict:
    from scripts.registry.validate_calls import _codes
    b8 = h10.apply(lambda col: col - (h10.sum(axis=1) - col) / (h10.shape[1] - 1))
    out = []
    for c in calls:
        t = pd.Timestamp(c["data"])
        i = _first_after(b8.index, t)
        if i >= len(b8.index):
            continue
        a = b8.index[i]
        strong, weak = _codes(c["tari"]), _codes(c["slabe"])
        rec = {"data": c["data"], "tari": strong, "slabe": weak}
        for h in (1, 2, 4):
            e = a + pd.Timedelta(weeks=h)
            if e > b8.index[-1]:
                continue
            j = b8.index.searchsorted(e, side="right") - 1
            r = (b8.iloc[j] - b8.loc[a]) * 1e4
            rec[f"ls_{h}w_bp"] = float(r[strong].mean() - r[weak].mean())
            rec[f"pairs_hit_{h}w"] = float(np.mean([r[s] > r[w] for s in strong for w in weak]))
            prev = snaps[(snaps["universe"] == "8") & (pd.to_datetime(snaps["asof"]) <= t)]
            for src in ("v3", "candidate_live"):
                p = prev[prev["source"] == src]
                if p.empty:
                    continue
                last = p[p["asof"] == p["asof"].max()].set_index("ccy")["score"].dropna().sort_values()
                if len(last) >= 4:
                    rec[f"{src}_ls_{h}w_bp"] = float(r[last.index[-2:]].mean() - r[last.index[:2]].mean())
        out.append(rec)
    return {"n": len(out), "rows": out}
