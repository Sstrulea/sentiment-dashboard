"""Stance S1.1 - the full official policy-rate trajectory from 2015, for the cycle only (config `policy_history:` in
config/cb_official.yaml): data/cb/policy_history/policy_history_YYYY.parquet, key (series_id, date), values as published.

Never used for decisions or for the rate of the stance (those stay on the official series / decisions.parquet). Refreshed by
the `stance` stage before the daily snapshot (lookback `meta.lookback_days`); `backfill=True` reads from `meta.from`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from . import cb_store as cs
from .cb_sources.official import PROVIDERS, load_official

log = logging.getLogger("cb_policy_history")

ROLES = ("policy_bis_history", "policy_official_history")


def _dataset():
    from .cb_collect import OFFICIAL_SCHEMA                          # late import: cb_collect imports stage modules lazily
    return cs.Dataset("policy_history", "policy_history", OFFICIAL_SCHEMA, ("series_id", "date"), "date",
                      ("currency", "value", "unit"), granularity="year")


def load_config(cfg: Optional[dict] = None) -> dict:
    return (cfg or load_official())["policy_history"]


def load(paths, end: Optional[date] = None) -> dict:
    return cs.load(_dataset(), paths.dir, None, end)


def by_currency(store: dict, series_cfg: dict, asof: Optional[date] = None) -> dict:
    """{currency: (series_id, [(date, value)] sorted)} - one history series per bank; values on or before `asof`."""
    out: dict = {}
    for sid, s in series_cfg.items():
        pts = sorted((r["date"], r["value"]) for (k, _), r in store.items() if k == sid and (asof is None or r["date"] <= asof)
                     and r["value"] is not None and r["value"] == r["value"])
        out[s["currency"]] = (sid, pts)
    return out


@dataclass
class HistoryReport:
    provider: str
    status: str = ""
    note: str = ""
    since: Optional[date] = None
    merge: cs.Merge = field(default_factory=cs.Merge)
    last: dict = field(default_factory=dict)            # series_id -> last date stored


def run_history(paths, today: date, *, backfill: bool = False, cfg: Optional[dict] = None,
                now: Optional[Callable[[], datetime]] = None, sources: Optional[dict] = None) -> list:
    full = cfg or load_official()
    hc = load_config(full)
    clock = now or (lambda: datetime.now(timezone.utc))
    ds = _dataset()
    store = cs.load(ds, paths.dir)
    have = {k for k, _ in store}
    provs: dict = {}
    for sid, s in hc["series"].items():
        provs.setdefault(s["provider"], {})[sid] = s
    reports, months = [], set()
    for prov, series in provs.items():
        rep = HistoryReport(prov)
        reports.append(rep)
        start = date.fromisoformat(str(hc["meta"]["from"]))
        rep.since = start if backfill or any(sid not in have for sid in series) else today - timedelta(days=hc["meta"]["lookback_days"])
        try:
            src = (sources or {}).get(prov) or PROVIDERS[prov](full["providers"][prov], series, now=clock)
            res = src.fetch(since=rep.since, state={})
        except Exception as e:                                          # noqa: BLE001 - reported, never raised
            rep.status, rep.note = "FAILED", f"{type(e).__name__}: {e}"
            continue
        if res is None:
            rep.status, rep.note = "FAILED", f"{src.last_status} {src.last_note}".strip()
            continue
        rep.status, rep.note = res.status, res.note
        fetched = clock()
        rows = [{"series_id": o.series_id, "currency": series[o.series_id]["currency"], "date": o.date, "value": o.value,
                 "unit": series[o.series_id]["unit"], "fetched_at": fetched}
                for o in res.obs if o.series_id in series and o.value is not None and o.value == o.value]
        rep.merge = cs.merge(ds, store, rows)
        months |= rep.merge.months
    cs.write(ds, paths.dir, store, months)
    for rep in reports:
        rep.last = {sid: max((d for (k, d) in store if k == sid), default=None) for sid, s in hc["series"].items() if s["provider"] == rep.provider}
    return reports


def history_report(reports: list) -> tuple:
    lines = [f"policy history {r.provider}: {r.status or '-'} since {r.since} new {r.merge.new} upd {r.merge.updated}"
             + (f" ({r.note})" if r.note else "") + "; last " + ", ".join(f"{k} {v}" for k, v in r.last.items()) for r in reports]
    text = "\n".join(lines)
    return text, "### policy history\n\n" + "\n".join(f"- {ln}" for ln in lines)
