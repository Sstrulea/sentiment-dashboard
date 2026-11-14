"""Loads the Central Banks datasets into an in-memory `cb_compute.engine.Context` (all I/O of phase 1B-2 lives here;
src/cb_compute/ stays pure)."""
from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Optional

import yaml

from . import cb_collect as cc
from . import cb_datasets as ds
from .cb_calendar import effective_date, load_calendars
from .cb_compute.decisions import SeriesView
from .cb_docs import store as dst
from .cb_summarize import store as sm
from .cb_compute.engine import Context, MarketIndex, Meeting
from .cb_sources.base import load_sources
from .cb_sources.official import load_official


def build_meetings(meetings_raw: dict, banks: dict, calendars: dict) -> dict:
    out = {}
    for cur, rows in meetings_raw.items():
        cfg = banks[cur]
        cal = calendars[cfg["calendar_id"]]
        out[cur] = sorted((Meeting(r["date"], effective_date(cfg["effective_rule"], r["date"], cal), r.get("first_day"),
                                   bool(r.get("has_projections")), bool(r.get("has_presser")), str(r.get("source") or ""),
                                   bool(r.get("verified"))) for r in rows), key=lambda m: m.decision)
    return out


NZD_SPREAD = Path(__file__).resolve().parent.parent / "config" / "cb_nzd_spread.yaml"


def load_estimated_spreads(path: Path | str | None = None) -> dict:
    """currency -> estimated benchmark - policy spread. NZD: config/cb_nzd_spread.yaml (scripts/cb_nzd_bkbm_spread.py); absent file = none."""
    p = Path(path) if path else NZD_SPREAD
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {"NZD": dict(doc, benchmark="BKBM 3M (estimated, monthly)", policy="OCR")} if doc.get("value_bp") is not None else {}


def load_context(data_dir: Path | str | None = None) -> Context:
    paths = cc.Paths(data_dir)
    banks = ds.load_banks()
    sources_cfg = load_sources()
    official = load_official()
    calendars = load_calendars()
    decisions: dict = {}
    for r in ds.load_decisions(paths):
        decisions.setdefault(r["currency"], []).append(r)
    for rows in decisions.values():
        rows.sort(key=lambda r: r["meeting_date"])
    return Context(
        banks=banks, sources=sources_cfg["sources"], calendars=calendars,
        market=MarketIndex(cc.load_store(paths).values()),
        view=SeriesView(list(cc.load_official_store(paths).values())),
        decisions=decisions,
        meetings=build_meetings(ds.load_meetings(paths.meetings), banks, calendars),
        series_calendar={sid: s.get("calendar_id") for sid, s in official["series"].items()},
        series_names={sid: s.get("name") or sid for sid, s in official["series"].items()},
        projections=ds.load_projections(paths),
        rbnz=ds.load_rbnz(paths.manual / "rbnz.yaml"),
        documents=sorted(dst.load_documents(paths).values(), key=lambda r: (r["currency"], r["published_date"], r["doc_id"])),
        votes={(r["currency"], r["meeting_date"]): r for r in dst.load_votes(paths)},
        redlines={(r["currency"], r["meeting_date"]): r for r in dst.load_redlines(paths)},
        summaries=sm.load(paths.summaries), summary_failures=sm.load_failures(paths.summaries), summary_no_text=sm.load_no_text(paths.summaries),

        estimated_spreads=load_estimated_spreads(),
        stale_after_bd=int(sources_cfg["meta"].get("stale_after_bd", 2)))


def load_pair_defs(path: Path | str | None = None) -> list:
    """The currency pairs of /economic (data/economic_instruments.yaml, type fx): [(name, base, quote, display)]."""
    p = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "economic_instruments.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return [(name, i["base"], i["quote"], i.get("display") or name) for name, i in doc["instruments"].items() if i.get("type") == "fx"]


def load_pairs(path: Path | str | None = None) -> list:
    """[(name, base, quote)] of the same pairs."""
    return [(n, b, q) for n, b, q, _ in load_pair_defs(path)]


# ---------------------------------------------------------------------------
# Stance (phase S1): every read for src/cb_compute/stance.py (which stays pure)
# ---------------------------------------------------------------------------

def load_stance(data_dir: Path | str | None = None, asof: Optional[date] = None, ctx: Optional[Context] = None) -> tuple:
    """(asof, params, inputs {ccy: BankInputs}, results {ccy: dict}). Validates the manual files (StanceDataError on an error)."""
    from . import cb_framework, cb_macro
    from .cb_compute import stance as ST
    from .cb_compute.framework_watch import last_projection_dates
    from .cb_compute.stance_inputs import current_version
    paths = cc.Paths(data_dir)
    ctx = ctx or load_context(data_dir)
    asof = asof or ctx.market.newest()
    params = ds.load_stance_config()
    mand = ds.load_mandates(paths, today=asof, validate=True)
    neut = ds.load_neutral(paths, today=asof, validate=True, mandates=mand)
    fc = ds.load_forecasts(paths, today=asof, validate=True, mandates=mand)
    cur = {b: current_version(v, asof) for b, v in ds.mandate_versions(mand).items()}
    mcfg = load_official()["macro"]
    per = cb_macro.by_series(cb_macro.load_macro(paths, end=asof))
    stale = {sid for sid, s in mcfg["series"].items()
             if cb_macro.is_stale(max((p for p, r in (per.get(sid) or {}).items() if r["period_start"] <= asof), default=None),
                                  s["lag_days"], asof, mcfg["meta"]["stale_grace_days"])}
    roster = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "cb_roster.yaml").read_text(encoding="utf-8"))
    try:
        alerts = cb_framework.open_alerts(paths, asof)
    except Exception:                                                     # noqa: BLE001 - a broken watch must not block the stance
        alerts = []
    lastp = last_projection_dates(ds.load_meetings(paths.meetings), asof)
    sep_last = max((r["meeting_date"] for r in ctx.projections if r["meeting_date"] <= asof), default=None)
    entered = {b: (sep_last if b == "USD" else ((fc.get("banks") or {}).get(b) or {}).get("meeting")) for b in ST.BANKS}
    newer = {b: bool(lastp.get(b) and entered.get(b) and lastp[b] > entered[b]) for b in ST.BANKS}
    inputs = ST.assemble(ctx, cur, neut, fc, per, mcfg["series"], roster, {a.bank for a in alerts}, params, asof, stale, newer)
    return asof, params, inputs, ST.compute_all(inputs, params)
