"""Phase 5 macro composite (attempt #5): components on unrevised OECD editions, BCI, IMF CTOT,
ECB FX and BIS policy rates, for 15 currencies. Rules: scripts/measure/prereg_phase5.yml.

Edition components (computed from ONE OECD STES-revisions edition, its own history):
  excess employment   annual growth of employment (3-month average vs the same 12 months earlier;
                      quarterly: last quarter vs 4 quarters earlier) minus the median of that
                      annual growth over the previous 60 months (20 quarters)
  unemployment trend  -(3-month average unemployment rate - its mean over the previous 60 months)
                      (quarterly: last quarter - mean of the previous 20 quarters); 60 months follows
                      the Macrosynergy academy notebook (3m MA minus the 5-year MA)
  external balance    change over 12 months of (X - M) / (X + M) summed over 12 rolling months
                      (quarterly: 4 rolling quarters, change over 4 quarters)
Monthly components (current vintage):
  confidence          BCI 3-month average - the same 6 months earlier, up to month t-1
  terms of trade      log(PCTOT of the last month <= t-1 / mean of the 12 months before it)
  carry               policy rate - cross-section mean (at t)
Cross-sectional z at t, clipped at +-3; labour = mean of the z of excess employment and
unemployment trend (one if only one exists); composite = mean of the available among labour,
confidence, external balance, terms of trade, carry (carry left out in high global FX volatility);
a currency is ranked only with >= 3 components."""
from __future__ import annotations

import numpy as np
import pandas as pd

CCYS15 = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF", "NOK", "SEK",
          "MXN", "KRW", "PLN", "CZK", "HUF"]
DEVELOPED = CCYS15[:10]
EMERGING = CCYS15[10:]
AREA = {"USA": "USD", "GBR": "GBP", "JPN": "JPY", "CAN": "CAD", "AUS": "AUD", "NZL": "NZD", "CHE": "CHF",
        "NOR": "NOK", "SWE": "SEK", "MEX": "MXN", "KOR": "KRW", "POL": "PLN", "CZE": "CZK", "HUN": "HUF"}
EA_CODES = ["EA20", "EA19", "EA18", "EA17", "EA16", "EA15", "EA13", "EA12", "EA11"]   # preference: widest
CLIP = 3.0
MIN_COMPONENTS = 3
COMPONENTS = ["labour", "confidence", "external", "tot", "carry"]


# ---------------------------------------------------------------------------
# One edition -> components
# ---------------------------------------------------------------------------

def _monthly(s: pd.Series) -> pd.Series:
    return s.sort_index()


def excess_employment(emp: pd.Series, freq: str) -> float | None:
    """emp: level indexed by period end (one edition)."""
    s = emp.dropna().sort_index()
    if freq == "M":
        avg = s.rolling(3, min_periods=3).mean()
        g = (avg / avg.shift(12) - 1) * 100
        hist = 60
    else:
        g = (s / s.shift(4) - 1) * 100
        hist = 20
    g = g.dropna()
    if len(g) < hist + 1:
        return None
    return float(g.iloc[-1] - g.iloc[-hist - 1:-1].median())


def unemployment_trend(u: pd.Series, freq: str) -> float | None:
    s = u.dropna().sort_index()
    if freq == "M":
        last, base = s.iloc[-3:].mean() if len(s) >= 3 else np.nan, s.iloc[-63:-3]
        need = 60
    else:
        last, base = s.iloc[-1] if len(s) else np.nan, s.iloc[-21:-1]
        need = 20
    if len(base) < need or not np.isfinite(last):
        return None
    return float(-(last - base.mean()))


def external_balance(x: pd.Series, m: pd.Series, freq: str) -> float | None:
    j = pd.concat([x.rename("x"), m.rename("m")], axis=1).dropna().sort_index()
    n = 12 if freq == "M" else 4
    sx, sm = j["x"].rolling(n, min_periods=n).sum(), j["m"].rolling(n, min_periods=n).sum()
    r = ((sx - sm) / (sx + sm)).dropna()
    if len(r) < n + 1:
        return None
    return float(r.iloc[-1] - r.iloc[-1 - n])


def edition_components(ed: pd.DataFrame) -> dict:
    """ed: rows of one area and one edition (MEASURE, FREQ, period, value).
    Monthly preferred per measure; quarterly when the edition has no monthly series."""
    out = {"freq": {}}

    def series(meas):
        g = ed[ed["MEASURE"] == meas]
        if g.empty:
            return None, None
        fr = "M" if (g["FREQ"] == "M").any() else "Q"
        g = g[g["FREQ"] == fr]
        return pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["period"])).sort_index(), fr

    emp, fe = series("EMP")
    un, fu = series("UNEMP")
    ex, fx = series("EX")
    im, fi = series("IM")
    out["excess_emp"] = excess_employment(emp, fe) if emp is not None else None
    out["unemp_trend"] = unemployment_trend(un, fu) if un is not None else None
    if ex is not None and im is not None and fx == fi:
        out["external"] = external_balance(ex, im, fx)
    else:
        out["external"] = None
    out["freq"] = {"EMP": fe, "UNEMP": fu, "EX": fx, "IM": fi}
    out["last_period"] = {k: (None if v is None else str(v.index.max().date())) for k, v in
                          (("EMP", emp), ("UNEMP", un), ("EX", ex), ("IM", im))}
    return out


# ---------------------------------------------------------------------------
# Monthly components (current vintage)
# ---------------------------------------------------------------------------

def confidence(bci: pd.Series, t: pd.Timestamp) -> float | None:
    """BCI 3-month average up to month t-1 minus the same 6 months earlier."""
    s = bci.dropna().sort_index()
    s = s[s.index <= (t - pd.offsets.MonthEnd(1))]
    if len(s) < 9 or s.index[-1] < t - pd.offsets.MonthEnd(1) - pd.offsets.MonthEnd(2):
        return None
    a = s.iloc[-3:].mean()
    b = s.iloc[-9:-6].mean()
    return float(a - b)


def terms_of_trade(p: pd.Series, t: pd.Timestamp, max_age_months: int = 12) -> float | None:
    s = p.dropna().sort_index()
    s = s[s.index <= (t - pd.offsets.MonthEnd(1))]
    if len(s) < 13:
        return None
    if (t - s.index[-1]).days > 31 * (max_age_months + 1):
        return None
    return float(np.log(s.iloc[-1] / s.iloc[-13:-1].mean()))


def cs_z(row: pd.Series, clip: float = CLIP) -> pd.Series:
    x = row.astype(float)
    sd = x.std(ddof=1)
    if not np.isfinite(sd) or sd == 0 or x.notna().sum() < 3:
        return x * np.nan
    return ((x - x.mean()) / sd).clip(-clip, clip)


def composite_row(raw: dict[str, pd.Series], high_vol: bool) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """raw: {component: Series over currencies} at one date (excess_emp, unemp_trend, confidence,
    external, tot, carry). -> (z table incl. labour, composite, count of components)."""
    z = pd.DataFrame({k: cs_z(v) for k, v in raw.items()})
    lab = z[["excess_emp", "unemp_trend"]].mean(axis=1, skipna=True)
    lab[z[["excess_emp", "unemp_trend"]].isna().all(axis=1)] = np.nan
    z["labour"] = lab
    use = ["labour", "confidence", "external", "tot"] + ([] if high_vol else ["carry"])
    n = z[use].notna().sum(axis=1)
    comp = z[use].mean(axis=1, skipna=True).where(n >= MIN_COMPONENTS)
    return z, comp, n


def last_ecb_business_day(month_end: pd.Timestamp, fx_dates: pd.DatetimeIndex) -> pd.Timestamp | None:
    d = fx_dates[(fx_dates <= month_end) & (fx_dates > month_end - pd.offsets.MonthBegin(1))]
    return d.max() if len(d) else None


# ---------------------------------------------------------------------------
# Panels (signals only: no forward return enters here)
# ---------------------------------------------------------------------------

class P5Inputs:
    def __init__(self, root=None):
        from pathlib import Path
        root = Path(root) if root else Path(__file__).resolve().parents[2]
        r = root / "data" / "research"
        self.oecd = pd.read_parquet(r / "p5_oecd_components.parquet")
        self.oecd["edition"] = self.oecd["edition"].astype(str)
        b = pd.read_parquet(r / "p5_bci.parquet")
        self.bci = {c: pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["period"])).sort_index()
                    for c, g in b.groupby("ccy")}
        p = pd.read_parquet(r / "p5_ctot.parquet")
        self.ctot = {c: pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["period"])).sort_index()
                     for c, g in p.groupby("ccy")}
        fx = pd.read_parquet(r / "p5_ecb_fx.parquet")
        self.fx = fx.pivot(index="date", columns="ccy", values="log_usd_value").sort_index()[CCYS15]
        pol = pd.read_parquet(r / "p5_bis_policy.parquet")
        self.policy = {c: pd.Series(g["rate"].to_numpy(float), index=pd.DatetimeIndex(g["date"])).sort_index()
                       for c, g in pol.groupby("ccy")}

    def edition_values(self, edition_cap: str) -> pd.DataFrame:
        """Per currency, the components of the latest edition <= edition_cap (YYYYMM)."""
        o = self.oecd[self.oecd["edition"] <= edition_cap]
        last = o.sort_values("edition").groupby("ccy").tail(1).set_index("ccy")
        return last[["edition", "excess_emp", "unemp_trend", "external"]].reindex(CCYS15)

    def rate_at(self, ccy: str, t: pd.Timestamp) -> float:
        s = self.policy.get(ccy)
        if s is None:
            return np.nan
        s = s[s.index <= t]
        return float(s.iloc[-1]) if len(s) else np.nan          # step function: last value carried forward

    def fx_vol_monthly(self) -> pd.Series:
        """Mean over the month of the daily mean |dlog spot vs USD| of the 14 non-USD currencies (bp)."""
        d = self.fx.drop(columns="USD").diff().abs().mean(axis=1) * 1e4
        return d.groupby(d.index.to_period("M")).mean()

    def high_vol_flags(self) -> pd.Series:
        v = self.fx_vol_monthly()
        out = {}
        for i, (per, x) in enumerate(v.items()):
            prev = v.iloc[max(0, i - 60): i]
            out[per] = bool(len(prev) >= 36 and (prev <= x).mean() * 100 >= 80) if len(prev) >= 36 else False
        return pd.Series(out)


def signals_at(inp: P5Inputs, t: pd.Timestamp, edition_cap: str, month_cap: pd.Timestamp, high_vol: bool) -> dict:
    """Raw components, z, composite at signal time t. edition_cap: latest usable OECD edition
    (YYYYMM); month_cap: month end whose BCI / CTOT are the last usable ones (+1 month = t's month)."""
    ed = inp.edition_values(edition_cap)
    tref = month_cap + pd.offsets.MonthEnd(1)          # confidence() / terms_of_trade() look at <= tref - 1 month
    raw = {
        "excess_emp": ed["excess_emp"].astype(float),
        "unemp_trend": ed["unemp_trend"].astype(float),
        "external": ed["external"].astype(float),
        "confidence": pd.Series({c: confidence(inp.bci[c], tref) if c in inp.bci else np.nan for c in CCYS15}, dtype=float),
        "tot": pd.Series({c: terms_of_trade(inp.ctot[c], tref) if c in inp.ctot else np.nan for c in CCYS15}, dtype=float),
    }
    rates = pd.Series({c: inp.rate_at(c, t) for c in CCYS15}, dtype=float)
    raw["carry"] = rates - rates.mean()
    raw = {k: v.reindex(CCYS15) for k, v in raw.items()}
    z, comp, n = composite_row(raw, high_vol)
    return {"raw": raw, "z": z, "composite": comp, "n": n, "editions": ed["edition"], "rates": rates}


def monthly_panel(inp: P5Inputs, start="1999-01-01") -> pd.DataFrame:
    flags = inp.high_vol_flags()
    rows = []
    months = pd.period_range(pd.Timestamp(start), inp.fx.index[-1], freq="M")
    for per in months:
        me = per.to_timestamp(how="end").normalize()
        t = last_ecb_business_day(me, inp.fx.index)
        if t is None:
            continue
        hv = bool(flags.get(per, False))
        s = signals_at(inp, t, per.strftime("%Y%m"), (per - 1).to_timestamp(how="end").normalize(), hv)
        rows.extend(_rows(t, per.strftime("%Y-%m"), s, hv))
    return pd.DataFrame(rows)


def weekly_panel(inp: P5Inputs, start="2015-01-02") -> pd.DataFrame:
    """Fridays: OECD edition of the previous month, BCI / CTOT to the month before the Friday's month - 1,
    FX / rates to the Friday, high-vol flag of the last completed month."""
    flags = inp.high_vol_flags()
    rows = []
    for t in pd.date_range(pd.Timestamp(start), inp.fx.index[-1], freq="W-FRI"):
        per = t.to_period("M")
        hv = bool(flags.get(per - 1, False))
        s = signals_at(inp, t, (per - 1).strftime("%Y%m"), (per - 1).to_timestamp(how="end").normalize(), hv)
        rows.extend(_rows(t, None, s, hv))
    return pd.DataFrame(rows)


def _rows(t, month, s, hv) -> list[dict]:
    comp = s["composite"]
    rank = comp.rank(ascending=False, method="min")
    out = []
    for c in CCYS15:
        r = {"asof": t, "month": month, "ccy": c, "high_vol": hv, "edition": s["editions"].get(c),
             "composite": comp.get(c), "rank": rank.get(c), "n_components": int(s["n"].get(c, 0)),
             "policy_rate": s["rates"].get(c)}
        for k, v in s["raw"].items():
            r[f"raw_{k}"] = v.get(c)
        for k in s["z"].columns:
            r[f"z_{k}"] = s["z"][k].get(c)
        out.append(r)
    return out
