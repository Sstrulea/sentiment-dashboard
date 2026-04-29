"""Pure transformations on retail sentiment parquet. No I/O of fresh data.

All inputs are pandas DataFrames already in memory; this module never reads
parquets directly. The render layer is responsible for I/O.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extreme classification (visual buckets — must match COT _ext_class)
# ---------------------------------------------------------------------------

def extreme_class(value: float | None) -> str:
    """Classify a 0..1 extreme value into a visual bucket.

    Returns one of: "ext-95", "ext-70", "ext-neutral", "ext-30", "ext-05",
    "ext-na". Same thresholds and class names as `_ext_class` in src/render.py
    for visual consistency.
    """
    if value is None:
        return "ext-na"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "ext-na"
    if pd.isna(v):
        return "ext-na"
    if v >= 0.95:
        return "ext-95"
    if v >= 0.70:
        return "ext-70"
    if v <= 0.05:
        return "ext-05"
    if v <= 0.30:
        return "ext-30"
    return "ext-neutral"


# ---------------------------------------------------------------------------
# Trailing-window extremes
# ---------------------------------------------------------------------------

def _last_n_days_window(df: pd.DataFrame, days: int) -> pd.DataFrame:
    """Return the slice of `df` whose `fetched_at` is within the last `days`
    calendar days from the most recent row (inclusive).
    """
    if df.empty:
        return df
    end = pd.to_datetime(df["fetched_at"].max())
    start = end - pd.Timedelta(days=days)
    out = df[pd.to_datetime(df["fetched_at"]) >= start]
    return out.sort_values("fetched_at").reset_index(drop=True)


def _extreme_in_window(values: pd.Series, min_n: int) -> tuple[float | None, int]:
    """Compute today's percentile rank within the given series.

    Returns (extreme_0_to_1 | None, n). If n < min_n, returns (None, n).
    The extreme is `(rank - 1) / (n - 1)` with strict-less + half-equal
    ranking, applied to the most recent observation.
    """
    s = pd.to_numeric(values, errors="coerce").dropna().reset_index(drop=True)
    n = int(len(s))
    if n < min_n:
        return None, n
    if n < 2:
        return None, n
    current = float(s.iloc[-1])
    less = int((s < current).sum())
    equal = int((s == current).sum())
    rank = less + 0.5 * equal
    extreme = float(rank) / float(n - 1) if n > 1 else 0.5
    extreme = max(0.0, min(1.0, extreme))
    return extreme, n


def compute_3m_extreme(
    series_long_pct: pd.Series,
    signal_config: dict,
) -> tuple[float | None, int]:
    """Today's long% rank in trailing ~63 trading days. (None, n) if insufficient."""
    min_n = int(signal_config.get("ramp_in", {}).get("min_n_for_extreme", 20))
    return _extreme_in_window(series_long_pct, min_n)


def compute_6m_extreme(
    series_long_pct: pd.Series,
    signal_config: dict,
) -> tuple[float | None, int]:
    """Today's long% rank in trailing ~126 trading days. (None, n) if insufficient."""
    min_n = int(signal_config.get("ramp_in", {}).get("min_n_for_extreme", 20))
    return _extreme_in_window(series_long_pct, min_n)


# ---------------------------------------------------------------------------
# Contrarian signal
# ---------------------------------------------------------------------------

def compute_contrarian_signal(long_pct: float | None, signal_config: dict) -> str:
    """Returns 'bearish_contrarian', 'bullish_contrarian', or 'neutral'."""
    if long_pct is None or pd.isna(long_pct):
        return "neutral"
    threshold = float(signal_config.get("contrarian_threshold", 70))
    short_pct = 100.0 - float(long_pct)
    if float(long_pct) >= threshold:
        return "bearish_contrarian"
    if short_pct >= threshold:
        return "bullish_contrarian"
    return "neutral"


# ---------------------------------------------------------------------------
# Underwater flag
# ---------------------------------------------------------------------------

def _spot_estimate(
    avg_long_price: float | None,
    avg_short_price: float | None,
    long_positions: int | None,
    short_positions: int | None,
) -> float | None:
    """Position-weighted midpoint between the two avg entry prices.

    Falls back to a simple mean if position counts are missing or zero.
    Returns None if both prices are missing.
    """
    have_long = avg_long_price is not None and not pd.isna(avg_long_price)
    have_short = avg_short_price is not None and not pd.isna(avg_short_price)
    if not have_long and not have_short:
        return None
    if not have_long:
        return float(avg_short_price)
    if not have_short:
        return float(avg_long_price)
    lp = int(long_positions) if long_positions else 0
    sp = int(short_positions) if short_positions else 0
    if lp + sp <= 0:
        return (float(avg_long_price) + float(avg_short_price)) / 2.0
    return (float(avg_long_price) * lp + float(avg_short_price) * sp) / float(lp + sp)


def _pip_size(spot: float | None) -> float:
    """Heuristic pip size from price magnitude.

    JPY pairs (>=10) → 0.01, most FX → 0.0001, large numbers (indices, BTC) → 1.
    Used only for the human-readable PnL display.
    """
    if spot is None or pd.isna(spot):
        return 0.0001
    s = abs(float(spot))
    if s >= 1000:
        return 1.0
    if s >= 50:
        return 0.01
    return 0.0001


def compute_underwater_flag(
    long_pct: float | None,
    avg_long_price: float | None,
    avg_short_price: float | None,
    spot_estimate: float | None,
    signal_config: dict,
    long_positions: int | None = None,
    short_positions: int | None = None,
) -> dict:
    """Are the crowd's long/short books underwater vs spot?

    Threshold = `underwater_threshold_pct` (default 0.5%) deviation from spot.
    """
    threshold = float(signal_config.get("underwater_threshold_pct", 0.5)) / 100.0

    spot = spot_estimate
    if spot is None:
        spot = _spot_estimate(avg_long_price, avg_short_price, long_positions, short_positions)

    pip = _pip_size(spot)

    longs_underwater = False
    shorts_underwater = False
    long_pnl_pips = 0.0
    short_pnl_pips = 0.0

    if spot is not None and avg_long_price is not None and not pd.isna(avg_long_price):
        # Longs underwater when avg long entry > spot * (1 + threshold)
        longs_underwater = float(avg_long_price) > float(spot) * (1.0 + threshold)
        if pip > 0:
            long_pnl_pips = (float(spot) - float(avg_long_price)) / pip

    if spot is not None and avg_short_price is not None and not pd.isna(avg_short_price):
        # Shorts underwater when avg short entry < spot * (1 - threshold)
        shorts_underwater = float(avg_short_price) < float(spot) * (1.0 - threshold)
        if pip > 0:
            short_pnl_pips = (float(avg_short_price) - float(spot)) / pip

    return {
        "longs_underwater": bool(longs_underwater),
        "shorts_underwater": bool(shorts_underwater),
        "long_pnl_pips_estimate": float(round(long_pnl_pips, 1)),
        "short_pnl_pips_estimate": float(round(short_pnl_pips, 1)),
        "spot_estimate": None if spot is None else float(spot),
    }


# ---------------------------------------------------------------------------
# Volume vs position divergence
# ---------------------------------------------------------------------------

def compute_volume_position_divergence(
    long_pct: float | None,
    long_volume: float | None,
    short_volume: float | None,
    signal_config: dict,
) -> dict:
    """Detect cases where most accounts are long but most volume is short (or vice-versa).

    Indicates a few large positions on one side dominate volume despite the
    crowd leaning the other way.
    """
    threshold = float(signal_config.get("vol_position_divergence_pp", 15))

    if long_pct is None or pd.isna(long_pct):
        return {
            "has_divergence": False,
            "position_long_pct": None,
            "volume_long_pct": None,
            "divergence_pp": None,
        }

    pos_long_pct = float(long_pct)

    if (
        long_volume is None or short_volume is None
        or pd.isna(long_volume) or pd.isna(short_volume)
    ):
        return {
            "has_divergence": False,
            "position_long_pct": pos_long_pct,
            "volume_long_pct": None,
            "divergence_pp": None,
        }

    total_vol = float(long_volume) + float(short_volume)
    if total_vol <= 0:
        return {
            "has_divergence": False,
            "position_long_pct": pos_long_pct,
            "volume_long_pct": None,
            "divergence_pp": None,
        }

    vol_long_pct = (float(long_volume) / total_vol) * 100.0
    divergence_pp = abs(vol_long_pct - pos_long_pct)

    return {
        "has_divergence": bool(divergence_pp > threshold),
        "position_long_pct": float(round(pos_long_pct, 2)),
        "volume_long_pct": float(round(vol_long_pct, 2)),
        "divergence_pp": float(round(divergence_pp, 2)),
    }


# ---------------------------------------------------------------------------
# COT cross-link divergence
# ---------------------------------------------------------------------------

def compute_cot_divergence(
    retail_long_pct: float | None,
    cot_link: str | None,
    invert_cot: bool,
    cot_history_df: pd.DataFrame | None,
    signal_config: dict,
) -> dict | None:
    """Compare retail long bias to CFTC large-spec long bias.

    Returns None when no COT cross-link is configured or no COT data is found.
    Otherwise returns a dict with normalized values and a divergence flag.
    """
    if cot_link is None or not cot_link:
        return None
    if cot_history_df is None or cot_history_df.empty:
        return None
    if retail_long_pct is None or pd.isna(retail_long_pct):
        return None

    # Find latest snapshot for this CFTC symbol.
    df = cot_history_df.copy()
    if "symbol" not in df.columns:
        return None
    sub = df[df["symbol"].astype(str).str.upper() == str(cot_link).upper()]
    if sub.empty:
        return None
    sub = sub.sort_values("report_date_as_yyyy_mm_dd")
    last = sub.iloc[-1]

    long_all = last.get("noncomm_positions_long_all")
    short_all = last.get("noncomm_positions_short_all")
    if long_all is None or short_all is None or pd.isna(long_all) or pd.isna(short_all):
        return None
    total = float(long_all) + float(short_all)
    if total <= 0:
        return None

    cot_spec_long = float(long_all) / total  # 0..1
    if invert_cot:
        cot_spec_long = 1.0 - cot_spec_long

    retail_long_norm = float(retail_long_pct) / 100.0
    magnitude = abs(retail_long_norm - cot_spec_long)
    threshold = float(signal_config.get("cot_divergence_threshold", 0.4))

    report_date = last.get("report_date_as_yyyy_mm_dd")
    if isinstance(report_date, (pd.Timestamp,)):
        report_date_str = report_date.date().isoformat()
    elif report_date is None:
        report_date_str = None
    else:
        try:
            report_date_str = pd.Timestamp(report_date).date().isoformat()
        except Exception:
            report_date_str = str(report_date)

    return {
        "has_divergence": bool(magnitude > threshold),
        "retail_long_normalized": float(round(retail_long_norm, 4)),
        "cot_spec_long_normalized": float(round(cot_spec_long, 4)),
        "divergence_magnitude": float(round(magnitude, 4)),
        "cot_report_date": report_date_str,
    }


# ---------------------------------------------------------------------------
# Per-symbol payload assembly
# ---------------------------------------------------------------------------

WINDOW_DAYS = {
    "1W":  7,
    "1M":  30,
    "3M":  90,
    "6M":  180,
    "1Y":  365,
    "All": -1,
}


def _to_iso_list(col: pd.Series) -> list[str]:
    return [pd.Timestamp(x).isoformat() for x in col]


def _to_jsonable_floats(col: pd.Series) -> list[float | None]:
    out: list[float | None] = []
    for v in col:
        if v is None or pd.isna(v):
            out.append(None)
        else:
            out.append(float(v))
    return out


def _slice_history_window(symbol_df: pd.DataFrame, days: int) -> pd.DataFrame:
    if symbol_df.empty:
        return symbol_df
    if days < 0:
        return symbol_df.sort_values("fetched_at").reset_index(drop=True)
    end = pd.to_datetime(symbol_df["fetched_at"].max())
    start = end - pd.Timedelta(days=days)
    out = symbol_df[pd.to_datetime(symbol_df["fetched_at"]) >= start]
    return out.sort_values("fetched_at").reset_index(drop=True)


def _build_history_windows(symbol_df: pd.DataFrame) -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    for label, days in WINDOW_DAYS.items():
        view = _slice_history_window(symbol_df, days)
        spot_est = view.apply(
            lambda r: _spot_estimate(
                r.get("avg_long_price"), r.get("avg_short_price"),
                r.get("long_positions"), r.get("short_positions"),
            ),
            axis=1,
        ) if not view.empty else pd.Series(dtype=float)
        out[label] = {
            "dates": _to_iso_list(view["fetched_at"]) if not view.empty else [],
            "long_pct": _to_jsonable_floats(view["long_pct"]) if not view.empty else [],
            "spot_estimate": _to_jsonable_floats(spot_est) if not view.empty else [],
        }
    return out


def _alert_count(signals: dict) -> int:
    cnt = 0
    if signals.get("contrarian", "neutral") != "neutral":
        cnt += 1
    underwater = signals.get("underwater") or {}
    if underwater.get("longs_underwater"):
        cnt += 1
    if underwater.get("shorts_underwater"):
        cnt += 1
    if (signals.get("vol_position_divergence") or {}).get("has_divergence"):
        cnt += 1
    extremes = signals.get("extremes") or {}
    if (extremes.get("ext_3m") or {}).get("class") in ("ext-95", "ext-05"):
        cnt += 1
    if (extremes.get("ext_6m") or {}).get("class") in ("ext-95", "ext-05"):
        cnt += 1
    cot_div = signals.get("cot_divergence")
    if cot_div and cot_div.get("has_divergence"):
        cnt += 1
    return cnt


def compute_signals_for_symbol(
    history_df: pd.DataFrame,
    symbol_cfg: dict,
    signal_config: dict,
    cot_history_df: pd.DataFrame | None = None,
) -> dict:
    """Compute the full per-symbol signal block from a single symbol's history.

    `history_df` must contain only rows for the target symbol, sorted by
    `fetched_at`. Returns a dict matching the schema documented in the spec.
    """
    if history_df.empty:
        return {
            "current": None,
            "signals": {
                "contrarian": "neutral",
                "underwater": {
                    "longs_underwater": False, "shorts_underwater": False,
                    "long_pnl_pips_estimate": 0.0, "short_pnl_pips_estimate": 0.0,
                },
                "vol_position_divergence": {
                    "has_divergence": False,
                    "position_long_pct": None, "volume_long_pct": None,
                    "divergence_pp": None,
                },
                "extremes": {
                    "ext_3m": {"value": None, "n": 0, "class": "ext-na"},
                    "ext_6m": {"value": None, "n": 0, "class": "ext-na"},
                },
                "cot_divergence": None,
            },
            "alert_count": 0,
            "history": {label: {"dates": [], "long_pct": [], "spot_estimate": []} for label in WINDOW_DAYS},
        }

    df = history_df.sort_values("fetched_at").reset_index(drop=True)
    last = df.iloc[-1]

    long_pct = _safe_float(last.get("long_pct"))
    short_pct = _safe_float(last.get("short_pct"))
    avg_long = _safe_float(last.get("avg_long_price"))
    avg_short = _safe_float(last.get("avg_short_price"))
    long_pos = _safe_int(last.get("long_positions"))
    short_pos = _safe_int(last.get("short_positions"))
    total_pos = _safe_int(last.get("total_positions"))
    long_vol = _safe_float(last.get("long_volume"))
    short_vol = _safe_float(last.get("short_volume"))

    spot = _spot_estimate(avg_long, avg_short, long_pos, short_pos)

    contrarian = compute_contrarian_signal(long_pct, signal_config)
    underwater = compute_underwater_flag(
        long_pct, avg_long, avg_short, spot, signal_config,
        long_positions=long_pos, short_positions=short_pos,
    )
    vol_pos_div = compute_volume_position_divergence(
        long_pct, long_vol, short_vol, signal_config,
    )

    # Extremes use the per-symbol long-pct series within the trailing window.
    win_3m = _last_n_days_window(df, 90)
    win_6m = _last_n_days_window(df, 180)
    ext_3m_val, ext_3m_n = compute_3m_extreme(win_3m["long_pct"], signal_config)
    ext_6m_val, ext_6m_n = compute_6m_extreme(win_6m["long_pct"], signal_config)

    cot_div = compute_cot_divergence(
        long_pct,
        symbol_cfg.get("cot_link"),
        bool(symbol_cfg.get("invert_cot", False)),
        cot_history_df,
        signal_config,
    )

    signals = {
        "contrarian": contrarian,
        "underwater": {
            "longs_underwater": underwater["longs_underwater"],
            "shorts_underwater": underwater["shorts_underwater"],
            "long_pnl_pips_estimate": underwater["long_pnl_pips_estimate"],
            "short_pnl_pips_estimate": underwater["short_pnl_pips_estimate"],
        },
        "vol_position_divergence": vol_pos_div,
        "extremes": {
            "ext_3m": {
                "value": ext_3m_val,
                "n": int(ext_3m_n),
                "class": extreme_class(ext_3m_val),
            },
            "ext_6m": {
                "value": ext_6m_val,
                "n": int(ext_6m_n),
                "class": extreme_class(ext_6m_val),
            },
        },
        "cot_divergence": cot_div,
    }

    current = {
        "long_pct": long_pct,
        "short_pct": short_pct,
        "long_volume": long_vol,
        "short_volume": short_vol,
        "long_positions": long_pos,
        "short_positions": short_pos,
        "total_positions": total_pos,
        "avg_long_price": avg_long,
        "avg_short_price": avg_short,
        "spot_estimate": spot,
        "fetched_at": pd.Timestamp(last["fetched_at"]).isoformat(),
    }

    return {
        "current": current,
        "signals": signals,
        "alert_count": _alert_count(signals),
        "history": _build_history_windows(df),
    }


def build_symbol_payload(
    symbol: str,
    history_df: pd.DataFrame,
    cot_history_df: pd.DataFrame | None,
    symbols_config: dict,
    signal_config: dict,
) -> dict:
    """Compose the full per-symbol object for the JSON payload."""
    sym_cfg = (symbols_config.get("symbols") or {}).get(symbol, {})
    sym_history = history_df[history_df["symbol"] == symbol] if not history_df.empty else history_df

    sig = compute_signals_for_symbol(
        sym_history,
        sym_cfg,
        signal_config,
        cot_history_df=cot_history_df,
    )
    return {
        "symbol": symbol,
        "display": sym_cfg.get("display", symbol),
        "current": sig["current"],
        "signals": sig["signals"],
        "alert_count": sig["alert_count"],
        "history": sig["history"],
    }


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    return None if f is None else int(round(f))
