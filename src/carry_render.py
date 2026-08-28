"""Render the Carry page (HTML + JSON).

Read-only over data/policy_rates.yaml (manual, human-edited — no MT5, no
broker feed, no fetcher) and data/economic_instruments.yaml (reused only to
discover which FX pairs /economic shows — never hardcoded here). Calls the
PURE `carry_compute.compute_carry`, shapes the JSON-ready payload, and
writes:

    public/data/carry.json
    public/carry.html

No scoring lives here — that's entirely in carry_compute.py. This module
only reads the YAML, calls compute, and renders the Jinja template, mirroring
src/economic_render.py's own render module (same `_env()`, `_jsonable`,
`generated_at`, meta shape).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.carry_compute import compute_carry, leg_configured
from src.static_assets import copy_static_assets

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
RATES_YAML = ROOT / "data" / "policy_rates.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"

# Carry gradient/bar saturation point, in percentage points — matches the
# ±5.00pp scale the /carry legend and CARRY-bar clamp caret describe.
SCALE_PP = 5.0


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _jsonable(v: Any) -> Any:
    """Recursively coerce a value to JSON-safe form (NaN->None, date->iso)."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _fx_pairs(instruments_cfg: dict) -> tuple[list[str], dict[str, str]]:
    """(symbols, display_by_symbol) for every type=fx instrument in
    data/economic_instruments.yaml, in file order. The single-currency
    US-DOLLAR (DXY proxy) row is type=single, so it's naturally excluded —
    it has no second leg and therefore no carry."""
    inst_cfg = instruments_cfg.get("instruments", {}) or {}
    symbols = [sym for sym, cfg in inst_cfg.items() if cfg.get("type") == "fx"]
    display_by_symbol = {sym: inst_cfg[sym].get("display", sym) for sym in symbols}
    return symbols, display_by_symbol


def build_carry_payload() -> dict:
    """Read the YAML configs, compute, and return the JSON-ready payload."""
    rates_cfg = _load_yaml(RATES_YAML)
    instruments_cfg = _load_yaml(INSTRUMENTS_YAML)

    meta = rates_cfg.get("meta") or {}
    stale_after_days = int(meta.get("stale_after_days", 45))
    rate_table = rates_cfg.get("rates") or {}

    pairs, display_by_symbol = _fx_pairs(instruments_cfg)
    as_of = datetime.now(timezone.utc).date()
    carry_rows = compute_carry(rates_cfg, pairs, as_of)

    configured = sum(1 for leg in rate_table.values() if leg_configured(leg))
    total_currencies = len(rate_table)

    rows = [
        {
            "symbol": r.symbol,
            "display": display_by_symbol.get(r.symbol, r.symbol),
            "base": r.base,
            "quote": r.quote,
            "base_rate": r.base_rate,
            "quote_rate": r.quote_rate,
            "carry_pct": r.carry_pct,
            "stale": r.stale,
            "available": r.available,
        }
        for r in carry_rows
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "scale_pp": SCALE_PP,
        "stale_after_days": stale_after_days,
        "configured": f"{configured}/{total_currencies}",
        "rates": rate_table,
        "rows": rows,
    }
    return _jsonable(payload)


def render_carry_page() -> Path:
    """Build payload, write JSON + HTML, copy static assets. Returns the HTML path."""
    copy_static_assets()

    payload = build_carry_payload()

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "carry.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("carry.html.j2")
    html = template.render(
        active_page="carry",
        data_url="/data/carry.json",
    )
    out_path = PUBLIC_DIR / "carry.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Carry page rendered → %s (%d pairs)", out_path, len(payload.get("rows", [])))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    out = render_carry_page()
    print(f"Rendered: {out}")
