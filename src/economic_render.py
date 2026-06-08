"""Render the Economic Dashboard page (HTML + JSON).

Read-only over the scoring layer: it reads data/economic_calendar.parquet and
the two YAML configs, calls the PURE `economic_compute.build_payload`, enriches
the per-indicator breakdown with display metadata (labels, recency, stale flag),
and writes:

    public/data/economic.json
    public/economic.html

No scoring or thresholds live here — all of that is in economic_compute.py and
the YAML (the authoritative source). This module only shapes the payload for the
client and renders the Jinja template, mirroring the existing COT/Retail render
modules.
"""
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

from src.economic_compute import build_payload
from src.static_assets import copy_static_assets

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
PARQUET = ROOT / "data" / "economic_calendar.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"

# Human-readable labels for the breakdown modal (presentation only — scoring is
# untouched). Keyed by the taxonomy indicator_key.
INDICATOR_LABELS = {
    "cpi_yoy": "CPI (YoY)",
    "core_cpi": "Core CPI",
    "ppi_yoy": "PPI",
    "core_pce": "Core PCE",
    "gdp_qoq": "GDP (QoQ)",
    "manufacturing_pmi": "Manufacturing PMI",
    "services_pmi": "Services PMI",
    "retail_sales": "Retail Sales",
    "employment_change": "Employment Change",
    "unemployment_rate": "Unemployment Rate",
    "wage_growth": "Wage Growth",
    "adp": "ADP Employment",
    "jolts": "JOLTS Job Openings",
    "jobless_claims": "Jobless Claims",
    "interest_rate_decision": "Interest Rate Decision",
}

CATEGORY_LABEL_FALLBACK = {
    "growth": "Growth",
    "inflation": "Inflation",
    "labour": "Labour Market",
    "rates": "Rates (display-only)",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _jsonable(v: Any) -> Any:
    """Recursively coerce a value to JSON-safe form (NaN->None, Timestamp->iso)."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _build_meta(indicators_cfg: dict, instruments_cfg: dict) -> dict:
    """Static display metadata the client needs: labels, categories, thresholds."""
    defaults = indicators_cfg.get("defaults", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}
    categories_cfg = indicators_cfg.get("categories", {}) or {}

    ind_meta: dict[str, dict] = {}
    for key, cfg in indicators.items():
        ind_meta[key] = {
            "label": INDICATOR_LABELS.get(key, key),
            "category": cfg.get("category"),
            "pillar": cfg.get("pillar"),
            "direction": int(cfg.get("direction", 1)),
            "weight": float(cfg.get("weight", 1.0)),
            "max_age_days": int(cfg.get("max_age_days", defaults.get("max_age_days", 120))),
        }

    cat_meta: dict[str, dict] = {}
    for key in list(categories_cfg) + ["rates"]:
        cat_meta[key] = {
            "label": (categories_cfg.get(key, {}) or {}).get(
                "label", CATEGORY_LABEL_FALLBACK.get(key, key.title())
            )
        }

    return {
        "categories": cat_meta,
        "indicators": ind_meta,
        "categories_display": instruments_cfg.get("categories_display", []),
        "bias_thresholds": instruments_cfg.get("bias_thresholds", {}),
        "scale": instruments_cfg.get("scale"),
        "pair_divisor": instruments_cfg.get("pair_divisor"),
    }


def _enrich_breakdowns(payload: dict, ind_meta: dict, as_of: pd.Timestamp) -> None:
    """Add recency (release_dt iso, age_days, stale) to every per-indicator entry.

    Mutates in place. Compute already drops indicators older than max_age_days, so
    `stale` is effectively always False here — it's surfaced defensively so the UI
    is honest if that ever changes.
    """
    for card in payload.get("currencies", {}).values():
        for key, entry in (card.get("breakdown") or {}).items():
            rdt = entry.get("release_dt")
            ts = pd.Timestamp(rdt) if rdt is not None else None
            max_age = (ind_meta.get(key, {}) or {}).get("max_age_days", 120)
            if ts is not None and not pd.isna(ts):
                age = int((as_of - ts).days)
                entry["age_days"] = age
                entry["stale"] = age > max_age
            else:
                entry["age_days"] = None
                entry["stale"] = False


def build_economic_payload() -> dict:
    """Read parquet + configs, compute, enrich, and return the JSON-ready payload."""
    indicators_cfg = _load_yaml(INDICATORS_YAML)
    instruments_cfg = _load_yaml(INSTRUMENTS_YAML)

    if PARQUET.exists():
        cal = pd.read_parquet(PARQUET)
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    else:
        cal = pd.DataFrame(
            columns=["currency", "indicator_key", "release_dt", "actual", "consensus"]
        )
        log.warning("%s missing — rendering an empty Economic page.", PARQUET)

    as_of = pd.Timestamp.utcnow().tz_localize(None)
    payload = build_payload(cal, indicators_cfg, instruments_cfg, as_of=as_of)

    meta = _build_meta(indicators_cfg, instruments_cfg)
    _enrich_breakdowns(payload, meta["indicators"], as_of)

    payload["meta"] = meta
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _jsonable(payload)


def render_economic_page() -> Path:
    """Build payload, write JSON + HTML, copy static assets. Returns the HTML path."""
    copy_static_assets()

    payload = build_economic_payload()

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "economic.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("economic.html.j2")
    html = template.render(
        active_page="economic",
        data_url="/data/economic.json",
    )
    out_path = PUBLIC_DIR / "economic.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Economic page rendered → %s (%d instruments)",
             out_path, len(payload.get("instruments", [])))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    out = render_economic_page()
    print(f"Rendered: {out}")
