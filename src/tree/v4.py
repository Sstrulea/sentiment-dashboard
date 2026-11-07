"""Phase 4: weekly as-of panels (8 currencies, Fridays) and the v4 score.

Everything a Friday t sees is dated before t (FX / rates: fixing or close date <= t - 1 day;
COT / TFF: report Tuesday + 6 days <= t; REER: period end + 25 days <= t; v3 reconstruction:
the value of the PREVIOUS Friday, because the reconstruction is stamped Friday 21:00 UTC, after
the noon fixing where forward returns start).

Panels (DataFrame Friday x currency)
  carry          BIS policy rate; carry_z = cross-sectional z of its rank
  macro_v3       v3 macro block (B2) of the previous Friday; macro_z = cross-sectional z
  crowding       sum_c (carry rank_c - mean rank) x z_c(non-commercial net / OI, own 156-week
                 window, >= 104), and its percentile in the previous 260 Fridays (>= 104)
  ms / ms_lite   market-style score: z(surprises) + z(COT pct) + z(13-week momentum sign) /
                 z(COT pct) + z(momentum sign); surprises = per catalog indicator the mean of
                 sign(direction x (actual - consensus)) over its last 12 releases, summed
  tff_am / tff_lev  -(percentile of asset-manager / leveraged-fund net / OI, 156 weeks, >= 104)
  reer_value     -(ln REER - mean ln REER of the previous 60 months, >= 36)
v4.0 = carry_z (x (1 - crowding pct) in liquidity_stress) + macro_z (0 before the reconstruction).
Calibration: logistic P(basket return > 0) walk-forward, regime-specific when the regime has
>= 100 training rows, else global; confidence halved (P -> 0.5 + (P - 0.5) / 2) outside calm;
abstain when 0.45 <= P <= 0.55. Concentration: share of the top-2 / bottom-2 portfolio variance
on PC1 of the 7 vs-USD daily returns (trailing 365 days); flag >= 0.60."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .backbone import Backbone
from .regimes import RegimeV1

ROOT = Path(__file__).resolve().parents[2]
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
P_ABSTAIN = (0.45, 0.55)
CROWD_CUT = 70.0
CONC_FLAG = 0.60
MIN_TRAIN = {"long": 156, "short": 52}


def cs_z(row: pd.Series) -> pd.Series:
    x = row.astype(float)
    sd = x.std(ddof=1)
    return (x - x.mean()) / sd if sd and np.isfinite(sd) and sd > 0 else x * np.nan


def rank_z(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1).apply(cs_z, axis=1)


def asof_frame(series: dict[str, pd.Series], avail_lag: pd.Timedelta, fridays) -> pd.DataFrame:
    """Value of the last observation with date + lag <= t, per currency."""
    out = {}
    for c, s in series.items():
        s = s.dropna().sort_index()
        idx = s.index.searchsorted(fridays - avail_lag, side="right") - 1
        v = s.to_numpy()[np.clip(idx, 0, None)] if len(s) else np.full(len(fridays), np.nan)
        out[c] = np.where(idx >= 0, v, np.nan)
    return pd.DataFrame(out, index=fridays).reindex(columns=CCYS)


def rolling_pct(s: pd.Series, n: int, min_n: int) -> pd.Series:
    vals = s.to_numpy(float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        w = vals[max(0, i - n + 1): i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= min_n and np.isfinite(vals[i]):
            out[i] = 100.0 * (w <= vals[i]).sum() / len(w)
    return pd.Series(out, index=s.index)


def rolling_z(s: pd.Series, n: int, min_n: int) -> pd.Series:
    m = s.rolling(n, min_periods=min_n).mean()
    sd = s.rolling(n, min_periods=min_n).std(ddof=1)
    return (s - m) / sd


def logistic(x: np.ndarray, y: np.ndarray, iters: int = 25) -> np.ndarray:
    """1-feature logistic regression by Newton (ridge 1e-6); returns (a, b)."""
    with np.errstate(all="ignore"):           # Accelerate BLAS raises spurious matmul warnings
        return _logistic(x, y, iters)


def _logistic(x, y, iters):
    X = np.column_stack([np.ones(len(x)), x])
    w = np.zeros(2)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (y - p) - 1e-6 * w
        H = -(X.T * (p * (1 - p))) @ X - 1e-6 * np.eye(2)
        step = np.linalg.solve(H, g)
        w = w - step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


class Phase4:
    def __init__(self, root: Path = ROOT, fridays=None):
        self.bb = Backbone.load()
        self.lv_usd = self.bb.fx_levels()
        self.basket = self.bb.basket_levels()
        last = self.basket.index[-1]
        self.fridays = fridays if fridays is not None else pd.date_range("2000-01-07", last + pd.Timedelta(days=7), freq="7D")
        self.legacy = pd.read_parquet(root / "data" / "cftc_legacy.parquet")
        self.tff = pd.read_parquet(root / "data" / "cftc_tff.parquet")
        self.reer = pd.read_parquet(root / "data" / "bis_reer.parquet")
        self.v3 = pd.read_parquet(root / "data" / "v3_reconstruction.parquet")
        self.events = pd.read_parquet(root / "data" / "reaction_events.parquet")
        self.rv = RegimeV1()
        self._c: dict = {}

    def memo(self, k, f):
        if k not in self._c:
            self._c[k] = f()
        return self._c[k]

    # --- inputs -----------------------------------------------------------------
    def regime(self) -> pd.Series:
        return self.memo("regime", lambda: pd.Series([self.rv.label(t) for t in self.fridays], index=self.fridays))

    def carry(self) -> pd.DataFrame:
        return self.memo("carry", lambda: asof_frame({c: self.bb.by[f"POL_{c}"] for c in CCYS},
                                                     pd.Timedelta(days=1), self.fridays))

    def carry_z(self) -> pd.DataFrame:
        return self.memo("carry_z", lambda: rank_z(self.carry()))

    def v3_panel(self, variant: str, lag_weeks: int = 1) -> pd.DataFrame:
        def f():
            v = self.v3[self.v3["variant"] == variant].pivot(index="asof", columns="ccy", values="score")
            v.index = v.index + pd.Timedelta(weeks=lag_weeks)
            return v.reindex(self.fridays).reindex(columns=CCYS)
        return self.memo(("v3", variant, lag_weeks), f)

    def macro_z(self) -> pd.DataFrame:
        return self.memo("macro_z", lambda: self.v3_panel("B2").apply(cs_z, axis=1))

    def _cot_ratio(self, df: pd.DataFrame, long: str, short: str) -> dict[str, pd.Series]:
        out = {}
        for c, g in df.groupby("ccy"):
            g = g.sort_values("date")
            out[c] = pd.Series(((g[long] - g[short]) / g["open_interest_all"]).to_numpy(float),
                               index=pd.DatetimeIndex(g["date"]))
        return out

    def cot_pct(self) -> pd.DataFrame:
        def f():
            r = self._cot_ratio(self.legacy, "noncomm_positions_long_all", "noncomm_positions_short_all")
            pct = {c: rolling_pct(s, 156, 104) for c, s in r.items()}
            return asof_frame(pct, pd.Timedelta(days=6), self.fridays)
        return self.memo("cot_pct", f)

    def crowding(self) -> pd.DataFrame:
        def f():
            r = self._cot_ratio(self.legacy, "noncomm_positions_long_all", "noncomm_positions_short_all")
            z = asof_frame({c: rolling_z(s, 156, 104) for c, s in r.items()}, pd.Timedelta(days=6), self.fridays)
            rk = self.carry().rank(axis=1)
            cent = rk.sub(rk.mean(axis=1), axis=0)
            idx = (cent * z).sum(axis=1, min_count=6)
            return pd.DataFrame({"crowding": idx, "pct": rolling_pct(idx, 260, 104)})
        return self.memo("crowding", f)

    def tff_signal(self, who: str = "asset_mgr") -> pd.DataFrame:
        def f():
            r = self._cot_ratio(self.tff, f"{who}_positions_long", f"{who}_positions_short")
            pct = {c: rolling_pct(s, 156, 104) for c, s in r.items()}
            return -asof_frame(pct, pd.Timedelta(days=6), self.fridays)
        return self.memo(("tff", who), f)

    def reer_value(self) -> pd.DataFrame:
        def f():
            out = {}
            for c, g in self.reer.groupby("ccy"):
                s = pd.Series(np.log(g["value"].to_numpy(float)), index=pd.DatetimeIndex(g["period"])).sort_index()
                dev = s - s.shift(1).rolling(60, min_periods=36).mean()
                out[c] = -dev
            return asof_frame(out, pd.Timedelta(days=25), self.fridays)
        return self.memo("reer", f)

    def momentum_sign(self) -> pd.DataFrame:
        def f():
            lv = self.lv_usd
            a = asof_frame({c: lv[c] for c in CCYS}, pd.Timedelta(days=1), self.fridays)
            b = asof_frame({c: lv[c] for c in CCYS}, pd.Timedelta(days=92), self.fridays)
            mv = a - b
            mv["USD"] = -mv[[c for c in CCYS if c != "USD"]].mean(axis=1)
            return np.sign(mv)
        return self.memo("mom", f)

    def surprises(self) -> pd.DataFrame:
        def f():
            ev = self.events[self.events["released"] & self.events["consensus"].notna() & self.events["actual"].notna()].copy()
            ev["s"] = np.sign((ev["actual"] - ev["consensus"]) * ev["direction"])
            ev = ev.sort_values("release_dt")
            out = pd.DataFrame(np.nan, index=self.fridays, columns=CCYS)
            for c, g in ev.groupby("ccy"):
                rel = g["release_dt"].to_numpy()
                for i, t in enumerate(self.fridays):
                    gg = g.iloc[:np.searchsorted(rel, np.datetime64(t, "ns"), side="left")]
                    if gg.empty:
                        continue
                    out.at[t, c] = gg.groupby("indicator_key")["s"].apply(lambda s: s.tail(12).mean()).sum()
            return out
        return self.memo("surprises", f)

    def ms(self, lite: bool = False) -> pd.DataFrame:
        def f():
            parts = [self.cot_pct().apply(cs_z, axis=1), self.momentum_sign().apply(cs_z, axis=1)]
            if not lite:
                parts.insert(0, self.surprises().apply(cs_z, axis=1))
            tot = sum(p.fillna(0.0) for p in parts)
            have = sum(p.notna().astype(int) for p in parts)
            return tot.where(have == len(parts))
        return self.memo(("ms", lite), f)

    # --- v4 ---------------------------------------------------------------------
    def v4_raw(self, macro: bool = True, extra: dict | None = None) -> pd.DataFrame:
        """v4.0 direction score; extra = {name: panel} of measured nodes that passed (equal weight)."""
        def f():
            carry = self.carry_z().copy()
            reg = self.regime()
            pct = self.crowding()["pct"]
            ls = (reg == "liquidity_stress") & pct.notna()
            carry.loc[ls] = carry.loc[ls].mul(1 - pct[ls] / 100.0, axis=0)
            s = carry
            if macro:
                s = s + self.macro_z().fillna(0.0)
            for _, p in (extra or {}).items():
                s = s + p.apply(cs_z, axis=1).fillna(0.0)
            return s
        return self.memo(("v4", macro, tuple(sorted((extra or {}).keys()))), f)

    def calibrate(self, score: pd.DataFrame, fwd: pd.DataFrame, h: int, min_weeks: int,
                  halve: bool, by_regime: bool = True) -> pd.DataFrame:
        """Walk-forward P(forward basket return > 0) per Friday x currency."""
        reg = self.regime()
        score, fwd = score.copy(), fwd.copy()
        for f in (score, fwd):                    # panels built by pivot carry index / column names
            f.index.name, f.columns.name = None, None
        long = score.stack().rename("s").to_frame().join(fwd.stack().rename("r"), how="left").reset_index()
        long.columns = ["asof", "ccy", "s", "r"]
        long["regime"] = long["asof"].map(reg)
        long = long.dropna(subset=["s"])
        first = long["asof"].min()
        P = pd.DataFrame(np.nan, index=score.index, columns=score.columns)
        for t in score.index:
            if (t - first).days < 7 * min_weeks:
                continue
            tr = long[(long["asof"] + pd.Timedelta(weeks=h) <= t - pd.Timedelta(days=1)) & long["r"].notna()]
            if len(tr) < 100:
                continue
            r_t = reg.get(t)
            sub = tr[tr["regime"] == r_t] if by_regime else tr
            use = sub if by_regime and len(sub) >= 100 else tr
            a, b = logistic(use["s"].to_numpy(), (use["r"] > 0).astype(float).to_numpy())
            p = 1 / (1 + np.exp(-(a + b * score.loc[t].to_numpy(float))))
            if halve and r_t != "calm":
                p = 0.5 + (p - 0.5) / 2
            P.loc[t] = p
        return P

    def concentration(self, score: pd.DataFrame) -> pd.Series:
        """Share of the top-2 / bottom-2 portfolio variance on PC1 of the 7 vs-USD daily returns."""
        rets = self.lv_usd.drop(columns="USD").diff().dropna()
        out = {}
        for t in score.index:
            s = score.loc[t].dropna()
            if len(s) < 5:
                continue
            w = pd.Series(0.0, index=CCYS)
            r = s.sort_values()
            w[r.index[-2:]] = 0.5
            w[r.index[:2]] = -0.5
            x = rets[(rets.index <= t - pd.Timedelta(days=1)) & (rets.index > t - pd.Timedelta(days=366))]
            if len(x) < 150:
                continue
            S = np.cov(x.to_numpy().T)
            vals, vecs = np.linalg.eigh(S)
            v1, l1 = vecs[:, -1], vals[-1]
            wn = w[x.columns].to_numpy()
            tot = wn @ S @ wn
            out[t] = float((wn @ v1) ** 2 * l1 / tot) if tot > 0 else np.nan
        return pd.Series(out)


def snapshot(asof, p4: Phase4 | None = None, horizons=(2, 4), live_macro: dict | None = None) -> dict:
    """v4 per currency and horizon at asof: rank, score, P, confidence, flags, contributions.
    live_macro: {ccy: v3 macro block} from the production payload, used only for the asof row
    when the reconstruction has no value there (prospective use; the history stays the reconstruction)."""
    asof = pd.Timestamp(asof).normalize()
    fr = pd.date_range("2000-01-07", asof, freq="7D")
    if fr[-1] != asof:
        fr = fr.append(pd.DatetimeIndex([asof]))
    p4 = p4 or Phase4(fridays=fr)
    raw = p4.v4_raw()
    t = raw.index[-1]
    macro_src = "reconstruction"
    if live_macro and p4.macro_z().loc[t].isna().all():
        mz_live = cs_z(pd.Series(live_macro, dtype=float).reindex(CCYS))
        raw = raw.copy()
        raw.loc[t] = raw.loc[t] + mz_live.fillna(0.0)
        p4._c["macro_z"] = p4.macro_z().copy()
        p4._c["macro_z"].loc[t] = mz_live
        macro_src = "production payload (live)"
    elif p4.macro_z().loc[t].isna().all():
        macro_src = "none (carry only)"
    reg = p4.regime().iloc[-1]
    crowd = p4.crowding().iloc[-1]
    conc = p4.concentration(raw.iloc[[-1]])
    lv = p4.basket
    out = {"asof": asof.strftime("%Y-%m-%d"), "model": "v4.0", "status": "uncalibrated-prospective",
           "macro_source": macro_src,
           "regime": reg, "crowding_pct": None if pd.isna(crowd["pct"]) else round(float(crowd["pct"]), 3),
           "concentration_pc1": None if conc.empty else round(float(conc.iloc[-1]), 4), "currencies": {}}
    flags = []
    if reg != "calm":
        flags.append(f"regime:{reg}")
    if out["crowding_pct"] is not None and out["crowding_pct"] > CROWD_CUT:
        flags.append("crowding")
    if out["concentration_pc1"] is not None and out["concentration_pc1"] >= CONC_FLAG:
        flags.append("dollar_concentration")
    s = raw.loc[t]
    rank = s.rank(ascending=False, method="min")
    cz, mz = p4.carry_z().loc[t], p4.macro_z().loc[t]
    for h in horizons:
        fwd = pd.DataFrame({c: [(_lvl(lv[c], u + pd.Timedelta(weeks=h)) - _lvl(lv[c], u)) * 1e4
                                if u + pd.Timedelta(weeks=h) <= lv.index[-1] else np.nan for u in raw.index]
                            for c in CCYS}, index=raw.index)
        P = p4.calibrate(raw, fwd, h, MIN_TRAIN["long"], halve=True).loc[t]
        for c in CCYS:
            p = P.get(c)
            e = out["currencies"].setdefault(c, {"score": _r(s.get(c)), "rank": _r(rank.get(c)),
                                                 "contributions": {"carry": _r(cz.get(c)), "macro_v3": _r(mz.get(c))},
                                                 "flags": flags, "by_horizon": {}})
            conf = None if p is None or np.isnan(p) else ("abstain" if P_ABSTAIN[0] <= p <= P_ABSTAIN[1]
                                                          else ("high" if abs(p - 0.5) >= 0.1 else "low"))
            e["by_horizon"][str(h)] = {"P_beats_basket": _r(p), "confidence": conf}
    return out


def _lvl(s: pd.Series, t) -> float:
    i = s.index.searchsorted(t, side="right") - 1
    return float(s.iloc[i]) if i >= 0 and (t - s.index[i]).days <= 4 else np.nan


def _r(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), 6)
