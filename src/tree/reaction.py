"""Reaction layer: how each currency's 2Y and FX react to data, as of a date.

Inputs: data/backbone_rates.parquet (scripts/fetch_backbone.py) and
data/reaction_events.parquet (scripts/build_reaction_events.py). Every node reads
only rows with avail <= asof; forward returns never enter a node. Rules and
numbers are pre-registered in scripts/measure/prereg_reaction_v0.yml.

beta_data_rates   joint Huber regression, per currency, on event days (2Y close
                  days that hold a scored release): d2y ~ 1 + sum_i beta_i z_raw_i
                  + gamma us_d2y (non-USD). Sample = the days of the last 24
                  scored releases of every indicator. beta_i is contracted to the
                  category mean: w beta_i + (1 - w) beta_cat, w = n_i / (n_i + 12),
                  beta_cat = n-weighted mean of the raw betas of the category
                  (indicators with n_i >= 6); n_i < 6 -> beta_cat alone.
digest_rates_4w   day residual = d2y - fitted (betas as of the release day, so the
                  day itself is out of sample); sum over the last 28 days, divided
                  by the std of the same sum on the 52 previous Fridays.
beta_rates_fx     OLS of the daily basket return on d(2Y_c - mean 2Y of the others
                  in Y2_SET), trailing 365 days (vs-USD: on d(2Y_c - 2Y_USD)).
digest_fx_Nd      sum of (basket return - beta x relative 2Y move) over the last N
                  fixings, divided by the std of the same N-day sum over 365 days.
gnbp_4w / bngp_4w good news / bad price (and the reverse), last 28 days.
focus_map         shares of sum |contracted beta| by category; focus_shift_labor_3m.
event_risk_2w     sum |beta| x std(z_raw, last 12) over the releases due in 14 days.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .backbone import CCYS, Backbone, basket
from .data import Inputs
from .nodes import NodeContext, NodeResult, node

ROOT = Path(__file__).resolve().parents[2]
EVENTS_FILE = ROOT / "data" / "reaction_events.parquet"
CATEGORIES = ["Inflation", "Labor", "Growth", "Rates"]
Y2_SET = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]    # daily, as-of usable 2Y (NZD none, CHF monthly)

# pre-registered constants (prereg_reaction_v0.yml)
WINDOW_RELEASES = 24
SHRINK_K = 12
MIN_N_INDICATOR = 6
MIN_DAYS_EXTRA = 10            # regression needs n_days >= n_params + 10
HUBER_C = 1.345
DIGEST_DAYS = 28
STD_WEEKS = 52
STD_MIN = 26
FX_BETA_DAYS = 365
FX_MIN_OBS = 150
FLAG_Z = 1.0


# ---------------------------------------------------------------------------
# Robust regression
# ---------------------------------------------------------------------------

def huber(X: np.ndarray, y: np.ndarray, c: float = HUBER_C, maxit: int = 100, tol: float = 1e-8):
    """Huber M-estimate by IRLS (scale = MAD of residuals, re-estimated each step).
    -> (beta, se); se from Huber's asymptotic covariance (1981, eq. 7.10, no
    small-sample K factor). Raises ValueError on a rank-deficient X."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    if np.linalg.matrix_rank(X) < p:
        raise ValueError("rank-deficient design")
    with np.errstate(all="ignore"):           # Accelerate BLAS raises spurious matmul warnings
        return _huber(X, y, c, maxit, tol, n, p)


def _huber(X, y, c, maxit, tol, n, p):
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    s = 1.0
    for _ in range(maxit):
        r = y - X @ beta
        s = np.median(np.abs(r - np.median(r))) / 0.6745
        if s <= 1e-12:
            s = max(float(np.std(r)), 1e-12)
        u = r / s
        w = np.where(np.abs(u) <= c, 1.0, c / np.maximum(np.abs(u), 1e-12))
        Xw = X * w[:, None]
        new = np.linalg.solve(X.T @ Xw, Xw.T @ y)
        done = np.max(np.abs(new - beta)) < tol * (1 + np.max(np.abs(beta)))
        beta = new
        if done:
            break
    if not np.all(np.isfinite(beta)):
        raise ValueError("non-finite Huber estimate")
    u = (y - X @ beta) / s
    psi = np.clip(u, -c, c)
    dpsi = (np.abs(u) <= c).astype(float)
    m = dpsi.mean()
    if m <= 0 or n <= p:
        return beta, np.full(p, np.nan)
    cov = (np.sum(psi ** 2) / (n - p)) / m ** 2 * s ** 2 * np.linalg.inv(X.T @ X)
    return beta, np.sqrt(np.clip(np.diag(cov), 0, None))


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, int]:
    """y = a + b x -> (b, t_b, n)."""
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 3 or np.var(x) == 0:
        return float("nan"), float("nan"), n
    X = np.column_stack([np.ones(n), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ b
    s2 = r @ r / (n - 2)
    se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
    return float(b[1]), float(b[1] / se) if se > 0 else float("nan"), n


def contract(raw: dict[str, tuple[float, float, int]], cats: dict[str, str], dirs: dict[str, float],
             k: float = SHRINK_K, min_n: int = MIN_N_INDICATOR) -> dict[str, dict]:
    """raw {indicator: (beta, se, n)} -> {indicator: {beta, raw, se, n, w, beta_cat, category}}.

    The contraction runs in polarity-aligned space (a = direction x beta, so an
    unemployment beta and a payrolls beta share one category prior); the result
    is mapped back (beta = direction x a). The raw sign is estimated freely.
    beta_cat is reported in the indicator's own space (direction x a_cat)."""
    by_cat: dict[str, list] = {}
    for i, (b, _, n) in raw.items():
        if n >= min_n and np.isfinite(b):
            by_cat.setdefault(cats[i], []).append((dirs[i] * b, n))
    acat = {c: sum(a * n for a, n in v) / sum(n for _, n in v) for c, v in by_cat.items()}
    out = {}
    for i, (b, se, n) in raw.items():
        c, d = cats[i], dirs[i]
        if c not in acat:
            continue
        if n >= min_n and np.isfinite(b):
            w = n / (n + k)
            a = w * d * b + (1 - w) * acat[c]
        else:
            w, a = 0.0, acat[c]
        out[i] = {"beta": d * a, "raw": b, "se": se, "n": n, "w": w, "beta_cat": d * acat[c],
                  "category": c, "direction": d}
    return out


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

class ReactionInputs(Inputs):
    """Backbone series (Inputs: series/date/avail/value) plus the event table and
    the derived daily frames. All slicing is by avail <= asof."""

    def __init__(self, backbone: pd.DataFrame, events: pd.DataFrame):
        super().__init__(backbone[["series", "date", "avail", "value"]])
        self.bb = Backbone(backbone)
        ev = events.copy()
        ev["release_dt"] = pd.to_datetime(ev["release_dt"])
        ev["avail"] = pd.to_datetime(ev["avail"])
        self.events_all = ev.sort_values(["avail", "release_dt"]).reset_index(drop=True)
        self._cache: dict = {}
        lv = self.bb.fx_levels()
        self.fx_usd = lv.diff()
        self.fx_basket = basket(lv).diff()
        self.fx_avail = lv.index + pd.Timedelta(days=1)
        y2 = pd.DataFrame({c: self.bb.by[f"Y2_{c}"] for c in Y2_SET if self.bb.has(f"Y2_{c}")})
        self.y2_daily = y2

    @classmethod
    def load(cls, backbone: Path | None = None, events: Path = EVENTS_FILE) -> "ReactionInputs":
        from .backbone import BACKBONE_FILE
        return cls(pd.read_parquet(backbone or BACKBONE_FILE), pd.read_parquet(events))

    def events(self, ccy: str, asof: pd.Timestamp) -> pd.DataFrame:
        e = self.events_all
        n = int(np.searchsorted(e["avail"].values, np.datetime64(pd.Timestamp(asof), "ns"), side="right"))
        e = e.iloc[:n]
        return e[(e["ccy"] == ccy) & e["released"]]

    def schedule(self, ccy: str) -> pd.DataFrame:
        e = self.events_all
        return e[e["ccy"] == ccy]

    # --- betas ----------------------------------------------------------------
    def betas(self, ccy: str, asof: pd.Timestamp) -> dict | None:
        key = ("betas", ccy, pd.Timestamp(asof).normalize())
        if key not in self._cache:
            self._cache[key] = self._betas(ccy, pd.Timestamp(asof).normalize())
        return self._cache[key]

    def _betas(self, ccy: str, asof: pd.Timestamp) -> dict | None:
        ev = self.events(ccy, asof)
        ev = ev[ev["z_raw"].notna() & ev["d2y"].notna() & ev["y2_date"].notna()]
        if ev.empty:
            return None
        keep = ev.sort_values("release_dt").groupby("indicator_key").tail(WINDOW_RELEASES)
        days = np.sort(keep["y2_date"].unique())
        ev = ev[ev["y2_date"].isin(days)]
        all_inds = sorted(ev["indicator_key"].unique())
        Z = ev.pivot_table(index="y2_date", columns="indicator_key", values="z_raw", aggfunc="sum").reindex(
            index=days, columns=all_inds).fillna(0.0)
        n_all = (Z != 0).sum().to_dict()
        inds = [i for i in all_inds if n_all[i] >= MIN_N_INDICATOR]   # thinner ones: category beta only
        if not inds:
            return None
        day = ev.groupby("y2_date").agg(d2y=("d2y", "first"), us=("us_d2y", "first")).reindex(days)
        cols = [np.ones(len(days))] + [Z[i].to_numpy() for i in inds]
        names = ["const"] + inds
        if ccy != "USD":
            ok = day["us"].notna().to_numpy()
            Z, day = Z[ok], day[ok]
            cols = [c[ok] for c in cols] + [day["us"].to_numpy()]
            names = names + ["us_d2y"]
        X = np.column_stack(cols)
        y = day["d2y"].to_numpy(float)
        if len(y) < X.shape[1] + MIN_DAYS_EXTRA:
            return None
        try:
            b, se = huber(X, y)
        except ValueError:
            return None
        n_i = (Z != 0).sum().to_dict()
        meta = ev.drop_duplicates("indicator_key").set_index("indicator_key")
        cats, dirs = meta["category"].to_dict(), meta["direction"].astype(float).to_dict()
        raw = {i: (float(b[names.index(i)]), float(se[names.index(i)]), int(n_i[i])) for i in inds}
        raw.update({i: (float("nan"), float("nan"), int(n_i[i])) for i in all_inds if i not in inds})
        con = contract(raw, cats, dirs)
        return {"indicators": con, "const": float(b[0]),
                "gamma": float(b[names.index("us_d2y")]) if "us_d2y" in names else 0.0,
                "n_days": int(len(y)), "last_avail": ev["avail"].max()}

    # --- day residuals (each with betas as of its release day) ----------------
    def day_residuals(self, ccy: str, asof: pd.Timestamp) -> pd.DataFrame:
        """One row per 2Y close day with scored releases and avail <= asof:
        y2_date, avail, resid (bp). A day's residual uses the betas as of its
        own release day only, so it does not depend on asof: computed once for
        every day, then cut at avail <= asof."""
        k = ("resid_all", ccy)
        if k not in self._cache:
            ev = self.events_all
            ev = ev[(ev["ccy"] == ccy) & ev["released"] & ev["d2y"].notna() & ev["y2_date"].notna()]
            rows = []
            for d, g in ev.groupby("y2_date", sort=True):
                r = self._resid(ccy, g)
                if r is not None:
                    rows.append({"y2_date": d, "avail": g["avail"].max(), "resid": r,
                                 "release_dt": g["release_dt"].min()})
            self._cache[k] = pd.DataFrame(rows, columns=["y2_date", "avail", "resid", "release_dt"])
        r = self._cache[k]
        return r[r["avail"] <= pd.Timestamp(asof)]

    def _resid(self, ccy: str, g: pd.DataFrame) -> float | None:
        b = self.betas(ccy, g["release_dt"].min().normalize())
        if b is None:
            return None
        fit = b["const"] + b["gamma"] * (0.0 if ccy == "USD" else float(np.nan_to_num(g["us_d2y"].iloc[0])))
        for i, z in zip(g["indicator_key"], g["z_raw"]):
            if pd.notna(z) and i in b["indicators"]:
                fit += b["indicators"][i]["beta"] * z
        return float(g["d2y"].iloc[0] - fit)

    def digest_rates(self, ccy: str, asof: pd.Timestamp) -> float | None:
        r = self.day_residuals(ccy, asof)
        r = r[r["avail"] <= asof]
        if r.empty:
            return None
        lo = asof - pd.Timedelta(days=DIGEST_DAYS)
        return float(r.loc[r["release_dt"] > lo, "resid"].sum())

    # --- FX ---------------------------------------------------------------------
    def rel_y2(self, ccy: str, vs: str = "basket") -> pd.Series:
        y = self.y2_daily
        if ccy not in y.columns:
            return pd.Series(dtype=float)
        if vs == "usd":
            rel = y[ccy] - y["USD"] if ccy != "USD" else pd.Series(dtype=float)
        else:
            others = [c for c in Y2_SET if c != ccy and c in y.columns]
            rel = y[ccy] - y[others].mean(axis=1, skipna=False)
        return rel.dropna().diff() * 100.0

    def fx_frame(self, ccy: str, asof: pd.Timestamp, vs: str = "basket") -> pd.DataFrame:
        """Daily (return bp, relative 2Y move bp) on common dates, avail <= asof."""
        ret = (self.fx_basket if vs == "basket" else self.fx_usd)[ccy] * 1e4
        ret = ret[self.fx_avail <= asof]
        rel = self.rel_y2(ccy, vs)
        rel = rel[rel.index + pd.Timedelta(days=1) <= asof]
        return pd.concat([ret.rename("ret"), rel.rename("rel")], axis=1, join="inner").dropna()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _ri(ctx: NodeContext) -> ReactionInputs:
    if not isinstance(ctx.inputs, ReactionInputs):
        raise TypeError("reaction nodes need ReactionInputs")
    return ctx.inputs


def _blocked(ctx: NodeContext):
    reason = (ctx.params.get("missing_by_ccy") or {}).get(ctx.params["ccy"])
    return ctx.missing(reason) if reason else None


@node("reaction_beta_data_rates")
def beta_data_rates(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    b = _ri(ctx).betas(ctx.params["ccy"], ctx.asof)
    if b is None:
        return ctx.missing("too few scored event days for the regression")
    by_cat = {}
    for i, v in b["indicators"].items():
        by_cat.setdefault(v["category"], []).append(v["beta"])
    meta = {"indicators": {i: {k: (round(x, 6) if isinstance(x, float) else x) for k, x in v.items()}
                           for i, v in sorted(b["indicators"].items())},
            "category_mean_beta": {c: round(float(np.mean(v)), 6) for c, v in sorted(by_cat.items())},
            "gamma_us": round(b["gamma"], 6), "const": round(b["const"], 6), "n_days": b["n_days"]}
    total = sum(abs(v["beta"]) for v in b["indicators"].values())
    return ctx.ok(total, None, b["last_avail"] - pd.Timedelta(days=1), **meta)


@node("reaction_digest_rates")
def digest_rates(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    v = ri.digest_rates(ccy, ctx.asof)
    if v is None:
        return ctx.missing("no residuals yet")
    hist = [ri.digest_rates(ccy, ctx.asof - pd.Timedelta(weeks=k)) for k in range(1, STD_WEEKS + 1)]
    hist = [h for h in hist if h is not None]
    r = ri.day_residuals(ccy, ctx.asof)
    last = r["avail"].max() - pd.Timedelta(days=1) if not r.empty else None
    if len(hist) < STD_MIN or np.std(hist, ddof=1) == 0:
        return ctx.ok(v, None, last, z_note=f"{len(hist)} weekly values for the std")
    return ctx.ok(v, v / float(np.std(hist, ddof=1)), last, n_resid=int(len(r)))


@node("reaction_beta_rates_fx")
def beta_rates_fx(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    f = ri.fx_frame(ccy, ctx.asof, "basket")
    f = f[f.index > ctx.asof - pd.Timedelta(days=FX_BETA_DAYS)]
    if len(f) < FX_MIN_OBS:
        return ctx.missing(f"{len(f)} common daily obs < {FX_MIN_OBS}")
    b, t, n = ols(f["rel"].to_numpy(), f["ret"].to_numpy())
    meta = {"t": round(t, 4), "n": n}
    if ccy != "USD":
        u = ri.fx_frame(ccy, ctx.asof, "usd")
        u = u[u.index > ctx.asof - pd.Timedelta(days=FX_BETA_DAYS)]
        bu, tu, nu = ols(u["rel"].to_numpy(), u["ret"].to_numpy())
        meta.update(beta_usd=round(bu, 6), t_usd=round(tu, 4), n_usd=nu)
    return ctx.ok(b, None, f.index[-1], **meta)


@node("reaction_digest_fx")
def digest_fx(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    beta = ctx.dep(ctx.spec["inputs"][0])
    if beta.missing:
        return ctx.missing("beta_rates_fx missing")
    ri, ccy, n = _ri(ctx), ctx.params["ccy"], int(ctx.params["days"])
    f = ri.fx_frame(ccy, ctx.asof, "basket")
    f = f[f.index > ctx.asof - pd.Timedelta(days=FX_BETA_DAYS + 40)]
    e = f["ret"] - beta.value * f["rel"]
    roll = e.rolling(n, min_periods=n).sum().dropna()
    if roll.empty:
        return ctx.missing("short history")
    v = float(roll.iloc[-1])
    w = roll[roll.index > roll.index[-1] - pd.Timedelta(days=FX_BETA_DAYS)]
    sd = float(w.std(ddof=1)) if len(w) > 20 else float("nan")
    return ctx.ok(v, v / sd if sd and np.isfinite(sd) and sd > 0 else None, f.index[-1])


@node("reaction_news_price_flag")
def news_price_flag(ctx: NodeContext) -> NodeResult:
    """kind gnbp: favourable z_signed >= +1 with residual <= 0 OR basket move <= 0;
    kind bngp: unfavourable z_signed <= -1 with residual >= 0 OR basket move >= 0.
    value = count over the last 28 days, meta.intensity = sum |z|."""
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    ev = ri.events(ccy, ctx.asof)
    if ev.empty:
        return ctx.missing("no events yet")
    ev = ev[ev["release_dt"] > ctx.asof - pd.Timedelta(days=DIGEST_DAYS)]
    res = ri.day_residuals(ccy, ctx.asof).set_index("y2_date")["resid"]
    gn = ctx.params["kind"] == "gnbp"
    cnt, inten, hits = 0, 0.0, []
    for r in ev.itertuples(index=False):
        z = r.z_signed
        if pd.isna(z) or (z < FLAG_Z if gn else z > -FLAG_Z):
            continue
        rr = res.get(r.y2_date, np.nan) if pd.notna(r.y2_date) else np.nan
        fx = r.fx_basket
        bad = ((pd.notna(rr) and (rr <= 0 if gn else rr >= 0))
               or (pd.notna(fx) and (fx <= 0 if gn else fx >= 0)))
        if bad:
            cnt += 1
            inten += abs(z)
            hits.append(r.event_id)
    last = ri.events(ccy, ctx.asof)["avail"].max() - pd.Timedelta(days=1)
    return ctx.ok(cnt, None, last, intensity=round(inten, 6), events=hits)


def _shares(b: dict | None) -> dict | None:
    if b is None:
        return None
    tot = {c: 0.0 for c in CATEGORIES}
    for v in b["indicators"].values():
        tot[v["category"]] += abs(v["beta"])
    s = sum(tot.values())
    return None if s == 0 else {c: tot[c] / s for c in CATEGORIES}


@node("reaction_focus_map")
def focus_map(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    sh = _shares(ri.betas(ccy, ctx.asof))
    if sh is None:
        return ctx.missing("no betas")
    return ctx.ok(sh["Labor"], None, ctx.asof - pd.Timedelta(days=1),
                  shares={c: round(v, 6) for c, v in sh.items()})


@node("reaction_focus_shift")
def focus_shift(ctx: NodeContext) -> NodeResult:
    if (m := _blocked(ctx)) is not None:
        return m
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    cat, days = ctx.params.get("category", "Labor"), int(ctx.params.get("days", 91))
    now, then = _shares(ri.betas(ccy, ctx.asof)), _shares(ri.betas(ccy, ctx.asof - pd.Timedelta(days=days)))
    if now is None or then is None:
        return ctx.missing("no betas at one end")
    return ctx.ok(now[cat] - then[cat], None, ctx.asof - pd.Timedelta(days=1),
                  now=round(now[cat], 6), then=round(then[cat], 6))


@node("reaction_event_risk")
def event_risk(ctx: NodeContext) -> NodeResult:
    """Week 1: releases on the calendar in (asof, asof + 7d]; week 2: the next
    release expected from the indicator's cadence (last release + median gap of
    the last 12) in (asof + 7d, asof + 14d]."""
    if (m := _blocked(ctx)) is not None:
        return m
    ri, ccy = _ri(ctx), ctx.params["ccy"]
    b = ri.betas(ccy, ctx.asof)
    if b is None:
        return ctx.missing("no betas")
    sch = ri.schedule(ccy)
    w1 = sch[(sch["release_dt"] > ctx.asof) & (sch["release_dt"] <= ctx.asof + pd.Timedelta(days=7))]
    past = sch[sch["release_dt"] <= ctx.asof]
    due = {i: 1 for i in w1["indicator_key"]}
    for i, g in past.groupby("indicator_key"):
        t = g["release_dt"].sort_values().tail(13)
        if len(t) < 3:
            continue
        nxt = t.iloc[-1] + t.diff().dropna().median()
        if ctx.asof + pd.Timedelta(days=7) < nxt <= ctx.asof + pd.Timedelta(days=14):
            due[i] = due.get(i, 0) + 1
    ev = ri.events(ccy, ctx.asof)
    total, parts = 0.0, {}
    for i, k in sorted(due.items()):
        if i not in b["indicators"]:
            continue
        zs = ev.loc[ev["indicator_key"] == i, "z_raw"].dropna().tail(12)
        sd = float(zs.std(ddof=1)) if len(zs) >= 3 else 1.0
        parts[i] = round(abs(b["indicators"][i]["beta"]) * sd * k, 6)
        total += parts[i]
    return ctx.ok(total, None, ctx.asof - pd.Timedelta(days=1), due=parts)


# ---------------------------------------------------------------------------
# Layer runner
# ---------------------------------------------------------------------------

def currency_specs(layer: dict, ccy: str) -> list[dict]:
    out = []
    for n in layer["nodes"]:
        spec = {**n, "params": {**(n.get("params") or {}), "ccy": ccy}}
        spec["params"]["missing_by_ccy"] = layer.get("missing_by_ccy", {}).get(n["id"], {})
        out.append(spec)
    return out


def snapshot(layer: dict, ri: ReactionInputs, asof) -> dict:
    from .engine import eval_nodes
    asof = pd.Timestamp(asof).normalize()
    per = {}
    for ccy in layer["currencies"]:
        res = eval_nodes(currency_specs(layer, ccy), ri, asof)
        per[ccy] = {k: res[k].to_dict() for k in sorted(res)}
    return {"layer_id": layer["layer_id"], "asof": asof.strftime("%Y-%m-%d"), "status": "uncalibrated",
            "currencies": per, "inputs_fingerprint": ri.fingerprint}


def panel(layer: dict, ri: ReactionInputs, asofs) -> pd.DataFrame:
    """Long walk-forward frame: asof, ccy, node, value, z, quality, reason."""
    from .engine import eval_nodes
    rows = []
    for t in asofs:
        t = pd.Timestamp(t).normalize()
        for ccy in layer["currencies"]:
            for nid, r in eval_nodes(currency_specs(layer, ccy), ri, t).items():
                rows.append({"asof": t, "ccy": ccy, "node": nid, "value": r.value, "z": r.z,
                             "quality": r.quality, "reason": r.meta.get("reason"),
                             "intensity": r.meta.get("intensity")})
    return pd.DataFrame(rows)
