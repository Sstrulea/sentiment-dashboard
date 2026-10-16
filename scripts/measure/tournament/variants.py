"""The tournament's scoring variants, point-in-time (docs/eval/scoring-tournament-prereg.md).

Every variant is a function v(D, t) -> {"ccy": {CCY: s}, "usd_row": s, "pair": {SYM: s}
(native pair scores only, A1/A2), "cross": {SYM: s}, "cats": {...}} with None for a
score that is not defined. It reads only data at or before t through `Data`:
FF releases with release_dt <= t (scoring_view), daily series with date <= t.date(),
COT reports with report date <= t, prices with date <= t.date(). `Data.cut(t)`
returns the same inputs truncated at t (the no-look-ahead test).

FX: per currency (8); a pair without a native score = S_base − S_quote; the US
Dollar row = the variant's USD score unless the variant has its own row (A1, A2).
"""
from __future__ import annotations

import logging
import math
import sys
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CCYS, CROSS, HOME_2Y, INDICES, METALS, ROOT  # noqa: E402
import prices as P  # noqa: E402

import v3_scales as V  # noqa: E402
import factor_scales as fs  # noqa: E402
from src import economic_render as er  # noqa: E402
from src.compute import compute_metrics, load_meta  # noqa: E402
from src.cot_score import flow_score, level_score  # noqa: E402
from src.crossasset_compute import v3_macro_raw, v3_yield_change  # noqa: E402
from src.economic_compute import (_dedup_flash_final, compute_indicator_score,  # noqa: E402
                                  effective_direction, effective_frequency)
from src.ff_scoring import scoring_view  # noqa: E402
from src.momentum_common import bday_lag  # noqa: E402
from src.policy_rate import load_decisions, policy_rate_history, rate_at  # noqa: E402
from src.rate_compute import max_age_for  # noqa: E402

log = logging.getLogger("tournament.variants")

START = fs.START                       # 2023-09-22 21:00, first reconstructed Friday
MACRO_CATS = ("growth", "inflation", "labour")
# B1 — EdgeFinder indicators present in data/economic_indicators.yaml
# (consumer confidence and PCE y/y are not in the config → not used).
EF_KEYS = ("gdp_qoq", "manufacturing_pmi", "services_pmi", "retail_sales", "employment_change",
           "unemployment_rate", "jobless_claims", "jolts", "adp", "cpi_yoy", "ppi_yoy")
B1_RATE_BAND = 0.10                    # pp, 2Y − policy rate
B1_YIELD_MEAN_OBS = 7                  # observations in the 2Y mean (indices/metals)
B1_PC_WINDOW = 252                     # observations in the 1-year P/C percentile
B1_RETAIL_MIN_COVERAGE = 0.80
SEAS_YEARS = 10
B3_DAYS, B3_HALF_LIFE = 90, 30.0
C1_TARGET = {"USD": 2.0, "EUR": 2.0, "GBP": 2.0, "JPY": 2.0, "CAD": 2.0, "NZD": 2.0, "AUD": 2.5, "CHF": 1.0}
C1_UR_DAYS = 182                       # 6 months
C2_DAYS = 365                          # 12 months
D2_FX_W, D2_X_W, END_K = 21, 63, 5
E3_WINDOW = 52
F1_OBS, F2_OBS = 252, 63
EXPANDING_MIN = 26                     # weeks, expanding z at indices/metals
XS_MIN = 3                             # currencies needed for a cross-sectional z
# COT symbol per tournament unit (data/contracts.yaml symbols; NIKKEI = cot_extra 240743)
COT_SYM = {**{c: c for c in CCYS if c != "USD"}, "USD": "DXY", "SP500": "SP500", "NASDAQ": "NASDAQ",
           "DJIA": "DOW", "NIKKEI": "NIKKEI", "GOLD": "GOLD", "SILVER": "SILVER"}
USD_ROW = "US-DOLLAR"


def _sign(x) -> int:
    return 0 if x is None or x == 0 or (isinstance(x, float) and math.isnan(x)) else (1 if x > 0 else -1)


def _mean(v):
    v = [x for x in v if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.mean(v)) if v else None


def xs_z(d: dict) -> dict:
    """Cross-sectional z over the present currencies (ddof=1); None for all when
    fewer than XS_MIN are present or the dispersion is 0."""
    pres = {k: float(v) for k, v in d.items() if v is not None and not math.isnan(v)}
    out = {k: None for k in d}
    if len(pres) < XS_MIN:
        return out
    a = np.array(list(pres.values()))
    sd = a.std(ddof=1)
    if not sd > 0:
        return out
    for k, v in pres.items():
        out[k] = (v - a.mean()) / sd
    return out


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def all_yield_series() -> dict:
    """v3_scales.yield_series (USD, EUR, JPY, GBP 2Y + DFII10) plus every other
    currency with a 2Y in data/rates.parquet (AUD, CAD), built the same way
    (rate_compute._series_for, source of the latest observation)."""
    from src.rate_compute import _series_for
    out = dict(V.yield_series())
    rates = pd.read_parquet(er.RATES_PARQUET)
    for c in CCYS:
        if c in out:
            continue
        sub = rates[rates["currency"] == c].sort_values("date")
        if sub.empty:
            continue
        d, y = _series_for(rates, c)
        out[c] = (d, y, str(sub["source"].iloc[-1]))
    return out


class Data:
    """All tournament inputs, loaded once; `cut(t)` gives the same truncated at t."""

    def __init__(self, recs: dict | None = None, *, _parts: dict | None = None):
        self._memo: dict = {}
        if _parts is not None:
            self.__dict__.update(_parts)
            return
        logging.disable(logging.CRITICAL)
        self.recs = recs or {}
        self.inst_cfg = yaml.safe_load(er.INSTRUMENTS_YAML.read_text())
        self.xcfg = yaml.safe_load(er.CROSSASSET_YAML.read_text())
        self.ind = yaml.safe_load(er.INDICATORS_YAML.read_text())
        self.k = V.config_constants(self.inst_cfg, self.xcfg)
        self.hist = policy_rate_history(pd.read_parquet(ROOT / "data/economic_calendar_ff.parquet"), load_decisions())
        self.series = all_yield_series()                    # {name: (dates, ys, source)}
        import json
        as_of = pd.Timestamp(json.loads((ROOT / "public/data/economic.json").read_text())["as_of"])
        cal = er._load_calendar_frame(as_of)
        cal = scoring_view(cal)
        cal = cal[cal["indicator_key"].notna()].copy()
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        self.cal = cal
        import cot_extra
        raw = pd.concat([pd.read_parquet(ROOT / "data/history.parquet"), cot_extra.fetch()], ignore_index=True)
        self.cot_raw = raw
        self.pc = pd.read_parquet(er.PC_HISTORY_PARQUET)[["date", "equity"]].dropna()
        self.prices = P.all_prices()
        self.cut_at = None

    # -- truncation -----------------------------------------------------
    def cut(self, t: pd.Timestamp) -> "Data":
        d = pd.Timestamp(t.date())
        parts = dict(self.__dict__)
        parts.pop("_memo", None)
        for k in ("cot", "basket"):
            parts.pop(k, None)
        parts["recs"] = {w: r for w, r in self.recs.items() if w <= t}
        parts["hist"] = self.hist[self.hist["release_dt"] <= t]
        parts["series"] = {n: (lambda ds, ys, src: ([x for x in ds if pd.Timestamp(x) <= d],
                                                     ys[:sum(1 for x in ds if pd.Timestamp(x) <= d)], src))(*v)
                           for n, v in self.series.items()}
        parts["cal"] = self.cal[self.cal["release_dt"] <= t]
        parts["cot_raw"] = self.cot_raw[pd.to_datetime(self.cot_raw["report_date_as_yyyy_mm_dd"]) <= t]
        parts["pc"] = self.pc[pd.to_datetime(self.pc["date"]) <= d]
        parts["prices"] = {u: s[s.index <= d] for u, s in self.prices.items()}
        parts["cut_at"] = t
        return Data(_parts=parts)

    # -- derived (cached) -----------------------------------------------
    @cached_property
    def cot(self) -> dict[str, pd.DataFrame]:
        """{COT symbol: enriched rows sorted by report date} (src.compute.compute_metrics)."""
        e = compute_metrics(self.cot_raw)
        meta = load_meta()
        e["symbol"] = e["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("symbol"))
        e.loc[e["cftc_contract_market_code"] == "240743", "symbol"] = "NIKKEI"
        return {s: g.sort_values("report_date_as_yyyy_mm_dd") for s, g in e.groupby("symbol")}

    @cached_property
    def basket(self) -> dict[str, pd.Series]:
        """FX: log price of each currency vs the equal-weighted basket of the other 7,
        on the FRED days where all 7 H.10 series exist."""
        lp = pd.concat({c: np.log(self.prices[c]) for c in CCYS if c != "USD"}, axis=1, join="inner").dropna()
        lp["USD"] = 0.0
        return {c: (lp[c] - lp[[j for j in CCYS if j != c]].mean(axis=1)) for c in CCYS}

    def unit_logprice(self, u: str) -> pd.Series:
        return self.basket[u] if u in CCYS else np.log(self.prices[u])

    def pairs(self):
        return [(s, c["base"], c["quote"]) for s, c in self.inst_cfg["instruments"].items() if c["type"] == "fx"]

    def memo(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    # -- point-in-time primitives ---------------------------------------
    def rec(self, t):
        return self.recs.get(t)

    def latest_print(self, ccy: str, key: str, t: pd.Timestamp) -> float | None:
        """Actual of the latest non-stale release of (ccy, key) at t — production's
        compute_indicator_score (flash/final collapse, recency window, superseded gate)."""
        cfg = self.ind["indicators"].get(key)
        if cfg is None:
            return None
        sub = self.cal[(self.cal["currency"] == ccy) & (self.cal["indicator_key"] == key)
                       & (self.cal["release_dt"] <= t)]
        if sub.empty:
            return None
        r = compute_indicator_score(sub, cfg, self.ind["defaults"], t, allow_stale=True, currency=ccy, indicator_key=key)
        if r is None or r.get("stale") or r.get("actual") is None:
            return None
        return float(r["actual"])

    def is_yoy(self, ccy: str, key: str, t) -> bool:
        names = self.cal[(self.cal["currency"] == ccy) & (self.cal["indicator_key"] == key)
                         & (self.cal["release_dt"] <= t)]["name_raw"].dropna().unique()
        return len(names) > 0 and all(str(n).strip().lower().endswith("y/y") for n in names)

    def y2(self, name: str, t: pd.Timestamp, back_days: int = 0):
        """(latest yield at or before t.date() − back_days, its date); None when the
        series is missing or its latest obs at t is stale (rate pillar max age)."""
        if name not in self.series:
            return None
        ds, ys, src = self.series[name]
        ref = pd.Timestamp(t.date())
        idx = [i for i, x in enumerate(ds) if pd.Timestamp(x) <= ref]
        if not idx:
            return None
        age = V.REAL_MAX_AGE_BD if name == "REAL10" else max_age_for(src)
        if bday_lag(ds[idx[-1]], ref.date()) > age:
            return None
        if back_days:
            cut = ref - pd.Timedelta(days=back_days)
            idx = [i for i in idx if pd.Timestamp(ds[i]) <= cut]
            if not idx:
                return None
        return ys[idx[-1]], ds[idx[-1]]

    def ychange(self, name: str, t: pd.Timestamp, W: int) -> float | None:
        """Δ over W observations with 5-observation averaged ends at t; None when
        missing, too short or stale."""
        if self.y2(name, t) is None:
            return None
        ds, ys, _ = self.series[name]
        delta, _ = v3_yield_change(ds, ys, t.date(), W=W, k=END_K)
        return delta

    def cot_rows(self, unit: str, t: pd.Timestamp) -> pd.DataFrame | None:
        s = COT_SYM.get(unit)
        g = self.cot.get(s) if s else None
        if g is None:
            return None
        g = g[g["report_date_as_yyyy_mm_dd"] <= t]
        return g if len(g) else None

    def logprice_at(self, u: str, t: pd.Timestamp) -> pd.Series:
        s = self.unit_logprice(u)
        return s[s.index <= pd.Timestamp(t.date())]

    def expanding_z(self, name: str, unit: str, t: pd.Timestamp, raw_fn) -> float | None:
        """z of raw_fn(D, s, unit) at t over its values at every Friday from START
        to t (ddof=1, at least EXPANDING_MIN values incl. t)."""
        x_t = self.memo((name, unit, t), lambda: raw_fn(self, t, unit))
        if x_t is None:
            return None
        vals = []
        s = START
        while s <= t:
            v = self.memo((name, unit, s), lambda s=s: raw_fn(self, s, unit))
            if v is not None:
                vals.append(v)
            s += pd.Timedelta(days=7)
        if len(vals) < EXPANDING_MIN:
            return None
        a = np.array(vals)
        sd = a.std(ddof=1)
        return None if not sd > 0 else float((x_t - a.mean()) / sd)


def _cat_sign(D: Data, sym: str, cat: str) -> float | None:
    f = (D.xcfg["instruments"][sym].get("factors") or {}).get(cat)
    return None if f is None else float(f.get("sign", 1))


def _out(ccy=None, cross=None, usd_row="same", pair=None, cats=None) -> dict:
    ccy = ccy or {}
    return {"ccy": ccy, "usd_row": ccy.get("USD") if usd_row == "same" else usd_row,
            "pair": pair or {}, "cross": cross or {}, "cats": cats or {}}


# ---------------------------------------------------------------------------
# A — production v3 and the v2 shadow (from the collected state_at records)
# ---------------------------------------------------------------------------

def a1(D: Data, t):
    r = D.rec(t)
    if r is None:
        return _out()
    w = V.v3_week(r, D.hist, D.k, D.inst_cfg, D.xcfg, D.series)
    pair = {s: w["scores"][s] for s, _, _ in D.pairs()}
    cross = {s: w["scores"][s] for s in CROSS}
    return _out(ccy=dict(w["ccy"]), usd_row=w["scores"][USD_ROW], pair=pair, cross=cross)


def a2(D: Data, t):
    r = D.rec(t)
    if r is None:
        return _out()
    v2 = {s: v[0] for s, v in r["v2_fx"].items()}
    ccy = {}
    for c in CCYS:
        vals = [(v2[s] if b == c else -v2[s]) for s, b, q in D.pairs() if c in (b, q) and v2.get(s) is not None]
        ccy[c] = float(np.mean(vals)) if vals else None
    pair = {s: v2.get(s) for s, _, _ in D.pairs()}
    return _out(ccy=ccy, usd_row=v2.get(USD_ROW), pair=pair, cross={s: r["v2_cross"][s][0] for s in CROSS})


# ---------------------------------------------------------------------------
# B1 — EdgeFinder clone
# ---------------------------------------------------------------------------

def _b1_macro_terms(D: Data, ccy: str, t) -> dict[str, int]:
    """{indicator: ±1/0} — the latest non-stale print vs consensus, in the indicator's direction."""
    r = D.rec(t)
    if r is None:
        return {}
    out = {}
    for key in EF_KEYS:
        e = (r["bd"].get(ccy) or {}).get(key)
        if e is None or e.get("stale") or e.get("flag") in ("no_consensus", "direction_mismatch"):
            continue
        if e.get("surprise") is None or (isinstance(e["surprise"], float) and math.isnan(e["surprise"])):
            continue
        d = effective_direction(D.ind["indicators"][key], D.ind["defaults"], ccy)
        out[key] = _sign(d * float(e["surprise"]))
    return out


def seasonality(D: Data, unit: str, t) -> int | None:
    """Sign of the mean log return of the entry month (month of the Monday after t)
    over the SEAS_YEARS calendar years before t's year; prices <= t only."""
    lp = D.logprice_at(unit, t)
    if lp.empty:
        return None
    month = (t.normalize() + pd.Timedelta(days=3)).month
    me = lp.groupby([lp.index.year, lp.index.month]).last()
    rets = []
    for y in range(t.year - SEAS_YEARS, t.year):
        py, pm = (y, month - 1) if month > 1 else (y - 1, 12)
        if (y, month) in me.index and (py, pm) in me.index:
            rets.append(me[(y, month)] - me[(py, pm)])
    if len(rets) < SEAS_YEARS:
        return None
    return _sign(float(np.mean(rets)))


def _b1_cot_change(D: Data, unit: str, t) -> int | None:
    g = D.cot_rows(unit, t)
    if g is None or len(g) < 2:
        return None
    a, b = g["spec_exp_long"].iloc[-2], g["spec_exp_long"].iloc[-1]
    return None if pd.isna(a) or pd.isna(b) else _sign(float(b) - float(a))


def _b1_pc(D: Data, t) -> int | None:
    pc = D.pc[pd.to_datetime(D.pc["date"]) <= pd.Timestamp(t.date())].sort_values("date")["equity"]
    if len(pc) < B1_PC_WINDOW:
        return None
    w = pc.tail(B1_PC_WINDOW)
    pct = 100.0 * float(w.rank(pct=True, method="average").iloc[-1])
    return 2 if pct >= 90 else (-1 if pct <= 10 else 0)


def b1_components(D: Data, t) -> dict:
    """{unit: {component: value}} — FX currencies and cross instruments."""
    def fx(c):
        comp = {}
        terms = _b1_macro_terms(D, c, t)
        if terms:
            comp["macro"] = sum(terms.values())
        y = D.y2(c, t)
        pol = rate_at(D.hist, t).get(c)
        if y is not None and pol is not None:
            diff = y[0] - pol
            comp["rates"] = 1 if diff > B1_RATE_BAND else (-1 if diff < -B1_RATE_BAND else 0)
        cot = _b1_cot_change(D, c, t)
        if cot is not None:
            comp["cot"] = cot
        s = seasonality(D, c, t)
        if s is not None:
            comp["seasonality"] = s
        return comp

    def cross(sym):
        cfg = D.xcfg["instruments"][sym]
        home = cfg["home_ccy"]
        comp = {}
        terms = _b1_macro_terms(D, home, t)
        signed = [_cat_sign(D, sym, D.ind["indicators"][k]["category"]) * v for k, v in terms.items()
                  if _cat_sign(D, sym, D.ind["indicators"][k]["category"]) is not None]
        if signed:
            comp["macro"] = sum(signed)
        name = HOME_2Y[sym]
        if D.y2(name, t) is not None:
            ds, ys, _ = D.series[name]
            n = sum(1 for x in ds if pd.Timestamp(x) <= pd.Timestamp(t.date()))
            if n >= B1_YIELD_MEAN_OBS:
                last, m7 = ys[n - 1], float(np.mean(ys[n - B1_YIELD_MEAN_OBS:n]))
                comp["rates"] = -1 if last > m7 else (1 if last < m7 else 0)
        cot = _b1_cot_change(D, sym, t)
        g = D.cot_rows(sym, t)
        if cot is not None and g is not None:
            comp["cot"] = cot + _sign(float(g["spec_net"].iloc[-1]))
        s = seasonality(D, sym, t)
        if s is not None:
            comp["seasonality"] = 2 * s
        if sym in ("SP500", "NASDAQ", "DJIA"):
            p = _b1_pc(D, t)
            if p is not None:
                comp["put_call"] = p
        return comp

    return {**{c: fx(c) for c in CCYS}, **{s: cross(s) for s in CROSS}}


def b1(D: Data, t):
    comp = D.memo(("b1c", t), lambda: b1_components(D, t))
    tot = {u: (float(sum(v.values())) if v else None) for u, v in comp.items()}
    return _out(ccy={c: tot[c] for c in CCYS}, cross={s: tot[s] for s in CROSS})


def f3(D: Data, t):
    ccy = {c: seasonality(D, c, t) for c in CCYS}
    cross = {s: (None if (v := seasonality(D, s, t)) is None else 2 * v) for s in CROSS}
    return _out(ccy={c: (None if v is None else float(v)) for c, v in ccy.items()},
                cross={s: (None if v is None else float(v)) for s, v in cross.items()})


# ---------------------------------------------------------------------------
# B2 — v3 Macro block alone; D1 — v3 carry block alone
# ---------------------------------------------------------------------------

def b2(D: Data, t):
    r = D.rec(t)
    if r is None:
        return _out()
    cs = V.currency_scores(r, D.hist, D.k)
    ccy = {c: cs[c]["blocks"]["macro"] for c in CCYS}
    cross, cats = {}, {"fx": {}, "cross": {}}
    for sym in CROSS:
        cfg = D.xcfg["instruments"][sym]
        home = cfg["home_ccy"]
        m = v3_macro_raw(cfg, r["cats"].get(home, {}), D.k["sigma_ccy"][home])
        cross[sym] = None if m is None else m / D.k["sigma_macro_inst"][sym]
    for cat in MACRO_CATS:
        def cat_val(c):
            cell = r["cats"][c].get(cat)
            sg = D.k["sigma_ccy"][c].get(cat)
            return None if cell is None or not sg or (cell.get("coverage") or 0) <= 0 else \
                float(cell["score_precise"]) / float(sg)
        cats["fx"][cat] = {c: cat_val(c) for c in CCYS}
        cats["cross"][cat] = {s: (None if (v := cat_val(D.xcfg["instruments"][s]["home_ccy"])) is None
                                  else _cat_sign(D, s, cat) * v) for s in CROSS}
    return _out(ccy=ccy, cross=cross, cats=cats)


def d1(D: Data, t):
    r = D.rec(t)
    if r is None:
        return _out()
    cs = V.currency_scores(r, D.hist, D.k)
    return _out(ccy={c: cs[c]["blocks"]["carry"] for c in CCYS})


# ---------------------------------------------------------------------------
# B3 — Citi-style surprise index
# ---------------------------------------------------------------------------

def _b3_surprises(D: Data, ccy: str, t) -> list[tuple[str, float, float]]:
    """[(category, surprise in σ, age days)] for every release in (t − 90 d, t]."""
    defaults = D.ind["defaults"]
    k = int(defaults.get("surprise_window_k", 12))
    min_prints = int(defaults.get("fallback_min_prints", 6))
    cal = D.cal[(D.cal["currency"] == ccy) & (D.cal["release_dt"] <= t)]
    out = []
    for key, cfg in D.ind["indicators"].items():
        cat = cfg.get("category")
        if cat not in MACRO_CATS:
            continue
        if cfg.get("currencies") and ccy not in cfg["currencies"]:
            continue
        sub = cal[cal["indicator_key"] == key]
        if sub.empty:
            continue
        freq = effective_frequency(cfg, defaults, ccy)
        df = sub.sort_values("release_dt").reset_index(drop=True).copy()
        df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
        df["consensus"] = pd.to_numeric(df["consensus"], errors="coerce")
        df = _dedup_flash_final(df, (defaults.get("dedup_gap_days", {}) or {}).get(freq))
        pairs = df[df["actual"].notna() & df["consensus"].notna()].reset_index(drop=True)
        direction = effective_direction(cfg, defaults, ccy)
        for i, row in pairs.iterrows():
            age = (t - row["release_dt"]).total_seconds() / 86400.0
            if age >= B3_DAYS or age < 0:
                continue
            diffs = (pairs["actual"] - pairs["consensus"]).iloc[max(0, i - k + 1):i + 1]
            if len(diffs) < min_prints:
                continue
            sd = float(diffs.std(ddof=1))
            if not sd > 0:
                continue
            out.append((cat, direction * float(row["actual"] - row["consensus"]) / sd, age))
    return out


def _decay_mean(items) -> float | None:
    if not items:
        return None
    w = np.array([0.5 ** (age / B3_HALF_LIFE) for _, age in items])
    s = np.array([v for v, _ in items])
    return float((w * s).sum() / w.sum())


def b3(D: Data, t):
    sur = {c: D.memo(("b3s", c, t), lambda c=c: _b3_surprises(D, c, t)) for c in CCYS}
    ccy = {c: _decay_mean([(v, a) for _, v, a in sur[c]]) for c in CCYS}
    cross, cats = {}, {"fx": {}, "cross": {}}
    for sym in CROSS:
        home = D.xcfg["instruments"][sym]["home_ccy"]
        items = [(_cat_sign(D, sym, cat) * v, a) for cat, v, a in sur[home] if _cat_sign(D, sym, cat) is not None]
        cross[sym] = _decay_mean(items)
    for cat in MACRO_CATS:
        cats["fx"][cat] = {c: _decay_mean([(v, a) for k, v, a in sur[c] if k == cat]) for c in CCYS}
        cats["cross"][cat] = {s: (None if (m := _decay_mean([(v, a) for k, v, a in
                                                             sur[D.xcfg["instruments"][s]["home_ccy"]] if k == cat])) is None
                                  else _cat_sign(D, s, cat) * m) for s in CROSS}
    return _out(ccy=ccy, cross=cross, cats=cats)


# ---------------------------------------------------------------------------
# C1 — macro trend (Macrosynergy style); C2 — macro momentum (AQR style)
# ---------------------------------------------------------------------------

def _pmi(D: Data, c, t) -> float | None:
    return _mean([D.latest_print(c, "manufacturing_pmi", t), D.latest_print(c, "services_pmi", t)])


def _cpi(D: Data, c, t) -> float | None:
    if not D.is_yoy(c, "cpi_yoy", t):
        return None
    return D.latest_print(c, "cpi_yoy", t)


def c1_raw(D: Data, t, c) -> dict:
    """{growth, inflation, labour} raw factors of currency c at t."""
    def f():
        cpi = _cpi(D, c, t)
        pmi = _pmi(D, c, t)
        ur, ur6 = D.latest_print(c, "unemployment_rate", t), D.latest_print(c, "unemployment_rate",
                                                                            t - pd.Timedelta(days=C1_UR_DAYS))
        return {"inflation": None if cpi is None else cpi - C1_TARGET[c],
                "growth": None if pmi is None else pmi - 50.0,
                "labour": None if ur is None or ur6 is None else -(ur - ur6)}
    return D.memo(("c1raw", c, t), f)


def c2_raw(D: Data, t, c) -> dict:
    """{growth: Δ12m PMI, inflation: Δ12m CPI y/y, rates: Δ12m 2Y} of currency c at t."""
    def f():
        back = t - pd.Timedelta(days=C2_DAYS)
        p0, p1 = _pmi(D, c, back), _pmi(D, c, t)
        i0, i1 = _cpi(D, c, back), _cpi(D, c, t)
        y1, y0 = D.y2(c, t), D.y2(c, t, back_days=C2_DAYS)
        return {"growth": None if p0 is None or p1 is None else p1 - p0,
                "inflation": None if i0 is None or i1 is None else i1 - i0,
                "rates": None if y1 is None or y0 is None else y1[0] - y0[0]}
    return D.memo(("c2raw", c, t), f)


def _fx_xs(raw: dict[str, dict], factors) -> tuple[dict, dict]:
    z = {f: xs_z({c: raw[c][f] for c in CCYS}) for f in factors}
    score = {c: _mean([z[f][c] for f in factors]) for c in CCYS}
    return score, z


def c1(D: Data, t):
    raw = {c: c1_raw(D, t, c) for c in CCYS}
    ccy, z = _fx_xs(raw, MACRO_CATS)
    cross, cats = {}, {"fx": z, "cross": {f: {} for f in MACRO_CATS}}
    for sym in CROSS:
        home = D.xcfg["instruments"][sym]["home_ccy"]
        parts = []
        for f in MACRO_CATS:
            zz = D.expanding_z(f"c1_{f}", home, t, lambda D_, s, u, f=f: c1_raw(D_, s, u)[f])
            v = None if zz is None else _cat_sign(D, sym, f) * zz
            cats["cross"][f][sym] = v
            parts.append(v)
        cross[sym] = _mean(parts)
    return _out(ccy=ccy, cross=cross, cats=cats)


def c2(D: Data, t):
    facs = ("growth", "inflation", "rates")
    raw = {c: c2_raw(D, t, c) for c in CCYS}
    ccy, z = _fx_xs(raw, facs)
    cross, cats = {}, {"fx": z, "cross": {f: {} for f in facs}}
    for sym in CROSS:
        home = D.xcfg["instruments"][sym]["home_ccy"]
        ycc = HOME_2Y[sym]
        parts = []
        for f in facs:
            unit = ycc if f == "rates" else home
            zz = D.expanding_z(f"c2_{f}", unit, t, lambda D_, s, u, f=f: c2_raw(D_, s, u)[f])
            sg = -1.0 if f == "rates" else _cat_sign(D, sym, f)
            v = None if zz is None else sg * zz
            cats["cross"][f][sym] = v
            parts.append(v)
        cross[sym] = _mean(parts)
    return _out(ccy=ccy, cross=cross, cats=cats)


# ---------------------------------------------------------------------------
# D2 — change in expected rates
# ---------------------------------------------------------------------------

def d2(D: Data, t):
    ch = {c: D.ychange(c, t, D2_FX_W) for c in CCYS}
    m = _mean(ch.values())
    ccy = {c: (None if v is None else v - m) for c, v in ch.items()}
    cross = {}
    for sym in CROSS:
        name = "REAL10" if sym in METALS else HOME_2Y[sym]
        v = D.ychange(name, t, D2_X_W)
        cross[sym] = None if v is None else -v
    return _out(ccy=ccy, cross=cross)


# ---------------------------------------------------------------------------
# E — COT level / flow / speculative pressure
# ---------------------------------------------------------------------------

def _units_cot():
    return list(CCYS) + [s for s in CROSS if s in COT_SYM]


def e1(D: Data, t):
    out = {}
    for u in _units_cot():
        g = D.cot_rows(u, t)
        if g is None or pd.isna(g["spec_extreme_6m"].iloc[-1]):
            out[u] = None
            continue
        out[u] = float(level_score(g["spec_extreme_6m"].iloc[-1], g["spec_extreme_3y"].iloc[-1])[0])
    return _out(ccy={c: out[c] for c in CCYS}, cross={s: out.get(s) for s in CROSS})


def e2(D: Data, t):
    out = {}
    for u in _units_cot():
        g = D.cot_rows(u, t)
        if g is None:
            out[u] = None
            continue
        f, m = flow_score(g["spec_net"])
        out[u] = None if m["basis"] != "ok" else float(f)
    return _out(ccy={c: out[c] for c in CCYS}, cross={s: out.get(s) for s in CROSS})


def e3(D: Data, t):
    out = {}
    for u in _units_cot():
        g = D.cot_rows(u, t)
        if g is None or len(g) < E3_WINDOW:
            out[u] = None
            continue
        w = g["spec_net"].astype(float).tail(E3_WINDOW)
        sd = w.std(ddof=1)
        out[u] = None if not sd > 0 else float((w.iloc[-1] - w.mean()) / sd)
    return _out(ccy={c: out[c] for c in CCYS}, cross={s: out.get(s) for s in CROSS})


# ---------------------------------------------------------------------------
# F — price trend (benchmarks)
# ---------------------------------------------------------------------------

def _trend(D: Data, u, t, n) -> float | None:
    lp = D.logprice_at(u, t)
    if len(lp) <= n:
        return None
    return float(lp.iloc[-1] - lp.iloc[-1 - n])


def f1(D: Data, t):
    return _out(ccy={c: _trend(D, c, t, F1_OBS) for c in CCYS}, cross={s: _trend(D, s, t, F1_OBS) for s in CROSS})


def f2(D: Data, t):
    return _out(ccy={c: _trend(D, c, t, F2_OBS) for c in CCYS}, cross={s: _trend(D, s, t, F2_OBS) for s in CROSS})


# ---------------------------------------------------------------------------
# G — conceptual-parity composites
# ---------------------------------------------------------------------------

def _g(D: Data, t, comps):
    fns = {"C1": c1, "D2": d2, "E3": e3, "F1": f1}
    res = {n: D.memo(("var", n, t), lambda n=n: fns[n](D, t)) for n in comps}
    zs = {n: xs_z(res[n]["ccy"]) for n in comps}
    ccy = {c: _mean([zs[n][c] for n in comps]) for c in CCYS}
    cross = {}
    for sym in CROSS:
        parts = [D.expanding_z(f"g_{n}", sym, t,
                               lambda D_, s, u, n=n: D_.memo(("var", n, s), lambda: fns[n](D_, s))["cross"].get(u))
                 for n in comps]
        cross[sym] = _mean(parts)
    return _out(ccy=ccy, cross=cross)


def g1(D: Data, t):
    return _g(D, t, ("C1", "D2", "E3", "F1"))


def g2(D: Data, t):
    return _g(D, t, ("C1", "D2", "E3"))


VARIANTS = {"A1": a1, "A2": a2, "B1": b1, "B2": b2, "B3": b3, "C1": c1, "C2": c2, "D1": d1, "D2": d2,
            "E1": e1, "E2": e2, "E3": e3, "F1": f1, "F2": f2, "F3": f3, "G1": g1, "G2": g2}
FX_ONLY = {"D1"}
NO_FF = ("D2", "E1", "E2", "E3", "F1", "F2", "F3", "D1")      # long window candidates
NAMES = {"A1": "v3 (producție)", "A2": "v2 (shadow)", "B1": "Clonă EdgeFinder", "B2": "Macro v3",
         "B3": "Surprize tip Citi", "C1": "Trend macro", "C2": "Macro momentum", "D1": "Carry v3",
         "D2": "Δ rate așteptate", "E1": "COT level", "E2": "COT flow", "E3": "Presiune speculativă",
         "F1": "Trend 12 luni", "F2": "Trend 3 luni", "F3": "Sezonalitate", "G1": "Compus parity",
         "G2": "Compus fără F1"}


def run_variant(D: Data, name: str, t) -> dict:
    return D.memo(("var", name, t), lambda: VARIANTS[name](D, t))


def scores_frame(D: Data, weeks, names=None) -> pd.DataFrame:
    """Long frame: variant, week, view (ccy | pair | usd_row | cross), unit, score."""
    rows = []
    pairs = D.pairs()
    for name in (names or VARIANTS):
        for t in weeks:
            r = run_variant(D, name, t)
            for c, v in r["ccy"].items():
                rows.append((name, t, "ccy", c, v))
            if r["ccy"] or r["usd_row"] is not None:
                rows.append((name, t, "usd_row", USD_ROW, r["usd_row"]))
            for s, b, q in pairs:
                if s in r["pair"]:
                    v = r["pair"][s]
                else:
                    sb, sq = r["ccy"].get(b), r["ccy"].get(q)
                    v = None if sb is None or sq is None else sb - sq
                if r["ccy"] or r["pair"]:
                    rows.append((name, t, "pair", s, v))
            if name not in FX_ONLY:
                for s in CROSS:
                    rows.append((name, t, "cross", s, r["cross"].get(s)))
    df = pd.DataFrame(rows, columns=["variant", "week", "view", "unit", "score"])
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    return df


def cats_frame(D: Data, weeks, names=("B2", "B3", "C1", "C2")) -> pd.DataFrame:
    rows = []
    for name in names:
        for t in weeks:
            r = run_variant(D, name, t)
            for view, per in r["cats"].items():
                for cat, d in per.items():
                    for u, v in d.items():
                        rows.append((name, t, "ccy" if view == "fx" else "cross", cat, u, v))
    df = pd.DataFrame(rows, columns=["variant", "week", "view", "cat", "unit", "score"])
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    return df
