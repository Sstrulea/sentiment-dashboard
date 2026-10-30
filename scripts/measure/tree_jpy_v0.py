"""Measure the JPY tree v0 against the pre-registration scripts/measure/prereg_tree_jpy_v0.yml.

    python scripts/measure/tree_jpy_v0.py [--out-json data/tree_runs/jpy_v0_metrics.json]
                                          [--out-md docs/eval/tree-jpy-v0.md]

Refuses to run while the pre-registration has uncommitted changes (or is not
committed at all). Runs the walk-forward itself (offline, data/tree_inputs.parquet),
then computes forward returns, metrics, baselines and the pre-registered verdicts.
Raw numbers come first; the verdict section only applies the criteria."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tree import schema  # noqa: E402
from src.tree.data import Inputs  # noqa: E402
from src.tree.engine import Engine  # noqa: E402
from src.tree.run import default_asof, walk_forward  # noqa: E402

PREREG = ROOT / "scripts" / "measure" / "prereg_tree_jpy_v0.yml"
PRICE = "DEXJPUS"
END_TOLERANCE_DAYS = 4


# ---------------------------------------------------------------------------
# Pre-registration guard
# ---------------------------------------------------------------------------

def prereg_commit(path: Path = PREREG) -> str:
    rel = str(path.relative_to(ROOT))
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                             capture_output=True).returncode == 0
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT).returncode != 0
    if not tracked or dirty:
        raise SystemExit(f"{rel} is not committed as-is: commit the pre-registration first")
    return subprocess.run(["git", "log", "-1", "--format=%H", "--", rel], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------

def forward_returns(price: pd.Series, asofs: pd.Series, h_weeks: int) -> pd.Series:
    """JPY return in bp = -(ln P[t + 7h] - ln P[t]); P = last fixing on or before the
    day. NaN when the end fixing is stale (> END_TOLERANCE_DAYS) or past the data."""
    price = price.dropna().sort_index()
    idx = price.index.values
    lp = np.log(price.to_numpy(float))
    out = []
    for t in pd.to_datetime(asofs):
        e = t + pd.Timedelta(days=7 * h_weeks)
        i0 = np.searchsorted(idx, np.datetime64(t, "ns"), side="right") - 1
        i1 = np.searchsorted(idx, np.datetime64(e, "ns"), side="right") - 1
        if i0 < 0 or e > price.index[-1] or (e - price.index[i1]).days > END_TOLERANCE_DAYS \
                or (t - price.index[i0]).days > END_TOLERANCE_DAYS:
            out.append(np.nan)
        else:
            out.append(-(lp[i1] - lp[i0]) * 1e4)
    return pd.Series(out, index=asofs.index)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _ic(a: pd.Series, b: pd.Series) -> float:
    m = a.notna() & b.notna()
    if m.sum() < 3 or a[m].nunique() < 2 or b[m].nunique() < 2:
        return float("nan")
    return float(spearmanr(a[m], b[m]).correlation)


def _t_ic(ic: float, n: int) -> float:
    if not np.isfinite(ic) or n < 3 or abs(ic) >= 1:
        return float("nan")
    return float(ic * np.sqrt((n - 2) / (1 - ic * ic)))


def metrics(signed: pd.Series, direction: pd.Series, ret: pd.Series, grid: pd.Series) -> dict:
    """signed conviction, direction, forward return (bp), grid = non-overlapping mask.
    All Series share the index; rows without a return are dropped here."""
    ok = ret.notna()
    s, d, r, g = signed[ok].fillna(0.0), direction[ok].fillna(0).astype(int), ret[ok], grid[ok]
    nd = d != 0
    hit_rows = nd & (r != 0)
    hit = float((np.sign(r[hit_rows]) == d[hit_rows]).mean()) if hit_rows.any() else float("nan")
    dr = d * r
    ic = _ic(s, r)
    gi = g.astype(bool)
    ic_no = _ic(s[gi], r[gi])
    ndg = gi & nd
    x = dr[ndg]
    t_mean = float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else float("nan")
    hr = ndg & (r != 0)
    hit_no = float((np.sign(r[hr]) == d[hr]).mean()) if hr.any() else float("nan")
    t_hit = float((hit_no - 0.5) / np.sqrt(0.25 / hr.sum())) if hr.any() else float("nan")
    half = len(s) // 2
    return {
        "N": int(len(s)), "N_dir": int(nd.sum()), "N_ind": int(gi.sum()), "N_ind_dir": int(ndg.sum()),
        "hit": hit, "mean_bp": float(dr[nd].mean()) if nd.any() else float("nan"),
        "IC": ic, "IC_no": ic_no, "t_IC": _t_ic(ic_no, int(gi.sum())),
        "t_mean": t_mean, "hit_no": hit_no, "t_hit": t_hit,
        "IC_h1": _ic(s.iloc[:half], r.iloc[:half]), "IC_h2": _ic(s.iloc[half:], r.iloc[half:]),
    }


def _dir_from(signed: pd.Series, z_min: float) -> pd.Series:
    return (np.sign(signed) * (signed.abs() >= z_min)).fillna(0).astype(int)


def baselines(wf: pd.DataFrame, ret: pd.Series, grid: pd.Series, z_min: float) -> dict:
    out = {"a_random_walk": {"IC": 0.0, "hit": 0.5, "mean_bp": 0.0, "note": "by definition"}}
    const = pd.Series(-1, index=wf.index)
    out["b_const_jpy_down"] = metrics(const.astype(float), const, ret, grid)
    sc = -wf["us2y_chg_20d__z"].astype(float)
    out["c_us2y_alone"] = metrics(sc.fillna(0.0), _dir_from(sc, z_min), ret, grid)
    sd = -wf["usdjpy_trend_20d__z"].astype(float)
    out["d_trend"] = metrics(sd.fillna(0.0), _dir_from(sd, z_min), ret, grid)
    return out


def best_baseline_ic(b: dict) -> float:
    ics = [0.0] + [b[k]["IC"] for k in ("c_us2y_alone", "d_trend") if np.isfinite(b[k]["IC"])]
    return float(max(ics))


def _same_sign(a: float, b: float) -> bool:
    return np.isfinite(a) and np.isfinite(b) and np.sign(a) == np.sign(b) and a != 0


def neighbours(horizons: list[int], h: int) -> list[int]:
    i = horizons.index(h)
    return [horizons[j] for j in (i - 1, i + 1) if 0 <= j < len(horizons)]


def verdict_signal(m: dict, best_ic: float, ic_by_h: dict, horizons: list[int], h: int) -> dict:
    c = {
        "IC>=0.05": bool(np.isfinite(m["IC"]) and m["IC"] >= 0.05),
        "t_IC>=2": bool(np.isfinite(m["t_IC"]) and m["t_IC"] >= 2),
        "halves_same_sign": _same_sign(m["IC_h1"], m["IC"]) and _same_sign(m["IC_h2"], m["IC"]),
        "IC-best_baseline>=0.03": bool(np.isfinite(m["IC"]) and m["IC"] - best_ic >= 0.03),
        "neighbours_same_sign": all(_same_sign(ic_by_h[n], m["IC"]) for n in neighbours(horizons, h)),
    }
    return {"criteria": c, "pass": all(c.values())}


def _cell_sign(m: dict) -> float:
    return m["IC"] if np.isfinite(m["IC"]) else m["mean_bp"]


def verdict_filter(m: dict, sign_by_h: dict, horizons: list[int], h: int) -> dict:
    c = {
        "N_dir>=20": m["N_dir"] >= 20,
        "hit>=0.58": bool(np.isfinite(m["hit"]) and m["hit"] >= 0.58),
        "t_hit>=2": bool(np.isfinite(m["t_hit"]) and m["t_hit"] >= 2),
        "neighbours_same_sign": all(_same_sign(sign_by_h[n], _cell_sign(m))
                                    for n in neighbours(horizons, h)),
    }
    return {"criteria": c, "pass": all(c.values())}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def measure(wf: pd.DataFrame, price: pd.Series, prereg: dict, z_min: float) -> dict:
    horizons = prereg["target"]["horizons_weeks"]
    regimes = sorted(wf["regime"].unique())
    res = {}
    for wname, w in prereg["walk_forward"]["windows"].items():
        sub = wf[wf["asof"] >= str(w["start"])].reset_index(drop=True)
        pos = pd.Series(np.arange(len(sub)), index=sub.index)
        wres = {}
        for h in horizons:
            ret = forward_returns(price, pd.to_datetime(sub["asof"]), h)
            grid = (pos % h == 0)
            s, d = sub["signed_conviction"].astype(float), sub["direction"].astype(int)
            tot = metrics(s, d, ret, grid)
            cells = {}
            for reg in regimes:
                m = sub["regime"] == reg
                cells[reg] = metrics(s[m], d[m], ret[m], grid[m])
            wres[h] = {"total": tot, "cells": cells,
                       "baselines": baselines(sub, ret, grid, z_min),
                       "window": [sub["asof"].iloc[0], sub.loc[ret.notna(), "asof"].iloc[-1]]}
        ic_by_h = {h: wres[h]["total"]["IC"] for h in horizons}
        for h in horizons:
            b = wres[h]["baselines"]
            wres[h]["best_baseline_ic"] = best_baseline_ic(b)
            wres[h]["verdict_signal"] = verdict_signal(wres[h]["total"], wres[h]["best_baseline_ic"],
                                                       ic_by_h, horizons, h)
            wres[h]["verdict_filter"] = {
                reg: verdict_filter(wres[h]["cells"][reg],
                                    {k: _cell_sign(wres[k]["cells"][reg]) for k in horizons},
                                    horizons, h)
                for reg in regimes}
        res[wname] = wres
    return res


def coverage(inputs: Inputs, glob: dict, tree: dict, wf: pd.DataFrame) -> list[dict]:
    rows = []
    srcs = {n["id"]: n["inputs"] for n in glob["nodes"] + tree["nodes"]}
    for nid in sorted(srcs):
        q = wf[f"{nid}__q"]
        ok = wf.loc[q == "ok", "asof"]
        zok = wf.loc[wf[f"{nid}__z"].notna(), "asof"]
        raw = [s for s in srcs[nid] if inputs.has(s)]
        gaps = []
        for s in raw:
            x = inputs.full(s).index.to_series()
            d = x.diff().dt.days
            gaps.append(f"{s}: max gap {int(d.max())}d, {int((d > 7).sum())} gaps >7d"
                        if len(d) > 1 else f"{s}: n/a")
        rows.append({"id": nid, "inputs": srcs[nid],
                     "first_ok": ok.iloc[0] if len(ok) else None, "first_z": zok.iloc[0] if len(zok) else None,
                     "last_ok": ok.iloc[-1] if len(ok) else None,
                     "weeks_missing": int((q == "missing").sum()), "weeks": int(len(q)),
                     "gaps": "; ".join(gaps)})
    return rows


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def markdown(out: dict) -> str:
    L = [f"# JPY tree v0 — measurement (pre-registration {out['prereg_commit'][:8]})", "",
         f"Generated by scripts/measure/tree_jpy_v0.py; inputs fingerprint {out['inputs_fingerprint']}, "
         f"config hash {out['config_hash']}. Raw numbers; verdicts at the end.", ""]
    hs = out["horizons"]
    order = [out["reported_first"]] + [h for h in hs if h != out["reported_first"]]
    for wname, w in out["windows"].items():
        L += [f"## Window {wname} ({w[str(order[0])]['window'][0]} → last asof with a return)", ""]
        L += ["| h (w) | N | N_ind | N_dir | hit | mean bp | IC | IC_no | t_IC | t_mean | IC 1st half | IC 2nd half | best baseline IC |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in order:
            m = w[str(h)]["total"]
            L.append(f"| {h} | {m['N']} | {m['N_ind']} | {m['N_dir']} | {_f(m['hit'])} | {_f(m['mean_bp'], 1)} | "
                     f"{_f(m['IC'])} | {_f(m['IC_no'])} | {_f(m['t_IC'], 2)} | {_f(m['t_mean'], 2)} | "
                     f"{_f(m['IC_h1'])} | {_f(m['IC_h2'])} | {_f(w[str(h)]['best_baseline_ic'])} |")
        L += ["", "Baselines:", "",
              "| h | baseline | N_dir | hit | mean bp | IC | t_IC |", "|---|---|---|---|---|---|---|"]
        for h in order:
            for bn, b in w[str(h)]["baselines"].items():
                L.append(f"| {h} | {bn} | {b.get('N_dir', '—')} | {_f(b['hit'])} | {_f(b['mean_bp'], 1)} | "
                         f"{_f(b['IC'])} | {_f(b.get('t_IC'), 2)} |")
        L += ["", "Regime cells:", "",
              "| h | cell | N | N_ind | N_dir | N_ind_dir | hit | hit_no | t_hit | mean bp | t_mean | IC |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in order:
            for reg, m in w[str(h)]["cells"].items():
                L.append(f"| {h} | {reg} | {m['N']} | {m['N_ind']} | {m['N_dir']} | {m['N_ind_dir']} | "
                         f"{_f(m['hit'])} | {_f(m['hit_no'])} | {_f(m['t_hit'], 2)} | {_f(m['mean_bp'], 1)} | "
                         f"{_f(m['t_mean'], 2)} | {_f(m['IC'])} |")
        L.append("")
    L += [f"## Verdicts (primary window: {out['primary_window']})", ""]
    pw = out["windows"][out["primary_window"]]
    for h in order:
        v = pw[str(h)]["verdict_signal"]
        failed = [k for k, ok in v["criteria"].items() if not ok]
        L.append(f"- {h}w signal: {'PASS' if v['pass'] else 'fail'}"
                 + (f" (fails: {', '.join(failed)})" if failed else ""))
        for reg, fv in pw[str(h)]["verdict_filter"].items():
            if fv["pass"]:
                L.append(f"  - cell {reg}: filter PASS")
    L.append("")
    return "\n".join(L)


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", type=Path, default=ROOT / "data" / "tree_runs" / "jpy_v0_metrics.json")
    ap.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval" / "tree-jpy-v0.md")
    a = ap.parse_args()
    commit = prereg_commit()
    prereg = yaml.safe_load(PREREG.read_text())
    glob, tree = schema.load("jpy")
    inputs = Inputs.load()
    engine = Engine(glob, inputs)
    t0 = time.time()
    start = min(str(w["start"]) for w in prereg["walk_forward"]["windows"].values())
    wf = walk_forward(engine, tree, start, default_asof(inputs))
    t_wf = time.time() - t0
    res = measure(wf, inputs.full(PRICE), prereg, float(tree["decision"]["z_min"]))
    wf["year"] = wf["asof"].str[:4]
    out = {
        "prereg_commit": commit, "config_hash": engine.run(tree, wf["asof"].iloc[-1])["config_hash"],
        "inputs_fingerprint": inputs.fingerprint, "horizons": prereg["target"]["horizons_weeks"],
        "reported_first": prereg["target"]["reported_first"],
        "primary_window": prereg["walk_forward"]["primary_window"],
        "walk_forward_seconds": round(t_wf, 1),
        "regime_by_year": pd.crosstab(wf["year"], wf["regime"]).to_dict(orient="index"),
        "degraded_by_year": wf.groupby("year")["degraded"].sum().astype(int).to_dict(),
        "coverage": coverage(inputs, glob, tree, wf),
        "windows": res,
    }
    out = _clean(out)
    a.out_json.parent.mkdir(parents=True, exist_ok=True)
    a.out_json.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    a.out_md.parent.mkdir(parents=True, exist_ok=True)
    a.out_md.write_text(markdown(out))
    print(f"prereg {commit}; walk-forward {len(wf)} asofs in {t_wf:.1f}s -> {a.out_json}, {a.out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
