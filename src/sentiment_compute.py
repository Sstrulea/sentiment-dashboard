"""Compute layer for VIX/VIX3M ratio and CBOE Put/Call ratios.

Pure transformations over parquet frames — no I/O of fresh data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PC_VARIANT_KEYS = ("total", "equity", "index", "spx_spxw", "vix")

VIX_RATIO_THRESHOLDS = {
    "acute_panic": 1.10,
    "backwardation": 1.00,
    "complacency": 0.90,
}


# ---------------------------------------------------------------------------
# VIX ratio regime logic
# ---------------------------------------------------------------------------

def _classify_regime(ratio: float) -> str:
    if pd.isna(ratio):
        return "UNKNOWN"
    if ratio > 1.10:
        return "ACUTE_PANIC"
    if ratio > 1.00:
        return "BACKWARDATION"
    if ratio >= 0.90:
        return "NORMAL"
    return "COMPLACENCY"


def compute_vix_ratio_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add regime/crossover/days_since_regime_flip columns to the VIX ratio frame.

    Input columns (required): date, vix_close, vix3m_close, ratio
    """
    out = df.copy().sort_values("date").reset_index(drop=True)
    ratio = out["ratio"]
    prev = ratio.shift(1)

    out["regime"] = ratio.apply(_classify_regime)
    out["crossed_above_1"] = (prev < 1.00) & (ratio >= 1.00)
    out["crossed_below_1"] = (prev >= 1.00) & (ratio < 1.00)

    # days_since_regime_flip: 0 on the first day of each run, incrementing after.
    regime_vals = out["regime"].values
    counter = np.zeros(len(out), dtype=int)
    for i in range(1, len(out)):
        counter[i] = 0 if regime_vals[i] != regime_vals[i - 1] else counter[i - 1] + 1
    out["days_since_regime_flip"] = counter

    return out


# ---------------------------------------------------------------------------
# P/C ratio compute
# ---------------------------------------------------------------------------

def _load_thresholds(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _signal_for(value: float, high_put: float, high_call: float, interpretation: str) -> str:
    if pd.isna(value) or pd.isna(high_put) or pd.isna(high_call):
        return "neutral"
    if interpretation == "contrarian":
        if value >= high_put:
            return "bearish_extreme"  # high puts = crowd fearful → contrarian bullish
        if value <= high_call:
            return "bullish_extreme"  # high calls = crowd greedy → contrarian bearish
        return "neutral"
    if interpretation == "direct":
        if value >= high_put:
            return "bullish"
        if value <= high_call:
            return "bearish"
        return "neutral"
    raise ValueError(f"Unknown interpretation: {interpretation!r}")


def _percentile_rank_in_window(series: pd.Series, window: int) -> pd.Series:
    """For each row, percentile rank (0..100) of current value vs previous `window` observations."""
    s = pd.Series(series.values, dtype=float)

    def _rank(arr):
        if len(arr) == 0:
            return np.nan
        current = arr[-1]
        if np.isnan(current):
            return np.nan
        valid = arr[~np.isnan(arr)]
        if len(valid) == 0:
            return np.nan
        return float((valid < current).sum() + 0.5 * (valid == current).sum()) / len(valid) * 100.0

    return s.rolling(window, min_periods=1).apply(_rank, raw=True).values


def compute_pc_metrics(df: pd.DataFrame, thresholds_yaml_path: str) -> dict[str, dict[str, Any]]:
    """Compute rolling percentiles, MAs, and signals for each P/C variant.

    Returns dict keyed by variant name with per-variant `df`, `current`, `config`.
    """
    cfg = _load_thresholds(thresholds_yaml_path)
    dynamic_from_day = int(cfg.get("dynamic_from_day", 252))
    window = int(cfg.get("rolling_window_days", 252))
    variants_cfg = cfg["variants"]

    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)

    out: dict[str, dict[str, Any]] = {}
    for variant in PC_VARIANT_KEYS:
        vcfg = variants_cfg[variant]
        static_high_put = float(vcfg["high_put"])
        static_high_call = float(vcfg["high_call"])
        interpretation = vcfg["interpretation"]

        series = pd.to_numeric(df[variant], errors="coerce")
        ma_10 = series.rolling(10, min_periods=1).mean()
        p90 = series.rolling(window, min_periods=1).quantile(0.90)
        p10 = series.rolling(window, min_periods=1).quantile(0.10)
        valid_count = series.notna().rolling(window, min_periods=1).sum()
        pct_rank = pd.Series(_percentile_rank_in_window(series, window), index=series.index)

        use_rolling = valid_count >= dynamic_from_day
        high_put_eff = np.where(use_rolling, p90, static_high_put)
        high_call_eff = np.where(use_rolling, p10, static_high_call)
        thresholds_source = np.where(use_rolling, "rolling_1y", "static_default")

        signals: list[str] = []
        for v, hp, hc in zip(series.values, high_put_eff, high_call_eff):
            signals.append(_signal_for(v, hp, hc, interpretation))

        vdf = pd.DataFrame(
            {
                "date": df["date"].values,
                "value": series.values,
                "ma_10": ma_10.values,
                "p90_rolling": p90.values,
                "p10_rolling": p10.values,
                "percentile_rank": pct_rank.values,
                "signal": signals,
                "high_put_eff": high_put_eff,
                "high_call_eff": high_call_eff,
                "thresholds_source": thresholds_source,
            }
        )

        if n > 0:
            last = vdf.iloc[-1]
            current = {
                "value": None if pd.isna(last["value"]) else float(last["value"]),
                "ma_10": None if pd.isna(last["ma_10"]) else float(last["ma_10"]),
                "p90_rolling": None if pd.isna(last["p90_rolling"]) else float(last["p90_rolling"]),
                "p10_rolling": None if pd.isna(last["p10_rolling"]) else float(last["p10_rolling"]),
                "percentile_rank": (
                    None if pd.isna(last["percentile_rank"]) else float(last["percentile_rank"])
                ),
                "signal": last["signal"],
                "thresholds_effective": {
                    "high_put": (
                        None if pd.isna(last["high_put_eff"]) else float(last["high_put_eff"])
                    ),
                    "high_call": (
                        None if pd.isna(last["high_call_eff"]) else float(last["high_call_eff"])
                    ),
                },
                "thresholds_source": str(last["thresholds_source"]),
            }
        else:
            current = {
                "value": None,
                "ma_10": None,
                "p90_rolling": None,
                "p10_rolling": None,
                "percentile_rank": None,
                "signal": "neutral",
                "thresholds_effective": {
                    "high_put": static_high_put,
                    "high_call": static_high_call,
                },
                "thresholds_source": "static_default",
            }

        config = {
            "display_name": vcfg["display_name"],
            "confidence": vcfg["confidence"],
            "source": vcfg["source"],
            "interpretation": interpretation,
            "static_high_put": static_high_put,
            "static_high_call": static_high_call,
        }
        if "note" in vcfg:
            config["note"] = vcfg["note"]

        out[variant] = {"df": vdf, "current": current, "config": config}

    return out


# ---------------------------------------------------------------------------
# Chart payloads
# ---------------------------------------------------------------------------

def _tail_window(df: pd.DataFrame, window_days: int) -> pd.DataFrame:
    if window_days is None or window_days < 0:
        return df
    return df.tail(window_days).reset_index(drop=True)


def _to_date_list(col: pd.Series) -> list[str]:
    return [pd.Timestamp(x).date().isoformat() for x in col]


def _to_value_list(col: pd.Series) -> list[float | None]:
    return [None if pd.isna(v) else float(v) for v in col]


def build_pc_chart_payload(pc_metrics: dict, variant: str, window_days: int) -> dict:
    if variant not in pc_metrics:
        raise KeyError(f"Unknown variant: {variant!r}")
    bundle = pc_metrics[variant]
    df = _tail_window(bundle["df"], window_days)
    cfg = bundle["config"]
    cur = bundle["current"]

    payload = {
        "dates": _to_date_list(df["date"]),
        "values": _to_value_list(df["value"]),
        "ma_10": _to_value_list(df["ma_10"]),
        "p90_rolling": _to_value_list(df["p90_rolling"]),
        "p10_rolling": _to_value_list(df["p10_rolling"]),
        "static_high_put": cfg["static_high_put"],
        "static_high_call": cfg["static_high_call"],
        "thresholds_source": cur["thresholds_source"],
        "interpretation": cfg["interpretation"],
        "current_value": cur["value"],
        "current_signal": cur["signal"],
        "display_name": cfg["display_name"],
        "confidence": cfg["confidence"],
        "note": cfg.get("note"),
    }
    return payload


def build_vix_chart_payload(vix_df: pd.DataFrame, window_days: int) -> dict:
    metrics = compute_vix_ratio_metrics(vix_df)
    view = _tail_window(metrics, window_days)

    crossovers_above = _to_date_list(view.loc[view["crossed_above_1"], "date"])
    crossovers_below = _to_date_list(view.loc[view["crossed_below_1"], "date"])

    if not metrics.empty:
        last = metrics.iloc[-1]
        current_value = None if pd.isna(last["ratio"]) else float(last["ratio"])
        current_regime = str(last["regime"])
        days_in_regime = int(last["days_since_regime_flip"])
    else:
        current_value = None
        current_regime = "UNKNOWN"
        days_in_regime = 0

    return {
        "dates": _to_date_list(view["date"]),
        "ratio": _to_value_list(view["ratio"]),
        "vix": _to_value_list(view["vix_close"]),
        "vix3m": _to_value_list(view["vix3m_close"]),
        "regimes": [str(r) for r in view["regime"].values],
        "crossovers_above_1": crossovers_above,
        "crossovers_below_1": crossovers_below,
        "current_value": current_value,
        "current_regime": current_regime,
        "days_in_regime": days_in_regime,
        "thresholds": dict(VIX_RATIO_THRESHOLDS),
    }
