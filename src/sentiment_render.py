"""Renders P/C Ratio and VIX/VIX3M pages."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.sentiment_compute import (
    PC_VARIANT_KEYS,
    VIX_RATIO_THRESHOLDS,
    compute_pc_metrics,
    compute_vix_ratio_metrics,
)
from src.static_assets import copy_static_assets

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
PC_PARQUET = ROOT / "data" / "pc_history.parquet"
PC_THRESHOLDS = ROOT / "data" / "pc_thresholds.yaml"
VIX_PARQUET = ROOT / "data" / "sentiment_history.parquet"

# Window size in trading days. -1 = "all available"
PC_WINDOW_DAYS: dict[str, int] = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "1Y": 252,
    "3Y": 756,
    "Max": -1,
}

VIX_WINDOW_DAYS: dict[str, int] = {
    "3M": 66,
    "6M": 132,
    "1Y": 252,
    "3Y": 756,
    "5Y": 1260,
    "Max": -1,
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _jsonable(v: Any) -> Any:
    """Convert pandas/NumPy scalars to plain JSON-safe types."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _slice_tail(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if n is None or n < 0:
        return df
    return df.tail(n).reset_index(drop=True)


# ---------------------------------------------------------------------------
# P/C page
# ---------------------------------------------------------------------------

def _pc_series_for_window(variant_df: pd.DataFrame, n: int) -> dict[str, list]:
    view = _slice_tail(variant_df, n)
    return {
        "dates": [pd.Timestamp(d).date().isoformat() for d in view["date"]],
        "values": [_jsonable(v) for v in view["value"]],
        "ma_10": [_jsonable(v) for v in view["ma_10"]],
        "p90_rolling": [_jsonable(v) for v in view["p90_rolling"]],
        "p10_rolling": [_jsonable(v) for v in view["p10_rolling"]],
    }


def build_pc_page_payload() -> dict:
    if not PC_PARQUET.exists():
        raise FileNotFoundError(
            f"{PC_PARQUET} not found — run `python -m src.sentiment_backfill --pc-from … --pc-to …` first."
        )

    pc_df = pd.read_parquet(PC_PARQUET)
    metrics = compute_pc_metrics(pc_df, str(PC_THRESHOLDS))

    variants_out: dict[str, Any] = {}
    for key in PC_VARIANT_KEYS:
        bundle = metrics[key]
        cfg = bundle["config"]
        cur = bundle["current"]
        vdf = bundle["df"]

        series: dict[str, dict[str, list]] = {
            label: _pc_series_for_window(vdf, n) for label, n in PC_WINDOW_DAYS.items()
        }

        current_out = {
            "date": pd.Timestamp(vdf["date"].iloc[-1]).date().isoformat() if len(vdf) else None,
            "value": _jsonable(cur["value"]),
            "ma_10": _jsonable(cur["ma_10"]),
            "p90_rolling": _jsonable(cur["p90_rolling"]),
            "p10_rolling": _jsonable(cur["p10_rolling"]),
            "percentile_rank": _jsonable(cur["percentile_rank"]),
            "signal": cur["signal"],
            "thresholds_source": cur["thresholds_source"],
        }

        variants_out[key] = {
            "display_name": cfg["display_name"],
            "confidence": cfg["confidence"],
            "source": cfg["source"],
            "interpretation": cfg["interpretation"],
            "note": cfg.get("note"),
            "static_thresholds": {
                "high_put": cfg["static_high_put"],
                "high_call": cfg["static_high_call"],
            },
            "current": current_out,
            "series": series,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variants": variants_out,
    }


def render_pc_ratio_page() -> Path:
    copy_static_assets()

    payload = build_pc_page_payload()

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "pc-ratio.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("pc_ratio.html.j2")
    html = template.render(active_page="pc-ratio", data_url="/data/pc-ratio.json")
    out_path = PUBLIC_DIR / "pc-ratio.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# VIX page
# ---------------------------------------------------------------------------

def _vix_series_for_window(vm: pd.DataFrame, n: int) -> dict[str, list]:
    view = _slice_tail(vm, n)
    return {
        "dates": [pd.Timestamp(d).date().isoformat() for d in view["date"]],
        "ratio": [_jsonable(v) for v in view["ratio"]],
        "vix": [_jsonable(v) for v in view["vix_close"]],
        "vix3m": [_jsonable(v) for v in view["vix3m_close"]],
        "regimes": [str(r) for r in view["regime"].values],
    }


def _build_crossovers(vm: pd.DataFrame, col: str) -> list[dict]:
    events: list[dict] = []
    idx_col = vm.reset_index(drop=True)
    flags = idx_col[col].values
    for i, is_flip in enumerate(flags):
        if not bool(is_flip):
            continue
        ratio_after = idx_col["ratio"].iloc[i]
        ratio_before = idx_col["ratio"].iloc[i - 1] if i > 0 else None
        events.append(
            {
                "date": pd.Timestamp(idx_col["date"].iloc[i]).date().isoformat(),
                "ratio_before": _jsonable(ratio_before),
                "ratio_after": _jsonable(ratio_after),
            }
        )
    return events


def build_vix_page_payload() -> dict:
    if not VIX_PARQUET.exists():
        raise FileNotFoundError(
            f"{VIX_PARQUET} not found — run `python -m src.sentiment_backfill --vix` first."
        )

    vix_df = pd.read_parquet(VIX_PARQUET)
    vm = compute_vix_ratio_metrics(vix_df)

    series = {label: _vix_series_for_window(vm, n) for label, n in VIX_WINDOW_DAYS.items()}

    if len(vm) == 0:
        current_out = {
            "date": None,
            "vix": None,
            "vix3m": None,
            "ratio": None,
            "regime": "UNKNOWN",
            "days_in_regime": 0,
            "regime_changed_today": False,
            "previous_regime": None,
        }
    else:
        last = vm.iloc[-1]
        days_in_regime = int(last["days_since_regime_flip"])
        regime_changed_today = days_in_regime == 0 and len(vm) >= 2
        previous_regime = str(vm.iloc[-2]["regime"]) if regime_changed_today else None
        current_out = {
            "date": pd.Timestamp(last["date"]).date().isoformat(),
            "vix": _jsonable(last["vix_close"]),
            "vix3m": _jsonable(last["vix3m_close"]),
            "ratio": _jsonable(last["ratio"]),
            "regime": str(last["regime"]),
            "days_in_regime": days_in_regime,
            "regime_changed_today": regime_changed_today,
            "previous_regime": previous_regime,
        }

    return {
        "thresholds": dict(VIX_RATIO_THRESHOLDS),
        "current": current_out,
        "series": series,
        "crossovers_above_1": _build_crossovers(vm, "crossed_above_1"),
        "crossovers_below_1": _build_crossovers(vm, "crossed_below_1"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def render_vix_ratio_page() -> Path:
    copy_static_assets()

    payload = build_vix_page_payload()

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "vix-ratio.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("vix_ratio.html.j2")
    html = template.render(active_page="vix-ratio", data_url="/data/vix-ratio.json")
    out_path = PUBLIC_DIR / "vix-ratio.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    pc_path = render_pc_ratio_page()
    vix_path = render_vix_ratio_page()
    print(f"Rendered: {pc_path}")
    print(f"Rendered: {vix_path}")
