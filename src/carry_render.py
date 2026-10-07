"""Render the Carry page (HTML + JSON).

Policy rates come from data/cb/decisions.parquet (written by the official CB
pipeline): per currency, the latest decision at as_of (src.policy_rate.
policy_rates_at — the same rule as the /economic display). data/policy_rates.yaml
(manual, human-edited) is only a per-currency FALLBACK: used when the parquet is
missing/unreadable or has no decision for that currency at or before as_of.
Staleness is set here, per leg: a decision leg is stale only when the meeting
check flags its currency (economic_render._policy_rates_freshness: a meeting in
data/cb/meetings.yaml dated before today with no decision in the parquet); a
fallback leg keeps the `verified` older-than-stale_after_days rule.
data/economic_instruments.yaml is reused only to discover which FX pairs
/economic shows — never hardcoded here. Calls the PURE
`carry_compute.compute_carry`, shapes the JSON-ready payload, and writes:

    public/data/carry.json
    public/carry.html

No scoring lives here — that's entirely in carry_compute.py. This module
only gathers the legs, calls compute, and renders the Jinja template, mirroring
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

import pandas as pd

from src.carry_compute import compute_carry, leg_configured, verified_stale
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


def _decision_rates(as_of: pd.Timestamp, decisions_path: Path | None,
                    meetings_path: Path | None = None) -> tuple[dict, dict | None]:
    """({CCY: policy_rates_at entry}, meeting-check result) — ({}, None) when the
    parquet is missing or unreadable (every leg then falls back to the yaml)."""
    from src.policy_rate import DECISIONS_PARQUET, load_decisions, policy_rates_at
    path = decisions_path or DECISIONS_PARQUET
    try:
        decisions = load_decisions(path)
    except Exception as e:  # noqa: BLE001 — missing/unreadable → manual fallback
        log.warning("decisions.parquet unavailable (%s); /carry uses data/policy_rates.yaml.", e)
        return {}, None
    from src.economic_render import _policy_rates_freshness
    try:
        fresh = _policy_rates_freshness(as_of, meetings_path=meetings_path, decisions_path=path)
    except Exception as e:  # noqa: BLE001
        log.warning("meeting check unavailable (%s); decision legs not flagged.", e)
        fresh = None
    return policy_rates_at(decisions, as_of), fresh


def build_legs(manual: dict, cb: dict, fresh: dict | None, as_of: pd.Timestamp,
               stale_after_days: int) -> dict:
    """{CCY: leg} — a decision leg where decisions.parquet has one at as_of,
    else the manual yaml leg. Every leg carries `source`, `stale` and
    `stale_reason` (set here, on the server). The yaml's source_name/source_url
    stay on every leg (the link)."""
    per = (fresh or {}).get("per_currency") or {}
    legs: dict = {}
    for ccy in list(manual) + [c for c in cb if c not in manual]:
        m = manual.get(ccy) or {}
        if ccy in cb:
            d = cb[ccy]
            info = per.get(ccy) or {}
            stale = bool(info.get("stale"))
            leg = {"rate_pct": d["rate_pct"], "source": "cb_decisions", "bank": d["bank"],
                   "meeting_date": d["meeting_date"], "effective_date": d["effective_date"],
                   "rate_source": d["rate_source"], "status": d["status"],
                   "source_name": m.get("source_name"), "source_url": m.get("source_url"),
                   "stale": stale,
                   "stale_reason": (("meeting(s) " + ", ".join(info.get("missing") or [])
                                     + " without a decision in decisions.parquet")
                                    if stale and info.get("missing") else
                                    ("meetings but no decision in decisions.parquet" if stale else None))}
            if "lower" in d:
                leg["range"] = {"lower": d["lower"], "upper": d["upper"]}
        else:
            stale = verified_stale(m, as_of.date(), stale_after_days)
            leg = dict(m, source="manual", stale=stale,
                       stale_reason=(f"verified {m.get('verified')} is older than "
                                     f"{stale_after_days} days" if stale else None))
        legs[ccy] = leg
    return legs


def build_carry_payload(as_of: pd.Timestamp | None = None, *, rates_yaml: Path | None = None,
                        decisions_path: Path | None = None, meetings_path: Path | None = None) -> dict:
    """Gather the legs (decisions.parquet, yaml fallback), compute, and return
    the JSON-ready payload."""
    rates_cfg = _load_yaml(rates_yaml or RATES_YAML)
    instruments_cfg = _load_yaml(INSTRUMENTS_YAML)

    meta = rates_cfg.get("meta") or {}
    stale_after_days = int(meta.get("stale_after_days", 45))
    now = (pd.Timestamp(as_of) if as_of is not None
           else pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)))
    cb, fresh = _decision_rates(now, decisions_path, meetings_path)
    rate_table = build_legs(rates_cfg.get("rates") or {}, cb, fresh, now, stale_after_days)

    pairs, display_by_symbol = _fx_pairs(instruments_cfg)
    as_of = now.date()
    carry_rows = compute_carry({"meta": meta, "rates": rate_table}, pairs, as_of)

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
