"""FAZA 1 (fix/watchdog-per-instrument) — per-instrument price freshness.

Closes the blind spot confirmed in docs/faza5-ftse100-frozen-diagnostic.md:
`economic_render._freshness()`'s `price` entry takes `p["date"].max()` over
ALL 37 board instruments combined — one instrument export dying (FTSE100,
frozen since 2026-05-15) is invisible as long as any other keeps updating.

Design (see docs/proposal-watchdog-per-instrument.md for the full writeup):
  - Threshold is DERIVED per instrument from its OWN observed trailing gap
    distribution (P95 of calendar-day gaps between consecutive bars, over
    the 90 days ending at its own last bar) — not a fixed day count. An
    index's weekend/holiday calendar differs from FX's; deriving from each
    series' own history absorbs that automatically, mirroring
    economic_compute's per-frequency `max_age_by_frequency` gate.
  - A board symbol with ZERO rows ever (e.g. DXY — no broker mapping,
    "by design" per data/price_symbols.yaml) is `no_data`, never `stale`:
    a known, accepted gap is not a new defect and must not alert daily.
  - Output is a single GROUPED report, not one entry per instrument.

Pure — no I/O beyond what's passed in; no exit()/print() side effects here
(see scripts/check_freshness.py for the CLI that decides exit codes).
"""
from __future__ import annotations

import pandas as pd

DEFAULT_TRAILING_DAYS = 90       # window (calendar days, ending at the instrument's
                                 # own last bar) used to derive its normal gap pattern
DEFAULT_SAFETY_MULTIPLIER = 1.5  # threshold = P95(trailing gaps) * this
DEFAULT_MIN_THRESHOLD_DAYS = 3.0 # floor: never tighter than a plain weekend
DEFAULT_MIN_GAPS = 5             # below this many trailing gaps, fall back
DEFAULT_FALLBACK_THRESHOLD_DAYS = 5.0  # used when history is too thin to derive one


def instrument_cadence_threshold(
    dates: pd.Series,
    *,
    trailing_days: int = DEFAULT_TRAILING_DAYS,
    safety_multiplier: float = DEFAULT_SAFETY_MULTIPLIER,
    min_threshold_days: float = DEFAULT_MIN_THRESHOLD_DAYS,
    min_gaps: int = DEFAULT_MIN_GAPS,
    fallback_threshold_days: float = DEFAULT_FALLBACK_THRESHOLD_DAYS,
) -> float:
    """Derive a stale-after threshold (days) from an instrument's OWN trailing
    gap distribution, so its particular weekend/holiday rhythm is absorbed
    without guessing it per-instrument. `dates` must already belong to ONE
    instrument (sorted internally). Ancient one-off discontinuities (a
    backfill artifact, not a real market closure) are excluded by construction
    — the window only looks at the `trailing_days` immediately before the
    instrument's own most recent bar, never its full history.
    """
    d = pd.to_datetime(pd.Series(dates)).dropna().sort_values()
    if d.empty:
        return fallback_threshold_days
    last = d.iloc[-1]
    window = d[d > last - pd.Timedelta(days=trailing_days)]
    gaps = window.diff().dt.days.dropna()
    if len(gaps) < min_gaps:
        return fallback_threshold_days
    p95 = float(gaps.quantile(0.95))
    return max(p95 * safety_multiplier, min_threshold_days)


def per_instrument_freshness(
    price_df: pd.DataFrame,
    board_symbols: list[str],
    as_of: pd.Timestamp,
    **threshold_kwargs,
) -> pd.DataFrame:
    """One row per `board_symbols` entry: {symbol, status, last_date, age_days,
    threshold_days}. `status` is one of:
      - "no_data"  — zero rows ever for this symbol (e.g. DXY, no broker
                     mapping configured — a known, accepted gap)
      - "stale"    — age_days > its own derived threshold
      - "fresh"    — otherwise
    Pure; does not read data/price_symbols.yaml or any file itself — pass in
    whatever `price_df`/`board_symbols` you already have.
    """
    as_of = pd.Timestamp(as_of)
    rows = []
    for sym in board_symbols:
        sub = price_df[price_df["symbol"] == sym] if not price_df.empty else price_df
        if sub is None or sub.empty:
            rows.append({"symbol": sym, "status": "no_data", "last_date": None,
                        "age_days": None, "threshold_days": None})
            continue
        dates = pd.to_datetime(sub["date"])
        last_date = dates.max()
        age_days = (as_of.normalize() - last_date.normalize()).days
        threshold = instrument_cadence_threshold(dates, **threshold_kwargs)
        status = "stale" if age_days > threshold else "fresh"
        rows.append({"symbol": sym, "status": status, "last_date": last_date,
                    "age_days": age_days, "threshold_days": round(threshold, 2)})
    return pd.DataFrame(rows, columns=["symbol", "status", "last_date", "age_days", "threshold_days"])


def freshness_report(per_instrument: pd.DataFrame) -> dict:
    """Collapse the per-instrument table into ONE grouped report — never one
    alert per instrument. `no_data` instruments are listed for visibility but
    excluded from `stale`/`any_stale` (a known, accepted gap is not a daily
    alert)."""
    stale = per_instrument[per_instrument["status"] == "stale"]
    no_data = per_instrument[per_instrument["status"] == "no_data"]
    fresh = per_instrument[per_instrument["status"] == "fresh"]
    return {
        "any_stale": bool(len(stale)),
        "stale_count": int(len(stale)),
        "stale": [
            {"symbol": r["symbol"], "last_date": r["last_date"].isoformat() if r["last_date"] is not None else None,
             "age_days": r["age_days"], "threshold_days": r["threshold_days"]}
            for _, r in stale.iterrows()
        ],
        "no_data": sorted(no_data["symbol"].tolist()),
        "fresh_count": int(len(fresh)),
        "total_count": int(len(per_instrument)),
    }
