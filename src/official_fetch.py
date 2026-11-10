"""Official-series fetcher (display-only layer) — guarded, append-only.

    python -m src.official_fetch               # fetch + verify + append if needed
    python -m src.official_fetch --dry-run     # same, never writes
    python -m src.official_fetch --dry-run --force
                                               # download + verify the LAST anchored
                                               # quarter (G1, G2) even if stored; no write

Per official series (src.official_series.SERIES_META):
  1. Anchors = the points of the anchor series /history itself computes (FF +
     manual overrides, via history_render.compute_history_series) with a real
     actual (not quarantined / recovered / telemetry). Reference quarter = the
     calendar quarter before the UTC release date.
  2. ZERO network requests unless an anchor's quarter is missing from the store
     (or --force).
  3. Not yet published (page 404 / no DocumentLink / quarter absent from CSV):
     nothing written, logged, retried on the next run.
  4. Guards before ANY write — any failure = refuse, nothing written, log the
     compared values:
       G1  official anchor-transform (QoQ) for each anchor being written ==
           the anchor's as-published actual (revised_from when present).
           On backfill that is every anchor (16 for NZD).
       G2  CSV index == stored index on every stored period of the last 5
           years (catches a rebasing or a revision).
  5. Never fails the job: every exception is caught, exit code 0.

Not imported by economic_compute / economic_render / ff_scoring.
"""
from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import official_series as osr
from .official_sources import REGISTRY, FetchResult

log = logging.getLogger("official_fetch")

G2_YEARS = 5


def load_anchors(meta: osr.OfficialSeriesMeta, as_of: pd.Timestamp) -> pd.DataFrame:
    """Anchor publications from the SAME series /history computes (not
    re-derived from the parquet): only the catalog slot holding the anchor is
    computed, through the same history_render/history_compute path."""
    from . import history_compute as hc
    from .history_render import compute_history_series

    catalog = hc.load_catalog()
    sub = {"categories": {}}
    for cat, ccys in catalog.get("categories", {}).items():
        entries = ccys.get(meta.currency, [])
        if any(e.get("indicator_key") == meta.anchor_indicator_key for e in entries):
            sub["categories"][cat] = {meta.currency: [e for e in entries if e.get("indicator_key")]}
            break
    computed = compute_history_series(as_of, catalog=sub)
    if computed is None:
        return hc.official_anchors(pd.DataFrame())
    _, _, cache = computed
    return hc.official_anchors(cache[(meta.currency, meta.anchor_indicator_key)])


def check_g1(index: dict, anchors: pd.DataFrame, quarters: set[str], kind: str) -> list[str]:
    """[] when every anchor in `quarters` matches; else one message per miss."""
    bad = []
    for a in anchors.itertuples(index=False):
        if a.period not in quarters:
            continue
        official = osr.transform(index, a.period, kind)
        published = osr.as_1dp(a.published)
        status = "ok" if official is not None and official == published else "MISMATCH"
        log.info("G1 %s %s: official %s=%s vs anchor as-published=%s -> %s",
                 a.period, pd.Timestamp(a.release_dt), kind, official, published, status)
        if status != "ok":
            bad.append(f"{a.period}: official {kind}={official} != anchor {published}")
    return bad


def check_g2(index: dict, stored: dict, latest_quarter: str) -> list[str]:
    """CSV index vs stored index over the last G2_YEARS years of stored periods."""
    floor = osr.quarter_shift(latest_quarter, -4 * G2_YEARS)
    window = sorted(p for p in stored if p > floor)
    bad = []
    for p in window:
        new = index.get(p)
        same = new is not None and Decimal(str(new)) == Decimal(str(stored[p]))
        if not same:
            bad.append(f"{p}: CSV={new} != store={stored[p]}")
    log.info("G2: %d stored period(s) in (%s, %s] compared, %d mismatch(es)%s",
             len(window), floor, latest_quarter, len(bad),
             "" if window else " — store empty for this window (initial backfill)")
    for b in bad:
        log.warning("G2 MISMATCH %s", b)
    return bad


def run_series(series_id: str, *, dry_run: bool, force: bool, as_of: pd.Timestamp,
               store_path: Path = osr.STORE_PATH,
               anchors: Optional[pd.DataFrame] = None,
               fetch: Optional[Callable[[str], FetchResult]] = None) -> dict:
    """One series. Returns a small summary dict (also what the tests assert on)."""
    meta = osr.SERIES_META[series_id]
    adapter = REGISTRY[series_id]
    fetch = fetch or adapter.fetch
    anchors = anchors if anchors is not None else load_anchors(meta, as_of)
    if anchors.empty:
        log.warning("%s: no anchors found — nothing to do.", series_id)
        return {"status": "no_anchors", "written": 0, "requests": False}

    store = osr.read_store(store_path)
    stored = osr.index_map(store, series_id)
    anchor_q = list(anchors["period"])
    # an anchor is covered once its display transform is computable
    missing = [q for q in anchor_q if osr.transform(stored, q, meta.display_transform) is None]
    log.info("%s: %d anchors (%s..%s), %d missing from store%s", series_id, len(anchor_q),
             anchor_q[0], anchor_q[-1], len(missing), f": {missing}" if missing else "")

    if not missing and not force:
        log.info("%s: store up to date — zero network requests.", series_id)
        return {"status": "up_to_date", "written": 0, "requests": False}

    target = missing[-1] if missing else anchor_q[-1]   # --force: re-verify the last anchored quarter
    res = fetch(target)
    log.info("%s: fetch %s -> %s (%s)", series_id, target, res.status, res.detail)
    if res.status == "not_yet":
        log.info("%s: %s not published yet — nothing written, retry next run.", series_id, target)
        return {"status": "not_yet", "written": 0, "requests": True, "target": target}
    if res.status != "ok":
        log.warning("%s: fetch error — nothing written: %s", series_id, res.detail)
        return {"status": "error", "written": 0, "requests": True, "target": target}

    g1_quarters = set(missing) if missing else {target}
    g1 = check_g1(res.index, anchors, g1_quarters, meta.anchor_transform)
    g2 = check_g2(res.index, stored, target)
    if g1 or g2:
        log.error("%s: REFUSED — guard failure, nothing written. G1=%s G2=%s", series_id, g1, g2)
        return {"status": "refused", "written": 0, "requests": True, "target": target,
                "g1": g1, "g2": g2}
    log.info("%s: G1 PASS (%d anchor(s)), G2 PASS", series_id, len(g1_quarters))

    if dry_run:
        log.info("%s: --dry-run — nothing written (%d new period(s) would be appended).",
                 series_id, sum(1 for p in res.index if p not in stored))
        return {"status": "dry_run_ok", "written": 0, "requests": True, "target": target}

    fetched_at = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    new = pd.DataFrame([{"series_id": series_id, "period": p, "value": v,
                         "value_kind": meta.value_kind, "source_url": res.source_url,
                         "fetched_at_utc": fetched_at} for p, v in sorted(res.index.items())])
    n = osr.append_rows(new, store_path)
    log.info("%s: appended %d new period(s) to %s.", series_id, n, store_path.name)
    return {"status": "written", "written": n, "requests": True, "target": target}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Official-series fetcher (display-only, append-only).")
    ap.add_argument("--dry-run", action="store_true", help="verify only, never write")
    ap.add_argument("--force", action="store_true", help="fetch + verify the last anchored quarter even if stored")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    as_of = pd.Timestamp.now()
    for series_id in osr.SERIES_META:
        try:
            out = run_series(series_id, dry_run=args.dry_run, force=args.force, as_of=as_of)
            log.info("%s: result %s", series_id, out)
        except Exception:   # never fail the refresh job
            log.exception("%s: unexpected error — nothing written.", series_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
