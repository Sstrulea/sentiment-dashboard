"""PHASE 3 — production FF calendar refresh (fetch weekly → merge historical parquet).

Fetches the keyless FF weekly JSON each economic-cron tick and merges new/revised prints
into data/economic_calendar_ff.parquet (canonical schema), history-preserving. Carries the
same anti-degradation contract as the MT5 ingest: a failed/empty/thin/bad-schema payload
never degrades the parquet — the last-good file is kept and the run logs + quarantines.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .econ_calendar_ff import CANON_COLUMNS, parse_ff_weekly, unmapped_summary

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
QUARANTINE_PARQUET = ROOT / "data" / "ff_quarantine.parquet"
PIPELINE_YAML = ROOT / "config" / "pipeline.yaml"

# fix/calendar-freshness-measures-source: last-SUCCESSFUL-refresh state, same
# contract as jb_actuals.STATE_JSON (data/jb_last_pull.json) — only a
# successful merge (refresh()'s "ok" status) advances this file; every
# failure path (fetch_failed/empty/thin/degraded) leaves it untouched, so a
# stale state file is fail-visible rather than silently reset. Deliberately
# duplicated here rather than imported from jb_actuals — the calendar
# (schedule/forecast) and actuals (JBlanked daily pull) modules stay
# uncoupled; they happen to share a tiny load/save-JSON shape, not a
# dependency.
STATE_JSON = ROOT / "data" / "ff_last_refresh.json"


def load_state(path: Path = STATE_JSON) -> dict:
    """Read the last-successful-refresh state; missing/corrupt file → {}
    (fail-open — the caller decides what an absent state means)."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — no state yet, or unreadable
        return {}


def save_state(state: dict, path: Path = STATE_JSON) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1) + "\n")


def load_pipeline_config(path: Path = PIPELINE_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def calendar_source(cfg: Optional[dict] = None) -> str:
    """'ff' (default) or 'mt5' (rollback)."""
    cfg = cfg or load_pipeline_config()
    return str(cfg.get("calendar_source", "ff")).strip().lower()


def _sanity_ok(df: pd.DataFrame, min_ccy: int) -> bool:
    """A good weekly payload maps events for >= min_ccy distinct currencies."""
    return (not df.empty) and (df["currency"].nunique() >= min_ccy)


def merge_weekly(existing: Optional[pd.DataFrame], weekly: pd.DataFrame) -> pd.DataFrame:
    """History-preserving, FIELD-AWARE merge: append new prints, update revised ones.

    Dedup key = (canonical_id, datetime_utc), last-write-wins per row for the
    schedule fields (forecast/previous/names) — BUT an existing non-null `actual`
    is NEVER overwritten by a null/NaN re-delivery. The hybrid flow requires
    this: the daily JBlanked pull writes actuals in the evening; next-day hourly
    faireconomy ticks re-deliver the same events actual-less (the weekly feed is
    structurally actual-less), and whole-row keep="last" would null the actual
    right back out. A non-null incoming actual (revision) still wins."""
    if existing is None or existing.empty:
        combined = weekly.copy()
    else:
        existing = existing.copy()
        existing["datetime_utc"] = pd.to_datetime(existing["datetime_utc"])
        combined = pd.concat([existing, weekly], ignore_index=True)
    combined["datetime_utc"] = pd.to_datetime(combined["datetime_utc"])
    # Field-aware actual: within a key group (concat order = existing first,
    # incoming last) carry the last non-null actual forward, so the kept (last)
    # row inherits it unless the incoming row brings its own non-null actual.
    combined["actual"] = combined.groupby(["canonical_id", "datetime_utc"],
                                          sort=False)["actual"].ffill()
    combined = (combined.sort_values(["canonical_id", "datetime_utc"])
                .drop_duplicates(["canonical_id", "datetime_utc"], keep="last")
                .sort_values(["currency", "canonical_id", "datetime_utc"])
                .reset_index(drop=True))
    return combined[CANON_COLUMNS]


def refresh(*, now_utc: Optional[pd.Timestamp] = None, cfg: Optional[dict] = None,
            parquet_path: Path = FF_PARQUET, state_path: Optional[Path] = None) -> dict:
    """Fetch the weekly feed, merge, write. Returns a report dict. Never raises on a
    fetch/parse failure — anti-degradation keeps the last-good parquet.

    `state_path` (fix/calendar-freshness-measures-source) only ever advances
    on the "ok" return below — every early-return status (fetch_failed/empty/
    thin/degraded) leaves it exactly as it was, so freshness.calendar
    (economic_render._freshness) can tell "the source stalled" from "nothing
    new was published" instead of conflating the two. Defaults to `None` and
    resolves to the module-level STATE_JSON INSIDE the function body (not as
    the parameter's bound default) so a test monkeypatching `STATE_JSON`
    still redirects callers that never pass `state_path` at all — a bound
    default is captured once at import time and would silently ignore that
    patch, letting an unrelated test's `refresh()` call write to the real
    data/ff_last_refresh.json."""
    resolved_state_path = state_path if state_path is not None else STATE_JSON
    cfg = cfg or load_pipeline_config()
    url = cfg.get("ff_weekly_url", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    min_ccy = int(cfg.get("ff_min_currencies", 4))
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else None
    n_before = 0 if existing is None else len(existing)

    # Archive the raw weekly SCHEDULE payload (data/ff_raw/) — additive,
    # fully decoupled from the merge/quarantine path below (own independent
    # fetch, own try/except). A failure here never affects `refresh()`'s
    # return value or the anti-degradation contract.
    if cfg.get("archive_ff_weekly", True):
        try:
            from .ff_raw_archive import fetch_and_archive_weekly
            arch = fetch_and_archive_weekly(url, now_utc=now_utc)
            if arch["status"] == "saved":
                log.info("FF weekly raw archive: saved %s (rotated out %d).",
                         arch["path"], len(arch.get("rotated_out", [])))
        except Exception as e:  # noqa: BLE001 — advisory; never break the refresh
            log.warning("FF weekly raw archive skipped (%s).", str(e)[:80])

    # --- fetch + parse (graceful) ---
    try:
        weekly = parse_ff_weekly(url, now_utc=now_utc)
    except Exception as e:  # noqa: BLE001 — fetch/HTTP/schema failure
        log.warning("FF weekly fetch/parse FAILED (%s); keeping last-good parquet (%d rows).",
                    str(e)[:100], n_before)
        return {"status": "fetch_failed", "rows_before": n_before, "rows_after": n_before, "merged": 0}

    # --- anti-degradation: empty / thin payload → quarantine, keep last-good ---
    if weekly.empty:
        log.warning("FF weekly payload mapped 0 events; QUARANTINE — keeping last-good (%d rows).", n_before)
        return {"status": "empty", "rows_before": n_before, "rows_after": n_before, "merged": 0}
    if not _sanity_ok(weekly, min_ccy):
        log.warning("FF weekly covers only %d currency(ies) (< %d); QUARANTINE — keeping last-good.",
                    weekly["currency"].nunique(), min_ccy)
        return {"status": "thin", "rows_before": n_before, "rows_after": n_before, "merged": 0}

    # HOTFIX 2026-07-29 — Patch A2 sare rândurile care crapă la parsare, ca un
    # defect de o celulă să nu mai carantineze tot payload-ul (pana de 4 zile,
    # 24-28 iulie). Dar o schimbare de format în MASĂ trebuie să păstreze
    # comportamentul vechi: mai bine last-good decât un weekly ciuruit.
    from .econ_calendar_ff import ff_row_failures
    _fails = ff_row_failures()
    n_failed = sum(_fails.values())
    if n_failed and n_failed > len(weekly):
        log.warning("FF weekly: %d rând(uri) au eșuat la parsare vs %d mapate (%s); "
                    "QUARANTINE — păstrez last-good (%d rows).",
                    n_failed, len(weekly), list(_fails)[:5], n_before)
        return {"status": "degraded", "rows_before": n_before,
                "rows_after": n_before, "merged": 0}
    if n_failed:
        log.warning("FF ingest: %d rând(uri) sărite: %s", n_failed,
                    "; ".join(f"{k}×{v}" for k, v in list(_fails.items())[:5]))

    merged = merge_weekly(existing, weekly)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)

    # Cross-check + ingest-guard quarantines (retroactive/advisory, EACH fail-open
    # independently) → ONE shared data/ff_quarantine.parquet. See
    # docs/faza1-pmi-guard-wiring.md for why a naive `q.to_parquet(...)` per
    # check would erase the other's rows, and why the PMI guard is scoped to
    # NEW rows only (below), not a full-history rescan.
    _quarantine_updates: dict[str, pd.DataFrame] = {}   # reason -> this cycle's rows

    if cfg.get("run_fred_crosscheck", True):
        try:
            from .ff_fred_crosscheck import crosscheck_us
            q = crosscheck_us(merged, now_utc=now_utc)
            if len(q):
                q = q.assign(reason="fred_mismatch")
            _quarantine_updates["fred_mismatch"] = q
            if len(q):
                log.warning("FRED cross-check quarantined %d US print(s).", len(q))
        except Exception as e:  # noqa: BLE001 — advisory; never break the refresh
            log.warning("FRED cross-check skipped (%s).", str(e)[:80])

    # PMI/country-hour ingest guard (docs/proposal-pmi-ingest-guard.md,
    # src/pmi_ingest_guard.py) — catches a mislabeled-country release (e.g. a US
    # S&P Global PMI print tagged GBP/CAD) at ingest. Scoped to THIS CYCLE'S
    # newly-arrived rows only (Option 1, docs/faza1-pmi-guard-wiring.md): the
    # guard needs the full merged history to compute each series' trailing
    # dominant local hour, but a deviation is only actionable for a row that
    # JUST arrived — cross-country contamination is a property of the source
    # AT THE MOMENT OF DELIVERY; an old historical deviation could just as
    # easily be a genuine disruption (verified: the Oct-Nov 2025 US government
    # shutdown delayed BLS/BEA releases, producing the exact same "deviates
    # from trailing hour" signature on real, non-contaminated data — see the
    # doc's "USD GDP" and "employment_change can_be_zero" open threads). The
    # guard judges only what it CAN judge: hour deviation on arrival.
    # KNOWN GAP, unchanged: never covers the US S&P Global Final PMI cluster
    # itself (see src/pmi_ingest_guard.py's module docstring).
    if cfg.get("run_pmi_ingest_guard", True):
        try:
            from .pmi_ingest_guard import country_hour_guard
            from .ff_scoring import CCY2COUNTRY, build_matcher
            all_flags = country_hour_guard(merged)
            if len(all_flags):
                new_keys = set(zip(weekly["canonical_id"], weekly["datetime_utc"]))
                is_new = [
                    (cid, dt) in new_keys
                    for cid, dt in zip(all_flags["canonical_id"], all_flags["datetime_utc"])
                ]
                n_old = int(len(all_flags) - sum(is_new))
                flags = all_flags[is_new].copy()
                if n_old:
                    log.info("PMI ingest guard: %d historical deviation(s) not in this cycle's "
                            "payload — left untouched (Option 1 scope).", n_old)
            else:
                flags = all_flags
            if len(flags):
                lookup = (merged.drop_duplicates(subset=["currency", "canonical_id", "datetime_utc"])
                                .set_index(["currency", "canonical_id", "datetime_utc"])["name_canonical"])
                flags = flags.merge(lookup, left_on=["currency", "canonical_id", "datetime_utc"],
                                    right_index=True, how="left")
                matcher = build_matcher()
                flags["indicator_key"] = [
                    matcher.match(CCY2COUNTRY.get(ccy, ""), name)
                    for ccy, name in zip(flags["currency"], flags["name_canonical"])
                ]
                n_unmapped = int(flags["indicator_key"].isna().sum())
                flags = flags[flags["indicator_key"].notna()].drop(columns=["name_canonical"])
                if n_unmapped:
                    log.info("PMI ingest guard: %d flagged row(s) already unmapped to any "
                            "indicator_key — no scoring exclusion needed.", n_unmapped)
            _quarantine_updates["country_mismatch"] = flags
            if len(flags):
                log.warning("PMI ingest guard: quarantined %d NEW print(s) with anomalous "
                           "local release hour.", len(flags))
        except Exception as e:  # noqa: BLE001 — advisory; never break the refresh
            log.warning("PMI ingest guard skipped (%s).", str(e)[:80])

    if _quarantine_updates:
        if QUARANTINE_PARQUET.exists():
            existing_q = pd.read_parquet(QUARANTINE_PARQUET)
            if len(existing_q) and "reason" not in existing_q.columns:
                existing_q["reason"] = "fred_mismatch"   # legacy rows predate the shared-file schema
        else:
            existing_q = pd.DataFrame(columns=["reason"])
        if len(existing_q):
            kept = existing_q[~existing_q["reason"].isin(_quarantine_updates.keys())]
        else:
            kept = existing_q
        parts = [df for df in [kept, *_quarantine_updates.values()] if len(df)]
        combined = pd.concat(parts, ignore_index=True, sort=False) if parts else kept
        combined.to_parquet(QUARANTINE_PARQUET, index=False)

    # ingest report: aggregated unmapped summary (makes a future alias gap visible)
    try:
        um = unmapped_summary(url)
        top = "; ".join(f"{r.currency}/{r['count']}×{r.name_raw}" for _, r in um.head(8).iterrows())
    except Exception:  # noqa: BLE001
        top = "(unavailable)"
    log.info("FF refresh: weekly=%d rows, %d ccy; parquet %d -> %d rows. Top unmapped: %s",
             len(weekly), weekly["currency"].nunique(), n_before, len(merged), top or "none")

    now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.utcnow().tz_localize(None)
    save_state({"last_success_at": now.isoformat(), "last_success_utc_date": str(now.date()),
               "rows_before": n_before, "rows_after": len(merged)}, resolved_state_path)

    return {"status": "ok", "rows_before": n_before, "rows_after": len(merged),
            "merged": len(merged) - n_before, "weekly_rows": len(weekly)}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    rep = refresh()
    print(f"FF refresh: {rep['status']} — parquet {rep['rows_before']} -> {rep['rows_after']} rows")
    return 0 if rep["status"] == "ok" else 0  # never non-zero (graceful)


if __name__ == "__main__":
    import sys
    sys.exit(main())
