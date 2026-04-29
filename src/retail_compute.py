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
    """Capitulation pressure: the crowded side is also deeply underwater.

    Long capitulation pressure fires only when:
        long_pct >= capitulation_long_min_crowd_pct
        AND avg_long_price > spot_estimate * (1 + capitulation_long_min_loss_pct/100)

    Short capitulation pressure fires only when:
        short_pct (= 100 - long_pct) >= capitulation_short_min_crowd_pct
        AND avg_short_price < spot_estimate * (1 - capitulation_short_min_loss_pct/100)

    The pip estimates are returned regardless (for use in modal text), but the
    boolean flags require the combined "crowded + losing" criteria so the
    badges only fire on genuine setups, not as wallpaper.
    """
    long_min_crowd = float(signal_config.get("capitulation_long_min_crowd_pct", 60))
    long_min_loss = float(signal_config.get("capitulation_long_min_loss_pct", 1.0)) / 100.0
    short_min_crowd = float(signal_config.get("capitulation_short_min_crowd_pct", 60))
    short_min_loss = float(signal_config.get("capitulation_short_min_loss_pct", 1.0)) / 100.0

    spot = spot_estimate
    if spot is None:
        spot = _spot_estimate(avg_long_price, avg_short_price, long_positions, short_positions)

    pip = _pip_size(spot)

    long_pressure = False
    short_pressure = False
    long_pnl_pips = 0.0
    short_pnl_pips = 0.0

    short_pct = None if long_pct is None or pd.isna(long_pct) else (100.0 - float(long_pct))

    if spot is not None and avg_long_price is not None and not pd.isna(avg_long_price):
        if pip > 0:
            long_pnl_pips = (float(spot) - float(avg_long_price)) / pip
        long_crowded = long_pct is not None and not pd.isna(long_pct) and float(long_pct) >= long_min_crowd
        long_deeply_underwater = float(avg_long_price) > float(spot) * (1.0 + long_min_loss)
        long_pressure = bool(long_crowded and long_deeply_underwater)

    if spot is not None and avg_short_price is not None and not pd.isna(avg_short_price):
        if pip > 0:
            short_pnl_pips = (float(avg_short_price) - float(spot)) / pip
        short_crowded = short_pct is not None and short_pct >= short_min_crowd
        short_deeply_underwater = float(avg_short_price) < float(spot) * (1.0 - short_min_loss)
        short_pressure = bool(short_crowded and short_deeply_underwater)

    return {
        "long_pressure": bool(long_pressure),
        "short_pressure": bool(short_pressure),
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
# COT confluence
# ---------------------------------------------------------------------------
#
# Both retail traders AND CFTC large speculators (non-commercials) are read as
# contrarian indicators at extremes — both groups statistically get squeezed
# at trend extremes. The high-conviction setup is when BOTH are crowded on the
# SAME side at extremes ("everyone is on the same wrong side") → strong
# contrarian confluence. When they disagree, there's no edge — it's noise.
# Commercials are ignored — they're hedgers, not directional bets, and their
# behavior reflects business needs not market views.
#
# COT side uses the 6M extreme percentile rank (0=6M low, 1=6M high) of
# large-spec long positioning, NOT the raw long%. That's how COT data is
# properly read — extremes within recent history, not absolute counts.

def compute_cot_confluence(
    retail_long_pct: float | None,
    cot_link: str | None,
    invert_cot: bool,
    cot_history_df: pd.DataFrame | None,
    signal_config: dict,
) -> dict | None:
    """Detect retail + large-spec confluence at same-direction extremes.

    Returns confluence dict only when retail AND CFTC large specs are BOTH at
    extremes on the SAME side. Otherwise returns None.

    Returns:
      {
        "has_confluence": True,
        "direction": "bullish_contrarian" | "bearish_contrarian",
        "retail_long_pct": float,           # 0..100
        "cot_spec_ext_6m": float,           # 0..1, post-invert if applicable
        "cot_report_date": str,
      }
    """
    if cot_link is None or not cot_link:
        return None
    if cot_history_df is None or cot_history_df.empty:
        return None
    if retail_long_pct is None or pd.isna(retail_long_pct):
        return None

    df = cot_history_df
    if "symbol" not in df.columns:
        return None
    sub = df[df["symbol"].astype(str).str.upper() == str(cot_link).upper()]
    if sub.empty:
        return None
    sub = sub.sort_values("report_date_as_yyyy_mm_dd")
    last = sub.iloc[-1]

    cot_ext = last.get("spec_ext_6m")
    if cot_ext is None or pd.isna(cot_ext):
        return None
    cot_spec_ext_6m = float(cot_ext)
    if invert_cot:
        cot_spec_ext_6m = 1.0 - cot_spec_ext_6m
    cot_spec_ext_6m = max(0.0, min(1.0, cot_spec_ext_6m))

    retail_extreme_pct = float(signal_config.get("cot_confluence_retail_extreme_pct", 70))
    cot_extreme_rank = float(signal_config.get("cot_confluence_cot_extreme_rank", 0.85))

    retail_long = float(retail_long_pct)
    retail_extreme_long = retail_long >= retail_extreme_pct
    retail_extreme_short = retail_long <= (100.0 - retail_extreme_pct)
    cot_extreme_long = cot_spec_ext_6m >= cot_extreme_rank
    cot_extreme_short = cot_spec_ext_6m <= (1.0 - cot_extreme_rank)

    direction: str | None = None
    if retail_extreme_long and cot_extreme_long:
        direction = "bearish_contrarian"  # both crowded long → fade → expect down
    elif retail_extreme_short and cot_extreme_short:
        direction = "bullish_contrarian"  # both crowded short → fade → expect up

    if direction is None:
        return None

    report_date = last.get("report_date_as_yyyy_mm_dd")
    if isinstance(report_date, pd.Timestamp):
        report_date_str = report_date.date().isoformat()
    elif report_date is None:
        report_date_str = None
    else:
        try:
            report_date_str = pd.Timestamp(report_date).date().isoformat()
        except Exception:
            report_date_str = str(report_date)

    return {
        "has_confluence": True,
        "direction": direction,
        "retail_long_pct": float(round(retail_long, 2)),
        "cot_spec_ext_6m": float(round(cot_spec_ext_6m, 4)),
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
    cap = signals.get("capitulation") or {}
    if cap.get("long_pressure"):
        cnt += 1
    if cap.get("short_pressure"):
        cnt += 1
    if (signals.get("vol_position_divergence") or {}).get("has_divergence"):
        cnt += 1
    extremes = signals.get("extremes") or {}
    if (extremes.get("ext_3m") or {}).get("class") in ("ext-95", "ext-05"):
        cnt += 1
    if (extremes.get("ext_6m") or {}).get("class") in ("ext-95", "ext-05"):
        cnt += 1
    cot_conf = signals.get("cot_confluence")
    if cot_conf and cot_conf.get("has_confluence"):
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
                "capitulation": {
                    "long_pressure": False, "short_pressure": False,
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
                "cot_confluence": None,
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
    capitulation = compute_underwater_flag(
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

    cot_conf = compute_cot_confluence(
        long_pct,
        symbol_cfg.get("cot_link"),
        bool(symbol_cfg.get("invert_cot", False)),
        cot_history_df,
        signal_config,
    )

    signals = {
        "contrarian": contrarian,
        "capitulation": {
            "long_pressure": capitulation["long_pressure"],
            "short_pressure": capitulation["short_pressure"],
            "long_pnl_pips_estimate": capitulation["long_pnl_pips_estimate"],
            "short_pnl_pips_estimate": capitulation["short_pnl_pips_estimate"],
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
        "cot_confluence": cot_conf,
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
