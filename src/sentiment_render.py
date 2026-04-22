"""Renders P/C Ratio and VIX/VIX3M pages."""
from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.sentiment_compute import PC_VARIANT_KEYS, compute_pc_metrics

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
PUBLIC_DIR = ROOT / "public"
PC_PARQUET = ROOT / "data" / "pc_history.parquet"
PC_THRESHOLDS = ROOT / "data" / "pc_thresholds.yaml"

# Window size in trading days. -1 = "all available"
WINDOW_DAYS: dict[str, int] = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "1Y": 252,
    "3Y": 756,
    "Max": -1,
}

# Static JS/CSS assets that must end up in public/ for the P/C page to work.
STATIC_ASSETS = ("style.css", "chart.umd.min.js", "chartjs-plugin-annotation.min.js", "pc-chart.js")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _copy_static_assets() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in STATIC_ASSETS:
        src = STATIC_DIR / name
        if src.exists():
            shutil.copyfile(src, PUBLIC_DIR / name)


def _jsonable(v: Any) -> Any:
    """Convert pandas/NumPy scalars to plain JSON-safe types."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if pd.isna(v):
        return None
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _slice_tail(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if n is None or n < 0:
        return df
    return df.tail(n).reset_index(drop=True)


def _series_for_window(variant_df: pd.DataFrame, n: int) -> dict[str, list]:
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
            label: _series_for_window(vdf, n) for label, n in WINDOW_DAYS.items()
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
    _copy_static_assets()

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


def render_vix_ratio_page() -> Path:
    """Stage 3 stub — renders empty page with navbar."""
    _copy_static_assets()
    env = _env()
    template = env.get_template("vix_ratio.html.j2")
    html = template.render(active_page="vix-ratio")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLIC_DIR / "vix-ratio.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    pc_path = render_pc_ratio_page()
    vix_path = render_vix_ratio_page()
    print(f"Rendered: {pc_path}")
    print(f"Rendered: {vix_path}")
