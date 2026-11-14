"""Stance phase S0a - official inflation / unemployment series -> data/cb/macro_series/macro_series_YYYY.parquet.

    python -m src.cb_collect --stage macro                 # the daily scheduled run (cb-refresh, cron 23 7 * * *)
    python -m src.cb_collect --stage macro --backfill      # from config macro.meta.backfill_from (2015-01)

DISPLAY-ONLY (ARCHITECTURE section 4): nothing here is read by /economic, the scoring or history_compute.

Store: one row per (series_id, period); period '2026-09' (monthly) or '2026Q3' (quarterly), dated by `period_start`;
yearly partitions. Values are stored exactly as the agency publishes them (`published_as` index | yoy | rate); y/y from
an index is computed in code (src/cb_compute/macro_yoy.py), never stored.

Rules
  * one adapter per provider (src/cb_sources/official.py); the series are the `macro:` section of config/cb_official.yaml;
  * ZERO requests for a provider when none of its series expects a new period: the next period is due `lag_days` after
    its end (asked from `check_lead_days` earlier); a backfill always asks;
  * guard, per series, before anything is written: stored periods whose value changes are revisions - accepted and
    logged (old -> new) when few; when at least `min_changed` periods AND more than `max_changed_share` of the overlap
    change (a rebasing, a redefinition), the series is REFUSED: nothing of it is written, the refusal is kept in
    state.json (`_macro_refused`) for --status until a later fetch passes;
  * a row whose content is unchanged keeps its `fetched_at` (idle runs leave no diff); `release_date` is the date the
    source prints for its latest release (ONS, RBA, SNB), else null; an older row keeps the release date it had.
"""
from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import pyarrow as pa

from . import cb_store as cs
from .cb_sources.official import PROVIDERS, load_official

log = logging.getLogger("cb_macro")

MACRO_SCHEMA = pa.schema([
    ("series_id", pa.string()), ("currency", pa.string()), ("period", pa.string()), ("period_start", pa.date32()),
    ("freq", pa.string()), ("value", pa.float64()), ("unit", pa.string()), ("published_as", pa.string()),
    ("release_date", pa.date32()), ("fetched_at", pa.timestamp("us", tz="UTC")), ("source_url", pa.string()),
])
MACRO = cs.Dataset("macro_series", "macro_series", MACRO_SCHEMA, ("series_id", "period"), "period_start",
                   ("currency", "value", "unit", "published_as", "freq"), granularity="year")
REFUSED_KEY = "_macro_refused"              # state.json: {series_id: {since, changed, overlap, examples}}
ROLES = ("headline", "underlying", "secondary", "unemployment", "crosscheck")
PUBLISHED_AS = ("index", "yoy", "rate")


# ---------------------------------------------------------------------------
# Periods (pure)
# ---------------------------------------------------------------------------

def period_label(d: date, freq: str) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}" if freq == "Q" else f"{d.year}-{d.month:02d}"


def period_start(p: str) -> date:
    if "Q" in p:
        return date(int(p[:4]), 3 * int(p[-1]) - 2, 1)
    return date(int(p[:4]), int(p[5:7]), 1)


def period_end(p: str) -> date:
    s = period_start(p)
    months = 3 if "Q" in p else 1
    y, m = divmod(s.month - 1 + months, 12)
    return date(s.year + y, m + 1, 1) - timedelta(days=1)


def next_period(p: str) -> str:
    from .official_series import period_shift
    return period_shift(p, 1)


def due_date(last_period: str, lag_days: int) -> date:
    """When the period after `last_period` is typically first released."""
    return period_end(next_period(last_period)) + timedelta(days=lag_days)


def is_due(last_period: Optional[str], lag_days: int, today: date, lead_days: int) -> bool:
    return last_period is None or today >= due_date(last_period, lag_days) - timedelta(days=lead_days)


def is_stale(last_period: Optional[str], lag_days: int, today: date, grace_days: int) -> bool:
    """The next period should have been released more than `grace_days` ago and is not stored."""
    return last_period is None or today > due_date(last_period, lag_days) + timedelta(days=grace_days)


# ---------------------------------------------------------------------------
# Guard (pure)
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    accepted: bool
    overlap: int = 0
    changed: list = field(default_factory=list)        # [(period, old, new)]
    reason: str = ""


def guard(stored: dict, fetched: dict, *, min_changed: int, max_changed_share: float) -> GuardResult:
    """stored / fetched: {period: value} of ONE series. Revisions are accepted when few; a change of most of the
    overlapping history (rebasing, redefinition) is refused."""
    common = sorted(set(stored) & set(fetched))
    changed = [(p, stored[p], fetched[p]) for p in common if round(stored[p], 9) != round(fetched[p], 9)]
    res = GuardResult(True, len(common), changed)
    if len(changed) >= min_changed and common and len(changed) / len(common) > max_changed_share:
        res.accepted = False
        res.reason = (f"{len(changed)} of {len(common)} stored periods changed (> {max_changed_share:.0%}): rebasing or redefinition"
                      " - nothing written; check the source and add a new series (new base) if it was rebased")
    return res


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def load_macro(paths, start=None, end=None) -> dict:
    return cs.load(MACRO, paths.dir, start, end)


def by_series(store: dict) -> dict:
    """{series_id: {period: row}}"""
    out: dict = {}
    for r in store.values():
        out.setdefault(r["series_id"], {})[r["period"]] = r
    return out


def load_macro_config(cfg: dict | None = None) -> dict:
    return (cfg or load_official())["macro"]


def _source_url(prov_cfg: dict, s: dict, since: date) -> str:
    """The provider URL with the series' parameters (unknown placeholders stay as they are)."""
    class _Keep(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    vals = _Keep({**{k: v for k, v in s.items() if isinstance(v, (str, int))}, "since": since.isoformat()})
    return string.Formatter().vformat(prov_cfg.get("url") or prov_cfg.get("provenance", ""), (), vals)   # FRED: the series page (no download URL)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@dataclass
class MacroReport:
    provider: str
    status: str = ""                 # ok | skipped | FAILED | refused
    note: str = ""
    requested: bool = False
    merge: cs.Merge = field(default_factory=cs.Merge)
    revisions: list = field(default_factory=list)       # [(series_id, period, old, new)]
    refused: dict = field(default_factory=dict)         # series_id -> reason
    failed: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)            # series_id -> (period, value)


def run_macro(paths, today: date, *, backfill_from: date | None = None, only: list | None = None,
              cfg: dict | None = None, now: Callable[[], datetime] | None = None, sources: dict | None = None) -> list:
    """One report per provider. `sources` (tests) maps provider -> an adapter instance."""
    from .cb_collect import load_state, save_state
    full = cfg or load_official()
    mcfg = full["macro"]
    meta = mcfg["meta"]
    clock = now or (lambda: datetime.now(timezone.utc))
    store = load_macro(paths)
    per = by_series(store)
    state = load_state(paths)
    refused_state = dict(state.get(REFUSED_KEY, {}))
    providers: dict = {}
    for sid, s in mcfg["series"].items():
        providers.setdefault(s["provider"], {})[sid] = s
    reports = []
    for prov, series in providers.items():
        if only and prov not in only:
            continue
        rep = MacroReport(prov)
        reports.append(rep)
        last = {sid: (max(per[sid]) if per.get(sid) else None) for sid in series}
        if backfill_from is None and not any(is_due(last[sid], s["lag_days"], today, meta["check_lead_days"]) for sid, s in series.items()):
            rep.status = "skipped"
            rep.note = "no new period expected (next due " + ", ".join(
                f"{sid} {due_date(last[sid], s['lag_days'])}" for sid, s in series.items() if last[sid]) + ")"
            continue
        since = backfill_from or min((period_start(last[sid]) - timedelta(days=400) if last[sid] else date.fromisoformat(str(meta["backfill_from"])))
                                     for sid in series)                       # 13 months back: y/y bases and revisions stay checked
        rep.requested = True
        try:
            src = (sources or {}).get(prov) or PROVIDERS[prov](full["providers"][prov], series, now=clock)
        except Exception as e:
            rep.status, rep.note = "FAILED", f"init {type(e).__name__}: {e}"
            continue
        res = src.fetch(since=since, state={})
        if res is None:
            rep.status, rep.note = "FAILED", f"{src.last_status} {src.last_note}".strip()
            continue
        rep.status, rep.failed = "ok", dict(res.failed)
        fetched_at = clock()
        rows: list = []
        for sid, s in series.items():
            obs = [o for o in res.obs if o.series_id == sid]
            if not obs:
                continue
            got = {period_label(o.date, s["freq"]): o.value for o in obs}
            old = {p: r["value"] for p, r in (per.get(sid) or {}).items()}
            g = guard(old, got, min_changed=meta["guard"]["min_changed"], max_changed_share=meta["guard"]["max_changed_share"])
            if not g.accepted:
                rep.refused[sid] = g.reason
                refused_state[sid] = {"since": refused_state.get(sid, {}).get("since", today.isoformat()), "checked": today.isoformat(),
                                      "changed": len(g.changed), "overlap": g.overlap,
                                      "examples": [f"{p}: {a} -> {b}" for p, a, b in g.changed[:5]]}
                log.error("macro %s REFUSED: %s", sid, g.reason)
                continue
            refused_state.pop(sid, None)
            for p, a, b in g.changed:
                rep.revisions.append((sid, p, a, b))
                log.warning("macro %s revision %s: %s -> %s", sid, p, a, b)
            url = res.urls.get(sid) or _source_url(full["providers"][prov], s, since)
            newest = max(got)
            for p, v in got.items():
                prev = (per.get(sid) or {}).get(p)
                rel = res.released.get(sid) if p == newest else None
                rows.append({"series_id": sid, "currency": s["currency"], "period": p, "period_start": period_start(p),
                             "freq": s["freq"], "value": float(v), "unit": s["unit"], "published_as": s["published_as"],
                             "release_date": rel or (prev or {}).get("release_date"), "fetched_at": fetched_at, "source_url": url})
            rep.last[sid] = (newest, got[newest])
        rep.merge = cs.merge(MACRO, store, rows)
        if rep.refused:
            rep.status = "refused" if not rows else "ok (refused: " + ", ".join(rep.refused) + ")"
    cs.write(MACRO, paths.dir, store, set().union(*(r.merge.months for r in reports)) if reports else set())
    new_state = dict(state)
    if refused_state:
        new_state[REFUSED_KEY] = refused_state
    else:
        new_state.pop(REFUSED_KEY, None)
    save_state(paths, new_state)
    return reports


def macro_report(reports: list, today: date) -> tuple:
    from .cb_collect import _fmt_text, _md_table
    heads = ["provider", "status", "requests", "new", "upd", "same", "revisions", "refused", "note"]
    rows = [[r.provider, r.status, "yes" if r.requested else "no", r.merge.new, r.merge.updated, r.merge.unchanged,
             len(r.revisions), len(r.refused), (r.note + ("; failed: " + "; ".join(f"{k}: {v}" for k, v in r.failed.items()) if r.failed else ""))[:240]]
            for r in reports]
    extra = [f"  REVISION {sid} {p}: {a} -> {b}" for r in reports for sid, p, a, b in r.revisions]
    extra += [f"  REFUSED {sid}: {why}" for r in reports for sid, why in r.refused.items()]
    text = f"macro series {today}\n" + _fmt_text(heads, rows) + ("\n" + "\n".join(extra) if extra else "")
    md = f"### macro series {today}\n\n" + _md_table(heads, rows) + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return text, md


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------

def status_section(paths, today: date, cfg: dict | None = None) -> tuple:
    """(title, heads, rows, notes): per series the last period, value, release, staleness; the guard's refusals."""
    from .cb_collect import load_state
    from .cb_compute.macro_yoy import yoy
    mcfg = load_macro_config(cfg)
    meta = mcfg["meta"]
    per = by_series(load_macro(paths))
    rows, notes = [], []
    for sid, s in mcfg["series"].items():
        got = per.get(sid) or {}
        if not got:
            rows.append([s["currency"], sid, s["role"], "", "", "", "", "STALE (no data)"])
            notes.append(f"WARN macro {sid}: no data stored yet")
            continue
        p = max(got)
        r = got[p]
        shown = r["value"]
        if s["published_as"] == "index":
            y = yoy({k: v["value"] for k, v in got.items()}, p)
            shown = f"{r['value']} (y/y {y if y is not None else 'n/a'})"
        stale = is_stale(p, s["lag_days"], today, meta["stale_grace_days"])
        rows.append([s["currency"], sid, s["role"], p, shown, r["release_date"] or "", due_date(p, s["lag_days"]), "STALE" if stale else ""])
        if stale:
            notes.append(f"WARN macro {sid}: last period {p}, the next one was due by {due_date(p, s['lag_days'])} (+{meta['stale_grace_days']} d grace)")
    for sid, x in sorted(load_state(paths).get(REFUSED_KEY, {}).items()):
        notes.append(f"REFUSED macro {sid} since {x['since']} (checked {x['checked']}): {x['changed']} of {x['overlap']} stored periods changed; "
                     + "; ".join(x.get("examples", [])))
    return ("macro series (stance S0a)", ["cur", "series", "role", "last period", "value", "release", "next due", "stale"], rows, notes)
