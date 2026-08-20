"""Renders /history.html (FAZA 1C — inflation-only UI pilot).

Mirrors economic_render.py / sentiment_render.py's render-function pattern,
but the payload is EMBEDDED in the page (a `window.HISTORY_PAYLOAD = {...}`
script block), not fetched at runtime — a deliberate deviation from every
other page on this site, per the FAZA 1C brief ("site static, fără fetch
runtime"). Read-only over history_compute/data_integrity; does not touch
to_scoring_frame, compute_indicator_score, or manual_actuals.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import history_compute as hc
from .data_integrity import build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros
from .economic_fetch import CompiledMatcher
from .ff_scoring import CCY2COUNTRY, build_matcher

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html"]))


def _build_quarantine_df(ff: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})[
        ["currency", "indicator_key", "canonical_id", "name_raw", "release_dt",
         "actual", "forecast", "previous"]]
    ghosts = detect_ghost_rows(df)
    zeros = detect_implausible_zeros(df)
    return build_quarantine_proposal(ghosts, zeros)


def build_inflation_only_catalog() -> dict:
    """The FAZA 1C pilot renders ONLY the `inflation` category — the other 3
    stay in data/econ_catalog.yml (used for backend testing/future phases)
    but are NOT included in this page's payload."""
    full = hc.load_catalog()
    inflation = full.get("categories", {}).get("inflation", {})
    return {"categories": {"inflation": inflation}}


def build_history_payload(as_of: pd.Timestamp | None = None) -> dict:
    as_of = as_of or pd.Timestamp.now()
    if not FF_PARQUET.exists():
        return {"meta": {"generated_at": as_of.isoformat(), "catalog_version": "unavailable",
                        "as_of": as_of.isoformat()}, "categories": {}}

    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    ind_cfg = hc.load_indicators_cfg()
    matcher = build_matcher()
    catalog = build_inflation_only_catalog()

    quarantine_df = _build_quarantine_df(ff, matcher)
    series_cache = hc.compute_catalog(ff, ind_cfg, catalog, quarantine_df, as_of=as_of)
    return hc.build_payload(catalog, series_cache, catalog_version="inflation-pilot-2026-08-20", as_of=as_of)


def render_history_page() -> Path:
    """Build the payload, embed it, write /history.html. No JSON file under
    public/data/ (unlike every other page) — the payload lives only in the
    HTML, per the "no runtime fetch" pilot requirement."""
    from .static_assets import copy_static_assets
    copy_static_assets()

    payload = build_history_payload()
    # allow_nan=False: fail loudly if a NaN ever leaked past history_compute's
    # own _json_num scrubbing, rather than emit invalid embedded JSON.
    payload_json = json.dumps(payload, default=str, allow_nan=False, separators=(",", ":"))
    # Prevent a literal "</script>" inside a string value (a name_raw or
    # source_note could plausibly contain it) from closing the embedding
    # <script> tag early.
    payload_json = payload_json.replace("</", "<\\/")

    env = _env()
    template = env.get_template("history.html.j2")
    html = template.render(active_page="history", payload_json=payload_json)
    out_path = PUBLIC_DIR / "history.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    print(f"Rendered: {render_history_page()}")
