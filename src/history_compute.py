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
from .ff_scoring import CCY2COUNTRY, SCORING_COLUMNS, build_matcher, load_can_be_zero, to_scoring_frame
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
                                     "z", "bucket", "revised_from", "quarantined", "has_override"])

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
                        "z": None, "bucket": None, "quarantined": True,
                        "has_override": False})
            continue
        clean_upto = clean[clean["release_dt"] <= row["release_dt"]]
        res = _score_one_point(clean_upto, cfg, defaults, row["release_dt"], currency, indicator_key)
        rows.append({"release_dt": row["release_dt"], "actual": row["actual"],
                    "forecast": row["consensus"], "previous": row["previous"],
                    "z": res["z"] if res else None, "bucket": res["score"] if res else None,
                    "quarantined": False, "has_override": bool(row["has_override"])})

    out = pd.DataFrame(rows)
    out["revised_from"] = None
    clean_positions = out.index[~out["quarantined"]].tolist()
    for pos in range(len(clean_positions) - 1):
        i, j = clean_positions[pos], clean_positions[pos + 1]
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
            for entry in entries:
                key = entry.get("indicator_key")
                label_source = "derived" if entry.get("mismatch_note") else "canonical"
                if key is None:
                    series_list.append({
                        "role": entry["role"], "rank": entry["rank"],
                        "indicator_key": None, "target_note": entry.get("target_note"),
                    })
                    continue
                df = series_cache[(ccy, key)]
                window_options = {}
                for wname, wdays in {**WINDOW_DAYS, "max": None}.items():
                    sub = _window_points(df, as_of, wdays)
                    if len(sub) >= WINDOW_MIN_POINTS:
                        window_options[wname] = {
                            "n": len(sub),
                            "points": [
                                {"release_dt": r["release_dt"].isoformat(), "actual": r["actual"],
                                "forecast": r["forecast"], "previous": r["previous"],
                                "z": r["z"], "bucket": r["bucket"], "revised_from": r["revised_from"]}
                                for _, r in sub.iterrows()
                            ],
                        }
                series_list.append({
                    "role": entry["role"], "rank": entry["rank"],
                    "indicator_key": key, "display_label": entry.get("display_label"),
                    "unit": entry.get("unit"), "transform_real": entry.get("transform_real"),
                    "label_source": label_source,
                    "target_bands": entry.get("target_bands"),
                    "quarantine_count": int(df["quarantined"].sum()),
                    "window_options": window_options,
                })
            series_list.sort(key=lambda s: s["rank"])
            cat_out[ccy] = series_list
        categories_out[cat] = cat_out

    return {
        "meta": {"generated_at": generated_at.isoformat(), "catalog_version": catalog_version,
                "as_of": as_of.isoformat()},
        "categories": categories_out,
    }


def payload_size_bytes(payload: dict) -> tuple[int, int]:
    raw = json.dumps(payload, default=str).encode("utf-8")
    return len(raw), len(gzip.compress(raw))
