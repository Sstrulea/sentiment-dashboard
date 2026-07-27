"""Diagnostic replay engine — READ-ONLY.

Rebuilds the exact FX + Cross-Asset board at an arbitrary historical `as_of` by
time-slicing every raw source (calendar, rates, COT history, price history,
real yields, net liquidity, equity put/call) and calling the existing PURE
compute functions from `src/*` UNMODIFIED. This lets scripts/diag/* replay the
last 12 months deterministically, exactly mirroring what src/economic_render.py
would have shown on each day.

Nothing here writes to data/ or config/ — pure read + compute, in memory only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import build_payload, bias_label  # noqa: E402
from src.rate_compute import compute_rate_scores  # noqa: E402
from src.realyield_compute import compute_realyield_score  # noqa: E402
from src.liquidity_compute import compute_liquidity_score  # noqa: E402
from src.crossasset_compute import compute_crossasset_scores  # noqa: E402
from src.cot_score import score_currencies, score_metals  # noqa: E402
from src.sentiment_compute import compute_pc_metrics, pc_index_score  # noqa: E402
from src.compute import compute_metrics, load_meta  # noqa: E402
from src.trend_score import score_all as trend_score_all  # noqa: E402
from src.ff_refresh import FF_PARQUET, calendar_source  # noqa: E402
from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402

DATA = ROOT / "data"
CAL_PATH = DATA / "economic_calendar.parquet"
FF_QUARANTINE_PATH = DATA / "ff_quarantine.parquet"
RATES_PATH = DATA / "rates.parquet"
HIST_PATH = DATA / "history.parquet"
PRICE_PATH = DATA / "price_history.parquet"
REAL_YIELDS_PATH = DATA / "real_yields.parquet"
NET_LIQUIDITY_PATH = DATA / "net_liquidity.parquet"
PC_PATH = DATA / "pc_history.parquet"
INDICATORS_YAML = DATA / "economic_indicators.yaml"
INSTRUMENTS_YAML = DATA / "economic_instruments.yaml"
CROSSASSET_YAML = DATA / "crossasset_instruments.yaml"
PC_THRESHOLDS_YAML = DATA / "pc_thresholds.yaml"


def _load_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _load_production_calendar() -> tuple[pd.DataFrame, str]:
    """Mirror src.economic_render._load_calendar_frame exactly: the LIVE source
    per config/pipeline.yaml (calendar_source), not calibration_analysis.py's
    hardcoded MT5 path — which is the pre-Phase-3 rollback and, as of this
    audit, frozen (no actuals past 2026-07-03) while the FF-bridged source is
    current. Using the frozen path would silently replay stale data; this
    diagnostic uses whatever the dashboard actually reads today. Returns
    (calendar_df, source_label)."""
    try:
        source = calendar_source()
    except Exception:
        source = "mt5"

    if source == "ff" and FF_PARQUET.exists():
        ffdf = pd.read_parquet(FF_PARQUET)
        ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
        cal = to_scoring_frame(ffdf, build_matcher())
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        if FF_QUARANTINE_PATH.exists() and len(cal):
            q = pd.read_parquet(FF_QUARANTINE_PATH)
            if len(q):
                q["datetime_utc"] = pd.to_datetime(q["datetime_utc"])
                key = ["currency", "indicator_key", "release_dt"]
                qkey = q.rename(columns={"datetime_utc": "release_dt"})[key]
                cal = cal.merge(qkey.assign(_q=1), on=key, how="left")
                cal = cal[cal["_q"].isna()].drop(columns="_q")
        return cal, "ff"

    cal = pd.read_parquet(CAL_PATH)
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    return cal, "mt5"


class Context:
    """All raw sources loaded once; every replay call slices from here."""

    def __init__(self) -> None:
        self.cal, self.cal_source = _load_production_calendar()

        self.rates = pd.read_parquet(RATES_PATH)
        self.rates["date"] = pd.to_datetime(self.rates["date"])

        self.hist = pd.read_parquet(HIST_PATH)
        self.hist["report_date_as_yyyy_mm_dd"] = pd.to_datetime(self.hist["report_date_as_yyyy_mm_dd"])

        self.real_yields = pd.read_parquet(REAL_YIELDS_PATH) if REAL_YIELDS_PATH.exists() else pd.DataFrame()
        if len(self.real_yields):
            self.real_yields["date"] = pd.to_datetime(self.real_yields["date"])

        self.net_liquidity = pd.read_parquet(NET_LIQUIDITY_PATH) if NET_LIQUIDITY_PATH.exists() else pd.DataFrame()
        if len(self.net_liquidity):
            self.net_liquidity["date"] = pd.to_datetime(self.net_liquidity["date"])

        self.pc = pd.read_parquet(PC_PATH) if PC_PATH.exists() else pd.DataFrame()
        if len(self.pc):
            self.pc["date"] = pd.to_datetime(self.pc["date"])

        self.indicators_cfg = _load_yaml(INDICATORS_YAML)
        self.instruments_cfg = _load_yaml(INSTRUMENTS_YAML)
        self.crossasset_cfg = _load_yaml(CROSSASSET_YAML)
        self.meta = load_meta()

        self.us_index_syms = {
            sym for sym, c in self.crossasset_cfg.get("instruments", {}).items()
            if c.get("type") == "index" and c.get("home_ccy") == "USD"
        }
        self.pc_proxy_syms = {
            sym for sym, c in self.crossasset_cfg.get("instruments", {}).items()
            if c.get("sentiment_proxy") == "us_equity_pc"
        }
        self.pc_syms = self.us_index_syms | self.pc_proxy_syms


def _fx_and_metal_cot(ctx: Context, as_of: pd.Timestamp):
    """Point-in-time COT: enrich history sliced to report_date<=as_of (no
    lookahead — the trailing 6m/3y percentile ranks only see past reports),
    then split into fx currency cells + metals cells exactly like
    src.economic_render._fx_currency_cells / _metals_cot_map."""
    hist_slice = ctx.hist[ctx.hist["report_date_as_yyyy_mm_dd"] <= as_of]
    if hist_slice.empty:
        return {}, {}, {}
    enriched = compute_metrics(hist_slice)
    if enriched.empty:
        return {}, {}, {}
    enriched["symbol"] = enriched["cftc_contract_market_code"].map(lambda c: ctx.meta.get(c, {}).get("symbol"))
    cat = enriched["cftc_contract_market_code"].map(lambda c: ctx.meta.get(c, {}).get("category"))

    fx_hist = enriched[cat == "fx"].copy()
    fx_cells, fx_details = {}, {}
    if not fx_hist.empty:
        scored = score_currencies(fx_hist)
        if not scored.empty:
            fx_cells = {r["symbol"]: int(r["cell"]) for _, r in scored.iterrows()}
            fx_details = {
                r["symbol"]: {"level": int(r["level"]), "flow": int(r["flow"]),
                              "blend": float(r["blend"]),
                              "z": None if pd.isna(r["z"]) else float(r["z"]),
                              "basis": str(r["basis"])}
                for _, r in scored.iterrows()
            }

    metal_hist = enriched[cat == "metals"].copy()
    metal_cells = {}
    if not metal_hist.empty:
        scored_m = score_metals(metal_hist)
        if not scored_m.empty:
            metal_cells = {r["symbol"]: int(r["cell"]) for _, r in scored_m.iterrows()}

    return fx_cells, fx_details, metal_cells


def _pc_cell(ctx: Context, as_of: pd.Timestamp):
    if ctx.pc.empty:
        return None
    pc_slice = ctx.pc[ctx.pc["date"] <= as_of]
    if pc_slice.empty or not PC_THRESHOLDS_YAML.exists():
        return None
    try:
        metrics = compute_pc_metrics(pc_slice, str(PC_THRESHOLDS_YAML))
        pct = metrics["equity"]["current"]["percentile_rank"]
    except Exception:
        return None
    if pct is None:
        return None
    cell, _detail = pc_index_score(pct)
    return int(cell)


def build_snapshot(as_of: pd.Timestamp, ctx: Context,
                   indicators_cfg: dict | None = None,
                   instruments_cfg: dict | None = None) -> dict:
    """Full FX + currencies + cross-asset payload as it would have looked on
    `as_of`. Pure reuse of src.economic_compute.build_payload and
    src.crossasset_compute.compute_crossasset_scores; only the INPUT slicing is
    diagnostic-specific.

    `indicators_cfg`/`instruments_cfg` default to the Context's (the real,
    on-disk YAMLs); pass IN-MEMORY overrides (e.g. reweighted copies) for a
    scratch counterfactual replay — never written back to disk."""
    as_of = pd.Timestamp(as_of)
    indicators_cfg = indicators_cfg if indicators_cfg is not None else ctx.indicators_cfg
    instruments_cfg = instruments_cfg if instruments_cfg is not None else ctx.instruments_cfg

    cal_slice = ctx.cal[ctx.cal["release_dt"] <= as_of]
    rates_slice = ctx.rates[ctx.rates["date"] <= as_of]
    rate_scores = compute_rate_scores(rates_slice, as_of=as_of.date()) if len(rates_slice) else {}

    fx_cells, fx_details, metal_cells = _fx_and_metal_cot(ctx, as_of)

    # TREND: score_all already accepts a `server_today` cutoff — closed bars are
    # those dated strictly before it, so server_today = as_of + 1 day keeps
    # every bar up to and including as_of (mirrors production's "last closed
    # bar as of now" semantics, just replayed at a historical `as_of`).
    trend_full = trend_score_all(parquet_path=PRICE_PATH, server_today=as_of + pd.Timedelta(days=1))
    trend_by_symbol = {k: int(v["trend_cell"]) for k, v in trend_full.items() if v.get("trend_cell") is not None}

    payload = build_payload(
        cal_slice, indicators_cfg, instruments_cfg, as_of=as_of,
        rate_scores=rate_scores or None,
        sentiment_cells=fx_cells or None,
        trend_cells=trend_by_symbol or None,
    )

    # --- Cross-asset ---
    categories_by_ccy = {ccy: card.get("categories", {}) for ccy, card in payload["currencies"].items()}
    real_yield_score = compute_realyield_score(ctx.real_yields, as_of=as_of.date()) if len(ctx.real_yields) else None
    liquidity_score = compute_liquidity_score(ctx.net_liquidity, as_of=as_of.date()) if len(ctx.net_liquidity) else None

    pc_cell = _pc_cell(ctx, as_of)
    sentiment_by_symbol = dict(metal_cells)
    if pc_cell is not None:
        for sym in ctx.pc_syms:
            sentiment_by_symbol[sym] = pc_cell

    ca_scores = compute_crossasset_scores(
        categories_by_ccy, real_yield_score, ctx.crossasset_cfg,
        liquidity_score=liquidity_score,
        sentiment_by_symbol=sentiment_by_symbol,
        trend_by_symbol=trend_by_symbol,
    )

    return {
        "as_of": as_of,
        "currencies": payload["currencies"],
        "instruments": payload["instruments"],
        "crossasset": ca_scores,
        "rate_scores": rate_scores,
        "fx_cot_details": fx_details,
        "fx_cot_cells": fx_cells,
        "trend_by_symbol": trend_by_symbol,
        "trend_full": trend_full,
    }


def business_day_window(ctx: Context, months: int = 12, freq: str = "W-FRI") -> pd.DatetimeIndex:
    """As-of window: the last `months` months ending at the latest common data
    point across calendar/rates/price/COT, sampled at `freq` (default weekly —
    matches the native COT cadence and keeps replay cost tractable across 27
    FX pairs + cross-asset for every point). Reports the REAL window actually
    used; callers must not extrapolate beyond it."""
    last_candidates = [
        ctx.cal.loc[ctx.cal["actual"].notna(), "release_dt"].max(),
        ctx.rates["date"].max(),
        ctx.hist["report_date_as_yyyy_mm_dd"].max(),
        pd.to_datetime(pd.read_parquet(PRICE_PATH)["date"]).max() if PRICE_PATH.exists() else pd.NaT,
    ]
    last = min([c for c in last_candidates if pd.notna(c)])
    first = last - pd.DateOffset(months=months)
    idx = pd.date_range(first, last, freq=freq)
    if len(idx) == 0 or idx[-1] < last:
        idx = idx.append(pd.DatetimeIndex([last]))
    return idx
