#!/usr/bin/env python3
"""FAZA 0.6 — manual overrides, phantom rows, catalog closure (read-only).

Imports helpers from audit_history_catalog.py (ph0) and audit_history_phase05.py
(ph05) — no resolution/discovery logic is re-derived. New in this script:
  Bloc A0 — does the audit trail read data/manual_actuals_overrides.json at all,
            and what changes once it's folded in (via the REAL production
            functions, ff_scoring.to_scoring_frame + manual_actuals.apply_overrides,
            not a re-implementation).
  Bloc A  — the zero-widening gate hypothesis (flagged_bad ~= "missed forecast",
            not "corrupted"), traced end-to-end through to_scoring_frame.
  Bloc B  — phantom wrong-year duplicate rows (contested finding from FAZA 0.5's
            C4), scanned across the WHOLE parquet, checked against raw layers.
  Bloc C  — 5 targeted gap closures.
  Bloc D  — label-mismatch inventory for every (currency, indicator_key) pair.
  Bloc E  — UI depth calibration on the corrected (phantom-excluded,
            post-override) series.

Zero writes under data/, zero src/ changes, zero network, zero merge.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_history_catalog as ph0  # noqa: E402
import audit_history_phase05 as ph05  # noqa: E402

from src.econ_calendar_ff import (  # noqa: E402
    canonical_id as mk_canonical_id, extract_period_suffix, flash_final_revisions,
    load_aliases, load_excluded_finals,
)
from src.economic_compute import effective_frequency  # noqa: E402
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.ff_scoring import (  # noqa: E402
    CCY2COUNTRY, SCORING_COLUMNS, build_matcher, detect_cadence, load_can_be_zero,
    to_scoring_frame,
)
from src.jb_actuals import ARCHIVE_JSON, RAW_DIR, build_flagged_bad_lookup  # noqa: E402
from src.manual_actuals import (  # noqa: E402
    apply_overrides, apply_relevance_window, find_actionable_rows, load_overrides,
)

MANUAL_ACTUALS_OVERRIDES = ROOT / "data" / "manual_actuals_overrides.json"
REPORTS_DIR = ROOT / "reports"
CADENCE_DAYS = ph05.CADENCE_DAYS
NOW = pd.Timestamp.now().normalize()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup() -> dict:
    ind_cfg = ph0.load_indicators_cfg()
    matcher = build_matcher()
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    cbz = load_can_be_zero(ind_cfg)
    flagged_bad = build_flagged_bad_lookup()
    ff = ph0.load_parquet()
    raw_uni = ph0.load_raw_universe()
    pools = {ccy: ph0.build_pool(ccy, ff, raw_uni) for ccy in ph0.CCYS}
    resolved = {}
    for cat, ccy, rol, desc in ph0.CANDIDATES:
        resolved[(cat, ccy, rol)] = ph0.resolve_one(cat, ccy, rol, desc, ff, raw_uni,
                                                     pools, matcher, indicators,
                                                     defaults, flagged_bad)
    overrides = load_overrides(MANUAL_ACTUALS_OVERRIDES)
    return {"ind_cfg": ind_cfg, "matcher": matcher, "defaults": defaults,
            "indicators": indicators, "cbz": cbz, "flagged_bad": flagged_bad,
            "ff": ff, "raw_uni": raw_uni, "pools": pools, "resolved": resolved,
            "overrides": overrides}


# ---------------------------------------------------------------------------
# BLOC A0 — the override layer
# ---------------------------------------------------------------------------

def a0_1_did_prior_audits_read_overrides() -> dict:
    """Functional-usage check (import/load_overrides/apply_overrides/the JSON
    path), NOT a bare substring match — audit_history_phase05.py mentions
    'src/manual_actuals.py' once, but only as a citation inside its A4 code-
    citation text (documenting flagged_bad's second consumer); it never
    imports the module or reads the override file."""
    functional_markers = ("import manual_actuals", "from src.manual_actuals",
                          "load_overrides(", "apply_overrides(",
                          "manual_actuals_overrides.json")
    hits = {}
    for fname in ("audit_history_catalog.py", "audit_history_phase05.py"):
        text = (ROOT / "scripts" / fname).read_text()
        hits[fname] = any(m in text for m in functional_markers)
    return {"read_overrides_file": any(hits.values()), "per_script": hits,
            "affected_metrics": ["zero_pct", "bad_pct (signal/all)", "revder_pct",
                                 "revrate_pct", "n/from/to/depth_months", "stale"]}


def a0_3_overrides_content(overrides: list[dict]) -> list[dict]:
    by_key = defaultdict(list)
    for e in overrides:
        by_key[(e["currency"], e["indicator_key"])].append(e)
    rows = []
    for (ccy, key), entries in sorted(by_key.items()):
        dates = sorted(e["datetime_utc"] for e in entries)
        states = Counter(e["state_resolved"] for e in entries)
        rows.append({"currency": ccy, "indicator_key": key, "n": len(entries),
                    "from": dates[0], "to": dates[-1], "states": dict(states),
                    "dates": dates})
    return rows


def a0_4_5_coverage_and_window(ctx: dict) -> dict:
    ff, matcher, cbz, flagged_bad = ctx["ff"], ctx["matcher"], ctx["cbz"], ctx["flagged_bad"]
    ind_cfg = ctx["ind_cfg"]
    actionable = find_actionable_rows(ff, now_utc=NOW, matcher=matcher, can_be_zero=cbz,
                                      flagged_bad=flagged_bad, indicators_cfg=ind_cfg)
    _manual_rows, remaining = apply_overrides(ff, ctx["overrides"], now_utc=NOW,
                                              matcher=matcher, can_be_zero=cbz,
                                              flagged_bad=flagged_bad, indicators_cfg=ind_cfg)
    recent, older_count = apply_relevance_window(actionable, now_utc=NOW)

    per_series = []
    for (ccy, key), g in actionable.groupby(["currency", "indicator_key"]):
        rem_g = remaining[(remaining["currency"] == ccy) & (remaining["indicator_key"] == key)]
        older_g = g[g["datetime_utc"] < NOW - pd.Timedelta(days=45)]
        covered = len(g) - len(rem_g)
        per_series.append({
            "currency": ccy, "indicator_key": key, "n_actionable": len(g),
            "n_covered": covered, "n_uncovered": len(rem_g),
            "coverage_pct": round(100 * covered / len(g), 1) if len(g) else None,
            "n_older_than_45d": len(older_g),
        })
    per_series.sort(key=lambda r: (r["coverage_pct"] if r["coverage_pct"] is not None else -1))

    return {
        "n_actionable_total": len(actionable), "n_covered_total": len(actionable) - len(remaining),
        "n_uncovered_total": len(remaining),
        "coverage_pct_total": round(100 * (len(actionable) - len(remaining)) / len(actionable), 1)
                              if len(actionable) else None,
        "n_older_than_45d_total": older_count,
        "per_series_by_coverage_asc": per_series,
    }


def _scored_subset_metrics(scored: pd.DataFrame, ccy: str, key: str, flagged_bad: dict) -> dict:
    """Metrics on a to_scoring_frame-shaped subset (release_dt/actual/consensus/
    previous) — same shape of metric as ph0.compute_metrics but adapted for the
    SCORING_COLUMNS schema (post zero-quarantine, possibly post-override)."""
    sub = scored[(scored["currency"] == ccy) & (scored["indicator_key"] == key)]
    printed = sub[sub["actual"].notna()].sort_values("release_dt").reset_index(drop=True)
    n = len(printed)
    if n == 0:
        return {"n": 0, "zero_pct": None, "revder_pct": None, "revrate_pct": None}
    zero_pct = round(100 * (printed["actual"] == 0.0).mean(), 1)
    n_pairs = n_der = n_rev = 0
    for i in range(n - 1):
        a_n, prev_n1 = printed.loc[i, "actual"], printed.loc[i + 1, "previous"]
        n_pairs += 1
        if pd.isna(a_n) or pd.isna(prev_n1):
            continue
        n_der += 1
        thresh = max(ph0.REV_EPS_ABS, ph0.REV_EPS_REL * abs(float(a_n)))
        if abs(float(prev_n1) - float(a_n)) > thresh:
            n_rev += 1
    return {"n": n, "zero_pct": zero_pct,
            "revder_pct": round(100 * n_der / n_pairs, 1) if n_pairs else None,
            "revrate_pct": round(100 * n_rev / n_der, 1) if n_der else None}


def a0_6_pre_post_override(ctx: dict) -> list[dict]:
    ff, matcher, cbz, flagged_bad = ctx["ff"], ctx["matcher"], ctx["cbz"], ctx["flagged_bad"]
    scored_pre = to_scoring_frame(ff, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    manual_rows, _ = apply_overrides(ff, ctx["overrides"], now_utc=NOW, matcher=matcher,
                                     can_be_zero=cbz, flagged_bad=flagged_bad,
                                     indicators_cfg=ctx["ind_cfg"])
    scored_post = pd.concat([scored_pre, manual_rows], ignore_index=True) if len(manual_rows) \
        else scored_pre

    rows = []
    for (cat, ccy, rol), r in ctx["resolved"].items():
        key = r.get("indicator_key")
        if not key:
            continue
        pre = _scored_subset_metrics(scored_pre, ccy, key, flagged_bad)
        post = _scored_subset_metrics(scored_post, ccy, key, flagged_bad)
        rows.append({"cat": cat, "ccy": ccy, "rol": rol, "key": key,
                    "n_pre": pre["n"], "n_post": post["n"],
                    "zero_pre": pre["zero_pct"], "zero_post": post["zero_pct"],
                    "revder_pre": pre["revder_pct"], "revder_post": post["revder_pct"],
                    "revrate_pre": pre["revrate_pct"], "revrate_post": post["revrate_pct"]})
    return rows


# ---------------------------------------------------------------------------
# BLOC A — the zero-widening gate hypothesis
# ---------------------------------------------------------------------------

def a1_zero_rows_census(ctx: dict) -> list[dict]:
    ff, matcher = ctx["ff"], ctx["matcher"]
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    zeros = df[(df["actual"] == 0.0) & df["indicator_key"].notna()]
    rows = []
    for (ccy, key, nr), g in zeros.groupby(["currency", "indicator_key", "name_raw"]):
        rows.append({"currency": ccy, "indicator_key": key, "name_raw": nr, "n": len(g),
                    "forecast_gt0": int((g["forecast"] > 0).sum()),
                    "forecast_eq0": int((g["forecast"] == 0).sum()),
                    "forecast_null": int(g["forecast"].isna().sum())})
    rows.sort(key=lambda r: -r["n"])
    return rows


def a2_contingency(ctx: dict) -> dict:
    ff, flagged_bad = ctx["ff"], ctx["flagged_bad"]
    df = ff.copy()

    def sig(r):
        d = pd.Timestamp(r["datetime_utc"]).date()
        return flagged_bad.get((r["currency"], r["name_raw"], d))

    zero_pos = df[(df["actual"] == 0.0) & (df["forecast"] > 0)]
    nonzero = df[df["actual"] != 0.0]
    z_sig = zero_pos.apply(sig, axis=1)
    n_sig = nonzero.apply(sig, axis=1)
    return {
        "n_zero_and_forecast_gt0": len(zero_pos),
        "P_flagged_given_zero_and_fcst_gt0": round(100 * (z_sig == True).sum() / max(1, (z_sig != None).sum()), 1),  # noqa: E711
        "n_with_signal_zero_group": int((z_sig != None).sum()),  # noqa: E711
        "n_nonzero": len(nonzero),
        "P_flagged_given_nonzero": round(100 * (n_sig == True).sum() / max(1, (n_sig != None).sum()), 1),  # noqa: E711
        "n_with_signal_nonzero_group": int((n_sig != None).sum()),  # noqa: E711
    }


def a3_trace(ctx: dict, ff_input: pd.DataFrame, flagged_bad_arg) -> list[dict]:
    """Per (currency, indicator_key): among raw actual==0.0 rows, how many
    survive to_scoring_frame with actual retained vs nulled, and by which route."""
    matcher, cbz = ctx["matcher"], ctx["cbz"]
    scored = to_scoring_frame(ff_input, matcher, can_be_zero=cbz, flagged_bad=flagged_bad_arg)
    scored_lookup = {(r.currency, r.indicator_key, pd.Timestamp(r.release_dt)): r.actual
                    for r in scored.itertuples(index=False)}

    df = ff_input.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    zeros = df[(df["actual"] == 0.0) & df["indicator_key"].notna()]

    out = defaultdict(lambda: {"n_in": 0, "n_kept": 0, "n_nulled": 0,
                               "reasons": Counter()})
    for r in zeros.itertuples(index=False):
        gkey = (r.currency, r.indicator_key)
        out[gkey]["n_in"] += 1
        out_key = (r.currency, r.indicator_key, pd.Timestamp(r.datetime_utc))
        kept = scored_lookup.get(out_key)
        kept_bool = pd.notna(kept) if kept is not None else False
        legit_cbz = r.indicator_key in cbz
        try:
            suffix_m = extract_period_suffix(r.name_raw) in ("m/m", "q/q")
        except ValueError:
            suffix_m = False
        d = pd.Timestamp(r.datetime_utc).date()
        fb = (ctx["flagged_bad"].get((r.currency, r.name_raw, d))
              if flagged_bad_arg is not None else None)
        if kept_bool:
            out[gkey]["n_kept"] += 1
            reason = "can_be_zero(config)" if legit_cbz else "suffix_widening(m/m|q/q)"
        else:
            out[gkey]["n_nulled"] += 1
            if legit_cbz and fb is True:
                reason = "flagged_bad_veto(cbz_route)"
            elif suffix_m and fb is not True and flagged_bad_arg is not None:
                reason = "suffix_ok_but_still_nulled(unexpected)"
            elif not legit_cbz and not suffix_m:
                reason = "no_route(not_cbz,no_suffix)"
            elif flagged_bad_arg is None:
                reason = "gate_disabled_defaults_to_old_contract"
            else:
                reason = "flagged_bad_veto(suffix_route)"
        out[gkey]["reasons"][reason] += 1

    rows = []
    for (ccy, key), v in out.items():
        rows.append({"currency": ccy, "indicator_key": key, **v, "reasons": dict(v["reasons"])})
    rows.sort(key=lambda r: -r["n_in"])
    return rows


def a4_zoom(ctx: dict) -> dict:
    ff, flagged_bad = ctx["ff"], ctx["flagged_bad"]
    matcher = ctx["matcher"]
    raw_events = ph05.load_raw_events()
    raw_idx = ph05.build_raw_key_index(raw_events)
    overrides_keys = {(e["currency"], pd.Timestamp(e["datetime_utc"])) for e in ctx["overrides"]}
    scored = to_scoring_frame(ff, matcher, can_be_zero=ctx["cbz"], flagged_bad=flagged_bad)
    scored_lookup = {(r.currency, r.indicator_key, pd.Timestamp(r.release_dt)): r.actual
                    for r in scored.itertuples(index=False)}

    targets = [("CHF", "cpi_yoy"), ("CAD", "gdp_qoq"), ("GBP", "gdp_qoq"),
              ("EUR", "gdp_qoq"), ("CHF", "gdp_qoq")]
    out = {}
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    for ccy, key in targets:
        sub = df[(df["currency"] == ccy) & (df["indicator_key"] == key) & (df["actual"] == 0.0)]
        rows = []
        for r in sub.itertuples(index=False):
            d = pd.Timestamp(r.datetime_utc).date()
            hits = raw_idx.get((ccy, r.name_raw, d), [])
            kept = scored_lookup.get((ccy, key, pd.Timestamp(r.datetime_utc)))
            has_override = (ccy, pd.Timestamp(r.datetime_utc)) in overrides_keys
            rows.append({"date": str(r.datetime_utc), "actual": r.actual,
                        "forecast": r.forecast, "previous": r.previous,
                        "name_raw": r.name_raw, "raw_quality_strength": hits,
                        "scoring_verdict": "kept" if pd.notna(kept) else "nulled",
                        "has_override": has_override})
        out[f"{ccy}/{key}"] = rows
    return out


def a5_counterfactual_no_gate(ctx: dict) -> list[dict]:
    ff, cbz = ctx["ff"], ctx["cbz"]
    matcher, flagged_bad = ctx["matcher"], ctx["flagged_bad"]
    scored = to_scoring_frame(ff, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    scored_lookup = {(r.currency, r.indicator_key, pd.Timestamp(r.release_dt)): r.actual
                    for r in scored.itertuples(index=False)}
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    zeros = df[(df["actual"] == 0.0) & df["indicator_key"].notna()]

    counts = defaultdict(lambda: {"n_zero": 0, "n_currently_nulled": 0, "n_would_recover": 0})
    for r in zeros.itertuples(index=False):
        gkey = (r.currency, r.indicator_key)
        legit_cbz = r.indicator_key in cbz
        try:
            suffix_m = extract_period_suffix(r.name_raw) in ("m/m", "q/q")
        except ValueError:
            suffix_m = False
        counterfactual_legit = legit_cbz or suffix_m
        cur = scored_lookup.get((r.currency, r.indicator_key, pd.Timestamp(r.datetime_utc)))
        currently_kept = pd.notna(cur) if cur is not None else False
        counts[gkey]["n_zero"] += 1
        if not currently_kept:
            counts[gkey]["n_currently_nulled"] += 1
            if counterfactual_legit:
                counts[gkey]["n_would_recover"] += 1
    rows = [{"currency": ccy, "indicator_key": key, **v} for (ccy, key), v in counts.items()]
    rows.sort(key=lambda r: -r["n_would_recover"])
    return rows


# ---------------------------------------------------------------------------
# BLOC B — phantom wrong-year duplicate rows
# ---------------------------------------------------------------------------

def _close(a, b, eps=1e-9) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) < eps


def b1_phantom_scan(ff: pd.DataFrame) -> dict:
    hits_a, hits_b = [], []
    for cid, g in ff.groupby("canonical_id"):
        g = g.sort_values("datetime_utc").reset_index(drop=True)
        n = len(g)
        for i in range(n):
            for j in range(i + 1, n):
                gap = (g.loc[j, "datetime_utc"] - g.loc[i, "datetime_utc"]).days
                if gap > 369:
                    break
                if abs(gap - 365) <= 3 or abs(gap - 366) <= 3:
                    ri, rj = g.loc[i], g.loc[j]
                    if (_close(ri["actual"], rj["actual"]) and pd.notna(ri["actual"])
                            and _close(ri["forecast"], rj["forecast"])
                            and _close(ri["previous"], rj["previous"])):
                        hits_a.append({
                            "canonical_id": cid, "currency": ri["currency"],
                            "gap_days": int(gap),
                            "row_i": {"datetime_utc": str(ri["datetime_utc"]),
                                      "name_raw": ri["name_raw"], "actual": ri["actual"],
                                      "forecast": ri["forecast"], "previous": ri["previous"]},
                            "row_j": {"datetime_utc": str(rj["datetime_utc"]),
                                      "name_raw": rj["name_raw"], "actual": rj["actual"],
                                      "forecast": rj["forecast"], "previous": rj["previous"]},
                        })
        dates = g["datetime_utc"]
        printed = g[g["actual"].notna()]
        if len(printed) >= 3:
            cadence = detect_cadence(printed["datetime_utc"])
            cad_days = CADENCE_DAYS.get(cadence)
            if cad_days:
                pg = printed.sort_values("datetime_utc").reset_index(drop=True)
                for i in range(len(pg) - 1):
                    gap_days = (pg.loc[i + 1, "datetime_utc"] - pg.loc[i, "datetime_utc"]).days
                    if 0 <= gap_days < 0.5 * cad_days:
                        hits_b.append({
                            "canonical_id": cid, "currency": pg.loc[i, "currency"],
                            "cadence_emp": cadence, "gap_days": gap_days,
                            "row_i": {"datetime_utc": str(pg.loc[i, "datetime_utc"]),
                                      "name_raw": pg.loc[i, "name_raw"], "actual": pg.loc[i, "actual"]},
                            "row_j": {"datetime_utc": str(pg.loc[i + 1, "datetime_utc"]),
                                      "name_raw": pg.loc[i + 1, "name_raw"], "actual": pg.loc[i + 1, "actual"]},
                        })
    return {"test_a_wrong_year_duplicates": hits_a, "test_b_sub_half_cadence": hits_b}


def b2_upstream_or_ours(hits_a: list[dict], raw_events: dict) -> list[dict]:
    def present_in_raw(ccy, name_raw, dt_str):
        target_date = pd.Timestamp(dt_str).strftime("%Y.%m.%d")
        for src_name in ("archive", "jb_raw"):
            for e in raw_events[src_name]:
                if (str(e.get("Currency", "")).strip() == ccy
                        and str(e.get("Name", "")).strip() == name_raw
                        and str(e.get("Date", "")).startswith(target_date)):
                    return True, src_name
        return False, None

    out = []
    for h in hits_a:
        ccy = h["currency"]
        in_i, src_i = present_in_raw(ccy, h["row_i"]["name_raw"], h["row_i"]["datetime_utc"])
        in_j, src_j = present_in_raw(ccy, h["row_j"]["name_raw"], h["row_j"]["datetime_utc"])
        verdict = ("upstream (both dates present in raw JBlanked feed)" if (in_i and in_j)
                  else "possibly introduced downstream (one date absent from raw)")
        out.append({**h, "row_i_in_raw": in_i, "row_i_raw_src": src_i,
                    "row_j_in_raw": in_j, "row_j_raw_src": src_j, "verdict": verdict})
    return out


def b3_impact(hits_a: list[dict], ff: pd.DataFrame) -> list[dict]:
    out = []
    seen = set()
    for h in hits_a:
        cid = h["canonical_id"]
        if cid in seen:
            continue
        seen.add(cid)
        g = ff[ff["canonical_id"] == cid].sort_values("datetime_utc")
        printed = g[g["actual"].notna()].reset_index(drop=True)
        last12_dates = set(printed["datetime_utc"].tail(12).astype(str))
        phantom_dates = {h2["row_i"]["datetime_utc"] for h2 in hits_a if h2["canonical_id"] == cid}
        phantom_dates |= {h2["row_j"]["datetime_utc"] for h2 in hits_a if h2["canonical_id"] == cid}
        in_window = phantom_dates & last12_dates
        out.append({"canonical_id": cid, "n_phantom_dates": len(phantom_dates),
                    "n_in_current_12print_window": len(in_window),
                    "phantom_dates_in_window": sorted(in_window)})
    return out


def b4_corrected_revisions(ctx: dict, phantom_datetimes: set) -> dict:
    ff, matcher, cbz, flagged_bad = ctx["ff"], ctx["matcher"], ctx["cbz"], ctx["flagged_bad"]
    ff_clean = ff[~ff["datetime_utc"].astype(str).isin(phantom_datetimes)].copy()
    scored = to_scoring_frame(ff_clean, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    manual_rows, _ = apply_overrides(ff_clean, ctx["overrides"], now_utc=NOW, matcher=matcher,
                                     can_be_zero=cbz, flagged_bad=flagged_bad,
                                     indicators_cfg=ctx["ind_cfg"])
    final = pd.concat([scored, manual_rows], ignore_index=True) if len(manual_rows) else scored

    out = {}
    for (cat, ccy, rol), r in ctx["resolved"].items():
        key = r.get("indicator_key")
        if not key:
            continue
        sub = final[(final["currency"] == ccy) & (final["indicator_key"] == key)]
        printed = sub[sub["actual"].notna()].sort_values("release_dt").reset_index(drop=True)
        n = len(printed)
        pairs = []
        n_pairs = n_der = n_rev = 0
        for i in range(n - 1):
            a_n, prev_n1 = printed.loc[i, "actual"], printed.loc[i + 1, "previous"]
            n_pairs += 1
            if pd.isna(a_n) or pd.isna(prev_n1):
                continue
            n_der += 1
            delta = float(prev_n1) - float(a_n)
            thresh = max(ph0.REV_EPS_ABS, ph0.REV_EPS_REL * abs(float(a_n)))
            if abs(delta) > thresh:
                n_rev += 1
                pairs.append({"release_dt_N": str(printed.loc[i, "release_dt"]),
                            "release_dt_N1": str(printed.loc[i + 1, "release_dt"]),
                            "actual_N": round(float(a_n), 4), "previous_N1": round(float(prev_n1), 4),
                            "delta_abs": round(delta, 4)})
        pairs.sort(key=lambda p: -abs(p["delta_abs"]))
        out[f"{cat}/{ccy}/{rol} ({key})"] = {
            "n": n, "revder_pct": round(100 * n_der / n_pairs, 1) if n_pairs else None,
            "revrate_pct": round(100 * n_rev / n_der, 1) if n_der else None,
            "top5": pairs[:5],
        }
    return out


# ---------------------------------------------------------------------------
# BLOC C — targeted gap closures
# ---------------------------------------------------------------------------

def c1_aud_retail_sales(ctx: dict) -> list[dict]:
    ff, matcher = ctx["ff"], ctx["matcher"]
    aud = ff[ff["currency"] == "AUD"].copy()
    aud["indicator_key"] = aud["name_canonical"].apply(
        lambda nc: matcher.match(CCY2COUNTRY["AUD"], nc))
    sub = aud[aud["indicator_key"] == "retail_sales"]
    rows = []
    for nr, g in sub.groupby("name_raw"):
        printed = g[g["actual"].notna()]
        rows.append({"name_raw": nr, "n": len(printed),
                    "from": printed["datetime_utc"].min().date().isoformat() if len(printed) else None,
                    "to": printed["datetime_utc"].max().date().isoformat() if len(printed) else None})
    rows.sort(key=lambda r: r["from"] or "")
    return rows


def c2_aud_trimmed_mean_chain(ctx: dict) -> dict:
    ff, matcher = ctx["ff"], ctx["matcher"]
    aud = ff[ff["currency"] == "AUD"].copy()
    aud["indicator_key"] = aud["name_canonical"].apply(
        lambda nc: matcher.match(CCY2COUNTRY["AUD"], nc))
    qoq = aud[aud["indicator_key"] == "core_cpi"]
    mm = aud[aud["indicator_key"] == "trimmed_mean_cpi_monthly"]
    qoq_p = qoq[qoq["actual"].notna()]
    mm_p = mm[mm["actual"].notna()]
    return {
        "qoq_dead_series": {"n": len(qoq_p),
                           "from": qoq_p["datetime_utc"].min().date().isoformat() if len(qoq_p) else None,
                           "to": qoq_p["datetime_utc"].max().date().isoformat() if len(qoq_p) else None,
                           "actual_min": round(float(qoq_p["actual"].min()), 3) if len(qoq_p) else None,
                           "actual_median": round(float(qoq_p["actual"].median()), 3) if len(qoq_p) else None,
                           "actual_max": round(float(qoq_p["actual"].max()), 3) if len(qoq_p) else None},
        "mm_live_series": {"n": len(mm_p),
                          "from": mm_p["datetime_utc"].min().date().isoformat() if len(mm_p) else None,
                          "to": mm_p["datetime_utc"].max().date().isoformat() if len(mm_p) else None,
                          "actual_min": round(float(mm_p["actual"].min()), 3) if len(mm_p) else None,
                          "actual_median": round(float(mm_p["actual"].median()), 3) if len(mm_p) else None,
                          "actual_max": round(float(mm_p["actual"].max()), 3) if len(mm_p) else None},
        "temporal_gap_days": (int((mm_p["datetime_utc"].min() - qoq_p["datetime_utc"].max()).days)
                              if len(qoq_p) and len(mm_p) else None),
    }


def c3_gbp_gdp_variants(ctx: dict) -> dict:
    names = ph05.all_names_for_currency("GBP", ctx["ff"], ctx["raw_uni"])
    gdp_names = sorted(n for n in names if "gdp" in n.lower())
    detail = [ph05._name_stats("GBP", n, ctx["ff"], ctx["raw_uni"]) for n in gdp_names]
    three_m = [d for d in detail if "3m/3m" in d["name"].lower()]
    return {"all_gdp_related_names": detail, "3m_3m_present": bool(three_m),
            "3m_3m_detail": three_m}


def c4_chf_rates_full(raw_events: dict, ctx: dict) -> list[dict]:
    overrides_keys = {(e["currency"], pd.Timestamp(e["datetime_utc"])) for e in ctx["overrides"]}
    rows = []
    for src_name, evs in raw_events.items():
        ccy_f, name_f, date_f = (("Currency", "Name", "Date") if src_name != "ff_raw"
                                 else ("country", "title", "date"))
        for e in evs:
            if str(e.get(ccy_f, "")).strip() != "CHF":
                continue
            nm = str(e.get(name_f, "")).strip()
            if not any(t in nm.lower() for t in ("rate", "policy", "snb")):
                continue
            d = str(e.get(date_f, ""))
            d_norm = d.replace("-", ".").replace("T", " ")[:10] if src_name == "ff_raw" else d[:10]
            if d_norm < "2025.01.01":
                continue
            dt = pd.Timestamp(d_norm.replace(".", "-")) if src_name != "ff_raw" else pd.Timestamp(d[:19])
            has_override = ("CHF", dt) in overrides_keys
            rows.append({"src": src_name, "name_raw": nm, "date": d_norm,
                        "actual": e.get("Actual", e.get("actual")),
                        "forecast": e.get("Forecast", e.get("forecast")),
                        "previous": e.get("Previous", e.get("previous")),
                        "has_override_near": has_override})
    rows.sort(key=lambda r: r["date"])
    return rows


def c5_eur_final_vs_flash(raw_events: dict) -> pd.DataFrame:
    aliases = load_aliases()
    excluded = load_excluded_finals()
    rev = flash_final_revisions(raw_events["archive"], aliases=aliases, excluded_finals=excluded)
    return rev[(rev["currency"] == "EUR") & (rev["canonical"] == "CPI y/y")]


# ---------------------------------------------------------------------------
# BLOC D — label-mismatch inventory, ALL (currency, indicator_key) pairs
# ---------------------------------------------------------------------------

def d_label_mismatch(ctx: dict) -> list[dict]:
    ff, matcher = ctx["ff"], ctx["matcher"]
    disc = ph0.discovery_by_indicator_key(ff, matcher)
    catalog_prim = {(r["ccy"], r["indicator_key"]) for r in ctx["resolved"].values()
                   if r.get("indicator_key") and r["rol"] == "PRIM"}
    catalog_sec = {(r["ccy"], r["indicator_key"]) for r in ctx["resolved"].values()
                  if r.get("indicator_key") and r["rol"] == "SEC"}

    rows = []
    for rec in disc.to_dict(orient="records"):
        ccy, key = rec["currency"], rec["indicator_key"]
        if not isinstance(key, str):
            continue
        sub = ff[(ff["currency"] == ccy)]
        canon_vals = sub[sub["name_raw"].isin(rec["name_raw_counts"].keys())]["name_canonical"].unique()
        canon_label = canon_vals[0] if len(canon_vals) else key
        try:
            label_suffix = extract_period_suffix(canon_label)
        except ValueError:
            label_suffix = "unknown"

        real_suffixes = Counter()
        for nr, cnt in rec["name_raw_counts"].items():
            try:
                s = extract_period_suffix(nr)
            except ValueError:
                s = "unknown"
            real_suffixes[s] += cnt
        dominant_real_suffix = real_suffixes.most_common(1)[0][0] if real_suffixes else "unknown"
        transform_mismatch = (label_suffix != "unknown" and dominant_real_suffix != "unknown"
                              and label_suffix != dominant_real_suffix)

        measure_mismatch = False
        dominant_name = max(rec["name_raw_counts"], key=rec["name_raw_counts"].get)
        if not any(t in canon_label.lower() for t in ph05.POLICY_EXCLUDE_TERMS) and \
                any(t in dominant_name.lower() for t in ph05.POLICY_EXCLUDE_TERMS):
            measure_mismatch = True

        # indicator_key's OWN name can embed a transform mnemonic (gdp_qoq,
        # cpi_yoy, ..._mm) chosen for whichever currency first got the key —
        # a DIFFERENT mismatch axis than canon_label vs real data: the label
        # can already be honest (GBP's canonical IS "GDP m/m") while the
        # shared key name still misleads for THIS currency (gdp_qoq -> q/q).
        key_suffix = None
        for token, suf in (("_yoy", "y/y"), ("_qoq", "q/q"), ("_mm", "m/m")):
            if key.endswith(token):
                key_suffix = suf
                break
        key_name_mismatch = (key_suffix is not None and dominant_real_suffix != "unknown"
                             and key_suffix != dominant_real_suffix)

        verdict = "MATCH"
        proposed = canon_label
        if transform_mismatch:
            verdict = "MISMATCH(transform)"
            base = canon_label
            for suf in ("y/y", "q/q", "m/m", "3m/y", "q/y", "w/w"):
                if base.lower().endswith(suf):
                    base = base[: -len(suf)].rstrip()
                    break
            proposed = f"{base} {dominant_real_suffix}" if dominant_real_suffix != "unknown" else base
        if measure_mismatch:
            verdict = "MISMATCH(measure)" if verdict == "MATCH" else verdict + "+measure"
            proposed = dominant_name
        if key_name_mismatch and not transform_mismatch:
            verdict = "MISMATCH(key_name)" if verdict == "MATCH" else verdict + "+key_name"

        is_prim = (ccy, key) in catalog_prim
        is_sec = (ccy, key) in catalog_sec
        impact = rec["n"] * (2 if is_prim else 1 if is_sec else 0.5)

        rows.append({
            "currency": ccy, "indicator_key": key, "n": rec["n"],
            "canonical_label": canon_label, "label_suffix": label_suffix,
            "name_raw_counts": rec["name_raw_counts"], "dominant_real_suffix": dominant_real_suffix,
            "verdict": verdict, "proposed_label": proposed,
            "is_catalog_prim": is_prim, "is_catalog_sec": is_sec, "impact_score": impact,
        })
    rows.sort(key=lambda r: (-r["impact_score"] if "MISMATCH" in r["verdict"] else 1, -r["impact_score"]))
    return rows


# ---------------------------------------------------------------------------
# BLOC E — UI depth calibration, corrected
# ---------------------------------------------------------------------------

def e_depth_calibration(ctx: dict, phantom_datetimes: set) -> list[dict]:
    ff, matcher, cbz, flagged_bad = ctx["ff"], ctx["matcher"], ctx["cbz"], ctx["flagged_bad"]
    ff_clean = ff[~ff["datetime_utc"].astype(str).isin(phantom_datetimes)].copy()
    scored = to_scoring_frame(ff_clean, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    manual_rows, _ = apply_overrides(ff_clean, ctx["overrides"], now_utc=NOW, matcher=matcher,
                                     can_be_zero=cbz, flagged_bad=flagged_bad,
                                     indicators_cfg=ctx["ind_cfg"])
    final = pd.concat([scored, manual_rows], ignore_index=True) if len(manual_rows) else scored

    rows = []
    for (cat, ccy, rol), r in ctx["resolved"].items():
        key = r.get("indicator_key")
        if not key:
            continue
        sub = final[(final["currency"] == ccy) & (final["indicator_key"] == key)]
        printed = sub[sub["actual"].notna()]
        dates = pd.to_datetime(printed["release_dt"])
        n = len(dates)
        if n == 0:
            rows.append({"cat": cat, "ccy": ccy, "rol": rol, "key": key, "n_total": 0})
            continue
        n12 = int((dates >= NOW - pd.Timedelta(days=365)).sum())
        n24 = int((dates >= NOW - pd.Timedelta(days=730)).sum())
        n36 = int((dates >= NOW - pd.Timedelta(days=1095)).sum())
        rows.append({"cat": cat, "ccy": ccy, "rol": rol, "key": key, "n_total": n,
                    "n12": n12, "n24": n24, "n36": n36,
                    "thin_12": n12 < 8, "thin_24": n24 < 8, "thin_36": n36 < 8})
    return rows


# ---------------------------------------------------------------------------
# Output + main
# ---------------------------------------------------------------------------

def main() -> int:
    ctx = setup()
    raw_events = ph05.load_raw_events()

    print("=" * 90)
    print("BLOC A0 — stratul de override")
    print("=" * 90)
    a01 = a0_1_did_prior_audits_read_overrides()
    print(f"A0.1 read_overrides_file (FAZA 0 + 0.5) = {a01['read_overrides_file']}")
    print(f"     per_script: {a01['per_script']}")
    print(f"     metrici afectate (pre-override): {a01['affected_metrics']}")

    a03 = a0_3_overrides_content(ctx["overrides"])
    print(f"\nA0.3 — {len(ctx['overrides'])} override-uri totale, per (currency, indicator_key):")
    for row in a03:
        print(f"  {row['currency']:<4} {row['indicator_key']:<28} n={row['n']} "
              f"{row['from']}..{row['to']}  states={row['states']}")

    a045 = a0_4_5_coverage_and_window(ctx)
    print(f"\nA0.4/A0.5 — universul actionable (MISSING+ZERO_CONFIRM): "
          f"{a045['n_actionable_total']} rânduri, "
          f"acoperire={a045['coverage_pct_total']}% ({a045['n_covered_total']} acoperite, "
          f"{a045['n_uncovered_total']} restante), "
          f"{a045['n_older_than_45d_total']} mai vechi de 45 zile (permanent needing history)")
    print("  per serie, acoperire crescătoare (restanța cea mai mare primele):")
    for row in a045["per_series_by_coverage_asc"][:20]:
        print(f"    {row['currency']:<4} {row['indicator_key']:<28} "
              f"n_actionable={row['n_actionable']:>3} covered={row['coverage_pct']!s:<6} "
              f"older45d={row['n_older_than_45d']}")

    a06 = a0_6_pre_post_override(ctx)
    print("\nA0.6 — recalculare pre vs post override (serii catalog cu indicator_key):")
    print(f"  {'CAT/CCY/ROL':<22}{'KEY':<25}{'N pre':>7}{'N post':>8}{'ZERO% pre':>11}"
          f"{'ZERO% post':>12}{'RVD% pre':>10}{'RVD% post':>11}{'RVR% pre':>10}{'RVR% post':>11}")
    for r in a06:
        label = f"{r['cat']}/{r['ccy']}/{r['rol']}"
        print(f"  {label:<22}{str(r['key'])[:24]:<25}{r['n_pre']:>7}{r['n_post']:>8}"
              f"{str(r['zero_pre']):>11}{str(r['zero_post']):>12}{str(r['revder_pre']):>10}"
              f"{str(r['revder_post']):>11}{str(r['revrate_pre']):>10}{str(r['revrate_post']):>11}")

    print()
    print("=" * 90)
    print("BLOC A — gate-ul de zero-widening")
    print("=" * 90)
    a1 = a1_zero_rows_census(ctx)
    print(f"A1 — {len(a1)} grupuri (currency, indicator_key, name_raw) cu actual==0.0:")
    for row in a1[:25]:
        print(f"  {row['currency']:<4} {row['indicator_key']:<28} {row['name_raw']:<24} "
              f"n={row['n']:>3} fcst>0={row['forecast_gt0']:>3} fcst==0={row['forecast_eq0']:>3} "
              f"fcst_null={row['forecast_null']:>3}")
    if len(a1) > 25:
        print(f"  ... ({len(a1) - 25} mai multe în JSON)")

    a2 = a2_contingency(ctx)
    print(f"\nA2 — contingență:")
    print(f"  P(flagged | actual==0 & forecast>0) = {a2['P_flagged_given_zero_and_fcst_gt0']}%  "
          f"(n cu semnal={a2['n_with_signal_zero_group']} / {a2['n_zero_and_forecast_gt0']})")
    print(f"  P(flagged | actual!=0)              = {a2['P_flagged_given_nonzero']}%  "
          f"(n cu semnal={a2['n_with_signal_nonzero_group']} / {a2['n_nonzero']})")

    a3_pre = a3_trace(ctx, ctx["ff"], ctx["flagged_bad"])
    print(f"\nA3 — trasare end-to-end (PRE-override), per (currency, indicator_key):")
    for row in a3_pre[:20]:
        print(f"  {row['currency']:<4} {row['indicator_key']:<28} in={row['n_in']:>3} "
              f"kept={row['n_kept']:>3} nulled={row['n_nulled']:>3}  reasons={row['reasons']}")

    a4 = a4_zoom(ctx)
    print(f"\nA4 — zoom CHF cpi_yoy + serii m/m|q/q cu zero%>10%:")
    for label, rows in a4.items():
        print(f"  --- {label} ({len(rows)} rânduri zero) ---")
        for row in rows:
            print(f"    {row['date']}  A={row['actual']} F={row['forecast']} P={row['previous']}  "
                  f"{row['name_raw']!r}  raw={row['raw_quality_strength']}  "
                  f"verdict={row['scoring_verdict']}  override={row['has_override']}")

    a5 = a5_counterfactual_no_gate(ctx)
    print(f"\nA5 — contrafactual (fără gate flagged_bad, cu can_be_zero+suffix păstrate):")
    for row in a5[:20]:
        print(f"  {row['currency']:<4} {row['indicator_key']:<28} n_zero={row['n_zero']:>3} "
              f"currently_nulled={row['n_currently_nulled']:>3} "
              f"would_recover={row['n_would_recover']:>3}")

    print()
    print("=" * 90)
    print("BLOC B — rânduri fantomă cu an gresit")
    print("=" * 90)
    b1 = b1_phantom_scan(ctx["ff"])
    print(f"B1(a) — {len(b1['test_a_wrong_year_duplicates'])} perechi triple-identice la ~365 zile:")
    for h in b1["test_a_wrong_year_duplicates"]:
        print(f"  {h['canonical_id']:<28} gap={h['gap_days']}d")
        print(f"    i: {h['row_i']}")
        print(f"    j: {h['row_j']}")
    print(f"\nB1(b) — {len(b1['test_b_sub_half_cadence'])} perechi sub jumătate din cadența empirică:")
    for h in b1["test_b_sub_half_cadence"][:15]:
        print(f"  {h['canonical_id']:<28} cadence={h['cadence_emp']} gap={h['gap_days']}d")
        print(f"    i: {h['row_i']}  j: {h['row_j']}")

    b2 = b2_upstream_or_ours(b1["test_a_wrong_year_duplicates"], raw_events)
    print(f"\nB2 — origine (upstream JBlanked vs introdus de noi):")
    for h in b2:
        print(f"  {h['canonical_id']}: {h['verdict']}  "
              f"(row_i in raw={h['row_i_in_raw']}/{h['row_i_raw_src']}, "
              f"row_j in raw={h['row_j_in_raw']}/{h['row_j_raw_src']})")

    b3 = b3_impact(b1["test_a_wrong_year_duplicates"], ctx["ff"])
    print(f"\nB3 — impact pe fereastra curentă de 12 printuri:")
    for row in b3:
        print(f"  {row['canonical_id']}: {row['n_phantom_dates']} date fantomă, "
              f"{row['n_in_current_12print_window']} în fereastra curentă de 12 "
              f"{row['phantom_dates_in_window']}")

    phantom_datetimes = set()
    for h in b1["test_a_wrong_year_duplicates"]:
        phantom_datetimes.add(h["row_i"]["datetime_utc"])
    b4 = b4_corrected_revisions(ctx, phantom_datetimes)
    print(f"\nB4 — revizii CORECTE (ungated, fantome excluse, post-override):")
    for label, d in b4.items():
        print(f"  {label}: n={d['n']} REVDER%={d['revder_pct']} REVRATE%={d['revrate_pct']}")
        for p in d["top5"]:
            print(f"      N={p['release_dt_N']} actual={p['actual_N']} -> "
                  f"previous[N+1]({p['release_dt_N1']})={p['previous_N1']}  delta={p['delta_abs']}")

    print()
    print("=" * 90)
    print("BLOC C — inchidere goluri catalog")
    print("=" * 90)
    c1 = c1_aud_retail_sales(ctx)
    print("C1 — AUD retail_sales, name_raw distincte:")
    for row in c1:
        print(f"  {row['name_raw']!r}: n={row['n']}  {row['from']}..{row['to']}")

    c2 = c2_aud_trimmed_mean_chain(ctx)
    print(f"\nC2 — AUD trimmed mean (q/q mort vs m/m viu):")
    print(f"  q/q: {c2['qoq_dead_series']}")
    print(f"  m/m: {c2['mm_live_series']}")
    print(f"  gol temporal intre ele: {c2['temporal_gap_days']} zile")

    c3 = c3_gbp_gdp_variants(ctx)
    print(f"\nC3 — GBP, toate numele legate de GDP:")
    for d in c3["all_gdp_related_names"]:
        print(f"  [{d['layer']:<24}] suffix={d['suffix']:<6} n={d['n']:>3} "
              f"{d['from']}..{d['to']}  {d['name']!r}")
    print(f"  'GDP 3m/3m' prezent = {c3['3m_3m_present']}  detaliu={c3['3m_3m_detail']}")

    c4 = c4_chf_rates_full(raw_events, ctx)
    print(f"\nC4 — CHF, toate evenimentele rate/policy/SNB dupa 2025-01-01 ({len(c4)} rânduri):")
    for row in c4:
        print(f"  [{row['src']:<8}] {row['date']}  A={row['actual']} F={row['forecast']} "
              f"P={row['previous']}  {row['name_raw']!r}  override_aproape={row['has_override_near']}")

    c5 = c5_eur_final_vs_flash(raw_events)
    print(f"\nC5 — EUR 'Final CPI y/y' vs 'CPI Flash Estimate y/y' ({len(c5)} perechi):")
    print(c5.to_string(index=False) if len(c5) else "  (nicio pereche gasita)")

    print()
    print("=" * 90)
    print("BLOC D — inventar label mismatch (TOATE perechile currency x indicator_key)")
    print("=" * 90)
    d = d_label_mismatch(ctx)
    mism = [r for r in d if "MISMATCH" in r["verdict"]]
    print(f"{len(mism)} MISMATCH din {len(d)} perechi (currency, indicator_key), ordonate dupa impact:")
    for r in mism:
        print(f"  {r['currency']:<4} {r['indicator_key']:<28} n={r['n']:>3} "
              f"PRIM={r['is_catalog_prim']!s:<5} SEC={r['is_catalog_sec']!s:<5} "
              f"impact={r['impact_score']:>5.1f}  verdict={r['verdict']:<20}")
        print(f"      label azi: {r['canonical_label']!r} (suffix={r['label_suffix']})  "
              f"real: {r['name_raw_counts']} (dominant suffix={r['dominant_real_suffix']})")
        print(f"      propus: {r['proposed_label']!r}")

    print()
    print("=" * 90)
    print("BLOC E — calibrare UI (post-override, fantome excluse)")
    print("=" * 90)
    e = e_depth_calibration(ctx, phantom_datetimes)
    print(f"{'CAT/CCY/ROL':<22}{'KEY':<26}{'N':>5}{'N12':>6}{'N24':>6}{'N36':>6}  THIN(12/24/36)")
    for r in e:
        label = f"{r['cat']}/{r['ccy']}/{r['rol']}"
        if r.get("n_total", 0) == 0:
            print(f"{label:<22}{str(r['key'])[:25]:<26} -- no printed rows --")
            continue
        thin = f"{r['thin_12']}/{r['thin_24']}/{r['thin_36']}"
        print(f"{label:<22}{str(r['key'])[:25]:<26}{r['n_total']:>5}{r['n12']:>6}{r['n24']:>6}"
              f"{r['n36']:>6}  {thin}")

    REPORTS_DIR.mkdir(exist_ok=True)
    payload = {
        "block_a0": {"a0_1": a01, "a0_3": a03, "a0_4_5": a045, "a0_6": a06},
        "block_a": {"a1": a1, "a2": a2, "a3_pre": a3_pre, "a4": a4, "a5": a5},
        "block_b": {"b1": b1, "b2": b2, "b3": b3, "b4": b4},
        "block_c": {"c1": c1, "c2": c2, "c3": c3, "c4": c4,
                    "c5": c5.to_dict(orient="records")},
        "block_d": d,
        "block_e": e,
    }
    path = REPORTS_DIR / "history_phase06_audit.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
