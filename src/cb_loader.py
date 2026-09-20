"""Loads the Central Banks datasets into an in-memory `cb_compute.engine.Context` (all I/O of phase 1B-2 lives here;
src/cb_compute/ stays pure)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from . import cb_collect as cc
from . import cb_datasets as ds
from .cb_calendar import effective_date, load_calendars
from .cb_compute.decisions import SeriesView
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
        projections=ds.load_projections(paths),
        rbnz=ds.load_rbnz(paths.manual / "rbnz.yaml"),
        stale_after_bd=int(sources_cfg["meta"].get("stale_after_bd", 2)))


def load_pair_defs(path: Path | str | None = None) -> list:
    """The currency pairs of /economic (data/economic_instruments.yaml, type fx): [(name, base, quote, display)]."""
    p = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "economic_instruments.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return [(name, i["base"], i["quote"], i.get("display") or name) for name, i in doc["instruments"].items() if i.get("type") == "fx"]


def load_pairs(path: Path | str | None = None) -> list:
    """[(name, base, quote)] of the same pairs."""
    return [(n, b, q) for n, b, q, _ in load_pair_defs(path)]
