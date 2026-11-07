"""Monthly macro (OECD, data/macro_monthly.parquet), as of a date: cycle root and
macro_momentum. A period is visible on asof t when avail <= t (period end + the
pre-registered publication lag). Values are the current vintage.

Components (per currency, on the visible periods; k = 3 months: 3 obs monthly, 1 obs quarterly)
  infl_head   yoy(CPI)      - yoy(CPI) k earlier
  infl_core   yoy(CPI core) - yoy k earlier
  lab_unemp   -(unemployment rate - k earlier)
  lab_emp     100 ln(employment / k earlier)
  gr_ip       yoy(IP) - yoy k earlier
  gr_bci      BCI - BCI k earlier
  gr_cli      CLI - CLI k earlier   (variant only; heavily revised)
z of a component = (last - mean) / std over its own derived history inside 5 years
(60 monthly / 20 quarterly obs, >= 36 / 12). Category = mean of the available z:
Inflation (infl_head, infl_core), Labor (lab_unemp, lab_emp), Growth (gr_ip, gr_bci);
Growth_cli (gr_ip, gr_cli). Composites: equal weights over the available categories
(production category weights are 1/1/1, data/economic_indicators.yaml, so (i) = (ii)),
and Inflation + Labor only.

Cycle root
  growth impulse     sign of BCI - BCI 3 months earlier (variant: CLI); >= 0 -> up
  inflation impulse  sign of the 3-month change of core CPI YoY (headline where no core)
  policy phase       BIS policy rate (daily, avail = date + 1): last change within 365 days
                     up -> tightening, down -> easing; none within 365 days -> hold_top
                     (last change up) / hold_bottom (last change down); no change seen -> None
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MACRO_FILE = ROOT / "data" / "macro_monthly.parquet"
COMPONENTS = {"infl_head": ("cpi", "yoy_d"), "infl_core": ("cpi_core", "yoy_d"),
              "lab_unemp": ("unemp", "neg_d"), "lab_emp": ("emp", "log_d"),
              "gr_ip": ("ip", "yoy_d"), "gr_bci": ("bci", "d"), "gr_cli": ("cli", "d")}
CATEGORIES = {"Inflation": ["infl_head", "infl_core"], "Labor": ["lab_unemp", "lab_emp"],
              "Growth": ["gr_ip", "gr_bci"]}
CATEGORIES_CLI = {"Inflation": ["infl_head", "infl_core"], "Labor": ["lab_unemp", "lab_emp"],
                  "Growth": ["gr_ip", "gr_cli"]}
WINDOW = {"M": (60, 36), "Q": (20, 12)}
STALE_DAYS = {"M": 200, "Q": 300}       # older visible data -> the component is missing
PHASES = ["tightening", "easing", "hold_top", "hold_bottom"]


def derived(g: pd.DataFrame, how: str) -> pd.Series:
    """3-month change series of one (ccy, series) frame, aligned by date (the value
    of the period 3 months earlier, month-end), so a missing month only blanks the
    changes that need it."""
    g = g.sort_values("period")
    idx = pd.DatetimeIndex(g["period"].to_numpy())
    v = pd.Series(g["value"].to_numpy(float), index=idx)
    y = pd.Series(g["yoy"].to_numpy(float), index=idx)
    back = idx - pd.offsets.DateOffset(months=3)
    back = back + pd.offsets.MonthEnd(0)
    v0, y0 = v.reindex(back).to_numpy(), y.reindex(back).to_numpy()
    if how == "yoy_d":
        d = y.to_numpy() - y0
    elif how == "neg_d":
        d = -(v.to_numpy() - v0)
    elif how == "log_d":
        d = 100 * (np.log(v.to_numpy()) - np.log(v0))
    else:
        d = v.to_numpy() - v0
    return pd.Series(d, index=idx)


class MacroInputs:
    def __init__(self, frame: pd.DataFrame, policy: dict[str, pd.Series] | None = None):
        f = frame.copy()
        f["period"] = pd.to_datetime(f["period"])
        f["avail"] = pd.to_datetime(f["avail"])
        self.frame = f.sort_values("avail")
        self.by = {k: g.sort_values("period") for k, g in f.groupby(["ccy", "series"])}
        self.policy = policy or {}
        self._cache: dict = {}

    @classmethod
    def load(cls, path: Path = MACRO_FILE, backbone: Path | None = None) -> "MacroInputs":
        from .backbone import BACKBONE_FILE
        bb = pd.read_parquet(backbone or BACKBONE_FILE)
        pol = {c[4:]: pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["date"].to_numpy()))
               for c, g in bb[bb["kind"] == "policy"].sort_values("date").groupby("series")}
        return cls(pd.read_parquet(path), pol)

    def visible(self, ccy: str, series: str, asof) -> pd.DataFrame:
        g = self.by.get((ccy, series))
        if g is None:
            return pd.DataFrame(columns=self.frame.columns)
        return g[g["avail"] <= pd.Timestamp(asof)]

    # --- macro_momentum -------------------------------------------------------
    def component(self, ccy: str, comp: str, asof) -> tuple[float | None, float | None, pd.Timestamp | None]:
        """(last derived value, z, period) of one component as of asof."""
        series, how = COMPONENTS[comp]
        g = self.visible(ccy, series, asof)
        if len(g) < 5:
            return None, None, None
        d = derived(g, how).dropna()
        if d.empty:
            return None, None, None
        fr = g["freq"].iloc[0]
        win, mn = WINDOW[fr]
        last_p = d.index[-1]
        if last_p < pd.Timestamp(asof) - pd.Timedelta(days=STALE_DAYS[fr]):
            return None, None, None
        w = d.iloc[-win:]
        if len(w) < mn or w.std(ddof=1) == 0:
            return float(d.iloc[-1]), None, last_p
        return float(d.iloc[-1]), float((w.iloc[-1] - w.mean()) / w.std(ddof=1)), last_p

    def momentum(self, ccy: str, asof) -> dict:
        key = ("mm", ccy, pd.Timestamp(asof).normalize())
        if key in self._cache:
            return self._cache[key]
        comps = {c: self.component(ccy, c, asof) for c in COMPONENTS}
        z = {c: v[1] for c, v in comps.items()}

        def cat(defs):
            out = {}
            for k, cs in defs.items():
                vals = [z[c] for c in cs if z[c] is not None]
                out[k] = float(np.mean(vals)) if vals else None
            return out
        cats, cats_cli = cat(CATEGORIES), cat(CATEGORIES_CLI)

        def mean(vals):
            vals = [v for v in vals if v is not None]
            return float(np.mean(vals)) if vals else None
        res = {"z": z, "categories": cats, "categories_cli": cats_cli,
               "composite_eq": mean(cats.values()),
               "composite_il": mean([cats["Inflation"], cats["Labor"]]),
               "composite_cli": mean(cats_cli.values()),
               "periods": {c: (None if v[2] is None else v[2].strftime("%Y-%m-%d")) for c, v in comps.items()}}
        self._cache[key] = res
        return res

    # --- cycle root -------------------------------------------------------------
    def impulse(self, ccy: str, series: str, how: str, asof) -> int | None:
        g = self.visible(ccy, series, asof)
        if len(g) < 5:
            return None
        d = derived(g, how).dropna()
        if d.empty or d.index[-1] < pd.Timestamp(asof) - pd.Timedelta(days=200):
            return None
        return 1 if d.iloc[-1] >= 0 else -1

    def phase(self, ccy: str, asof) -> str | None:
        s = self.policy.get(ccy)
        if s is None:
            return None
        s = s[s.index + pd.Timedelta(days=1) <= pd.Timestamp(asof)].dropna()
        ch = s.diff()
        ch = ch[ch.abs() > 1e-9]
        if ch.empty:
            return None
        last_d, last_v = ch.index[-1], ch.iloc[-1]
        if (pd.Timestamp(asof) - last_d).days <= 365:
            return "tightening" if last_v > 0 else "easing"
        return "hold_top" if last_v > 0 else "hold_bottom"

    def cycle(self, ccy: str, asof) -> dict:
        gi = self.impulse(ccy, "bci", "d", asof)
        gi_cli = self.impulse(ccy, "cli", "d", asof)
        ii = self.impulse(ccy, "cpi_core", "yoy_d", asof)
        src = "core"
        if ii is None:
            ii, src = self.impulse(ccy, "cpi", "yoy_d", asof), "headline"
        ph = self.phase(ccy, asof)
        sg = lambda x: None if x is None else ("up" if x > 0 else "down")  # noqa: E731
        cell16 = None if None in (ph, gi, ii) else f"{ph}|g_{sg(gi)}|i_{sg(ii)}"
        cell8 = None if None in (ph, ii) else f"{ph}|i_{sg(ii)}"
        return {"phase": ph, "growth": sg(gi), "growth_cli": sg(gi_cli), "inflation": sg(ii),
                "inflation_source": src if ii is not None else None, "cell16": cell16, "cell8": cell8}
