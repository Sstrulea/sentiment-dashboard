"""TREND scoring — pure MA-structure technical score per instrument (integer ±3).

Reads data/price_history.parquet (daily OHLC, BOARD-keyed) and produces one
integer trend cell in [-3, +3] per instrument. This module is SCORING ONLY —
no column, no render, no SCORE integration (price first, score after; same
staging as COT/SENTIMENT).

MODEL (locked) — per instrument, on the daily close series:
  short  = +1 if SMA20 > SMA50  else -1
  long   = +1 if SMA50 > SMA200 else -1
  slope  = sign(SMA50[now] - SMA50[now-20])   (+1 / -1 / 0 within epsilon)
  raw    = short + long + slope                       in [-3, +3]
  factor = ADX(14) strength filter:
             ADX >= 22            -> 1.0
             15 <= ADX < 22       -> 0.5
             ADX <  15  (or NaN)  -> 0.25
  trend_cell = clamp(round(raw * factor), -3, +3)
  Convention: + = bullish for the instrument (same as SENTIMENT).

Rounding is half-away-from-zero (so 0.5 -> 1, 1.5 -> 2), NOT Python's
banker's rounding — keeps the ±0.5 cases symmetric and intuitive.

GUARDS (like COT):
  < 200 closes (SMA200) OR < ~15 bars (ADX) -> trend = None (empty), not 0.
  ADX NaN/undefined -> factor 0.25 (most conservative) + log.
  instrument absent from parquet (NASDAQ/DAX/NIKKEI/FTSE100/DXY) -> None, skip.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .price_fetch import load_symbol_map

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "price_history.parquet"
SYMBOLS_YAML = ROOT / "data" / "price_symbols.yaml"

# --- model constants (all calibratable here, not in code paths) ---
SMA_SHORT = 20
SMA_MID = 50
SMA_LONG = 200
SLOPE_LOOKBACK = 20
SLOPE_EPS = 0.0          # dead-zone on the SMA50 slope (absolute, price units)

ADX_PERIOD = 14
ADX_STRONG = 22.0        # >= -> factor 1.0
ADX_WEAK = 15.0          # >= -> factor 0.5  (else 0.25)
FACTOR_STRONG = 1.0
FACTOR_MEDIUM = 0.5
FACTOR_WEAK = 0.25

MIN_BARS_SMA = SMA_LONG          # need 200 closes for SMA200
MIN_BARS_ADX = 2 * ADX_PERIOD + 1  # ~29; ADX needs a couple of smoothing windows

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

def _round_half_away(x: float) -> int:
    """Round half AWAY from zero (0.5->1, -0.5->-1), unlike Python's round()."""
    if not np.isfinite(x):
        return 0
    return int(np.sign(x) * np.floor(np.abs(x) + 0.5))


def _clamp_round(x: float) -> int:
    """round-half-away then clamp into [-3, +3]."""
    return int(max(-3, min(3, _round_half_away(x))))


def _slope_sign(now: float, past: float, eps: float = SLOPE_EPS) -> int:
    diff = now - past
    if abs(diff) <= eps:
        return 0
    return 1 if diff > 0 else -1


def _factor_from_adx(adx: float) -> float:
    """Strength factor from an ADX value; NaN/undefined -> most conservative."""
    if adx is None or not np.isfinite(adx):
        return FACTOR_WEAK
    if adx >= ADX_STRONG:
        return FACTOR_STRONG
    if adx >= ADX_WEAK:
        return FACTOR_MEDIUM
    return FACTOR_WEAK


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

def trend_components(df: pd.DataFrame) -> tuple[int, int, int, int] | None:
    """(short, long, slope, raw) on the close series, or None if < 200 bars."""
    closes = pd.to_numeric(df["close"], errors="coerce").dropna().reset_index(drop=True)
    if len(closes) < MIN_BARS_SMA:
        return None

    sma20 = closes.rolling(SMA_SHORT).mean()
    sma50 = closes.rolling(SMA_MID).mean()
    sma200 = closes.rolling(SMA_LONG).mean()

    short = 1 if sma20.iloc[-1] > sma50.iloc[-1] else -1
    long = 1 if sma50.iloc[-1] > sma200.iloc[-1] else -1
    slope = _slope_sign(sma50.iloc[-1], sma50.iloc[-1 - SLOPE_LOOKBACK])
    raw = short + long + slope
    return short, long, slope, raw


def adx_factor(df: pd.DataFrame) -> float:
    """Strength factor in {1.0, 0.5, 0.25} from the latest ADX(14)."""
    adx = compute_adx(df, ADX_PERIOD)
    val = float(adx.iloc[-1]) if len(adx) else float("nan")
    if not np.isfinite(val):
        log.warning("ADX undefined (NaN); using conservative factor %.2f.", FACTOR_WEAK)
    return _factor_from_adx(val)


def trend_cell(df: pd.DataFrame) -> int | None:
    """Integer trend cell in [-3, +3], or None if data is insufficient."""
    comp = trend_components(df)
    if comp is None:
        return None
    _, _, _, raw = comp
    return _clamp_round(raw * adx_factor(df))


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
    return {k: None for k in ("short", "long", "slope", "raw", "adx", "factor", "trend_cell")}


def score_all(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML,
              server_today: pd.Timestamp | None = None) -> dict[str, dict]:
    """{board_key: {short, long, slope, raw, adx, factor, trend_cell}} for ALL
    board keys. Instruments without a (sufficient) series get an all-None entry.

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
        comp = trend_components(g) if len(g) >= MIN_BARS_SMA else None
        if comp is None:
            results[key] = _empty_entry()
            continue
        short, long, slope, raw = comp
        adx_series = compute_adx(g, ADX_PERIOD)
        adx_val = float(adx_series.iloc[-1]) if len(adx_series) else float("nan")
        if not np.isfinite(adx_val):
            log.warning("%s: ADX undefined; conservative factor %.2f.", key, FACTOR_WEAK)
        factor = _factor_from_adx(adx_val)
        results[key] = {
            "short": short,
            "long": long,
            "slope": slope,
            "raw": raw,
            "adx": adx_val,
            "factor": factor,
            "trend_cell": _clamp_round(raw * factor),
        }
    return results


# ---------------------------------------------------------------------------
# CLI report
# ---------------------------------------------------------------------------

def _print_report(parquet_path: Path = PARQUET, yaml_path: Path = SYMBOLS_YAML) -> None:
    res = score_all(parquet_path, yaml_path)
    scored = {k: v for k, v in res.items() if v["trend_cell"] is not None}
    empty = [k for k, v in res.items() if v["trend_cell"] is None]

    print(f"TREND scores — {len(scored)} instrument(s) with data, {len(empty)} None\n")
    hdr = f"{'board_key':<9} {'short':>5} {'long':>4} {'slope':>5} {'raw':>4} {'ADX':>6} {'factor':>6} {'trend':>5}"
    print(hdr)
    print("-" * len(hdr))
    for k in sorted(scored):
        v = scored[k]
        print(f"{k:<9} {v['short']:>5d} {v['long']:>4d} {v['slope']:>5d} {v['raw']:>4d} "
              f"{v['adx']:>6.1f} {v['factor']:>6.2f} {v['trend_cell']:>5d}")
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
