"""COT sentiment scoring engine for the economic dashboard SENTIMENT column.

Pure transformations over already-enriched COT frames — no I/O of fresh data
inside the scoring functions. The trailing percentile ranks (``spec_extreme_6m``
/ ``spec_extreme_3y``, 0..1) and ``spec_net`` are produced upstream by
``src.compute.compute_metrics`` and are REUSED here, never recomputed.

Sign convention (global): ``+`` = bullish for the asset. Metals trade as a
single contract with NO inversion — crowded speculative longs (high extreme)
yield a negative Level (contrarian-bearish for the metal).

Scoring is split into three pure pieces — ``level_score`` (contrarian
positioning), ``flow_score`` (directional momentum of net positioning) and
``cot_cell`` (their clamped sum) — plus a thin ``score_metals`` orchestrator
and an even thinner parquet loader kept separate from the pure functions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Column names of the enriched COT frame consumed by score_metals(). These match
# the output of src.compute.compute_metrics (the existing, read-only producer of
# the extremes) plus the symbol mapping from data/contracts.yaml.
EXT_6M_COL = "spec_extreme_6m"
EXT_3Y_COL = "spec_extreme_3y"
NET_COL = "spec_net"
SYMBOL_COL = "symbol"
DATE_COL = "report_date_as_yyyy_mm_dd"

# Metals universe (exact CFTC symbols from data/contracts.yaml).
METAL_SYMBOLS: tuple[str, ...] = ("GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER")

# Flow tuning.
FLOW_LOOKBACK = 4          # net change measured over 4 weekly reports
FLOW_SIGMA_WINDOW = 52     # volatility window for the 4w-change distribution
FLOW_MIN_REPORTS = 12      # below this, flow is not trustworthy
FLOW_Z_THRESHOLD = 0.5     # |z| band for a directional signal

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "history.parquet"


# ---------------------------------------------------------------------------
# Level: contrarian positioning extreme
# ---------------------------------------------------------------------------

def level_score(ext_6m: float, ext_3y: float | None) -> tuple[int, dict]:
    """Map blended positioning extremes to a contrarian Level in [-3, +3].

    ``ext_6m`` / ``ext_3y`` are trailing percentile ranks in 0..1 (as produced
    by compute_metrics). The blend is the average of the two, rescaled to a
    0..100 percentile. When ``ext_3y`` is missing (None/NaN) the blend falls
    back to ``ext_6m`` alone and ``basis`` becomes ``"6m_only"``.

    Bands are contrarian (crowded long -> negative), lower-inclusive /
    upper-exclusive.
    """
    if ext_3y is None or pd.isna(ext_3y):
        blend = float(ext_6m) * 100.0
        basis = "6m_only"
    else:
        blend = (float(ext_6m) + float(ext_3y)) / 2.0 * 100.0
        basis = "6m_3y"

    if blend >= 85:
        level = -3
    elif blend >= 70:
        level = -2
    elif blend >= 55:
        level = -1
    elif blend >= 45:
        level = 0
    elif blend >= 30:
        level = 1
    elif blend >= 15:
        level = 2
    else:
        level = 3

    return level, {"blend": blend, "basis": basis}


# ---------------------------------------------------------------------------
# Flow: directional momentum of net positioning
# ---------------------------------------------------------------------------

def flow_score(net_history: pd.Series) -> tuple[int, dict]:
    """Score the 4-week change in net positioning, directionally, in [-1, +1].

    ``net_history`` is the spec_net series for ONE symbol, sorted ascending by
    report date. We measure the latest 4-report change (``chg4``) against the
    volatility of all 4-week changes over the trailing ``FLOW_SIGMA_WINDOW``
    reports. The mean is intentionally NOT subtracted — the sign of ``z`` is the
    direction of the flow.

    Thresholds are directional (NOT contrarian): adding longs / covering shorts
    (positive flow) leans bullish; the reverse leans bearish.

    Guards: fewer than ``FLOW_MIN_REPORTS`` reports, or a zero/NaN sigma, yield
    a flat 0 with ``basis="insufficient_history"``.
    """
    net = pd.to_numeric(pd.Series(net_history), errors="coerce").dropna()
    n = len(net)

    insufficient = {
        "chg4": float("nan"),
        "sigma": float("nan"),
        "z": float("nan"),
        "basis": "insufficient_history",
    }

    if n < FLOW_MIN_REPORTS:
        return 0, insufficient

    chg4 = float(net.iloc[-1] - net.iloc[-(FLOW_LOOKBACK + 1)])
    chg4_series = net.diff(FLOW_LOOKBACK).dropna()
    sigma = float(chg4_series.tail(FLOW_SIGMA_WINDOW).std())

    if sigma == 0 or np.isnan(sigma):
        return 0, {**insufficient, "chg4": chg4, "sigma": sigma}

    z = chg4 / sigma
    if z >= FLOW_Z_THRESHOLD:
        flow = 1
    elif z <= -FLOW_Z_THRESHOLD:
        flow = -1
    else:
        flow = 0

    return flow, {"chg4": chg4, "sigma": sigma, "z": z, "basis": "ok"}


# ---------------------------------------------------------------------------
# Cell: clamped Level + Flow
# ---------------------------------------------------------------------------

def cot_cell(
    ext_6m: float, ext_3y: float | None, net_history: pd.Series
) -> tuple[int, dict]:
    """Combine Level and Flow into a single COT cell score in [-4, +4].

    The Level and Flow ``basis`` strings are kept under distinct keys
    (``level_basis`` / ``flow_basis``) so neither shadows the other when the
    two metadata dicts are merged.
    """
    level, lm = level_score(ext_6m, ext_3y)
    flow, fm = flow_score(net_history)
    cell = max(-4, min(4, level + flow))
    meta = {
        "level": level,
        "flow": flow,
        "cell": cell,
        "blend": lm["blend"],
        "level_basis": lm["basis"],
        "chg4": fm["chg4"],
        "sigma": fm["sigma"],
        "z": fm["z"],
        "flow_basis": fm["basis"],
    }
    return cell, meta


# ---------------------------------------------------------------------------
# Orchestration over the metals universe
# ---------------------------------------------------------------------------

def score_metals(cot_history: pd.DataFrame) -> pd.DataFrame:
    """Score every metal present in an enriched COT history frame.

    ``cot_history`` is a long frame with (at least) the columns named by
    ``SYMBOL_COL``, ``DATE_COL``, ``EXT_6M_COL``, ``EXT_3Y_COL`` and
    ``NET_COL`` — i.e. the output of compute_metrics annotated with symbols.
    For each metal we take the latest row's extremes and the full spec_net
    series, and emit one row per symbol.
    """
    cols = [
        "symbol", "ext_6m", "ext_3y", "blend", "level",
        "chg4", "sigma", "z", "flow", "cell", "basis",
    ]
    if cot_history.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    for symbol in METAL_SYMBOLS:
        g = cot_history[cot_history[SYMBOL_COL] == symbol]
        if g.empty:
            continue
        g = g.sort_values(DATE_COL)
        last = g.iloc[-1]
        ext_6m = last[EXT_6M_COL]
        ext_3y = last[EXT_3Y_COL]
        net_history = g[NET_COL]

        cell, m = cot_cell(ext_6m, ext_3y, net_history)
        rows.append(
            {
                "symbol": symbol,
                "ext_6m": None if pd.isna(ext_6m) else float(ext_6m),
                "ext_3y": None if pd.isna(ext_3y) else float(ext_3y),
                "blend": m["blend"],
                "level": m["level"],
                "chg4": m["chg4"],
                "sigma": m["sigma"],
                "z": m["z"],
                "flow": m["flow"],
                "cell": m["cell"],
                "basis": f"{m['level_basis']}+{m['flow_basis']}",
            }
        )
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Thin loader (kept separate from the pure scoring functions)
# ---------------------------------------------------------------------------

def load_metals_history(history_path: str | Path | None = None) -> pd.DataFrame:
    """Read the raw COT parquet and return an enriched metals-only long frame.

    Reuses src.compute (read-only) to produce the trailing percentile extremes
    and net positions, then maps CFTC codes to symbols and keeps only metals.
    This is the only function that touches the filesystem.
    """
    from .compute import compute_metrics, load_meta

    path = Path(history_path) if history_path is not None else HISTORY_FILE
    raw = pd.read_parquet(path)
    enriched = compute_metrics(raw)
    if enriched.empty:
        return enriched

    meta = load_meta()
    enriched[SYMBOL_COL] = enriched["cftc_contract_market_code"].map(
        lambda c: meta.get(c, {}).get("symbol")
    )
    return enriched[enriched[SYMBOL_COL].isin(METAL_SYMBOLS)].copy()


# ===========================================================================
# FX (currencies) — additive: metals functions above are left untouched.
# ===========================================================================
#
# CFTC currency contracts are quoted vs USD (e.g. 6E = EUR/USD). So a per-asset
# COT cell for a currency is its score *against the dollar*. An FX pair cell is
# then leg(base) − leg(quote), where the USD leg is 0 (using DXY as the USD leg
# would double-count the dollar). The USD-index row uses the DXY contract.

CURRENCY_CATEGORY = "fx"


def score_currencies(cot_history: pd.DataFrame) -> pd.DataFrame:
    """Score every currency contract present in an enriched COT history frame.

    Same shape and columns as ``score_metals`` (one row per symbol), but it
    iterates over whatever symbols the frame carries — typically the CFTC
    currency contracts (EUR, GBP, JPY, AUD, CAD, CHF, NZD, …, plus DXY) — rather
    than the fixed metals universe. Each ``cell`` is the currency's COT score vs
    USD; the FX-pair combination happens later via ``pair_cot``.
    """
    cols = [
        "symbol", "ext_6m", "ext_3y", "blend", "level",
        "chg4", "sigma", "z", "flow", "cell", "basis",
    ]
    if cot_history.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    for symbol in sorted(cot_history[SYMBOL_COL].dropna().unique()):
        g = cot_history[cot_history[SYMBOL_COL] == symbol].sort_values(DATE_COL)
        if g.empty:
            continue
        last = g.iloc[-1]
        ext_6m = last[EXT_6M_COL]
        ext_3y = last[EXT_3Y_COL]
        cell, m = cot_cell(ext_6m, ext_3y, g[NET_COL])
        rows.append(
            {
                "symbol": symbol,
                "ext_6m": None if pd.isna(ext_6m) else float(ext_6m),
                "ext_3y": None if pd.isna(ext_3y) else float(ext_3y),
                "blend": m["blend"],
                "level": m["level"],
                "chg4": m["chg4"],
                "sigma": m["sigma"],
                "z": m["z"],
                "flow": m["flow"],
                "cell": m["cell"],
                "basis": f"{m['level_basis']}+{m['flow_basis']}",
            }
        )
    return pd.DataFrame(rows, columns=cols)


def pair_cot(base_cell: int | None, quote_cell: int | None) -> int:
    """Combine two currency COT cells into an FX-pair cell, clamped to [-4, +4].

    ``pair = clamp(base_cell - quote_cell, -4, +4)``. A ``None`` leg counts as 0
    — this is how the USD leg passes through (USD has no own contract here; its
    contribution to a vs-USD pair is zero). The builder must NOT route a
    genuinely-missing currency through here (it renders that pair blank instead).
    """
    b = 0 if base_cell is None else int(base_cell)
    q = 0 if quote_cell is None else int(quote_cell)
    return max(-4, min(4, b - q))


def load_currencies_history(history_path: str | Path | None = None) -> pd.DataFrame:
    """Read the raw COT parquet and return an enriched currencies-only long frame.

    Mirrors ``load_metals_history`` but keeps the ``fx`` category (currency
    contracts incl. the DXY US-dollar index). Reuses src.compute (read-only) for
    the extremes; this is the only function here that touches the filesystem.
    """
    from .compute import compute_metrics, load_meta

    path = Path(history_path) if history_path is not None else HISTORY_FILE
    raw = pd.read_parquet(path)
    enriched = compute_metrics(raw)
    if enriched.empty:
        return enriched

    meta = load_meta()
    enriched[SYMBOL_COL] = enriched["cftc_contract_market_code"].map(
        lambda c: meta.get(c, {}).get("symbol")
    )
    cat = enriched["cftc_contract_market_code"].map(
        lambda c: meta.get(c, {}).get("category")
    )
    return enriched[cat == CURRENCY_CATEGORY].copy()
