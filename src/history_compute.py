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
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .economic_compute import compute_indicator_score
from .ff_scoring import CCY2COUNTRY, SCORING_COLUMNS, build_matcher, detect_cadence, load_can_be_zero, to_scoring_frame
from .jb_actuals import build_flagged_bad_lookup
from .manual_actuals import apply_overrides, load_overrides

ROOT = Path(__file__).resolve().parents[1]
CATALOG_YAML = ROOT / "data" / "econ_catalog.yml"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
MANUAL_ACTUALS_OVERRIDES = ROOT / "data" / "manual_actuals_overrides.json"

REVISION_EPS_REL = 0.02
REVISION_EPS_ABS = 1e-9
WINDOW_DAYS = {"1y": 365, "2y": 730}   # "max" has no day cutoff
WINDOW_MIN_POINTS = 4


def load_catalog(path: Path = CATALOG_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_indicators_cfg(path: Path = INDICATORS_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def build_full_frame(ff: pd.DataFrame, matcher, cbz: set, flagged_bad: dict,
                     overrides: list[dict], as_of: pd.Timestamp) -> pd.DataFrame:
    """scoring frame -> overlay overrides, EXACTLY the economic_render.py:1111,
    1130-1138 sequence — to_scoring_frame and apply_overrides are called with
    the same arguments production uses, not re-derived."""
    scored = to_scoring_frame(ff, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    manual_rows, _ = apply_overrides(ff, overrides, now_utc=as_of, flagged_bad=flagged_bad)
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
    printed_positions = out.index[(~out["quarantined"]) & out["actual"].notna()].tolist()
    for pos in range(len(printed_positions) - 1):
        i, j = printed_positions[pos], printed_positions[pos + 1]
        a_n, prev_n1 = out.loc[i, "actual"], out.loc[j, "previous"]
        if pd.isna(a_n) or pd.isna(prev_n1):
            continue
        thresh = max(REVISION_EPS_ABS, REVISION_EPS_REL * abs(float(a_n)))
        if abs(float(prev_n1) - float(a_n)) > thresh:
            out.loc[j, "revised_from"] = float(a_n)
    return out


def compute_catalog(ff: pd.DataFrame, ind_cfg: dict, catalog: dict,
                    quarantine_df: pd.DataFrame, as_of: Optional[pd.Timestamp] = None,
                    overrides: Optional[list[dict]] = None) -> dict[tuple[str, str], pd.DataFrame]:
    """{(currency, indicator_key): history DataFrame}, one entry per DISTINCT
    series referenced anywhere in the catalog (a series referenced by both a
    `market` and a `policy` role — e.g. EUR cpi_yoy — is computed once)."""
    as_of = as_of or pd.Timestamp.now()
    matcher = build_matcher()
    cbz = load_can_be_zero(ind_cfg)
    flagged_bad = build_flagged_bad_lookup()
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
                  catalog_version: str, as_of: Optional[pd.Timestamp] = None,
                  generated_at: Optional[pd.Timestamp] = None) -> dict:
    as_of = as_of or pd.Timestamp.now()
    generated_at = generated_at or pd.Timestamp.now()

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
                if key in first_role_for_key:
                    # Same series already materialized under an earlier role in
                    # THIS (category, currency) — e.g. EUR/GBP/AUD's `policy`
                    # entry is the identical series as `market` (BAND_OK, FAZA
                    # 0.5 Bloc B). Reference it instead of duplicating every
                    # point a second time (P1.4) — the UI resolves `points_ref`
                    # against the sibling entry with that role in this same list.
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

    return {
        "meta": {"generated_at": generated_at.isoformat(), "catalog_version": catalog_version,
                "as_of": as_of.isoformat()},
        "categories": categories_out,
    }


def payload_size_bytes(payload: dict) -> tuple[int, int]:
    """`allow_nan=False`: a bare NaN/Infinity is not valid JSON (most parsers
    reject it) — fail loudly here rather than silently emit an invalid payload."""
    raw = json.dumps(payload, default=str, allow_nan=False).encode("utf-8")
    return len(raw), len(gzip.compress(raw))
