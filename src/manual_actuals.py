"""Manual Actuals Panel — Phase A: pure detection of parquet rows that need
human intervention (feat/manual-actuals-panel). Read-only: no persistence, no
rendering. Reuses the existing zero-quarantine decision code instead of
reimplementing it — see `_zero_passes_widening` (ff_scoring.zero_verdicts).

Two states, no others:
  MISSING      — datetime_utc < now - missing_after AND actual is NaN. The
                 event was scheduled, the time has passed, we have nothing.
  ZERO_CONFIRM — actual == 0.0 that does NOT pass the widening check
                 `ff_scoring.to_scoring_frame` applies at scoring time (i.e.
                 it would be quarantined to NaN there). Ambiguous: a genuine
                 flat print, or the JBlanked "unreleased" placeholder.

Distinct from `stale` (economic_render._freshness): stale means nothing new
has been released yet — informational. MISSING/ZERO_CONFIRM mean something
WAS released (or was due) and the pipeline cannot use it without a human.

Scope: only rows that `ff_scoring`'s matcher maps to a modeled indicator_key
are considered. Unmatched rows are dropped by `to_scoring_frame` before they
ever reach scoring (see its docstring) and never appear anywhere else on the
dashboard, so there is no "indicator" to show for them and no downstream
consumer waiting on a human to fix them — flagging them would just be FF
calendar noise. This mirrors `to_scoring_frame`'s own scope, not an
independent judgment call about which events matter.

`find_actionable_rows` and `apply_overrides` return the FULL, unwindowed
universe — see `apply_relevance_window` for the panel's display-only
RELEVANCE_WINDOW (default 45d) that keeps years of archive backlog off the
list without making those rows stop being actionable or overridable.

Duplicate suppression (feat/manual-actuals-dedupe, 2026-08): three real
patterns on the FF calendar produce multiple actionable rows for what is
provably the SAME underlying release, which the operator can never fully
resolve (confirming one duplicate leaves its siblings still ambiguous).
Applied inside `_suppress_duplicates`, in order, on the RAW per-row scan:
  (a) same calendar day, a sibling elsewhere in the FULL ff frame (not just
      the actionable subset) already carries a valid actual at scoring ->
      every actionable row in the group is redundant, drop them all. Scope
      is deliberately SAME-DAY ONLY, not the wider window used by (c) below
      — a same-day-plus-window version was measured against the full
      parquet and chains an entire indicator's multi-year release history
      into one false "duplicate" group for anything published more often
      than the window (e.g. gbp_gdp, 26 distinct releases merged). Same-day
      can never make that mistake: two real, distinct releases of one
      indicator never land on the same UTC calendar day.
  (b) same calendar day, no valid sibling (both/all ambiguous) -> keep the
      row with the LATEST datetime_utc, drop the rest. FF revises a listed
      time as it gets more certain, so the latest is the best-informed
      guess; this is the same "latest wins" convention already used by
      `economic_compute._keep_latest_published` for the equivalent MT5-side
      problem.
  (c) cross-calendar-day duplicates of ONE indicator, close enough in time
      that they cannot be two distinct real occurrences (e.g. an FF release
      time revised across a UTC midnight boundary). Scoped to canonical_id,
      clustering consecutive rows whose gap is <= dedup_gap_days[
      effective_frequency(...)] (config already used for the equivalent
      MT5-side flash/final collapse, see `economic_compute._dedup_flash_final`)
      — except `interest_rate_decision`, overridden to 7 days
      (RULE_C_GAP_DAYS_BY_INDICATOR): the monthly default of 18 days leaves
      too little margin below the real minimum gap measured between two
      DISTINCT central bank decisions across all 8 tracked banks (28 days,
      docs/manual-actuals-dedupe-interest-rate-window.md) to safely rule out
      an unscheduled/emergency decision landing near a regular one; 7 days
      still covers every confirmed same-event revision span found (<=2
      days) with room to spare. Content guard, this rule ONLY: even inside
      the gap window, two consecutive rows only cluster if their `forecast`
      is identical — a genuinely distinct event (scheduled or unscheduled)
      would carry its own consensus, not its neighbor's; a differing
      forecast means "don't merge" regardless of how close in time.
      Clustering is over the ALREADY-ambiguous subset only, same reasoning
      as (a)'s same-day restriction: including valid-actual rows in the
      window search reintroduces the gbp_gdp-style transitive-chain risk.
      Within a surviving cluster, same "latest wins" tie-break as (b).

Suppression is a DETECTION-layer concern only — it changes what
`find_actionable_rows` returns, never `clean_jblanked_actuals`,
`to_scoring_frame`, the parquet, scoring, or thresholds. A suppressed row
is not removed from any data; it is only removed from the ACTIONABLE
universe. Critically, `apply_overrides` reconciles written overrides
against the RAW (pre-suppression) universe, never the suppressed one — an
override written before this dedup logic existed, on a row that dedup would
now suppress, must keep resolving and keep contributing to scoring exactly
as before (see `apply_overrides`'s docstring for the mechanics).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
from typing import Optional

import pandas as pd

from .economic_compute import effective_frequency
from .economic_fetch import CompiledMatcher
from .econ_calendar_ff import ensure_provenance_columns
from .ff_scoring import (CCY2COUNTRY, SCORING_COLUMNS, build_matcher,
                         effective_consensus, load_can_be_zero, load_configs,
                         load_zero_possible, zero_verdicts)

MISSING = "MISSING"
ZERO_CONFIRM = "ZERO_CONFIRM"

DEFAULT_MISSING_AFTER = pd.Timedelta(hours=6)

# Rule (c): per-indicator override of the frequency-derived dedup_gap_days
# window (see module docstring for the interest_rate_decision measurement
# that motivated this). Empty for every other indicator -> falls through to
# defaults.dedup_gap_days[effective_frequency(...)] unchanged.
RULE_C_GAP_DAYS_BY_INDICATOR: dict[str, int] = {
    "interest_rate_decision": 7,
}

# Panel display window (2026-08 feedback): with both fix/cbz-flagged-bad-guard
# and fix/ingest-preserve-zeros in main, ZERO_CONFIRM went from 133 to 168
# rows on real data — almost all historical archive prints (2024-2025) nobody
# will ever manually confirm and that are not even the latest release of their
# series. This is a PANEL concern only — see `apply_relevance_window` below,
# never `find_actionable_rows` itself, whose full-universe output is still
# what `apply_overrides` reconciles eligibility against (an override for a
# 46-day-old row must keep working; the row isn't gone, just off the list).
RELEVANCE_WINDOW = pd.Timedelta(days=45)

RESULT_COLUMNS = [
    "canonical_id", "currency", "indicator_key", "name_raw", "name_canonical",
    "datetime_utc", "forecast", "previous", "actual", "state", "forecast_origin",
]

# One entry per human-supplied actual (feat/manual-actuals-panel, Phase B).
# Persisted verbatim in data/manual_actuals_overrides.json — the git-committed
# source of truth chosen for this panel's writes.
OVERRIDE_COLUMNS = [
    "canonical_id", "currency", "indicator_key", "datetime_utc", "actual",
    "state_resolved", "entered_by", "entered_at", "note",
]


def _zero_passes_widening(verdicts: dict, canonical_id: str, dt) -> bool:
    """A 0.0 actual is usable (no human needed) unless ff_scoring.zero_verdicts —
    THE zero rule, the same one to_scoring_frame applies (audit Z1-Z4) — calls
    it a placeholder it could not recover from the next print's `previous`.
    The `can_be_zero` and `flagged_bad` parameters still accepted by the public
    functions below are IGNORED (kept only so existing call sites keep working)."""
    v = verdicts.get((canonical_id, pd.Timestamp(dt)))
    return v is None or not v[0] or pd.notna(v[1])


def _raw_actionable_rows(ff: pd.DataFrame, *, now_utc: pd.Timestamp,
                         matcher: CompiledMatcher, verdicts: dict,
                         missing_after: pd.Timedelta) -> pd.DataFrame:
    """The per-row MISSING/ZERO_CONFIRM scan, UNSUPPRESSED — every actionable
    FF row, duplicates included. Split out from `find_actionable_rows` so
    `apply_overrides` can reconcile a written override against this full set
    (see that function's docstring for why the suppressed set is wrong for
    that purpose)."""
    if ff is None or ff.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    cutoff = pd.Timestamp(now_utc) - missing_after
    rows: list[dict] = []
    for r in ff.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        dt = pd.Timestamp(r.datetime_utc)
        if pd.isna(r.actual):
            if dt >= cutoff:
                continue
            state = MISSING
        elif r.actual == 0.0:
            release_date = dt.date()
            if _zero_passes_widening(verdicts, r.canonical_id, dt):
                continue
            state = ZERO_CONFIRM
        else:
            continue
        rows.append({
            "canonical_id": r.canonical_id, "currency": r.currency,
            "indicator_key": key, "name_raw": r.name_raw,
            "name_canonical": r.name_canonical, "datetime_utc": dt,
            "forecast": r.forecast, "previous": r.previous,
            "forecast_origin": getattr(r, "forecast_origin", None),
            "actual": r.actual, "state": state,
        })

    out = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    return out.sort_values("datetime_utc").reset_index(drop=True)


def _has_valid_sibling_same_day(ff: pd.DataFrame, matcher: CompiledMatcher,
                                verdicts: dict, canonical_id: str, cal_date,
                                exclude_dt: pd.Timestamp) -> bool:
    """Rule (a). Scans the FULL ff frame for the (canonical_id, cal_date)
    group — not just the actionable subset — since the sibling that makes a
    row redundant is, by definition, NOT itself actionable (it already has a
    valid actual). A same-day scope only: see module docstring for why this
    must not be widened to rule (c)'s window."""
    day = ff[(ff["canonical_id"] == canonical_id) &
            (ff["datetime_utc"].dt.date == cal_date) &
            (ff["datetime_utc"] != exclude_dt)]
    for r in day.itertuples(index=False):
        if pd.isna(r.actual):
            continue
        if r.actual == 0.0:
            key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
            if key is None or not _zero_passes_widening(verdicts, r.canonical_id, r.datetime_utc):
                continue   # sibling itself ambiguous -> not "valid"
        return True
    return False


def _drop_keys(df: pd.DataFrame, keys: set[tuple]) -> pd.DataFrame:
    if not keys:
        return df
    idx = pd.MultiIndex.from_arrays([df["canonical_id"], df["datetime_utc"]])
    return df[~idx.isin(keys)]


def _suppress_duplicates(raw: pd.DataFrame, ff: pd.DataFrame, *,
                         matcher: CompiledMatcher, verdicts: dict,
                         indicators_cfg: Optional[dict]) -> pd.DataFrame:
    """Applies rules (a)/(b)/(c) to the raw, unsuppressed actionable set — see
    the module docstring for the full design and the measurements behind
    each choice. Pure; does not mutate `raw` or `ff`."""
    if raw.empty:
        return raw

    if indicators_cfg is None:
        indicators_cfg, _ = load_configs()
    indicators = indicators_cfg.get("indicators", {}) or {}
    defaults = indicators_cfg.get("defaults", {}) or {}
    dedup_gap_days = defaults.get("dedup_gap_days", {}) or {}

    df = raw.copy()
    df["_cal_date"] = df["datetime_utc"].dt.date

    # --- rule (a): same-day, valid sibling elsewhere -> drop the whole group
    drop_a: set[tuple] = set()
    for (cid, cal_date), g in df.groupby(["canonical_id", "_cal_date"]):
        for r in g.itertuples(index=False):
            if _has_valid_sibling_same_day(ff, matcher, verdicts,
                                           cid, cal_date, r.datetime_utc):
                drop_a.add((cid, r.datetime_utc))
    df = _drop_keys(df, drop_a)

    # --- rule (b): same-day, no valid sibling -> keep the latest
    drop_b: set[tuple] = set()
    for (cid, cal_date), g in df.groupby(["canonical_id", "_cal_date"]):
        if len(g) > 1:
            kept_dt = g["datetime_utc"].max()
            for dt in g["datetime_utc"]:
                if dt != kept_dt:
                    drop_b.add((cid, dt))
    df = _drop_keys(df, drop_b)

    # --- rule (c): cross-day, ambiguous-subset-only clustering + content guard
    drop_c: set[tuple] = set()
    for cid, g in df.groupby("canonical_id"):
        g = g.sort_values("datetime_utc")
        if len(g) < 2:
            continue
        ind_key = g.iloc[0]["indicator_key"]
        currency = g.iloc[0]["currency"]
        if ind_key in RULE_C_GAP_DAYS_BY_INDICATOR:
            gap = RULE_C_GAP_DAYS_BY_INDICATOR[ind_key]
        else:
            freq = effective_frequency(indicators.get(ind_key, {}), defaults, currency)
            gap = dedup_gap_days.get(freq)
        if not gap:
            continue

        members = list(g.itertuples(index=False))
        clusters = [[members[0]]]
        for prev, cur in zip(members, members[1:]):
            gap_days = (cur.datetime_utc - prev.datetime_utc).total_seconds() / 86400
            same_forecast = (cur.forecast == prev.forecast) or \
                (pd.isna(cur.forecast) and pd.isna(prev.forecast))
            if gap_days <= gap and same_forecast:
                clusters[-1].append(cur)
            else:
                clusters.append([cur])
        for cluster in clusters:
            if len(cluster) > 1:
                kept_dt = max(m.datetime_utc for m in cluster)
                for m in cluster:
                    if m.datetime_utc != kept_dt:
                        drop_c.add((cid, m.datetime_utc))
    df = _drop_keys(df, drop_c)

    return df.drop(columns="_cal_date").reset_index(drop=True)


def find_actionable_rows(ff: pd.DataFrame, *, now_utc: pd.Timestamp,
                         matcher: Optional[CompiledMatcher] = None,
                         can_be_zero: Optional[set[str]] = None,
                         flagged_bad: Optional[dict] = None,
                         missing_after: pd.Timedelta = DEFAULT_MISSING_AFTER,
                         indicators_cfg: Optional[dict] = None,
                         zero_possible: Optional[dict] = None
                         ) -> pd.DataFrame:
    """Pure — no I/O, no mutation of `ff`. Returns one row per actionable FF
    calendar row STILL NEEDING A HUMAN after duplicate suppression (rules
    (a)/(b)/(c), see module docstring), oldest first, with columns
    RESULT_COLUMNS and `state` in {MISSING, ZERO_CONFIRM}.

    `matcher`/`can_be_zero` default to the live indicator config
    (`ff_scoring.build_matcher` / `load_can_be_zero`) when omitted;
    `indicators_cfg` defaults to `ff_scoring.load_configs()`'s indicators
    dict (needed for rule (c)'s `effective_frequency`/`dedup_gap_days`).
    `flagged_bad` / `can_be_zero` are accepted and IGNORED (audit 2.3): a
    zero is a placeholder iff its row's jb_status is "Data Not Loaded".
    """
    if ff is None or ff.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    matcher = matcher or build_matcher()
    cbz = load_can_be_zero() if can_be_zero is None else can_be_zero
    verdicts = zero_verdicts(ff, load_zero_possible() if zero_possible is None else zero_possible,
                             matcher)
    raw = _raw_actionable_rows(ff, now_utc=now_utc, matcher=matcher, verdicts=verdicts,
                               missing_after=missing_after)
    return _suppress_duplicates(raw, ff, matcher=matcher, verdicts=verdicts,
                                indicators_cfg=indicators_cfg)


def apply_relevance_window(rows: pd.DataFrame, *, now_utc: pd.Timestamp,
                           window: pd.Timedelta = RELEVANCE_WINDOW
                           ) -> tuple[pd.DataFrame, int]:
    """Pure — splits an already-computed actionable frame (RESULT_COLUMNS
    shape, e.g. `find_actionable_rows`'s or `apply_overrides`'s
    `remaining_actionable` output) into (recent, older_count) by
    `datetime_utc >= now_utc - window`.

    Display-layer filter ONLY: call this on the way to the panel, never
    before or inside `find_actionable_rows`/`apply_overrides` — those must
    keep operating on the full, unwindowed universe so an override for a row
    older than `window` still resolves correctly (the row didn't stop being
    actionable, it just stopped being LISTED). `older_count` is for a
    discreet "+N older" indicator — the rows themselves are dropped from the
    return value, not zeroed out or otherwise flagged in the data.
    """
    if rows.empty:
        return rows, 0
    cutoff = pd.Timestamp(now_utc) - window
    mask = rows["datetime_utc"] >= cutoff
    return rows[mask].reset_index(drop=True), int((~mask).sum())


def load_overrides(path: Path) -> list[dict]:
    """data/manual_actuals_overrides.json -> list of entries, oldest write order
    preserved. Missing/corrupt file -> [] (fail-open, matches load_state in
    jb_actuals.py — never blocks a render)."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — no file yet, or unreadable
        return []


def _key(canonical_id: str, datetime_utc) -> tuple[str, pd.Timestamp]:
    return (canonical_id, pd.Timestamp(datetime_utc))


def apply_overrides(ff: pd.DataFrame, overrides: list[dict], *, now_utc: pd.Timestamp,
                    matcher: Optional[CompiledMatcher] = None,
                    can_be_zero: Optional[set[str]] = None,
                    flagged_bad: Optional[dict] = None,
                    missing_after: pd.Timedelta = DEFAULT_MISSING_AFTER,
                    indicators_cfg: Optional[dict] = None,
                    zero_possible: Optional[dict] = None
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pure — no I/O. Reconciles `overrides` against the CURRENT actionable set
    (freshly computed here, not trusted from whenever an override was written).

    Matches overrides against the RAW (pre-duplicate-suppression) universe,
    deliberately NOT `find_actionable_rows`'s deduped output: an override
    written before feat/manual-actuals-dedupe existed, on a row that dedup
    now suppresses (its duplicate-group sibling was kept instead), must keep
    resolving and keep contributing to scoring exactly as before — dedup is a
    detection-layer/panel-listing concern, not a reason to orphan a real,
    already-recorded human answer. `remaining_actionable` (what the panel
    should still show) is computed from the SUPPRESSED set instead — a
    suppressed row must never reappear just because nobody wrote an override
    for it.

    Returns (manual_rows, remaining_actionable):
      manual_rows          — one row per override whose (canonical_id,
                              datetime_utc) is STILL actionable in the RAW
                              universe right now, shaped like ff_scoring.
                              SCORING_COLUMNS with source='manual' (consensus/
                              previous carried over from the matching FF row,
                              same as to_scoring_frame's forecast->consensus
                              rename). An override whose target a REAL actual
                              has since resolved is silently dropped —
                              re-deriving eligibility on every call, rather
                              than trusting a flag stored at submit time, IS
                              the 'never overwrite an existing non-null
                              actual' guarantee: once a row is no longer
                              actionable it is no longer eligible, full stop,
                              regardless of what the override says.
      remaining_actionable  — find_actionable_rows(ff, ...) (deduped) minus
                              every row that has a still-valid override —
                              what the panel should still show as needing a
                              human.

    Never routed through `ff_scoring.to_scoring_frame`: that function hardcodes
    source='ff' on every row it emits (see its docstring), which would
    silently relabel a manual entry — manual rows are built directly in
    SCORING_COLUMNS shape and are meant to be concatenated onto
    `to_scoring_frame`'s output by the caller, not passed through it.
    """
    matcher = matcher or build_matcher()
    cbz = load_can_be_zero() if can_be_zero is None else can_be_zero
    verdicts = zero_verdicts(ff, load_zero_possible() if zero_possible is None else zero_possible,
                             matcher)
    raw = _raw_actionable_rows(ff, now_utc=now_utc, matcher=matcher, verdicts=verdicts,
                               missing_after=missing_after)
    actionable = _suppress_duplicates(raw, ff, matcher=matcher, verdicts=verdicts,
                                      indicators_cfg=indicators_cfg)
    if not overrides:
        return pd.DataFrame(columns=SCORING_COLUMNS), actionable

    # An override keeps applying while its row's actual is still NaN or 0.0
    # (audit, phase 2): a human's reading of a zero is the final say, even when
    # the zero rule would now call that zero real (e.g. no JB payload and no
    # next print left on disk to show it was a placeholder). A real non-zero
    # actual landing still retires it — the same test api/manual-actual.py applies.
    by_key: dict = {}
    for r in ensure_provenance_columns(ff).itertuples(index=False):
        if pd.notna(r.actual) and r.actual != 0.0:
            continue
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        by_key[_key(r.canonical_id, r.datetime_utc)] = SimpleNamespace(
            currency=r.currency, indicator_key=key, datetime_utc=pd.Timestamp(r.datetime_utc),
            forecast=r.forecast, forecast_origin=r.forecast_origin, previous=r.previous)

    manual_rows: list[dict] = []
    resolved_keys: set[tuple] = set()
    for entry in overrides:
        key = _key(entry["canonical_id"], entry["datetime_utc"])
        row = by_key.get(key)
        if row is None:
            continue   # stale: superseded by a real actual, or never actionable
        manual_rows.append({
            "currency": row.currency, "indicator_key": row.indicator_key,
            "release_dt": row.datetime_utc, "actual": float(entry["actual"]),
            # audit 2.2: a manual row's consensus follows the same provenance
            # rule as every scored row — it no longer bypasses it.
            "consensus": effective_consensus(row.forecast, row.forecast_origin),
            "previous": row.previous, "source": "manual", "actual_origin": "manual",
        })
        resolved_keys.add(key)

    idx = pd.MultiIndex.from_arrays(
        [actionable["canonical_id"], actionable["datetime_utc"]])
    remaining = actionable[~idx.isin(resolved_keys)].reset_index(drop=True)
    manual_df = pd.DataFrame(manual_rows, columns=SCORING_COLUMNS)
    return manual_df, remaining
