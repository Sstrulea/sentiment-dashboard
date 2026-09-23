"""History-page compute layer (FAZA 1B) — read-only over the existing scoring
pipeline. Produces, per catalog series, one row per release with the SAME
z-score/bucket `economic_compute.compute_indicator_score` would produce (called
repeatedly, once per point in time — NOT re-implemented, see
`_score_one_point`'s docstring for why a single call cannot serve this).

Fixed order (per FAZA 1B): scoring frame -> overlay manual overrides -> mark
quarantine. Overrides always win over quarantine — a row with
`source == "manual"` is never quarantined, by construction (checked before any
quarantine-key lookup, not as a special case bolted on after).

`data_integrity.build_quarantine_proposal` is consulted here ONLY to decide
what to grey out / drop on the history page. Nothing in this module calls
`to_scoring_frame` or `compute_indicator_score` differently than production
does, and nothing here is imported by economic_render.py or any other
production caller — see tests/test_history_compute.py's non-regression test.
"""
from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .economic_compute import _max_age_for, compute_indicator_score, effective_frequency
from .ff_scoring import CCY2COUNTRY, SCORING_COLUMNS, build_matcher, detect_cadence, load_can_be_zero, to_scoring_frame
from .manual_actuals import apply_overrides, load_overrides

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CATALOG_YAML = ROOT / "data" / "econ_catalog.yml"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
MANUAL_ACTUALS_OVERRIDES = ROOT / "data" / "manual_actuals_overrides.json"

REVISION_EPS_REL = 0.02
REVISION_EPS_ABS = 1e-9
WINDOW_DAYS = {"1y": 365, "2y": 730}   # "max" has no day cutoff
WINDOW_MIN_POINTS = 4

# FAZA 2D 1.2 — history's OWN stale threshold, per (currency, indicator_key),
# derived from that series' empirical print-to-print gap. Deliberately NOT
# economic_compute._max_age_for (a fixed per-frequency-bucket table: weekly
# 14d/monthly 45d/quarterly 110d) — that function is production scoring
# (compute_indicator_score's allow_stale gate feeds /economic's aggregation
# directly) and must never change here, or /economic's byte-identical
# non-regression breaks. The bucket table is also just wrong for a bank
# whose real cadence runs longer than its assumed bucket: RBNZ's empirical
# median gap is 49 days, bucketed as "monthly" (45d) — meaning the series is
# structurally "stale" for several days after EVERY meeting, independent of
# any real problem (FAZA 2D 1.2 finding). 2x the series' own median gap
# gives a full cycle's slack past its typical release rhythm, uniformly, no
# NZD special-case.
EMPIRICAL_STALE_MULTIPLIER = 2


def _empirical_max_age_days(printed_dates: pd.Series) -> Optional[int]:
    """2x this series' own empirical median print-to-print gap (days).
    `None` when fewer than 2 real prints exist — not enough history to
    derive a real rhythm; the caller falls back to the production
    per-frequency-bucket value in that case (still just reading it as a
    fallback constant, not calling into economic_compute's stale/scoring
    decision itself)."""
    dates = pd.to_datetime(pd.Series(printed_dates)).sort_values()
    if len(dates) < 2:
        return None
    median_gap = dates.diff().dt.days.dropna().median()
    if pd.isna(median_gap) or median_gap <= 0:
        return None
    return int(round(EMPIRICAL_STALE_MULTIPLIER * median_gap))


def load_catalog(path: Path = CATALOG_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_indicators_cfg(path: Path = INDICATORS_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


_DUP_GAP_HOURS = 1.0   # FF-side ±1h DST-style artifact — see docs/known-debt-ff-dst-duplicate.md


def _drop_display_duplicate_rows(ff: pd.DataFrame) -> pd.DataFrame:
    """FAZA 1I 2 — DISPLAY-only collapse of a raw-feed artifact: the same
    canonical_id sometimes appears twice on the same UTC calendar date, <1h
    apart (a DST-style duplicate on the FF side, same family as the already-
    documented JBlanked one in jb_actuals.clean_jblanked_actuals — but on a
    different pipeline: merge_weekly's own dedup key is (canonical_id, EXACT
    datetime_utc), so it never merges two rows that differ by an hour; see
    docs/known-debt-ff-dst-duplicate.md for why that root cause is left
    alone). This is NOT that fix — it never touches data/economic_calendar_ff
    .parquet or ff_scoring/economic_compute; it only decides which rows THIS
    module's own per-row history/payload shows.

    Collapsed ONLY when every one of actual/forecast/previous matches
    EXACTLY (both NaN counts as a match) between the two rows — that is the
    single condition under which "the same real print, recorded twice" is
    actually true. The moment any field differs, this is evidence of a real
    disagreement between two deliveries of the same nominal event, not a
    harmless duplicate — both rows are kept, unmodified, and a warning is
    logged so a genuine conflict is never silently resolved by picking one.

    FAZA 2D 2 — one specific non-identical case is ALSO not a conflict: a
    pre-print placeholder (actual still null — the event was merely
    scheduled when this row was captured) sitting next to the real print of
    the SAME event (forecast/previous unchanged between the two). There is
    nothing ambiguous about which row to keep there, so it collapses too
    (logged at INFO, not WARNING) — only a genuine divergence (forecast or
    previous also differing, or two DIFFERING real actual values) still
    keeps both rows and warns.

    Returns a NEW DataFrame (copy) with the redundant later row of each
    confirmed-identical (or placeholder-superseded) pair removed. `ff`
    itself is never mutated — this function is called on a private copy
    inside build_full_frame, and the original parquet-backed frame
    production reads is untouched.
    """
    df = ff.copy()
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["cal_date"] = df["datetime_utc"].dt.date

    def _same(a, b) -> bool:
        if pd.isna(a) and pd.isna(b):
            return True
        return a == b

    drop_idx: list = []
    for _key, g in df.groupby(["canonical_id", "currency", "cal_date"], sort=False):
        if len(g) < 2:
            continue
        g = g.sort_values("datetime_utc")
        rows = list(g.itertuples())
        anchor = 0   # index of the last SURVIVING row — never a dropped one, so a
                     # 3+-row identical chain (e.g. a scheduled event re-delivered
                     # twice more before it ever prints) collapses to exactly 1,
                     # not "compare against whatever the loop variable happens to
                     # point at" (which could be a row already marked for drop).
        for j in range(1, len(rows)):
            r0, r1 = rows[anchor], rows[j]
            gap_h = (r1.datetime_utc - r0.datetime_utc).total_seconds() / 3600
            # FAZA 2D 2 — 0 <= gap_h (not 0 <): a same-instant pair (a
            # pre-print placeholder and its eventual real print, both
            # captured at the identical scheduled datetime_utc) used to slip
            # past this range check entirely — neither collapsed nor even
            # warned about, just silently rendered as two adjacent x-axis
            # slots for one event.
            if 0 <= gap_h <= _DUP_GAP_HOURS:
                identical = (_same(r0.actual, r1.actual) and _same(r0.forecast, r1.forecast)
                            and _same(r0.previous, r1.previous))
                if identical:
                    log.info("history display dedup: %s %s @ %s — dropping duplicate row at %s "
                            "(%.0fmin after %s, actual/forecast/previous all identical: "
                            "actual=%s forecast=%s previous=%s).",
                            r0.currency, r0.canonical_id, r0.cal_date, r1.datetime_utc,
                            gap_h * 60, r0.datetime_utc, r0.actual, r0.forecast, r0.previous)
                    drop_idx.append(r1.Index)
                    continue   # anchor unchanged — r1 is gone, keep comparing forward from r0

                # A pre-print placeholder (actual still null) next to the
                # real print of the SAME event (forecast/previous unchanged)
                # is not a conflict — there is nothing ambiguous about which
                # row to keep. Only a genuine divergence (forecast/previous
                # also differ, or two DIFFERING real actual values) still
                # falls through to the warning below.
                fp_match = _same(r0.forecast, r1.forecast) and _same(r0.previous, r1.previous)
                r0_is_placeholder = pd.isna(r0.actual) and not pd.isna(r1.actual)
                r1_is_placeholder = pd.isna(r1.actual) and not pd.isna(r0.actual)
                if fp_match and (r0_is_placeholder or r1_is_placeholder):
                    placeholder, real = (r0, r1) if r0_is_placeholder else (r1, r0)
                    log.info("history display dedup: %s %s @ %s — dropping pre-print "
                            "placeholder row at %s (actual not yet known), superseded by "
                            "the real print at %s (actual=%s forecast=%s previous=%s).",
                            r0.currency, r0.canonical_id, r0.cal_date, placeholder.datetime_utc,
                            real.datetime_utc, real.actual, real.forecast, real.previous)
                    drop_idx.append(placeholder.Index)
                    if placeholder.Index == r1.Index:
                        continue   # r1 (placeholder) dropped — r0 (real) stays anchor
                    # r0 (the anchor itself) was the placeholder — r1 (real)
                    # becomes the new anchor; fall through to `anchor = j`.
                else:
                    log.warning("history display dedup: %s %s @ %s has two prints %.0fmin apart "
                               "(%s vs %s) with DIFFERING values — actual=%s/%s forecast=%s/%s "
                               "previous=%s/%s — NOT a duplicate, keeping BOTH rows.",
                               r0.currency, r0.canonical_id, r0.cal_date, gap_h * 60,
                               r0.datetime_utc, r1.datetime_utc,
                               r0.actual, r1.actual, r0.forecast, r1.forecast,
                               r0.previous, r1.previous)
            anchor = j   # r1 is either out of gap range or a genuine conflict — it
                        # survives, and becomes the new anchor for subsequent rows

    if drop_idx:
        df = df.drop(index=drop_idx)
    return df.drop(columns="cal_date").reset_index(drop=True)


def build_full_frame(ff: pd.DataFrame, matcher, cbz: set, flagged_bad: dict,
                     overrides: list[dict], as_of: pd.Timestamp) -> pd.DataFrame:
    """scoring frame -> overlay overrides, EXACTLY the economic_render.py:1111,
    1130-1138 sequence — to_scoring_frame and apply_overrides are called with
    the same arguments production uses, not re-derived. The one deliberate
    deviation (FAZA 1I 2): a DEDUPED copy of `ff` (via
    _drop_display_duplicate_rows, a DISPLAY-only collapse of a raw-feed
    artifact) feeds to_scoring_frame — it operates on a private copy and
    never touches the parquet-backed frame production itself reads, so this
    has no effect on /economic or /strength (see
    test_non_regression_economic_and_strength_output_unchanged).

    `apply_overrides` still receives the ORIGINAL, undeduped `ff` —
    deliberately, matching that function's own existing contract (see its
    docstring: overrides match against the RAW, pre-duplicate-suppression
    universe on purpose, because a suppressed row's sibling might be the
    exact (canonical_id, datetime_utc) a human override was entered
    against). Feeding it the deduped frame instead silently orphaned a real
    override during testing (JPY BOJ 2026-07-31 03:11:00 — the display dedup
    collapsed that exact raw duplicate down to its 02:30 sibling, and the
    override, keyed to 03:11, could no longer find a match) — exactly the
    "silently hide a real conflict" failure this phase's own dedup logic is
    supposed to avoid, just one step removed. Never repeat that: overrides
    must always see the untouched `ff`.
    """
    deduped_ff = _drop_display_duplicate_rows(ff)
    scored = to_scoring_frame(deduped_ff, matcher, can_be_zero=cbz)
    manual_rows, _ = apply_overrides(ff, overrides, now_utc=as_of)

    # FAZA 2D 2 — a SEPARATE placeholder-vs-real duplicate from the raw-feed
    # one _drop_display_duplicate_rows handles: a manual override supplies
    # the real actual for a release the automatic feed never captured
    # (`scored`'s own row for that exact (currency, indicator_key,
    # release_dt) is still NaN — apply_overrides is matched against the RAW
    # ff, not `scored`, by design, so it never edits that row in place, see
    # the docstring above). Same non-conflict as the raw-feed case (nothing
    # ambiguous about which value is real), just discovered one step later
    # in the pipeline, after overrides are known — so it is resolved here,
    # not in _drop_display_duplicate_rows, which has already finished by
    # this point and never sees manual_rows at all.
    if len(manual_rows):
        manual_keys = set(zip(manual_rows["currency"], manual_rows["indicator_key"],
                              pd.to_datetime(manual_rows["release_dt"])))
        is_superseded_placeholder = scored.apply(
            lambda r: bool(pd.isna(r["actual"])
                          and (r["currency"], r["indicator_key"], r["release_dt"]) in manual_keys),
            axis=1)
        n_superseded = int(is_superseded_placeholder.sum())
        if n_superseded:
            log.info("history display dedup: dropping %d placeholder row(s) (actual still "
                    "unknown to the automatic feed) superseded by a manual override at the "
                    "same (currency, indicator_key, release_dt).", n_superseded)
            scored = scored[~is_superseded_placeholder]

    combined = pd.concat([scored, manual_rows], ignore_index=True) if len(manual_rows) else scored
    combined["release_dt"] = pd.to_datetime(combined["release_dt"])
    return combined[SCORING_COLUMNS]


def quarantine_key_set(quarantine_df: pd.DataFrame) -> set:
    if quarantine_df.empty:
        return set()
    return set(zip(quarantine_df["currency"], quarantine_df["name_raw"],
                  pd.to_datetime(quarantine_df["release_dt"])))


def _score_one_point(clean_upto: pd.DataFrame, cfg: dict, defaults: dict,
                     release_dt: pd.Timestamp, currency: str, indicator_key: str) -> dict | None:
    """One `compute_indicator_score` call, `as_of=release_dt`, on the frame
    TRUNCATED to that point in time. `compute_indicator_score` itself only
    ever scores "the latest release as of `as_of`" (that is its whole
    production contract — see its docstring) — it is not a per-row backtest
    function, so getting a z/bucket for EVERY historical row (needed for a
    history chart, not just today's scorecard) means calling it once per row
    with the input pre-sliced to that row's own history. This is data-slicing
    on the CALLER side, not a re-implementation of the scoring math itself —
    the actual z/sigma/bucket computation inside is untouched."""
    if clean_upto.empty:
        return None
    return compute_indicator_score(clean_upto, cfg, defaults, release_dt,
                                   allow_stale=True, currency=currency,
                                   indicator_key=indicator_key)


def compute_series_history(full_frame: pd.DataFrame, currency: str, indicator_key: str,
                           ind_cfg: dict, quarantine_keys: set) -> pd.DataFrame:
    """One row per release for (currency, indicator_key): release_dt, actual,
    forecast, previous, z, bucket, revised_from, quarantined, has_override.

    Quarantined rows are NEVER fed into any OTHER row's z/bucket computation
    (the trailing-K sigma window is built exclusively from the clean
    sequence) — but they ARE still returned here (z/bucket=None) so the
    caller can audit what was dropped and why; the actual display-time
    removal happens in `build_payload`, not here.
    """
    defaults = ind_cfg.get("defaults", {}) or {}
    cfg = (ind_cfg.get("indicators", {}) or {}).get(indicator_key, {})

    sub = full_frame[(full_frame["currency"] == currency)
                     & (full_frame["indicator_key"] == indicator_key)]
    sub = sub.sort_values("release_dt").reset_index(drop=True)
    if sub.empty:
        return pd.DataFrame(columns=["release_dt", "actual", "forecast", "previous",
                                     "z", "bucket", "score_status", "revised_from",
                                     "quarantined", "has_override"])

    def _is_quarantined(row) -> bool:
        if row["source"] == "manual":
            return False   # an override ALWAYS wins over quarantine, by construction
        return (currency, row.get("name_raw"), row["release_dt"]) in quarantine_keys

    sub["quarantined"] = sub.apply(_is_quarantined, axis=1)
    sub["has_override"] = sub["source"] == "manual"
    clean = sub[~sub["quarantined"]].reset_index(drop=True)

    rows = []
    for _, row in sub.iterrows():
        if row["quarantined"]:
            rows.append({"release_dt": row["release_dt"], "actual": row["actual"],
                        "forecast": row["consensus"], "previous": row["previous"],
                        "z": None, "bucket": None, "score_status": "quarantined",
                        "quarantined": True, "has_override": False})
            continue
        if pd.isna(row["actual"]):
            # No print at all yet (scheduled/nulled) — there is nothing to
            # score. `compute_indicator_score` would still return a result
            # here (the LATEST *prior* real print, since its own `fresh`
            # filter drops actual-null rows) — attaching THAT score to THIS
            # row would silently mislabel an earlier release's z/bucket as
            # this row's own (the bug: z=0.0/bucket=0.0 showing up on a
            # release with no actual, indistinguishable from a genuine
            # zero-surprise print). Never call it for a null-actual row.
            rows.append({"release_dt": row["release_dt"], "actual": row["actual"],
                        "forecast": row["consensus"], "previous": row["previous"],
                        "z": None, "bucket": None, "score_status": "no_actual",
                        "quarantined": False, "has_override": bool(row["has_override"])})
            continue

        clean_upto = clean[clean["release_dt"] <= row["release_dt"]]
        res = _score_one_point(clean_upto, cfg, defaults, row["release_dt"], currency, indicator_key)
        if res is None:
            z, bucket, status = None, None, "no_actual"
        elif res["flag"] in ("no_consensus", "direction_mismatch"):
            # A real print, but no trustworthy score: no forecast to compare
            # against, or the direction-override guard tripped. compute_
            # indicator_score forces score=0 for aggregation purposes (a
            # "can't tell" 0, not a "no surprise" 0) — do not carry that
            # forced 0 into a history chart as if it were a real bucket.
            z, bucket, status = None, None, "insufficient_history"
        elif res["z"] is None:
            # Fallback path (< fallback_min_prints pairs, or sigma in {0, NaN}):
            # z is genuinely undefined, but `score` is still a real pct-based
            # bucket (the same one /economic shows) — keep it.
            z, bucket, status = None, res["score"], "insufficient_history"
        else:
            z, bucket, status = res["z"], res["score"], "scored"
        rows.append({"release_dt": row["release_dt"], "actual": row["actual"],
                    "forecast": row["consensus"], "previous": row["previous"],
                    "z": z, "bucket": bucket, "score_status": status,
                    "quarantined": False, "has_override": bool(row["has_override"])})

    out = pd.DataFrame(rows)
    out["revised_from"] = None
    # A revision compares two REAL prints only — a row with no actual can be
    # neither the "before" nor the "after" side of a revision (P1.2: a null-
    # actual row previously could still inherit a `revised_from` value purely
    # because its own `previous` field happened to differ from an earlier
    # print's actual — nonsensical, since THIS row itself never printed).
    #
    # FAZA 1H — the revision belongs to the EARLIER row (N), not the later one
    # (N+1): row N+1's own `previous` field IS the current, as-of-today true
    # value of row N's reference period (whatever the market/BLS has since
    # settled on), so:
    #   original_actual[N] = actual[N]          (row N's OWN published print)
    #   revised_value[N]   = previous[N+1]      (row N+1's `previous`, if any)
    #   effective_value[N] = revised_value[N] if it differs, else original
    # A published print can never itself carry "revised from" (that would
    # mean row N+1's print already has a revision before row N+1 prints) —
    # that was the bug: row N's original actual was showing up as row N+1's
    # "revised from", implying N+1 was pre-revised.
    #
    # The bar renders `actual` (now overwritten to the effective/revised
    # value below — the CURRENT truth, matching how a reader looks back at
    # history) but z/bucket/score_status were already computed above from
    # the ORIGINAL, as-published actual — that's the real-time surprise that
    # moved the market and feeds /economic; it is never recomputed here.
    printed_positions = out.index[(~out["quarantined"]) & out["actual"].notna()].tolist()
    for pos in range(len(printed_positions) - 1):
        i, j = printed_positions[pos], printed_positions[pos + 1]
        a_n, prev_n1 = out.loc[i, "actual"], out.loc[j, "previous"]
        if pd.isna(a_n) or pd.isna(prev_n1):
            continue
        thresh = max(REVISION_EPS_ABS, REVISION_EPS_REL * abs(float(a_n)))
        if abs(float(prev_n1) - float(a_n)) > thresh:
            out.loc[i, "revised_from"] = float(a_n)
            out.loc[i, "actual"] = float(prev_n1)
    return out


def compute_catalog(ff: pd.DataFrame, ind_cfg: dict, catalog: dict,
                    quarantine_df: pd.DataFrame, as_of: Optional[pd.Timestamp] = None,
                    overrides: Optional[list[dict]] = None) -> dict[tuple[str, str], pd.DataFrame]:
    """{(currency, indicator_key): history DataFrame}, one entry per DISTINCT
    series referenced anywhere in the catalog (if two roles in the same
    (category, currency) ever shared an indicator_key, it would be computed
    once — no catalog entry does this today; the FAZA 2C 4 removal of the
    inflation-category `policy` duplicate-reference role eliminated the only
    case that used to)."""
    as_of = as_of or pd.Timestamp.now()
    matcher = build_matcher()
    cbz = load_can_be_zero(ind_cfg)
    flagged_bad = None   # audit 2.3: ignored; zero rule = jb_status
    overrides = overrides if overrides is not None else load_overrides(MANUAL_ACTUALS_OVERRIDES)

    full_frame = build_full_frame(ff, matcher, cbz, flagged_bad, overrides, as_of)
    qkeys = quarantine_key_set(quarantine_df)

    wanted: set[tuple[str, str]] = set()
    for ccys in catalog.get("categories", {}).values():
        for ccy, entries in ccys.items():
            for entry in entries:
                key = entry.get("indicator_key")
                if key:
                    wanted.add((ccy, key))

    return {(ccy, key): compute_series_history(full_frame, ccy, key, ind_cfg, qkeys)
           for ccy, key in wanted}


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def _json_num(v):
    """NaN/pd.NA -> None — plain float('nan') serializes as the bare `NaN`
    token in Python's json.dumps, which is not valid JSON (most parsers
    reject it); every numeric field in the payload must go through this."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _window_points(df: pd.DataFrame, as_of: pd.Timestamp, days: Optional[int]) -> pd.DataFrame:
    visible = df[~df["quarantined"] | df["has_override"]]
    if days is not None:
        visible = visible[visible["release_dt"] >= as_of - pd.Timedelta(days=days)]
    return visible


def build_payload(catalog: dict, series_cache: dict[tuple[str, str], pd.DataFrame],
                  catalog_version: str, ind_cfg: Optional[dict] = None,
                  as_of: Optional[pd.Timestamp] = None,
                  generated_at: Optional[pd.Timestamp] = None) -> dict:
    as_of = as_of or pd.Timestamp.now()
    generated_at = generated_at or pd.Timestamp.now()
    ind_cfg = ind_cfg or {}
    ind_defaults = ind_cfg.get("defaults", {}) or {}
    ind_indicators = ind_cfg.get("indicators", {}) or {}

    # FAZA 1G 3.1 — a catalog entry that never has any real print (a typo'd
    # indicator_key, a matcher regression, a currency the data source dropped)
    # or whose latest real print has fallen outside its recency window (the
    # exact `stale` concept /economic already has via compute_indicator_score,
    # here computed once per underlying series since history_compute doesn't
    # otherwise carry it) must never pass through unnoticed: logged loudly
    # here (visible in any cron run's output) AND collected into
    # payload["health"] so both the UI (a visible badge, static/history.js)
    # and scripts/verify_data.py (an automated check, not just a human
    # glancing at the page) can act on it without re-deriving anything.
    health_no_data: list[dict] = []
    health_stale: list[dict] = []
    seen_for_health: set[tuple[str, str]] = set()

    categories_out: dict = {}
    for cat, ccys in catalog.get("categories", {}).items():
        cat_out: dict = {}
        for ccy, entries in ccys.items():
            series_list = []
            first_role_for_key: dict[str, str] = {}   # indicator_key -> role that materialized it first
            for entry in entries:
                key = entry.get("indicator_key")
                label_source = "derived" if entry.get("mismatch_note") else "canonical"
                if key is None:
                    series_list.append({
                        "role": entry["role"], "rank": entry["rank"],
                        "indicator_key": None, "target_note": entry.get("target_note"),
                    })
                    continue

                base = {
                    "role": entry["role"], "rank": entry["rank"],
                    "indicator_key": key, "display_label": entry.get("display_label"),
                    "unit": entry.get("unit"), "transform_real": entry.get("transform_real"),
                    "label_source": label_source, "target": entry.get("target"),
                    "target_note": entry.get("target_note"),
                    # FAZA 2B 3.3 — a target BAND/LINE is only a valid overlay on
                    # a y/y series (same units as the target itself); on a m/m or
                    # q/q series it would be a unit error (a monthly-change chart
                    # with an annual-rate line drawn straight across it). Derived
                    # from transform_real so it can never drift out of sync with
                    # a catalog author adding/editing `target` by hand — the UI
                    # gates the on-chart annotation on this field, not on
                    # target's mere presence.
                    "comparable_with_target": entry.get("transform_real") == "YoY",
                    # Rendering hints (Part 3): rates is a policy LEVEL series —
                    # a bar chart implies discrete period-over-period surprise,
                    # which is misleading for "the rate that's been in effect
                    # since the last change"; the UI renders it as a step line
                    # instead. `cadence_empirical` lets the UI badge a
                    # quarterly/irregular series so its evenly-spaced x-axis
                    # positions are never mistaken for evenly-spaced TIME.
                    "chart_type": "step" if cat == "rates" else "bar",
                }
                printed_dates = series_cache[(ccy, key)]
                printed_dates = printed_dates[printed_dates["actual"].notna()]["release_dt"]
                base["cadence_empirical"] = (detect_cadence(printed_dates)
                                             if len(printed_dates) >= 2 else "unknown")

                # FAZA 1G 3.1 — stale / no-data, computed once per underlying
                # series (identical for every role sharing this indicator_key
                # via points_ref) but stamped onto every role's own payload
                # entry so the UI can badge whichever chip is on screen.
                has_data = len(printed_dates) > 0
                last_print_dt = printed_dates.max() if has_data else None
                age_days = int((as_of - last_print_dt).days) if last_print_dt is not None else None
                cfg_for_key = ind_indicators.get(key, {})
                freq = effective_frequency(cfg_for_key, ind_defaults, ccy)
                # FAZA 2D 1.2 — empirical (this series' own real cadence)
                # over the production per-frequency-bucket fallback, used
                # only when there isn't enough history yet to derive one.
                max_age = _empirical_max_age_days(printed_dates)
                if max_age is None:
                    max_age = _max_age_for(cfg_for_key, ind_defaults, freq)
                stale = bool(has_data and age_days is not None and age_days > max_age)
                base["has_data"] = has_data
                base["stale"] = stale
                base["last_print_release_dt"] = last_print_dt.isoformat() if last_print_dt is not None else None
                base["age_days"] = age_days

                if (ccy, key) not in seen_for_health:
                    seen_for_health.add((ccy, key))
                    label = entry.get("display_label") or key
                    if not has_data:
                        log.warning(
                            "history catalog: %s/%s (%s, role=%s) has ZERO real prints — "
                            "typo'd indicator_key, matcher regression, or a series that "
                            "should have been removed from data/econ_catalog.yml.",
                            ccy, key, label, entry["role"])
                        health_no_data.append({"currency": ccy, "indicator_key": key,
                                               "display_label": label, "category": cat})
                    elif stale:
                        log.warning(
                            "history catalog: %s/%s (%s) STALE — last real print %s, "
                            "%dd old, effective_frequency=%s (max_age=%dd).",
                            ccy, key, label, base["last_print_release_dt"], age_days, freq, max_age)
                        health_stale.append({"currency": ccy, "indicator_key": key,
                                             "display_label": label, "category": cat,
                                             "last_print_release_dt": base["last_print_release_dt"],
                                             "age_days": age_days, "max_age_days": max_age})

                if key in first_role_for_key:
                    # Same series already materialized under an earlier role in
                    # THIS (category, currency) — general dedup-by-reference
                    # (P1.4), kept for whatever future entry might need it.
                    # Its one historical user (EUR/GBP/AUD's inflation `policy`
                    # role duplicating `market`, BAND_OK, FAZA 0.5 Bloc B) was
                    # removed at FAZA 2C 4 — no catalog entry triggers this
                    # branch today, but the UI's points_ref resolution (a
                    # sibling lookup by role within this same list) still
                    # works if one ever does.
                    base["points_ref"] = {"role": first_role_for_key[key]}
                    series_list.append(base)
                    continue

                first_role_for_key[key] = entry["role"]
                df = series_cache[(ccy, key)]
                window_options = {}
                for wname, wdays in {**WINDOW_DAYS, "max": None}.items():
                    sub = _window_points(df, as_of, wdays)
                    if len(sub) >= WINDOW_MIN_POINTS:
                        window_options[wname] = {
                            "n": len(sub),
                            "points": [
                                {"release_dt": r["release_dt"].isoformat(),
                                "actual": _json_num(r["actual"]), "forecast": _json_num(r["forecast"]),
                                "previous": _json_num(r["previous"]), "z": _json_num(r["z"]),
                                "bucket": _json_num(r["bucket"]), "score_status": r["score_status"],
                                "revised_from": _json_num(r["revised_from"])}
                                for _, r in sub.iterrows()
                            ],
                        }
                base["quarantine_count"] = int(df["quarantined"].sum())
                base["window_options"] = window_options
                series_list.append(base)
            series_list.sort(key=lambda s: s["rank"])
            cat_out[ccy] = series_list
        categories_out[cat] = cat_out

    if health_no_data or health_stale:
        log.warning("history catalog health: %d no-data + %d stale series (see entries above).",
                   len(health_no_data), len(health_stale))

    return {
        "meta": {"generated_at": generated_at.isoformat(), "catalog_version": catalog_version,
                "as_of": as_of.isoformat()},
        "categories": categories_out,
        "health": {"no_data": health_no_data, "stale": health_stale},
    }


def payload_size_bytes(payload: dict) -> tuple[int, int]:
    """`allow_nan=False`: a bare NaN/Infinity is not valid JSON (most parsers
    reject it) — fail loudly here rather than silently emit an invalid payload."""
    raw = json.dumps(payload, default=str, allow_nan=False).encode("utf-8")
    return len(raw), len(gzip.compress(raw))
