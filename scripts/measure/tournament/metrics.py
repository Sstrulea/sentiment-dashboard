"""Step 3–4 (and 6) — targets, metrics, bootstrap and the pre-registered decision
rule of docs/eval/scoring-tournament-prereg.md (section E). Reads the cached weekly
scores (scores.py) and the prices (prices.py); writes the result JSON to the cache
and the metrics CSV to docs/eval/.

    python scripts/measure/tournament/metrics.py main
    python scripts/measure/tournament/metrics.py long
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, CCYS, CROSS, HORIZONS, INDICES, METALS, ROOT  # noqa: E402
import prices as P  # noqa: E402

import yaml  # noqa: E402

COMMON_START = pd.Timestamp("2023-09-22 21:00")
END = pd.Timestamp("2026-09-04 21:00")
LONG_START = pd.Timestamp("2014-01-03 21:00")
LATE_WEEKS = 13
B, SEED = 2000, 20261016
BLOCK = {1: 4, 2: 4, 4: 8}
MIN_TS_WEEKS = 26
FX_IC_MIN = {"main": 6, "long": 4}
SIGMA_DAYS = 20
USD_ROW = "US-DOLLAR"
CLASSES = ("FX", "IDX", "MET")
N_VARIANTS = {"FX": 17, "IDX": 16, "MET": 16}
OUT_JSON = {"main": CACHE / "metrics_main.json", "long": CACHE / "metrics_long.json"}
CSV = ROOT / "docs" / "eval" / "scoring-tournament-metrics.csv"


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def _pairs():
    inst = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())["instruments"]
    return [(s, c["base"], c["quote"]) for s, c in inst.items() if c["type"] == "fx"]


def _sigma20(px: pd.Series, t: pd.Timestamp, log_level: bool = False) -> float | None:
    s = px[px.index <= pd.Timestamp(t.date())]
    lr = (s.diff() if log_level else np.log(s).diff()).dropna().tail(SIGMA_DAYS)
    return float(lr.std(ddof=1)) if len(lr) == SIGMA_DAYS else None


def targets(weeks) -> pd.DataFrame:
    """One row per (week, view, unit): fwd_h, past_h for h in 1/2/4, sigma20.
    view: pair (28 pairs + US Dollar row), ccy (8, demeaned vs USD), cross."""
    usd = P.fred_usd_values()
    pxs = {s: P.cross_price(s) for s in CROSS}
    pairs = _pairs()
    pair_px = {s: P.pair_price(usd, b, q) for s, b, q in pairs}
    lp = pd.concat({c: np.log(usd[c]) for c in usd}, axis=1, join="inner").dropna()
    usd_dm_level = -lp.sum(axis=1) / 8.0                  # log level of USD demeaned over the 8
    rows = []
    for t in weeks:
        rec = {}
        # currencies vs USD, demeaned over the 8 (as v3_scales.currency_ic)
        for kind, fn in (("fwd", P.fwd_log_return), ("past", P.past_log_return)):
            for h in HORIZONS:
                lr = {c: (0.0 if c == "USD" else fn(usd[c], t, h)) for c in CCYS}
                ok = all(v is not None for v in lr.values())
                m = np.mean(list(lr.values())) if ok else None
                for c in CCYS:
                    rec.setdefault(("ccy", c), {})[f"{kind}_{h}"] = (lr[c] - m) if ok else None
                rec.setdefault(("pair", USD_ROW), {})[f"{kind}_{h}"] = (0.0 - m) if ok else None
                for s, _, _ in pairs:
                    rec.setdefault(("pair", s), {})[f"{kind}_{h}"] = fn(pair_px[s], t, h)
                for s in CROSS:
                    rec.setdefault(("cross", s), {})[f"{kind}_{h}"] = fn(pxs[s], t, h)
        for s, _, _ in pairs:
            rec[("pair", s)]["sigma20"] = _sigma20(pair_px[s], t)
        rec[("pair", USD_ROW)]["sigma20"] = _sigma20(usd_dm_level, t, log_level=True)
        for s in CROSS:
            rec[("cross", s)]["sigma20"] = _sigma20(pxs[s], t)
        for (view, unit), v in rec.items():
            rows.append({"week": t, "view": view, "unit": unit, **v})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def class_units(cls: str):
    if cls == "FX":
        return "pair", [s for s, _, _ in _pairs()] + [USD_ROW]
    if cls == "IDX":
        return "cross", list(INDICES)
    if cls == "MET":
        return "cross", list(METALS)
    if cls == "CCY":
        return "ccy", list(CCYS)
    raise KeyError(cls)


def panel(scores: pd.DataFrame, tg: pd.DataFrame, cls: str, weeks) -> dict:
    """{variant: S [weeks × units]}, targets {col: [weeks × units]}, plus F1 aligned."""
    view, units = class_units(cls)
    sv = scores.copy()
    sv.loc[sv["view"] == "usd_row", "view"] = "pair"
    sv = sv[(sv["view"] == view) & sv["unit"].isin(units) & sv["week"].isin(weeks)]
    S = {}
    for v, g in sv.groupby("variant"):
        m = g.pivot(index="week", columns="unit", values="score").reindex(index=weeks, columns=units)
        S[v] = m.to_numpy(dtype=float)
    t = tg[(tg["view"] == view) & tg["unit"].isin(units) & tg["week"].isin(weeks)]
    T = {c: t.pivot(index="week", columns="unit", values=c).reindex(index=weeks, columns=units).to_numpy(dtype=float)
         for c in t.columns if c not in ("week", "view", "unit")}
    return {"S": S, "T": T, "units": units, "weeks": list(weeks)}


# ---------------------------------------------------------------------------
# Metric pieces (per week counts so the bootstrap is a resum)
# ---------------------------------------------------------------------------

def call_counts(s: np.ndarray, r: np.ndarray) -> np.ndarray:
    """[weeks × 5]: up_correct, up, down_correct, down, hits — calls = s≠0, r≠0."""
    ok = np.isfinite(s) & np.isfinite(r) & (s != 0) & (r != 0)
    up, dn = ok & (r > 0), ok & (r < 0)
    return np.stack([(up & (s > 0)).sum(1), up.sum(1), (dn & (s < 0)).sum(1), dn.sum(1),
                     (up & (s > 0)).sum(1) + (dn & (s < 0)).sum(1)], axis=1).astype(float)


def ba_from(c: np.ndarray) -> tuple[float, float, int]:
    """(balanced accuracy, hit rate, calls) from summed counts."""
    uc, u, dc, d, hit = c[..., 0], c[..., 1], c[..., 2], c[..., 3], c[..., 4]
    with np.errstate(invalid="ignore", divide="ignore"):
        ba = 0.5 * (uc / u + dc / d)
        hr = hit / (u + d)
    return ba, hr, u + d


def spearman(a, b) -> float:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return float("nan")
    x, y = stats.rankdata(a[m]), stats.rankdata(b[m])
    if x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def weekly_xs_ic(s: np.ndarray, r: np.ndarray, min_units: int) -> np.ndarray:
    out = np.full(s.shape[0], np.nan)
    for i in range(s.shape[0]):
        m = np.isfinite(s[i]) & np.isfinite(r[i])
        if m.sum() >= min_units and np.nanstd(s[i][m]) > 0:
            out[i] = spearman(s[i][m], r[i][m])
    return out


def ts_ic(s: np.ndarray, r: np.ndarray, idx=None) -> float:
    """Mean over instruments of the time-series Spearman (>= 26 weeks each)."""
    vals = []
    for j in range(s.shape[1]):
        a, b = (s[:, j], r[:, j]) if idx is None else (s[idx, j], r[idx, j])
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() >= MIN_TS_WEEKS:
            v = spearman(a[m], b[m])
            if np.isfinite(v):
                vals.append(v)
    return float(np.mean(vals)) if vals else float("nan")


def boot_idx(n: int, L: int) -> np.ndarray:
    """[B × n] circular block bootstrap indices (deterministic per n and L)."""
    rng = np.random.default_rng([SEED, n, L])
    nb = math.ceil(n / L)
    starts = rng.integers(0, n, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]) % n
    return idx.reshape(B, nb * L)[:, :n]


def ci(x) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        return float("nan"), float("nan")
    return float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))


def holm(p: dict) -> dict:
    keys = sorted((k for k in p if np.isfinite(p[k])), key=lambda k: p[k])
    m, out, run = len(keys), {}, 0.0
    for i, k in enumerate(keys):
        run = max(run, min(1.0, (m - i) * p[k]))
        out[k] = run
    for k in p:
        out.setdefault(k, float("nan"))
    return out


def dsr(pnl: np.ndarray, sr_all: list[float], n: int) -> float:
    """Deflated Sharpe ratio probability (Bailey & López de Prado 2014), per-period SR."""
    x = pnl[np.isfinite(pnl)]
    if len(x) < 10 or x.std(ddof=1) == 0:
        return float("nan")
    sr = x.mean() / x.std(ddof=1)
    srs = np.array([v for v in sr_all if np.isfinite(v)])
    if len(srs) < 2:
        return float("nan")
    g = 0.5772156649
    sr0 = math.sqrt(srs.var(ddof=1)) * ((1 - g) * stats.norm.ppf(1 - 1 / n) + g * stats.norm.ppf(1 - 1 / (n * math.e)))
    sk, ku = stats.skew(x), stats.kurtosis(x, fisher=False)
    den = 1 - sk * sr + (ku - 1) / 4 * sr ** 2
    if den <= 0:
        return float("nan")
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(len(x) - 1) / math.sqrt(den)))


# ---------------------------------------------------------------------------
# Evaluation of one (class, variant) on a set of weeks
# ---------------------------------------------------------------------------

def ic_series(cls, Sx, Tc, h, kind="fwd", min_fx=6):
    """FX: weekly cross-sectional IC on the currency panel; cross: None (TS)."""
    if cls == "CCY":
        return weekly_xs_ic(Sx, Tc[f"{kind}_{h}"], min_fx)
    return None


def strong_hits(s: np.ndarray, r: np.ndarray) -> dict:
    with np.errstate(invalid="ignore"):
        rms = np.sqrt(np.nanmean(s ** 2, axis=0))
        z = s / rms[None, :]
    az = np.abs(z[np.isfinite(z)])
    out = {}
    if not len(az):
        return {"p55": {"thr": None, "hit": None, "n": 0}, "p90": {"thr": None, "hit": None, "n": 0}}
    for q, name in ((0.55, "p55"), (0.90, "p90")):
        thr = float(np.quantile(az, q))
        m = np.isfinite(z) & (np.abs(z) >= thr) & (s != 0) & np.isfinite(r) & (r != 0)
        n = int(m.sum())
        out[name] = {"thr": thr, "hit": float((np.sign(s[m]) == np.sign(r[m])).mean()) if n else None, "n": n}
    return out


def quintiles(s: np.ndarray, r: np.ndarray) -> list:
    m = np.isfinite(s) & np.isfinite(r)
    if m.sum() < 10:
        return []
    q = pd.qcut(pd.Series(s[m]), 5, duplicates="drop")
    g = pd.Series(r[m]).groupby(q.values, observed=True)
    return [{"bin": str(k), "mean_ret": float(v.mean()), "n": int(v.size)} for k, v in g]


def value_over_trend(s, f1, r, sig, h, idx_all=None):
    y = r / (sig * math.sqrt(5 * h))
    m = np.isfinite(s) & np.isfinite(f1) & np.isfinite(y)

    def beta(mask):
        if mask.sum() < 30:
            return float("nan")
        a, b = s[mask], f1[mask]
        if a.std() == 0 or b.std() == 0:
            return float("nan")
        X = np.column_stack([np.ones(mask.sum()), (a - a.mean()) / a.std(), (b - b.mean()) / b.std()])
        return float(np.linalg.lstsq(X, y[mask], rcond=None)[0][1])

    est = beta(m)
    boots = []
    if idx_all is not None and np.isfinite(est):
        for idx in idx_all:
            boots.append(beta_rows(s, f1, y, idx))
    return est, ci(boots), int(m.sum())


def beta_rows(s, f1, y, idx):
    a, b, yy = s[idx].ravel(), f1[idx].ravel(), y[idx].ravel()
    m = np.isfinite(a) & np.isfinite(b) & np.isfinite(yy)
    if m.sum() < 30:
        return float("nan")
    a, b, yy = a[m], b[m], yy[m]
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    X = np.column_stack([np.ones(len(a)), (a - a.mean()) / a.std(), (b - b.mean()) / b.std()])
    return float(np.linalg.lstsq(X, yy, rcond=None)[0][1])


def pnl_series(s, r, sig) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = np.where(np.isfinite(s) & (s != 0) & np.isfinite(r) & np.isfinite(sig) & (sig > 0),
                       np.sign(s) * r / sig, np.nan)
    out = np.nanmean(np.where(np.isnan(pos), np.nan, pos), axis=1) if pos.size else np.array([])
    return np.where(np.isfinite(out), out, 0.0)


def common_start(st: dict) -> pd.Timestamp:
    """The latest FX start among the variants starting within 13 weeks of A1."""
    a1 = st[("FX", "A1")]
    return max(v for (cls, _), v in st.items() if cls == "FX" and v <= a1 + pd.Timedelta(weeks=LATE_WEEKS))


def window_of(start_fx: dict, cls: str, v: str, all_weeks) -> list:
    st = start_fx.get((cls if cls != "CCY" else "FX", v))
    if st is None:
        return []
    c0 = common_start(start_fx)
    late = st > start_fx[("FX", "A1")] + pd.Timedelta(weeks=LATE_WEEKS)
    s0 = st if late else c0
    return [w for w in all_weeks if s0 <= w <= END]


def starts(scores: pd.DataFrame) -> dict:
    """{(class, variant): first defined Friday} — FX: >= 6 currencies; cross: any instrument."""
    out = {}
    for v, g in scores.groupby("variant"):
        c = g[(g["view"] == "ccy") & g["score"].notna()].groupby("week")["unit"].nunique()
        if len(c[c >= 6]):
            out[("FX", v)] = c[c >= 6].index.min()
        x = g[(g["view"] == "cross") & g["score"].notna()]
        for cls, units in (("IDX", INDICES), ("MET", METALS)):
            w = x[x["unit"].isin(units)]["week"]
            if len(w):
                out[(cls, v)] = w.min()
    return out


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

def evaluate_main(scores: pd.DataFrame, cats: pd.DataFrame, tg: pd.DataFrame) -> dict:
    all_weeks = sorted(scores["week"].unique())
    st = starts(scores)
    res = {"starts": {f"{k[0]}|{k[1]}": str(v) for k, v in st.items()}, "common_start": str(common_start(st)),
           "end": str(END), "classes": {}}
    variants = sorted(scores["variant"].unique())
    for cls in CLASSES:
        R = {}
        for v in variants:
            if (cls, v) not in st:
                R[v] = {"na": "fără scor în clasă" if cls != "FX" else "fără scor FX"}
                continue
            weeks = window_of(st, cls, v, all_weeks)
            R[v] = eval_variant(scores, tg, cls, v, weeks)
        # paired vs v3 + Holm + decision
        comp = {"ba": {}, "ic": {}}
        for v in variants:
            if v == "A1" or "na" in R[v]:
                continue
            d = paired_vs(scores, tg, cls, v, "A1", R[v]["weeks"])
            R[v]["vs_v3"] = d
            comp["ba"][v], comp["ic"][v] = d["p_ba"], d["p_ic"]
        hb, hi = holm(comp["ba"]), holm(comp["ic"])
        for v in comp["ba"]:
            d = R[v]["vs_v3"]
            d["p_holm_ba"], d["p_holm_ic"] = hb[v], hi[v]
            d["c1"] = bool(d["d_ba"] > 0 and hb[v] < 0.05)
            d["c2"] = bool(d["d_ba_h1"] > 0 and d["d_ba_h2"] > 0)
            d["c3"] = bool(np.isfinite(d["ic_var"]) and np.isfinite(d["ic_v3"]) and d["ic_var"] >= d["ic_v3"])
            d["beats_v3"] = d["c1"] and d["c2"] and d["c3"]
        # DSR within class and horizon
        for h in HORIZONS:
            srs = [R[v]["pnl"][str(h)]["sr_period"] for v in variants if "na" not in R[v]]
            for v in variants:
                if "na" in R[v]:
                    continue
                R[v]["pnl"][str(h)]["dsr"] = dsr(np.array(R[v]["pnl"][str(h)]["_series"]), srs, N_VARIANTS[cls])
        for v in variants:
            if "na" not in R[v]:
                for h in HORIZONS:
                    R[v]["pnl"][str(h)].pop("_series", None)
        res["classes"][cls] = R
    res["sign_diag"] = sign_diag(cats, tg, all_weeks)
    return res


def _ccy_panel(scores, tg, v, weeks):
    p = panel(scores[scores["variant"] == v], tg, "CCY", weeks)
    return p["S"].get(v), p["T"]


def eval_variant(scores, tg, cls, v, weeks) -> dict:
    n = len(weeks)
    out = {"weeks": [str(w) for w in weeks], "window": [str(weeks[0]), str(weeks[-1]), n]}
    p = panel(scores[scores["variant"].isin([v, "F1"])], tg, cls, weeks)
    s = p["S"][v]
    f1 = p["S"].get("F1")
    out["coverage"] = float((np.isfinite(s) & (s != 0)).sum() / s.size)
    half = math.ceil(n / 2)
    sc, Tc = (None, None)
    if cls == "FX":
        sc, Tc = _ccy_panel(scores, tg, v, weeks)
    out["ba"], out["ic"], out["strong"], out["pnl"], out["coinc"], out["vot"] = {}, {}, {}, {}, {}, {}
    for h in HORIZONS:
        r = p["T"][f"fwd_{h}"]
        cnt = call_counts(s, r)
        ba, hr, nc = ba_from(cnt.sum(0))
        idx = boot_idx(n, BLOCK[h])
        bb = ba_from(cnt[idx].sum(1))[0]
        h1 = ba_from(cnt[:half].sum(0))[0]
        h2 = ba_from(cnt[half:].sum(0))[0]
        out["ba"][str(h)] = {"ba": ba, "hit": hr, "calls": int(nc), "ci": ci(bb), "half1": h1, "half2": h2}
        out["strong"][str(h)] = strong_hits(s, r)
        # IC
        if cls == "FX":
            w = weekly_xs_ic(sc, Tc[f"fwd_{h}"], FX_IC_MIN["main"])
            ic = float(np.nanmean(w)) if np.isfinite(w).any() else float("nan")
            icb = [np.nanmean(w[i]) if np.isfinite(w[i]).any() else np.nan for i in idx]
            wp = weekly_xs_ic(sc, Tc[f"past_{h}"], FX_IC_MIN["main"])
            icp = float(np.nanmean(wp)) if np.isfinite(wp).any() else float("nan")
            ic_h = (float(np.nanmean(w[:half])), float(np.nanmean(w[half:])))
            n_ic = int(np.isfinite(w).sum())
        else:
            ic = ts_ic(s, r)
            icb = [ts_ic(s, r, i) for i in idx] if h == 2 else []
            icp = ts_ic(s, p["T"][f"past_{h}"])
            ic_h = (ts_ic(s[:half], r[:half]), ts_ic(s[half:], r[half:]))
            n_ic = int(np.isfinite(s).any(axis=0).sum())
        out["ic"][str(h)] = {"ic": ic, "ci": ci(icb) if len(icb) else (float("nan"), float("nan")),
                             "half1": ic_h[0], "half2": ic_h[1], "n": n_ic}
        out["coinc"][str(h)] = {"ic_past": icp, "ic_fwd": ic}
        sig = p["T"]["sigma20"]
        pnl = pnl_series(s, r, sig)
        sr = float(pnl.mean() / pnl.std(ddof=1)) if pnl.std(ddof=1) > 0 else float("nan")
        out["pnl"][str(h)] = {"sharpe_ann": sr * math.sqrt(52 / h) if np.isfinite(sr) else float("nan"),
                              "sr_period": sr, "_series": pnl.tolist()}
        if f1 is not None and v != "F1":
            est, c, nn = value_over_trend(s, f1, r, sig, h, idx if h == 2 else None)
            out["vot"][str(h)] = {"beta": est, "ci": c, "n": nn}
    if cls != "FX":
        out["quint"] = quintiles(s, p["T"]["fwd_2"])
    else:
        out["quint"] = quintiles(s, p["T"]["fwd_2"])
    # per unit robustness (h = 2)
    r2 = p["T"]["fwd_2"]
    out["per_unit"] = {u: {"ba": ba_from(call_counts(s[:, [j]], r2[:, [j]]).sum(0))[0],
                           "ic": ts_ic(s[:, [j]], r2[:, [j]])} for j, u in enumerate(p["units"])}
    if cls == "FX":
        out["per_ccy"] = {c: {"ba": ba_from(call_counts(sc[:, [j]], Tc["fwd_2"][:, [j]]).sum(0))[0],
                              "ic": ts_ic(sc[:, [j]], Tc["fwd_2"][:, [j]])} for j, c in enumerate(CCYS)}
        cnt = call_counts(sc, Tc["fwd_2"])
        out["ccy_ba2"] = ba_from(cnt.sum(0))[0]
    return out


def paired_vs(scores, tg, cls, v, ref, weeks_s) -> dict:
    weeks = [pd.Timestamp(w) for w in weeks_s]
    n, h = len(weeks), 2
    half = math.ceil(n / 2)
    p = panel(scores[scores["variant"].isin([v, ref])], tg, cls, weeks)
    sv, sr_, r = p["S"][v], p["S"][ref], p["T"]["fwd_2"]
    cv, cr = call_counts(sv, r), call_counts(sr_, r)
    idx = boot_idx(n, BLOCK[h])
    d = ba_from(cv.sum(0))[0] - ba_from(cr.sum(0))[0]
    db = ba_from(cv[idx].sum(1))[0] - ba_from(cr[idx].sum(1))[0]
    out = {"d_ba": d, "d_ba_ci": ci(db), "p_ba": float((1 + np.sum(~(db > 0))) / (B + 1)),
           "d_ba_h1": ba_from(cv[:half].sum(0))[0] - ba_from(cr[:half].sum(0))[0],
           "d_ba_h2": ba_from(cv[half:].sum(0))[0] - ba_from(cr[half:].sum(0))[0]}
    if cls == "FX":
        sc_v, Tc = _ccy_panel(scores, tg, v, weeks)
        sc_r, _ = _ccy_panel(scores, tg, ref, weeks)
        wv = weekly_xs_ic(sc_v, Tc["fwd_2"], FX_IC_MIN["main"])
        wr = weekly_xs_ic(sc_r, Tc["fwd_2"], FX_IC_MIN["main"])
        icv, icr = float(np.nanmean(wv)), float(np.nanmean(wr))
        dib = np.array([np.nanmean(wv[i]) - np.nanmean(wr[i]) for i in idx])
    else:
        icv, icr = ts_ic(sv, r), ts_ic(sr_, r)
        dib = np.array([ts_ic(sv, r, i) - ts_ic(sr_, r, i) for i in idx])
    out.update({"ic_var": icv, "ic_v3": icr, "d_ic": icv - icr, "d_ic_ci": ci(dib),
                "p_ic": float((1 + np.sum(~(dib > 0))) / (B + 1))})
    return out


def sign_diag(cats: pd.DataFrame, tg: pd.DataFrame, all_weeks) -> dict:
    weeks = [w for w in all_weeks if COMMON_START <= w <= END]
    out = {}
    for (v, view, cat), g in cats.groupby(["variant", "view", "cat"]):
        sc = g.rename(columns={"score": "score"}).assign(variant=v)
        if view == "ccy":
            p = panel(sc[["variant", "week", "view", "unit", "score"]], tg, "CCY", weeks)
            s = p["S"].get(v)
            if s is None:
                continue
            w = weekly_xs_ic(s, p["T"]["fwd_2"], FX_IC_MIN["main"])
            out[f"{v}|FX|{cat}"] = {"ic": float(np.nanmean(w)) if np.isfinite(w).any() else float("nan"),
                                    "weeks": int(np.isfinite(w).sum())}
        else:
            for cls in ("IDX", "MET"):
                p = panel(sc[["variant", "week", "view", "unit", "score"]], tg, cls, weeks)
                s = p["S"].get(v)
                if s is None:
                    continue
                out[f"{v}|{cls}|{cat}"] = {"ic": ts_ic(s, p["T"]["fwd_2"]),
                                           "weeks": int(np.isfinite(s).any(axis=1).sum())}
    return out


# ---------------------------------------------------------------------------
# Long window
# ---------------------------------------------------------------------------

def evaluate_long(scores: pd.DataFrame, tg: pd.DataFrame) -> dict:
    weeks = [w for w in sorted(scores["week"].unique()) if LONG_START <= w <= END]
    n = len(weeks)
    res = {"window": [str(weeks[0]), str(weeks[-1]), n], "classes": {}}
    variants = sorted(scores["variant"].unique())
    for cls in CLASSES:
        R, pv = {}, {}
        for v in variants:
            p = panel(scores[scores["variant"] == v], tg, cls, weeks)
            s = p["S"].get(v)
            if s is None or not np.isfinite(s).any():
                R[v] = {"na": "fără scor în clasă"}
                continue
            first = weeks[int(np.argmax(np.isfinite(s).any(axis=1)))]
            o = {"first_week": str(first), "coverage": float((np.isfinite(s) & (s != 0)).sum() / s.size),
                 "ba": {}, "ic": {}}
            sc, Tc = (_ccy_panel(scores, tg, v, weeks) if cls == "FX" else (None, None))
            for h in HORIZONS:
                r = p["T"][f"fwd_{h}"]
                cnt = call_counts(s, r)
                idx = boot_idx(n, BLOCK[h])
                ba, hr, nc = ba_from(cnt.sum(0))
                o["ba"][str(h)] = {"ba": ba, "hit": hr, "calls": int(nc), "ci": ci(ba_from(cnt[idx].sum(1))[0])}
                if cls == "FX":
                    w = weekly_xs_ic(sc, Tc[f"fwd_{h}"], FX_IC_MIN["long"])
                    icb = [np.nanmean(w[i]) if np.isfinite(w[i]).any() else np.nan for i in idx]
                    o["ic"][str(h)] = {"ic": float(np.nanmean(w)) if np.isfinite(w).any() else float("nan"),
                                       "ci": ci(icb), "n": int(np.isfinite(w).sum())}
                else:
                    icb = [ts_ic(s, r, i) for i in idx] if h == 2 else []
                    o["ic"][str(h)] = {"ic": ts_ic(s, r), "ci": ci(icb) if icb else (float("nan"), float("nan")),
                                       "n": int(np.isfinite(s).any(axis=0).sum())}
            R[v] = o
        # paired Δ BA 2w vs F1, Holm
        if "F1" in R and "na" not in R["F1"]:
            for v in variants:
                if v == "F1" or "na" in R[v]:
                    continue
                p = panel(scores[scores["variant"].isin([v, "F1"])], tg, cls, weeks)
                r = p["T"]["fwd_2"]
                cv, cf = call_counts(p["S"][v], r), call_counts(p["S"]["F1"], r)
                idx = boot_idx(n, BLOCK[2])
                db = ba_from(cv[idx].sum(1))[0] - ba_from(cf[idx].sum(1))[0]
                R[v]["vs_f1"] = {"d_ba": ba_from(cv.sum(0))[0] - ba_from(cf.sum(0))[0], "d_ba_ci": ci(db),
                                 "p": float((1 + np.sum(~(db > 0))) / (B + 1))}
                pv[v] = R[v]["vs_f1"]["p"]
            hp = holm(pv)
            for v in pv:
                R[v]["vs_f1"]["p_holm"] = hp[v]
        res["classes"][cls] = R
    return res


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def csv_rows(main: dict, long: dict | None) -> pd.DataFrame:
    rows = []

    def add(win, cls, v, h, metric, value, lo=None, hi=None, n=None, note=None):
        rows.append({"window": win, "class": cls, "variant": v, "h_weeks": h, "metric": metric,
                     "value": value, "ci_lo": lo, "ci_hi": hi, "n": n, "note": note})

    for cls, R in main["classes"].items():
        for v, o in R.items():
            if "na" in o:
                add("main", cls, v, None, "n/a", None, note=o["na"])
                continue
            win = f"{o['window'][0][:10]}..{o['window'][1][:10]}"
            add("main", cls, v, None, "coverage", o["coverage"], n=o["window"][2], note=win)
            for h in HORIZONS:
                b = o["ba"][str(h)]
                add("main", cls, v, h, "balanced_accuracy", b["ba"], *b["ci"], b["calls"], win)
                add("main", cls, v, h, "hit_rate", b["hit"], n=b["calls"], note=win)
                add("main", cls, v, h, "balanced_accuracy_half1", b["half1"], note=win)
                add("main", cls, v, h, "balanced_accuracy_half2", b["half2"], note=win)
                ic = o["ic"][str(h)]
                add("main", cls, v, h, "ic", ic["ic"], *ic["ci"], ic["n"], win)
                add("main", cls, v, h, "ic_half1", ic["half1"], note=win)
                add("main", cls, v, h, "ic_half2", ic["half2"], note=win)
                add("main", cls, v, h, "ic_past", o["coinc"][str(h)]["ic_past"], note=win)
                for k in ("p55", "p90"):
                    sh = o["strong"][str(h)][k]
                    add("main", cls, v, h, f"strong_hit_{k}", sh["hit"], n=sh["n"], note=f"thr={sh['thr']}")
                pn = o["pnl"][str(h)]
                add("main", cls, v, h, "sharpe_ann", pn["sharpe_ann"], note=win)
                add("main", cls, v, h, "deflated_sharpe_prob", pn.get("dsr"), note=win)
                if str(h) in o["vot"]:
                    vt = o["vot"][str(h)]
                    add("main", cls, v, h, "beta_over_trend", vt["beta"], *vt["ci"], vt["n"], win)
            for q in o["quint"]:
                add("main", cls, v, 2, "quintile_mean_ret", q["mean_ret"], n=q["n"], note=q["bin"])
            for u, x in o["per_unit"].items():
                add("main", cls, v, 2, f"unit_ba:{u}", x["ba"])
                add("main", cls, v, 2, f"unit_ic:{u}", x["ic"])
            for c, x in (o.get("per_ccy") or {}).items():
                add("main", cls, v, 2, f"ccy_ba:{c}", x["ba"])
                add("main", cls, v, 2, f"ccy_ic:{c}", x["ic"])
            if "vs_v3" in o:
                d = o["vs_v3"]
                add("main", cls, v, 2, "d_ba_vs_v3", d["d_ba"], *d["d_ba_ci"], note=f"p={d['p_ba']:.4f}")
                add("main", cls, v, 2, "p_holm_ba_vs_v3", d["p_holm_ba"])
                add("main", cls, v, 2, "d_ba_vs_v3_half1", d["d_ba_h1"])
                add("main", cls, v, 2, "d_ba_vs_v3_half2", d["d_ba_h2"])
                add("main", cls, v, 2, "d_ic_vs_v3", d["d_ic"], *d["d_ic_ci"], note=f"p={d['p_ic']:.4f}")
                add("main", cls, v, 2, "p_holm_ic_vs_v3", d["p_holm_ic"])
                add("main", cls, v, 2, "beats_v3", float(d["beats_v3"]),
                    note=f"c1={d['c1']} c2={d['c2']} c3={d['c3']}")
    for k, x in main["sign_diag"].items():
        v, cls, cat = k.split("|")
        add("main", cls, v, 2, f"category_ic:{cat}", x["ic"], n=x["weeks"])
    if long:
        for cls, R in long["classes"].items():
            for v, o in R.items():
                if "na" in o:
                    add("long", cls, v, None, "n/a", None, note=o["na"])
                    continue
                add("long", cls, v, None, "coverage", o["coverage"], note=f"first={o['first_week'][:10]}")
                for h in HORIZONS:
                    b, ic = o["ba"][str(h)], o["ic"][str(h)]
                    add("long", cls, v, h, "balanced_accuracy", b["ba"], *b["ci"], b["calls"])
                    add("long", cls, v, h, "hit_rate", b["hit"], n=b["calls"])
                    add("long", cls, v, h, "ic", ic["ic"], *ic["ci"], ic["n"])
                if "vs_f1" in o:
                    d = o["vs_f1"]
                    add("long", cls, v, 2, "d_ba_vs_f1", d["d_ba"], *d["d_ba_ci"],
                        note=f"p={d['p']:.4f} p_holm={d.get('p_holm', float('nan')):.4f}")
    return pd.DataFrame(rows)


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "main"
    import scores as SC
    if cmd == "main":
        sc = pd.read_parquet(SC.MAIN)
        cats = pd.read_parquet(SC.CATS)
        tg = targets(sorted(sc["week"].unique()))
        tg.to_parquet(CACHE / "targets_main.parquet", index=False)
        res = evaluate_main(sc, cats, tg)
    else:
        sc = pd.read_parquet(SC.LONG)
        tg = targets([w for w in sorted(sc["week"].unique()) if w <= END])
        tg.to_parquet(CACHE / "targets_long.parquet", index=False)
        res = evaluate_long(sc, tg)
    OUT_JSON[cmd].write_text(json.dumps(res, default=_json_default))
    m = json.loads(OUT_JSON["main"].read_text()) if OUT_JSON["main"].exists() else None
    lg = json.loads(OUT_JSON["long"].read_text()) if OUT_JSON["long"].exists() else None
    if m:
        csv_rows(m, lg).to_csv(CSV, index=False, float_format="%.6g")
    print("ok", cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
