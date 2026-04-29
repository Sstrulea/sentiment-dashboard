"""Renders the Retail Sentiment page (HTML + JSON)."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.retail_compute import build_symbol_payload
from src.retail_fetch import (
    RETAIL_GENERAL_FILE,
    RETAIL_HISTORY_FILE,
    SYMBOLS_YAML,
    load_symbols_config,
)
from src.static_assets import copy_static_assets

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
COT_HISTORY_FILE = ROOT / "data" / "history.parquet"
CONTRACTS_FILE = ROOT / "data" / "contracts.yaml"

# History windows (calendar days; -1 means "all available").
RETAIL_WINDOW_DAYS = {
    "1W":  5,
    "1M":  22,
    "3M":  66,
    "6M":  132,
    "1Y":  252,
    "All": -1,
}

PROVIDER_DISPLAY = {
    "myfxbook": "Myfxbook Community Outlook",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


# ---------------------------------------------------------------------------
# COT cross-link prep: attach 'symbol' column to history.parquet using contracts.yaml
# ---------------------------------------------------------------------------

def _load_cot_history() -> pd.DataFrame | None:
    if not COT_HISTORY_FILE.exists():
        return None
    try:
        df = pd.read_parquet(COT_HISTORY_FILE)
    except Exception as e:
        log.warning("Failed to read COT history.parquet: %s", e)
        return None
    if df.empty:
        return df

    if not CONTRACTS_FILE.exists():
        return df
    with open(CONTRACTS_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    code_to_symbol: dict[str, str] = {}
    for cat in (cfg.get("categories") or {}).values():
        for inst in cat.get("instruments", []):
            code_to_symbol[str(inst["cftc_code"])] = str(inst["symbol"])
    df = df.copy()
    df["symbol"] = df["cftc_contract_market_code"].astype(str).map(code_to_symbol)
    return df


# ---------------------------------------------------------------------------
# Provider meta-bar
# ---------------------------------------------------------------------------

def _build_provider_meta(general_df: pd.DataFrame, snapshot_count: int) -> dict:
    if general_df is None or general_df.empty:
        return {
            "name": "myfxbook",
            "display": PROVIDER_DISPLAY.get("myfxbook"),
            "last_fetched_at": None,
            "real_account_pct": None,
            "demo_account_pct": None,
            "profitable_pct": None,
            "total_funds_usd": None,
            "snapshot_count": snapshot_count,
        }
    last = general_df.sort_values("fetched_at").iloc[-1]
    name = str(last.get("provider", "myfxbook"))
    return {
        "name": name,
        "display": PROVIDER_DISPLAY.get(name, name),
        "last_fetched_at": pd.Timestamp(last["fetched_at"]).isoformat(),
        "real_account_pct": _jsonable(last.get("real_account_pct")),
        "demo_account_pct": _jsonable(last.get("demo_account_pct")),
        "profitable_pct": _jsonable(last.get("profitable_pct")),
        "total_funds_usd": _jsonable(last.get("total_funds_usd")),
        "snapshot_count": int(snapshot_count),
    }


# ---------------------------------------------------------------------------
# Window remap + payload
# ---------------------------------------------------------------------------

def _remap_history_windows(history: dict[str, dict[str, list]]) -> dict[str, dict[str, list]]:
    """Re-slice the compute layer's broad windows down to the trading-day-sized
    spec windows by simple tail-of-N, keeping schema stable.

    The compute layer uses calendar-day cutoffs ('1W'=7d ... 'All'=-1). The
    spec asks for trading-day-sized payload windows ('1W'=5 ... 'All'=-1).
    Since we may have multiple snapshots per day, we just tail the 'All'
    series by the configured row count.
    """
    all_window = history.get("All") or {"dates": [], "long_pct": [], "spot_estimate": []}
    out: dict[str, dict[str, list]] = {}
    for label, n in RETAIL_WINDOW_DAYS.items():
        dates = list(all_window.get("dates", []))
        longs = list(all_window.get("long_pct", []))
        spots = list(all_window.get("spot_estimate", []))
        if n is not None and n >= 0:
            dates = dates[-n:]
            longs = longs[-n:]
            spots = spots[-n:]
        out[label] = {"dates": dates, "long_pct": longs, "spot_estimate": spots}
    return out


def build_retail_payload() -> dict:
    """Build the full JSON payload for the Retail Sentiment page."""
    cfg = load_symbols_config()
    symbols_cfg: dict = cfg.get("symbols") or {}
    categories_cfg: dict = cfg.get("categories") or {}
    signal_config: dict = cfg.get("signal_config") or {}

    # --- Load history ---
    if RETAIL_HISTORY_FILE.exists():
        history_df = pd.read_parquet(RETAIL_HISTORY_FILE)
    else:
        history_df = pd.DataFrame(columns=[
            "fetched_at", "provider", "symbol", "long_pct", "short_pct",
            "long_volume", "short_volume", "long_positions", "short_positions",
            "total_positions", "avg_long_price", "avg_short_price",
        ])

    if not history_df.empty:
        history_df["fetched_at"] = pd.to_datetime(history_df["fetched_at"])

    if RETAIL_GENERAL_FILE.exists():
        general_df = pd.read_parquet(RETAIL_GENERAL_FILE)
        if not general_df.empty:
            general_df["fetched_at"] = pd.to_datetime(general_df["fetched_at"])
    else:
        general_df = pd.DataFrame()

    cot_history_df = _load_cot_history()

    # --- Build per-symbol entries grouped by category ---
    cats_sorted = sorted(
        categories_cfg.items(),
        key=lambda kv: int(kv[1].get("order", 99)),
    )

    categories_out: list[dict] = []
    snapshot_count = 0
    for cat_key, cat_meta in cats_sorted:
        symbols_in_cat = [
            sym for sym, sym_cfg in symbols_cfg.items()
            if sym_cfg.get("category") == cat_key
        ]
        symbols_in_cat.sort()

        sym_entries: list[dict] = []
        for sym in symbols_in_cat:
            payload = build_symbol_payload(
                sym, history_df, cot_history_df, cfg, signal_config,
            )
            payload["history"] = _remap_history_windows(payload["history"])
            payload["current"] = _jsonable_dict(payload["current"])
            payload["signals"] = _jsonable_signals(payload["signals"])
            sym_entries.append(payload)
            if payload["current"] is not None:
                snapshot_count += 1

        categories_out.append({
            "key": cat_key,
            "label": cat_meta.get("label", cat_key),
            "symbols": sym_entries,
        })

    provider_meta = _build_provider_meta(general_df, snapshot_count)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider_meta,
        "categories": categories_out,
    }


def _jsonable_dict(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: _jsonable(v) for k, v in d.items()}


def _jsonable_signals(signals: dict) -> dict:
    out: dict = {}
    for k, v in signals.items():
        if isinstance(v, dict):
            out[k] = {k2: _jsonable(v2) for k2, v2 in v.items()}
        else:
            out[k] = _jsonable(v)
    # Nested extremes
    extremes = signals.get("extremes")
    if isinstance(extremes, dict):
        out["extremes"] = {
            "ext_3m": _jsonable_dict(extremes.get("ext_3m")),
            "ext_6m": _jsonable_dict(extremes.get("ext_6m")),
        }
    return out


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_retail_sentiment_page() -> Path:
    """Build payload, write JSON + HTML, copy static assets. Returns HTML path."""
    copy_static_assets()

    payload = build_retail_payload()

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "retail-sentiment.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("retail_sentiment.html.j2")
    html = template.render(
        active_page="retail-sentiment",
        data_url="/data/retail-sentiment.json",
    )
    out_path = PUBLIC_DIR / "retail-sentiment.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    out = render_retail_sentiment_page()
    print(f"Rendered: {out}")
