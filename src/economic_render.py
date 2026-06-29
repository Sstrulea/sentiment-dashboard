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
from src.cot_score import (
    load_currencies_history,
    load_metals_history,
    pair_cot,
    score_currencies,
    score_metals,
)
from src.sentiment_compute import compute_pc_metrics, pc_index_score
from src.rate_compute import compute_rate_scores
from src.realyield_compute import compute_realyield_score
from src.liquidity_compute import compute_liquidity_score
from src.crossasset_compute import compute_crossasset_scores
from src.static_assets import copy_static_assets

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
PARQUET = ROOT / "data" / "economic_calendar.parquet"
RATES_PARQUET = ROOT / "data" / "rates.parquet"
REAL_YIELDS_PARQUET = ROOT / "data" / "real_yields.parquet"
NET_LIQUIDITY_PARQUET = ROOT / "data" / "net_liquidity.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
CROSSASSET_YAML = ROOT / "data" / "crossasset_instruments.yaml"
PC_HISTORY_PARQUET = ROOT / "data" / "pc_history.parquet"
PC_THRESHOLDS_YAML = ROOT / "data" / "pc_thresholds.yaml"

# Display labels for the cross-asset top-level factors + instruments.
CROSSASSET_FACTOR_LABELS = {
    "growth": "Growth", "inflation": "Inflation", "labour": "Labour Market",
    "rates": "Rates & Liquidity",
}
CROSSASSET_DISPLAY = {
    "DJIA": "Dow 30", "SP500": "S&P 500", "NASDAQ": "Nasdaq 100",
    "DAX": "DAX 40", "NIKKEI": "Nikkei 225", "FTSE100": "FTSE 100",
    "GOLD": "Gold", "SILVER": "Silver",
}

# FX-style sub-column layout for the cross-asset table: each top-level factor is
# a column GROUP with sub-columns showing the home-currency raw indicator scores
# (growth/inflation/labour reuse the FX indicator keys); the `rates` group's
# sub-columns are the rate composite's components (room for Balance Sheet later).
CROSSASSET_TABLE_LAYOUT = [
    {"key": "growth", "label": "Growth", "columns": [
        ("manufacturing_pmi", "Mfg PMI"), ("services_pmi", "Services"),
        ("gdp_qoq", "GDP"), ("retail_sales", "Retail")]},
    {"key": "inflation", "label": "Inflation", "columns": [
        ("cpi_yoy", "CPI"), ("core_cpi", "Core CPI"),
        ("core_pce", "Core PCE"), ("ppi_yoy", "PPI")]},
    {"key": "labour", "label": "Labour Market", "columns": [
        ("employment_change", "NFP/Emp"), ("unemployment_rate", "Unemp"),
        ("wage_growth", "Wages"), ("adp", "ADP"), ("jolts", "JOLTS"),
        ("jobless_claims", "Claims")]},
    {"key": "rates", "label": "Rates & Liquidity", "columns": [
        ("rate_exp_2y", "Rate Exp 2Y"), ("real_yield_10y", "10Y Real Yield"),
        ("balance_sheet", "Net Liquidity")]},
]
# Home-ccy indicator keys to read from the FX breakdown for the sub-columns.
CROSSASSET_CATEGORY_KEYS = [
    k for g in CROSSASSET_TABLE_LAYOUT if g["key"] != "rates" for (k, _) in g["columns"]
]
# indicator_key -> its category (for applying the per-asset category sign to cells).
CROSSASSET_KEY_CATEGORY = {
    k: g["key"] for g in CROSSASSET_TABLE_LAYOUT if g["key"] != "rates"
    for (k, _) in g["columns"]
}

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
    "rate_expectations": "Rate Expectations (2y)",
}

CATEGORY_LABEL_FALLBACK = {
    "growth": "Growth",
    "inflation": "Inflation",
    "labour": "Labour Market",
    "monetary": "Monetary Policy",
    "rates": "Rates (display-only)",
}

# Dense per-indicator table layout (EdgeFinder-style): one column per indicator,
# grouped under category headers. `interest_rate_decision` is intentionally
# omitted (display-only, weight 0) — it stays in the modal. Short labels are the
# column headers. This is presentation only; scoring is untouched.
TABLE_LAYOUT = [
    {"category": "growth", "columns": [
        ("manufacturing_pmi", "Mfg PMI"),
        ("services_pmi", "Services"),
        ("gdp_qoq", "GDP"),
        ("retail_sales", "Retail"),
    ]},
    {"category": "inflation", "columns": [
        ("cpi_yoy", "CPI"),
        ("core_cpi", "Core CPI"),
        ("core_pce", "Core PCE"),
        ("ppi_yoy", "PPI"),
    ]},
    {"category": "labour", "columns": [
        ("employment_change", "NFP/Emp"),
        ("unemployment_rate", "Unemp"),
        ("wage_growth", "Wages"),
        ("adp", "ADP"),
        ("jolts", "JOLTS"),
        ("jobless_claims", "Claims"),
    ]},
    {"category": "monetary", "columns": [
        ("rate_expectations", "Rate Exp (2y)"),
    ]},
]

TABLE_COLUMN_KEYS = [k for g in TABLE_LAYOUT for (k, _) in g["columns"]]


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
    # Synthetic meta for the standing rate sub-indicator (not in the YAML taxonomy).
    ind_meta["rate_expectations"] = {
        "label": INDICATOR_LABELS["rate_expectations"],
        "category": "monetary",
        "pillar": "monetary",
        "direction": 1,
        "weight": 1.0,
        "max_age_days": 7,
    }

    cat_meta: dict[str, dict] = {}
    for key in list(categories_cfg) + ["monetary", "rates"]:
        cat_meta[key] = {
            "label": (categories_cfg.get(key, {}) or {}).get(
                "label", CATEGORY_LABEL_FALLBACK.get(key, key.title())
            )
        }

    table_layout = [
        {
            "category": g["category"],
            "label": cat_meta.get(g["category"], {}).get("label", g["category"].title()),
            "columns": [{"key": k, "label": lbl} for k, lbl in g["columns"]],
        }
        for g in TABLE_LAYOUT
    ]

    return {
        "categories": cat_meta,
        "indicators": ind_meta,
        "categories_display": instruments_cfg.get("categories_display", []),
        "table_layout": table_layout,
        "bias_thresholds": instruments_cfg.get("bias_thresholds", {}),
        "scale": instruments_cfg.get("scale"),
        "pair_divisor": instruments_cfg.get("pair_divisor"),
    }


def _build_indicator_cells(payload: dict, instruments_cfg: dict) -> None:
    """Attach a per-indicator cell to each instrument for the dense table.

    Pure recombination of the already-computed per-indicator scores (no scoring):
      - single  -> currency's indicator score × sign
      - fx pair -> base score − quote score (a leg missing the indicator counts
                   as 0; if BOTH legs lack it — e.g. a USD-only indicator on a
                   non-USD cross — the cell is None and renders as “—”).
    Each cell is {"v": int|None, "stale": bool}; `stale` is true when any
    contributing leg's indicator is stale (shown greyed, not folded into scoring).
    """
    currencies = payload.get("currencies", {}) or {}
    inst_cfg = instruments_cfg.get("instruments", {}) or {}

    def _bd(ccy: str | None) -> dict:
        return (currencies.get(ccy, {}) or {}).get("breakdown", {}) or {}

    for inst in payload.get("instruments", []):
        cfg = inst_cfg.get(inst["symbol"], {})
        cells: dict[str, dict] = {}
        bdn = inst.get("breakdown", {}) or {}
        base_ccy = (bdn.get("base") or {}).get("currency")
        quote_ref = bdn.get("quote")
        quote_ccy = quote_ref.get("currency") if quote_ref else None

        if inst.get("type") == "single":
            sign = float(cfg.get("sign", 1))
            bd = _bd(base_ccy)
            for k in TABLE_COLUMN_KEYS:
                e = bd.get(k)
                if e is None:
                    cells[k] = {"v": None, "stale": False}
                else:
                    cells[k] = {"v": int(round(e["score"] * sign)), "stale": bool(e.get("stale"))}
        else:
            bb, bq = _bd(base_ccy), _bd(quote_ccy)
            for k in TABLE_COLUMN_KEYS:
                eb, eq = bb.get(k), bq.get(k)
                if eb is None and eq is None:
                    cells[k] = {"v": None, "stale": False}
                else:
                    v = (eb["score"] if eb else 0) - (eq["score"] if eq else 0)
                    stale = bool((eb and eb.get("stale")) or (eq and eq.get("stale")))
                    cells[k] = {"v": int(v), "stale": stale}
        inst["indicator_cells"] = cells


def _enrich_breakdowns(payload: dict, ind_meta: dict, as_of: pd.Timestamp) -> None:
    """Add recency (release_dt iso, age_days) to every per-indicator entry.

    Mutates in place. `stale` is set authoritatively by compute (an indicator is
    stale iff its latest actual is older than max_age_days — it's then shown but
    excluded from every average/index); here we only add the age in days and a
    defensive fallback if compute didn't set the flag.
    """
    for card in payload.get("currencies", {}).values():
        for key, entry in (card.get("breakdown") or {}).items():
            rdt = entry.get("release_dt")
            ts = pd.Timestamp(rdt) if rdt is not None else None
            max_age = (ind_meta.get(key, {}) or {}).get("max_age_days", 120)
            if ts is not None and not pd.isna(ts):
                age = int((as_of - ts).days)
                entry["age_days"] = age
                entry.setdefault("stale", age > max_age)
            else:
                entry["age_days"] = None
                entry.setdefault("stale", False)


def _metals_cot_map() -> dict[str, dict]:
    """{metal_symbol: COT detail dict} from the validated COT engine (read-only).

    Computed ONCE and reused for both the cross-asset SCORE (sentiment factor)
    and the display sub-cell. Reuses src.cot_score.score_metals over the extremes
    from src.compute — no scoring logic here. COT parquet missing/empty → {} (so
    metals carry no `cot` key and are excluded from the sentiment factor exactly
    like a missing factor), mirroring the graceful real_yield/net_liquidity path.
    """
    try:
        scored = score_metals(load_metals_history())
    except Exception as e:  # noqa: BLE001 — display-only path; never break the page
        log.warning("COT sentiment unavailable (%s); metals shown blank.", e)
        return {}
    if scored.empty:
        return {}
    return {
        row["symbol"]: {
            "cell": int(row["cell"]),
            "level": int(row["level"]),
            "flow": int(row["flow"]),
            "blend": float(row["blend"]),
            "z": None if pd.isna(row["z"]) else float(row["z"]),
            "basis": str(row["basis"]),
        }
        for _, row in scored.iterrows()
    }


def _current_equity_pc_percentile() -> float | None:
    """Latest rolling-1Y percentile (0..100) of the CBOE equity P/C ratio.

    Reuses the existing P/C pipeline (sentiment_compute.compute_pc_metrics over
    data/pc_history.parquet) — read-only; the percentile is NOT recomputed here.
    Returns None if the parquet is missing/unreadable or the percentile is not
    available (so the caller can render a blank cell instead of a fake 0).
    """
    if not PC_HISTORY_PARQUET.exists():
        return None
    try:
        pc_df = pd.read_parquet(PC_HISTORY_PARQUET)
        metrics = compute_pc_metrics(pc_df, str(PC_THRESHOLDS_YAML))
        return metrics["equity"]["current"]["percentile_rank"]
    except Exception as e:  # noqa: BLE001 — display-only; never break the page
        log.warning("equity P/C percentile unavailable (%s); US indices blank.", e)
        return None


def _us_index_pc(cfg: dict) -> tuple[int | None, dict | None, set[str]]:
    """(cell, detail, us_index_symbols) for the market-wide US equity P/C score.

    The CBOE equity put/call ratio is market-wide and US-only, so EVERY US index
    (type=index, home_ccy=USD) gets the SAME contrarian cell; foreign indices
    (DAX/NIKKEI/FTSE100) get nothing → blank and excluded from the sentiment
    factor. A missing percentile → cell None (US indices blank, score unchanged).
    """
    instruments_cfg = cfg.get("instruments", {}) or {}
    us_syms = {
        sym for sym, c in instruments_cfg.items()
        if c.get("type") == "index" and c.get("home_ccy") == "USD"
    }
    pct = _current_equity_pc_percentile()
    if pct is None:
        return None, None, us_syms
    cell, detail = pc_index_score(pct)
    return int(cell), detail, us_syms


def _fx_currency_cells() -> tuple[dict[str, int], dict[str, dict]]:
    """(cells, details) per currency COT contract from score_currencies (read-only).

    Computed ONCE and reused for both the FX SCORE (sentiment factor, via
    build_payload) and the display sub-cell. cells: {COT_symbol: cell int};
    details: {COT_symbol: {level, flow, blend, z, basis}}. Empty on failure
    (FX rows then carry no sentiment in score or display).
    """
    try:
        scored = score_currencies(load_currencies_history())
    except Exception as e:  # noqa: BLE001 — never break the page
        log.warning("FX COT sentiment unavailable (%s); FX shown blank.", e)
        return {}, {}
    if scored.empty:
        return {}, {}
    cells = {row["symbol"]: int(row["cell"]) for _, row in scored.iterrows()}
    details = {
        row["symbol"]: {
            "level": int(row["level"]), "flow": int(row["flow"]),
            "blend": float(row["blend"]),
            "z": None if pd.isna(row["z"]) else float(row["z"]),
            "basis": str(row["basis"]),
        }
        for _, row in scored.iterrows()
    }
    return cells, details


def _attach_fx_cot_cells(payload: dict, cells: dict[str, int],
                         details: dict[str, dict]) -> None:
    """Attach the COT SENTIMENT sub-cell to each FX instrument (in place).

    Display sub-cell, reusing the SAME per-currency cells folded into the FX
    score (build_payload sentiment_cells). CFTC currency contracts are quoted vs
    USD, so an FX-pair cell = pair_cot(leg(base), leg(quote)) with the USD leg = 0
    (no DXY here — that would double-count the dollar). The single USD-index row
    uses the DXY contract directly. A pair whose non-USD leg has no COT data
    renders blank. No cells → every FX row blank.
    """
    instruments = payload.get("instruments", []) or []
    if not instruments or not cells:
        return

    def _leg(ccy: str | None):
        """(cell, detail) for a leg. USD -> 0 (no detail). Missing ccy -> None."""
        if ccy == "USD":
            return 0, None
        return cells.get(ccy), details.get(ccy)

    for inst in instruments:
        bdn = inst.get("breakdown", {}) or {}
        base_ccy = (bdn.get("base") or {}).get("currency")
        quote_ref = bdn.get("quote")
        quote_ccy = quote_ref.get("currency") if quote_ref else None

        if inst.get("type") == "single" and base_ccy == "USD":
            # USD-index row → the DXY contract cell directly.
            dxy = cells.get("DXY")
            if dxy is None:
                continue
            inst["cot"] = {
                "cell": dxy, "base": "USD", "quote": None,
                "base_cell": dxy, "quote_cell": None,
                "base_detail": details.get("DXY"), "quote_detail": None,
            }
            continue

        if inst.get("type") != "fx":
            continue

        base_cell, base_detail = _leg(base_ccy)
        quote_cell, quote_detail = _leg(quote_ccy)
        # A genuinely-missing (non-USD) currency leg → leave the pair blank.
        if (base_ccy != "USD" and base_cell is None) or (
            quote_ccy != "USD" and quote_cell is None
        ):
            continue

        inst["cot"] = {
            "cell": pair_cot(base_cell, quote_cell),
            "base": base_ccy, "quote": quote_ccy,
            "base_cell": base_cell, "quote_cell": quote_cell,
            "base_detail": base_detail, "quote_detail": quote_detail,
        }


def _build_crossasset_block(payload: dict, as_of: pd.Timestamp) -> dict:
    """Compute the cross-asset section (indices + metals) from the FX payload's
    per-currency category scores + the US real-yield momentum. Read-only over
    local parquets; real_yields.parquet missing/empty → real_yield excluded
    gracefully. Returns a JSON-ready dict (instruments sorted most-bullish first).
    """
    try:
        cfg = _load_yaml(CROSSASSET_YAML)
    except FileNotFoundError:
        return {}

    categories_by_ccy = {
        ccy: card.get("categories", {})
        for ccy, card in payload.get("currencies", {}).items()
    }

    real_yield_score = None
    real_yield_meta = {"present": False, "series": "DFII10"}
    if REAL_YIELDS_PARQUET.exists():
        try:
            rdf = pd.read_parquet(REAL_YIELDS_PARQUET)
            if len(rdf):
                rys = compute_realyield_score(rdf, as_of=as_of.date())
                if rys is not None:
                    real_yield_score = rys
                    real_yield_meta = {
                        "present": True, "series": rys.series, "score": rys.score,
                        "delta_w": rys.delta_w, "latest_yield": rys.latest_yield,
                        "z": rys.z, "method": rys.method,
                        "as_of": rys.as_of.isoformat() if rys.as_of is not None else None,
                        "stale": rys.stale,
                    }
        except Exception as e:
            log.warning("real_yields.parquet present but unreadable (%s); real_yield excluded.", e)

    # Fed net liquidity (WALCL−TGA−RRP) → balance_sheet sub-component. Missing
    # parquet → excluded gracefully (mirrors real_yield).
    liquidity_score = None
    liquidity_meta = {"present": False, "series": "NET_LIQUIDITY"}
    if NET_LIQUIDITY_PARQUET.exists():
        try:
            ndf = pd.read_parquet(NET_LIQUIDITY_PARQUET)
            if len(ndf):
                ls = compute_liquidity_score(ndf, as_of=as_of.date())
                if ls is not None:
                    liquidity_score = ls
                    liquidity_meta = {
                        "present": True, "series": "NET_LIQUIDITY", "score": ls.score,
                        "roc": ls.roc, "latest": ls.latest, "method": ls.method,
                        "as_of": ls.as_of.isoformat() if ls.as_of is not None else None,
                        "stale": ls.stale,
                    }
        except Exception as e:
            log.warning("net_liquidity.parquet present but unreadable (%s); balance_sheet excluded.", e)

    # SENTIMENT cells computed ONCE (read-only over the COT/P-C engines), then
    # fed into the SCORE as a weight-0.5 factor AND reused for the display cell.
    metal_cot = _metals_cot_map()
    pc_cell, pc_detail, us_index_syms = _us_index_pc(cfg)
    sentiment_by_symbol: dict[str, int] = {
        sym: d["cell"] for sym, d in metal_cot.items()
    }
    if pc_cell is not None:
        for sym in us_index_syms:
            sentiment_by_symbol[sym] = pc_cell

    scores = compute_crossasset_scores(categories_by_ccy, real_yield_score, cfg,
                                       liquidity_score=liquidity_score,
                                       sentiment_by_symbol=sentiment_by_symbol)

    currencies = payload.get("currencies", {}) or {}

    def _home_breakdown(home: str) -> dict:
        return (currencies.get(home, {}) or {}).get("breakdown", {}) or {}

    instruments = []
    for sym, r in scores.items():
        r = dict(r)
        r["display"] = CROSSASSET_DISPLAY.get(sym, sym)
        home = r.get("home_ccy")
        brk = _home_breakdown(home)

        # Sub-column cells: the PER-ASSET signed score (home-ccy raw × this
        # instrument's category sign), so each cell already points the right way
        # for the asset. The per-indicator `direction` is already baked into the
        # home-ccy raw score; the category sign is the separate asset layer — we
        # apply it exactly once here.
        fsign = {f["name"]: f.get("sign") for f in r.get("factors", []) if f.get("sign") is not None}
        cells: dict[str, dict] = {}
        for key in CROSSASSET_CATEGORY_KEYS:
            e = brk.get(key)
            raw = None if e is None else e.get("score")
            if raw is None:
                cells[key] = {"score": None, "stale": False}
            else:
                s = fsign.get(CROSSASSET_KEY_CATEGORY.get(key, ""), 1)
                cells[key] = {"score": float(s) * raw, "stale": bool(e.get("stale"))}
        # Rate sub-columns: signed by the component's own sign (already directional).
        # Shown even when stale (greyed); the stale exclusion happens in the mean.
        rates_factor = next((f for f in r.get("factors", []) if f.get("name") == "rates"), None)
        for c in (rates_factor.get("components", []) if rates_factor else []):
            raw = c.get("raw")
            signed = (float(c.get("sign", 1)) * raw) if raw is not None else None
            cells[c["name"]] = {"score": signed, "stale": bool(c.get("stale"))}
        r["cells"] = cells

        # SENTIMENT display sub-cell, reusing the SAME cells folded into the score
        # above: COT for metals, P/C for US indices. Foreign indices get neither.
        if sym in metal_cot:
            r["cot"] = metal_cot[sym]
        elif pc_cell is not None and sym in us_index_syms:
            r["sentiment"] = {
                "source": "pc", "cell": pc_cell,
                "pct": float(pc_detail["pct"]), "basis": str(pc_detail["basis"]),
            }

        instruments.append(r)

    instruments.sort(key=lambda r: r.get("score_precise", 0.0), reverse=True)

    return {
        "instruments": instruments,
        "table_layout": [
            {"key": g["key"], "label": g["label"],
             "columns": [{"key": k, "label": lbl} for k, lbl in g["columns"]]}
            for g in CROSSASSET_TABLE_LAYOUT
        ],
        "factor_labels": dict(CROSSASSET_FACTOR_LABELS),
        "real_yield": real_yield_meta,
        "net_liquidity": liquidity_meta,
        "bias_thresholds": cfg.get("bias_thresholds", {}),
        "scale": cfg.get("scale"),
    }


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

    # Rate-Expectations engine (C1): optional 4th "monetary" category. Missing
    # parquet → render exactly as before (3 categories), no crash.
    rate_scores = {}
    rate_sources_by_ccy: dict[str, str] = {}
    if RATES_PARQUET.exists():
        try:
            rates_df = pd.read_parquet(RATES_PARQUET)
            rate_scores = compute_rate_scores(rates_df, as_of=as_of.date())
            rates_df = rates_df.sort_values("date")
            rate_sources_by_ccy = {
                str(c): str(g["source"].iloc[-1])
                for c, g in rates_df.groupby("currency")
            }
        except Exception as e:
            log.warning("rates.parquet present but unreadable (%s); skipping monetary.", e)
            rate_scores = {}

    # SENTIMENT (COT) per currency, computed ONCE: fed into the FX SCORE as a
    # weight-0.5 factor AND reused for the display sub-cell below.
    fx_cells, fx_details = _fx_currency_cells()

    payload = build_payload(cal, indicators_cfg, instruments_cfg,
                            as_of=as_of, rate_scores=rate_scores or None,
                            sentiment_cells=fx_cells or None)

    meta = _build_meta(indicators_cfg, instruments_cfg)
    _enrich_breakdowns(payload, meta["indicators"], as_of)
    # Attach the chosen source to each rate_expectations breakdown entry.
    for ccy, card in payload.get("currencies", {}).items():
        entry = (card.get("breakdown") or {}).get("rate_expectations")
        if entry is not None and ccy in rate_sources_by_ccy:
            entry["source"] = rate_sources_by_ccy[ccy]
    _build_indicator_cells(payload, instruments_cfg)

    # Presentation-only label trim (keeps the symbol column tight).
    for inst in payload.get("instruments", []):
        if inst.get("display"):
            inst["display"] = inst["display"].replace("(DXY proxy)", "(DXY)")

    # SENTIMENT display sub-cell on the FX rows, reusing the same per-currency
    # cells folded into the FX score above.
    _attach_fx_cot_cells(payload, fx_cells, fx_details)

    # Cross-Asset block (indices + metals) — separate key, FX payload untouched.
    payload["crossasset"] = _build_crossasset_block(payload, as_of)

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
