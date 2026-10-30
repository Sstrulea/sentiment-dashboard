"""Phase-3 regimes, as of a date.

risk_regime_v1   data/trees/regime_v1.yml on data/regime_inputs.parquet (rolling 5-year
                 percentiles; three-valued rules, see the YAML).
trend / range    cross-sectional, weekly, on H.10 basket levels (data/backbone_rates.parquet):
                 efficiency ratio over 13 weeks per currency = |net 13-week move| / sum of the
                 13 weekly |moves|, averaged over the 8 currencies; `trend` when it is >= the
                 median of the same average over the previous 5 years (260 Fridays, >= 104),
                 else `range`. Plus the percentile of the 13-week realised volatility (daily
                 basket returns, std averaged over the 8) within the same 5 years.
Both read only data available on the asof (daily data: date + 1 day)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import schema
from .data import Inputs
from .engine import classify, eval_nodes

ROOT = Path(__file__).resolve().parents[2]
REGIME_INPUTS = ROOT / "data" / "regime_inputs.parquet"
REGIME_V1 = ROOT / "data" / "trees" / "regime_v1.yml"
ER_WEEKS = 13
MEDIAN_WEEKS = 260
MEDIAN_MIN = 104


def load_v1() -> dict:
    return schema.validate_global(schema.load_yaml(REGIME_V1), REGIME_V1.name)


class RegimeV1:
    def __init__(self, inputs: Inputs | None = None, cfg: dict | None = None):
        self.cfg = cfg or load_v1()
        self.inputs = inputs or Inputs.load(REGIME_INPUTS)
        self._cache: dict = {}

    def at(self, asof) -> dict:
        t = pd.Timestamp(asof).normalize()
        if t not in self._cache:
            res = eval_nodes(self.cfg["nodes"], self.inputs, t)
            out = classify(self.cfg["classifiers"]["risk_regime_v1"], res)
            out["values"] = {k: r.value for k, r in res.items()}
            self._cache[t] = out
        return self._cache[t]

    def label(self, asof) -> str:
        return self.at(asof)["regime"]


def weekly_levels(levels: pd.DataFrame, fridays: pd.DatetimeIndex) -> pd.DataFrame:
    """Basket log level at the last fixing usable on each Friday (date <= t - 1 day)."""
    lv = levels.sort_index()
    idx = lv.index.searchsorted(fridays - pd.Timedelta(days=1), side="right") - 1
    out = lv.iloc[np.clip(idx, 0, None)].copy()
    out.index = fridays
    out[idx < 0] = np.nan
    return out


def trend_range(basket_levels: pd.DataFrame, fridays: pd.DatetimeIndex) -> pd.DataFrame:
    """Per Friday: er (8-currency mean efficiency ratio), er_median_5y, regime
    (trend | range | None), vol13 (mean 13-week std of daily basket returns, bp),
    vol_pct (percentile of vol13 in the previous 5 years incl. today)."""
    wk = weekly_levels(basket_levels, fridays)
    mv = wk.diff()
    net = (wk - wk.shift(ER_WEEKS)).abs()
    path = mv.abs().rolling(ER_WEEKS, min_periods=ER_WEEKS).sum()
    er = (net / path).mean(axis=1, skipna=True)
    er[path.isna().all(axis=1)] = np.nan
    med = er.shift(1).rolling(MEDIAN_WEEKS, min_periods=MEDIAN_MIN).median()
    reg = pd.Series(np.where(er.isna() | med.isna(), None, np.where(er >= med, "trend", "range")), index=fridays)
    daily = basket_levels.sort_index().diff() * 1e4
    vols = []
    for t in fridays:
        d = daily[(daily.index <= t - pd.Timedelta(days=1)) & (daily.index > t - pd.Timedelta(days=1 + 7 * ER_WEEKS))]
        vols.append(float(d.std(ddof=1).mean()) if len(d) > 20 else np.nan)
    vol = pd.Series(vols, index=fridays)
    vp = []
    for i, t in enumerate(fridays):
        w = vol.iloc[max(0, i - MEDIAN_WEEKS + 1): i + 1].dropna()
        vp.append(100.0 * float((w <= vol.iloc[i]).sum()) / len(w) if len(w) >= MEDIAN_MIN and pd.notna(vol.iloc[i]) else np.nan)
    return pd.DataFrame({"er": er, "er_median_5y": med, "regime": reg, "vol13": vol, "vol_pct": vp}, index=fridays)
