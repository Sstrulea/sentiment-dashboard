"""TREND scoring v2 — 3-layer daily trend score per instrument (integer −3…+3).

Reads data/price_history.parquet (daily OHLC, BOARD-keyed) and produces one
integer trend cell in [-3, +3] per instrument. SCORING ONLY (price first, score
after; same staging as COT/SENTIMENT). Same ±3 range as v1 ⇒ its weight in the
composite is unchanged.

MODEL (v2) — per instrument, on the daily CLOSE series (last closed bar):
  Layer 1 REGIME (dominant, −2…+2):
    bull_points = (close>SMA50) + (close>SMA200) + (SMA50>SMA200)   each 0/1
    map 3→+2, 2→+1, 1→−1, 0→−2   (no neutral regime — neutrality is Layer 2's job;
    a classic uptrend pullback [below SMA50, above SMA200, SMA50>SMA200] = 2 → +1).
  Layer 2 MOMENTUM (−1…+1):
    slope_atr = (SMA50[0] − SMA50[lookback]) / (lookback × ATR14)   (ATR-normalized,
    so the threshold scales across instruments)
    > +SLOPE_THRESH_ATR → +1;  < −SLOPE_THRESH_ATR → −1;  else 0
  Layer 3 ADX(14): DISPLAY-ONLY "trend quality" — kept in the payload, shown in the
    modal, NO effect on the score.
  trend_cell = clamp(regime + momentum, −3, +3).
  Convention: + = bullish for the instrument (same as SENTIMENT).

Rationale (v1 defects fixed): v1 raw = short(SMA10v20)+long(SMA20v50)+slope(SMA20)
double-penalized short-term pullbacks (position + slope correlated) and its ADX factor
damped fresh breakouts (low ADX). v2 makes the long regime dominant and ADX display-only.

GUARDS: < MIN_BARS (SMA_long, 200) valid closes -> None (excluded, not 0). ATR<=0 /
undefined -> momentum 0. Instrument absent from parquet -> None, skip.
Calibration params live in config/trend.yaml (SLOPE_THRESH_ATR etc.).
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .price_fetch import load_symbol_map

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "price_history.parquet"
SYMBOLS_YAML = ROOT / "data" / "price_symbols.yaml"
TREND_YAML = ROOT / "config" / "trend.yaml"


def _load_config() -> dict:
    """Load config/trend.yaml with safe defaults (missing file/keys -> defaults)."""
    try:
        with open(TREND_YAML) as f:
            c = yaml.safe_load(f) or {}
    except FileNotFoundError:
        c = {}
    reg = c.get("regime", {}) or {}
    mom = c.get("momentum", {}) or {}
    # back-compat: a single slope_thresh_atr degenerates to enter==exit (no hysteresis).
    thresh = mom.get("slope_thresh_atr")
    return {
        "sma_mid": int(reg.get("sma_mid", 50)),
        "sma_long": int(reg.get("sma_long", 200)),
        "slope_lookback": int(mom.get("slope_lookback", 20)),
        "atr_period": int(mom.get("atr_period", 14)),
        "slope_enter": float(mom.get("slope_enter", thresh if thresh is not None else 0.035)),
        "slope_exit": float(mom.get("slope_exit", thresh if thresh is not None else 0.025)),
        "min_bars": int(c.get("min_bars", 200)),
        "adx_period": int(c.get("adx_period", 14)),
    }


_CFG = _load_config()
SMA_MID = _CFG["sma_mid"]                 # 50 — regime mid MA + momentum slope MA
SMA_LONG = _CFG["sma_long"]               # 200 — regime long MA
SLOPE_LOOKBACK = _CFG["slope_lookback"]   # 20 bars
ATR_PERIOD = _CFG["atr_period"]           # 14
SLOPE_ENTER = _CFG["slope_enter"]         # 0.035 — activate ±1 above this
SLOPE_EXIT = _CFG["slope_exit"]           # 0.025 — deactivate below this (hysteresis band)
MIN_BARS = _CFG["min_bars"]               # 200
ADX_PERIOD = _CFG["adx_period"]           # 14 (display-only)

REGIME_MAP = {3: 2, 2: 1, 1: -1, 0: -2}

# Daily bars are stamped with the SERVER open date (EA writes TimeToString on the
# bar's server open time). "Today" in server wall-clock is utcnow + this offset;
# the bar dated on it is still FORMING and must be excluded (Option A: score the
# last CLOSED bar). +3 = EET/EEST, the typical MetaQuotes demo server in summer.
# The cron runs ~01:00–02:00 server, hours from midnight, so a ±1–2h offset error
# never changes which bar is excluded — robust by design. Calibratable.
SERVER_UTC_OFFSET_HOURS = 3


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: int = -3, hi: int = 3) -> int:
    """Clamp an integer trend score into [-3, +3]."""
    return int(max(lo, min(hi, x)))


def atr(df: pd.DataFrame, n: int = ATR_PERIOD) -> pd.Series:
    """ATR(n) via Wilder RMA of true range (used to normalize the momentum slope)."""
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return _wilder_rma(tr, n)


# ---------------------------------------------------------------------------
# ADX(14) — Wilder smoothing
# ---------------------------------------------------------------------------

def _wilder_rma(values: pd.Series, n: int) -> pd.Series:
    """Wilder's running moving average, seeded with the simple mean of the
    first `n` finite values. Returns a same-length series (NaN before seed)."""
    arr = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    if len(arr) < n:
        return pd.Series(out, index=values.index)
    seed_slice = arr[:n]
    if np.isnan(seed_slice).all():
        return pd.Series(out, index=values.index)  # nothing to seed (e.g. flat market)
    seed = np.nanmean(seed_slice)
    out[n - 1] = seed
    prev = seed
    for i in range(n, len(arr)):
        cur = arr[i]
        if not np.isfinite(cur):
            cur = prev  # carry through gaps rather than poison the recursion
        prev = prev + (cur - prev) / n
        out[i] = prev
    return pd.Series(out, index=values.index)


def compute_adx(df: pd.DataFrame, n: int = ADX_PERIOD) -> pd.Series:
    """Wilder ADX(n) on (high, low, close). Returns the ADX series (NaN early)."""
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")

    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    up = high - prev_high
    down = prev_low - low
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    atr = _wilder_rma(tr, n)
    # +DI / -DI; ATR==0 (flat market) -> undefined DI -> NaN propagates to ADX.
    atr_safe = atr.replace(0.0, np.nan)
    plus_di = 100.0 * _wilder_rma(plus_dm, n) / atr_safe
    minus_di = 100.0 * _wilder_rma(minus_dm, n) / atr_safe

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = _wilder_rma(dx, n)
    return adx


# ---------------------------------------------------------------------------
# Components / factor / cell  (per-instrument df, ascending by date)
# ---------------------------------------------------------------------------

def regime_score(closes: pd.Series) -> tuple[int, int] | None:
    """Layer 1 — (bull_points, regime) from SMA50/SMA200 structure, or None if
    < MIN_BARS closes. bull_points = (close>SMA50)+(close>SMA200)+(SMA50>SMA200)."""
    closes = pd.to_numeric(closes, errors="coerce").dropna().reset_index(drop=True)
    if len(closes) < MIN_BARS:
        return None
    sma_mid = closes.rolling(SMA_MID).mean().iloc[-1]
    sma_long = closes.rolling(SMA_LONG).mean().iloc[-1]
    px = closes.iloc[-1]
    bull_points = int(px > sma_mid) + int(px > sma_long) + int(sma_mid > sma_long)
    return bull_points, REGIME_MAP[bull_points]


def slope_atr_series(df: pd.DataFrame) -> pd.Series:
    """Per-bar slope_atr = (SMA50[t]−SMA50[t−lookback]) / (lookback × ATR14[t]).
    NaN where undefined (early bars, ATR<=0). Used both for the current display value
    and for the hysteresis walk."""
    closes = pd.to_numeric(df["close"], errors="coerce")
    sma_mid = closes.rolling(SMA_MID).mean()
    slope = sma_mid - sma_mid.shift(SLOPE_LOOKBACK)
    atr_s = atr(df, ATR_PERIOD).replace(0.0, np.nan)
    return slope / (SLOPE_LOOKBACK * atr_s)


def hysteresis_step(slope_atr: float, prev: int, enter: float = None, exit: float = None) -> int:
    """One hysteresis step: ±1 activates when |slope_atr| > enter (sign of slope_atr),
    deactivates to 0 when |slope_atr| < exit, and in the band [exit, enter] KEEPS the
    previous state `prev`. NaN → 0. Cold start = prev 0 → active only if > enter."""
    enter = SLOPE_ENTER if enter is None else enter
    exit = SLOPE_EXIT if exit is None else exit
    if slope_atr is None or not np.isfinite(slope_atr):
        return 0
    mag = abs(slope_atr)
    if mag > enter:
        return 1 if slope_atr > 0 else -1
    if mag < exit:
        return 0
    return int(prev)   # band [exit, enter] → keep previous state (hysteresis)


def momentum_hysteresis(sa_series: pd.Series, enter: float = None, exit: float = None) -> int:
    """Walk the slope_atr series applying `hysteresis_step` from a cold start (0) to the
    last bar. Stateless — the final momentum is fully determined by the price history."""
    m = 0
    for sa in sa_series:
        m = hysteresis_step(float(sa) if sa is not None else float("nan"), m, enter, exit)
    return m


def momentum_score(df: pd.DataFrame, enter: float = None, exit: float = None) -> tuple[float, int]:
    """Layer 2 — (current slope_atr, momentum). Momentum applies HYSTERESIS over the
    historical slope_atr series (enter/exit band). Returns (nan, 0) if undefined."""
    sa = slope_atr_series(df)
    sa_valid = sa.dropna()
    if sa_valid.empty:
        return float("nan"), 0
    return float(sa_valid.iloc[-1]), momentum_hysteresis(sa_valid, enter, exit)


def trend_cell(df: pd.DataFrame) -> int | None:
    """Integer trend cell in [-3, +3] = clamp(regime + momentum), or None if
    insufficient data. ADX plays no part (display-only)."""
    closes = pd.to_numeric(df["close"], errors="coerce").dropna().reset_index(drop=True)
    reg = regime_score(closes)
    if reg is None:
        return None
    _, regime = reg
    _, mom = momentum_score(df)
    return _clamp(regime + mom)


# ---------------------------------------------------------------------------
# Loader + score_all
# ---------------------------------------------------------------------------

def _server_today(now_utc: pd.Timestamp | None = None) -> pd.Timestamp:
    """Midnight of the current trading day in SERVER wall-clock.

    server_now = utcnow + SERVER_UTC_OFFSET_HOURS, normalized to midnight. A daily
    bar dated >= this is the current-day (forming) bar.
    """
    now = now_utc if now_utc is not None else pd.Timestamp.utcnow().tz_localize(None)
    return (pd.Timestamp(now) + pd.Timedelta(hours=SERVER_UTC_OFFSET_HOURS)).normalize()


def drop_forming_bar(df: pd.DataFrame, server_today: pd.Timestamp | None = None) -> pd.DataFrame:
    """Keep only CLOSED daily bars: date strictly before the current server day.

    Excludes the in-formation bar (date == today server). This removes the
    CURRENT-DAY bar — it does NOT subtract a fixed 1 day — so it is weekend/holiday
    safe (Monday → last remaining bar is automatically Friday) and a no-op when the
    parquet is already stale (latest bar already closed).
    """
    if df is None or df.empty:
        return df
    st = server_today if server_today is not None else _server_today()
    return df[pd.to_datetime(df["date"]) < st].reset_index(drop=True)


def load_history(parquet_path: Path = PARQUET, server_today: pd.Timestamp | None = None,
                 drop_forming: bool = True) -> pd.DataFrame:
    """Load the OHLC parquet; by default exclude the forming (current server-day)
    bar so every downstream computation (SMA20/50/200, ADX) sees only closed bars."""
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"])
    if drop_forming:
        df = drop_forming_bar(df, server_today)
    return df


def _series_for(df: pd.DataFrame, board_key: str) -> pd.DataFrame:
    g = df[df["symbol"] == board_key]
    return (
        g.drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _empty_entry() -> dict:
    return {k: None for k in ("bull_points", "regime", "slope_atr", "momentum", "adx", "trend_cell")}


def score_all(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML,
              server_today: pd.Timestamp | None = None) -> dict[str, dict]:
    """{board_key: {bull_points, regime, slope_atr, momentum, adx, trend_cell}} for
    ALL board keys. Instruments without a sufficient series (< MIN_BARS closes) get an
    all-None entry. ADX is display-only ("trend quality"), no effect on trend_cell.

    The forming (current server-day) bar is excluded at load — scores reflect the
    last CLOSED daily bar. `server_today` overrides "now" (for tests)."""
    _, board_keys = load_symbol_map(yaml_path)

    if not parquet_path.exists():
        log.warning("price_history.parquet absent; all trend cells None.")
        return {k: _empty_entry() for k in board_keys}

    df = load_history(parquet_path, server_today=server_today)
    results: dict[str, dict] = {}
    for key in board_keys:
        g = _series_for(df, key)
        closes = pd.to_numeric(g["close"], errors="coerce").dropna().reset_index(drop=True)
        reg = regime_score(closes)
        if reg is None:
            results[key] = _empty_entry()
            continue
        bull_points, regime = reg
        slope_atr, momentum = momentum_score(g)   # hysteresis walk over the series
        # ADX(14) — display-only trend-quality metadata, NOT part of the score.
        adx_series = compute_adx(g, ADX_PERIOD)
        adx_val = float(adx_series.iloc[-1]) if len(adx_series) else float("nan")
        results[key] = {
            "bull_points": bull_points,
            "regime": regime,
            "slope_atr": None if not np.isfinite(slope_atr) else round(float(slope_atr), 4),
            "momentum": momentum,
            "adx": None if not np.isfinite(adx_val) else round(adx_val, 1),
            "trend_cell": _clamp(regime + momentum),
        }
    return results


# ---------------------------------------------------------------------------
# CLI report
# ---------------------------------------------------------------------------

def _print_report(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML) -> None:
    res = score_all(parquet_path, yaml_path)
    scored = {k: v for k, v in res.items() if v["trend_cell"] is not None}
    empty = [k for k, v in res.items() if v["trend_cell"] is None]

    print(f"TREND v2 scores — {len(scored)} instrument(s) with data, {len(empty)} None\n")
    hdr = f"{'board_key':<9} {'bull_pts':>8} {'regime':>6} {'slope/atr':>9} {'mom':>4} {'ADX*':>5} {'trend':>5}"
    print(hdr + "   (*ADX display-only)")
    print("-" * len(hdr))
    for k in sorted(scored):
        v = scored[k]
        sa = "—" if v["slope_atr"] is None else f"{v['slope_atr']:>9.3f}"
        adx = "—" if v["adx"] is None else f"{v['adx']:>5.1f}"
        print(f"{k:<9} {v['bull_points']:>8d} {v['regime']:>+6d} {sa:>9} {v['momentum']:>+4d} "
              f"{adx:>5} {v['trend_cell']:>+5d}")
    print()
    print(f"None (no/insufficient series): {', '.join(empty) if empty else '—'}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Compute TREND scores from data/price_history.parquet")
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    _print_report()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
