#!/usr/bin/env python3
"""FAZA 1A — quarantine proposal + scoring-impact report (read-only).

Builds the proposed quarantine list via src/data_integrity.py (ghost rows +
implausible LEVEL zeros), then simulates its adoption against the REAL
production scoring functions (economic_compute.compute_indicator_score,
compute_currency_scorecard) — nothing in src/ff_scoring.py or
src/economic_compute.py is modified or called differently than production
does; this script only compares "scored with today's parquet" vs "scored
with the parquet minus the proposed rows".

Zero writes under data/, zero src/ changes beyond the new isolated module,
zero network, zero merge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_integrity import (  # noqa: E402
    build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros,
)
from src.econ_calendar_ff import canonical_id as mk_canonical_id  # noqa: E402
from src.economic_compute import (  # noqa: E402
    _indicator_applies, compute_currency_scorecard, compute_indicator_score,
)
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, build_matcher, load_can_be_zero, to_scoring_frame  # noqa: E402
from src.jb_actuals import ARCHIVE_JSON, RAW_DIR, build_flagged_bad_lookup  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
REPORTS_DIR = ROOT / "reports"
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
AS_OF = pd.Timestamp.now()

SIGMA_CHANGE_THRESHOLD_PCT = 5.0


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_cfgs():
    with open(INDICATORS_YAML) as f:
        ind_cfg = yaml.safe_load(f) or {}
    with open(INSTRUMENTS_YAML) as f:
        inst_cfg = yaml.safe_load(f) or {}
    return ind_cfg, inst_cfg


def load_parquet() -> pd.DataFrame:
    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    return df


def load_raw_universe() -> pd.DataFrame:
    def _load(paths):
        out = []
        for p in paths:
            try:
                out += json.loads(Path(p).read_text())
            except Exception:  # noqa: BLE001
                pass
        return out
    from src.econ_calendar_ff import jblanked_to_utc
    recs = []
    for e in _load([ARCHIVE_JSON]) + _load(sorted(RAW_DIR.glob("jb_range_*.json"))):
        ccy = str(e.get("Currency", "")).strip()
        if ccy not in CCYS:
            continue
        dt = jblanked_to_utc(e.get("Date", ""))
        if dt is None:
            continue
        recs.append({"currency": ccy, "name_raw": str(e.get("Name", "")).strip(),
                    "release_dt": dt, "actual": e.get("Actual"), "forecast": e.get("Forecast"),
                    "previous": e.get("Previous")})
    df = pd.DataFrame(recs)
    for c in ("actual", "forecast", "previous"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def build_ghost_input_from_parquet(ff: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})
    return df[["currency", "indicator_key", "canonical_id", "name_raw",
              "release_dt", "actual", "forecast", "previous"]]


def build_ghost_input_from_raw_universe(raw_uni: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    """Restricted to rows that look like a real data release, not a calendar
    MARKER event (speeches, DST shifts, minutes, reports) — those legitimately
    recur every year near the same date BY DESIGN and are uniformly stamped
    actual=forecast=previous=0.0 as a non-value placeholder (same pattern
    already confirmed for 'SNB Chairman Speaks' etc., FAZA 0.5/0.6): applying
    the ghost-row test to them floods with trivial (0.0,0.0,0.0)==(0.0,0.0,0.0)
    matches unrelated to the wrong-year bug. A genuine economic print
    essentially never has all three fields simultaneously exactly 0.0."""
    df = raw_uni.copy()
    df = df[df["actual"].notna() & ~((df["actual"] == 0.0) & (df["forecast"] == 0.0)
                                     & (df["previous"] == 0.0))]
    df["canonical_id"] = df.apply(lambda r: mk_canonical_id(r["currency"], r["name_raw"]), axis=1)
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_raw"]), axis=1)
    return df[["currency", "indicator_key", "canonical_id", "name_raw",
              "release_dt", "actual", "forecast", "previous"]]


# ---------------------------------------------------------------------------
# L1 — ghost rows: parquet-level intersection test vs FAZA 0.6's condition-(a)-only 93
# ---------------------------------------------------------------------------

def condition_a_only(df: pd.DataFrame) -> pd.DataFrame:
    """Re-derive JUST condition (a) (365-day identical triple, no cadence check)
    for comparison against FAZA 0.6's reported 93 — diagnostic only, not part
    of src/data_integrity.py's (intersection-only) contract."""
    from src.data_integrity import GHOST_YEAR_TARGETS, GHOST_YEAR_TOL_DAYS, _close
    hits = []
    for cid, g in df.groupby("canonical_id"):
        g = g.sort_values("release_dt").reset_index(drop=True)
        n = len(g)
        for i in range(n):
            ri = g.loc[i]
            if pd.isna(ri["actual"]):
                continue
            for j in range(i + 1, n):
                rj = g.loc[j]
                gap = (rj["release_dt"] - ri["release_dt"]).days
                if gap > max(GHOST_YEAR_TARGETS) + GHOST_YEAR_TOL_DAYS:
                    break
                if (any(abs(gap - t) <= GHOST_YEAR_TOL_DAYS for t in GHOST_YEAR_TARGETS)
                        and _close(ri["actual"], rj["actual"]) and _close(ri["forecast"], rj["forecast"])
                        and _close(ri["previous"], rj["previous"])):
                    hits.append({"canonical_id": cid, "currency": ri["currency"],
                                "release_dt": ri["release_dt"], "paired_with_dt": rj["release_dt"],
                                "gap_days": gap})
    return pd.DataFrame(hits)


def l1_report(ff_input: pd.DataFrame) -> dict:
    a_only = condition_a_only(ff_input)
    intersection = detect_ghost_rows(ff_input)
    a_only_keys = set(zip(a_only["canonical_id"], a_only["release_dt"])) if len(a_only) else set()
    inter_keys = set(zip(intersection["currency"], intersection["release_dt"])) if len(intersection) else set()

    dropped = []
    for _, row in a_only.iterrows():
        key_match = intersection[(intersection["release_dt"] == row["release_dt"])
                                 & (intersection["currency"] == row["currency"])]
        if key_match.empty:
            dropped.append({"canonical_id": row["canonical_id"], "currency": row["currency"],
                            "release_dt": str(row["release_dt"]), "gap_days": row["gap_days"]})

    jan2023_a_only = a_only[(a_only["release_dt"] >= "2023-01-01") & (a_only["release_dt"] < "2023-02-01")] \
        if len(a_only) else a_only
    jan2023_inter = intersection[(intersection["release_dt"] >= "2023-01-01")
                                 & (intersection["release_dt"] < "2023-02-01")] if len(intersection) else intersection

    return {
        "n_condition_a_only": len(a_only), "n_intersection": len(intersection),
        "n_dropped_by_cadence_check": len(dropped), "dropped_detail": dropped,
        "n_jan2023_condition_a_only": len(jan2023_a_only), "n_jan2023_intersection": len(jan2023_inter),
    }


# ---------------------------------------------------------------------------
# L2 — implausible zeros: coverage vs flagged_bad
# ---------------------------------------------------------------------------

def l2_report(ff_input: pd.DataFrame, flagged_bad: dict) -> dict:
    zeros = detect_implausible_zeros(ff_input)
    rows = []
    for r in zeros.itertuples(index=False):
        d = pd.Timestamp(r.release_dt).date()
        sig = flagged_bad.get((r.currency, r.name_raw, d))
        rows.append({"currency": r.currency, "indicator_key": r.indicator_key,
                    "name_raw": r.name_raw, "release_dt": str(r.release_dt),
                    "detail": r.detail, "flagged_bad_signal": sig})
    n_caught = sum(1 for r in rows if r["flagged_bad_signal"] is True)
    n_missed = sum(1 for r in rows if r["flagged_bad_signal"] is not True)
    return {"n_hits": len(rows), "n_caught_by_flagged_bad": n_caught,
            "n_missed_by_flagged_bad": n_missed, "rows": rows}


# ---------------------------------------------------------------------------
# Impact simulation — real compute_indicator_score / compute_currency_scorecard
# ---------------------------------------------------------------------------

def _score_all(scored_frame: pd.DataFrame, ind_cfg: dict) -> dict:
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    out = {}
    for ccy in CCYS:
        for key, cfg in indicators.items():
            if not _indicator_applies(ccy, cfg):
                continue
            sub = scored_frame[(scored_frame["currency"] == ccy)
                              & (scored_frame["indicator_key"] == key)]
            res = compute_indicator_score(sub, cfg, defaults, AS_OF, allow_stale=True,
                                          currency=ccy, indicator_key=key)
            out[(ccy, key)] = res
    return out


def _sigma_from(res: dict | None) -> float | None:
    if not res or res.get("z") is None or res.get("surprise") is None:
        return None
    z = res["z"]
    if z == 0:
        return None
    return abs(res["surprise"] / z)


def impact_report(ff: pd.DataFrame, ff_after: pd.DataFrame, matcher, cbz, flagged_bad,
                  ind_cfg: dict, inst_cfg: dict) -> dict:
    scored_before = to_scoring_frame(ff, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    scored_after = to_scoring_frame(ff_after, matcher, can_be_zero=cbz, flagged_bad=flagged_bad)
    scored_before["release_dt"] = pd.to_datetime(scored_before["release_dt"])
    scored_after["release_dt"] = pd.to_datetime(scored_after["release_dt"])

    before_all = _score_all(scored_before, ind_cfg)
    after_all = _score_all(scored_after, ind_cfg)

    rows = []
    for key in before_all:
        b, a = before_all[key], after_all.get(key)
        if b is None and a is None:
            continue
        sigma_b, sigma_a = _sigma_from(b), _sigma_from(a)
        sigma_pct = (abs(sigma_a - sigma_b) / sigma_b * 100
                    if sigma_b and sigma_a and sigma_b != 0 else None)
        z_b = b.get("z") if b else None
        z_a = a.get("z") if a else None
        z_changed = (z_b is None) != (z_a is None) or (
            z_b is not None and z_a is not None and abs(z_b - z_a) > 1e-9)
        score_b = b.get("score") if b else None
        score_a = a.get("score") if a else None
        bucket_changed = score_b != score_a
        if (sigma_pct and sigma_pct >= SIGMA_CHANGE_THRESHOLD_PCT) or z_changed or bucket_changed:
            rows.append({
                "currency": key[0], "indicator_key": key[1],
                "sigma_before": sigma_b, "sigma_after": sigma_a, "sigma_change_pct": sigma_pct,
                "z_before": z_b, "z_after": z_a, "z_changed": z_changed,
                "bucket_before": score_b, "bucket_after": score_a, "bucket_changed": bucket_changed,
            })
    rows.sort(key=lambda r: -(r["sigma_change_pct"] or 0))

    ccy_rows = []
    for ccy in CCYS:
        card_before = compute_currency_scorecard(scored_before, ccy, ind_cfg, inst_cfg, AS_OF)
        card_after = compute_currency_scorecard(scored_after, ccy, ind_cfg, inst_cfg, AS_OF)
        delta = card_after["index"] - card_before["index"]
        ccy_rows.append({"currency": ccy, "index_before": round(card_before["index"], 3),
                        "index_after": round(card_after["index"], 3), "delta": round(delta, 3)})
    ccy_rows.sort(key=lambda r: -abs(r["delta"]))

    return {
        "n_indicator_pairs_checked": len(before_all),
        "n_pairs_sigma_changed_5pct": sum(1 for r in rows if r["sigma_change_pct"] and r["sigma_change_pct"] >= 5),
        "n_pairs_z_changed": sum(1 for r in rows if r["z_changed"]),
        "n_pairs_bucket_changed": sum(1 for r in rows if r["bucket_changed"]),
        "n_currencies_index_changed": sum(1 for r in ccy_rows if abs(r["delta"]) > 1e-9),
        "pairs": rows, "currencies": ccy_rows,
        "note": "compute_currency_scorecard called with rate_entry=None (surprise-only, "
               "3-category variant) — the standing rates/monetary category (FRED-sourced) "
               "is out of scope for this parquet-only simulation.",
    }


# ---------------------------------------------------------------------------
# Annex — UI display-threshold calibration
# ---------------------------------------------------------------------------

def annex_display_thresholds(ff_after: pd.DataFrame, matcher, ind_cfg: dict,
                             label_mismatches: set) -> list[dict]:
    from src.ff_scoring import detect_cadence
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365}
    as_of = AS_OF

    df = ff_after.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)

    rows = []
    for ccy in CCYS:
        for key, cfg in indicators.items():
            if not _indicator_applies(ccy, cfg):
                continue
            sub = df[(df["currency"] == ccy) & (df["indicator_key"] == key)]
            printed = sub[sub["actual"].notna()]
            if printed.empty:
                rows.append({"currency": ccy, "indicator_key": key, "n_1y": 0, "n_2y": 0,
                            "n_max": 0, "verdict": "BLOCKED", "note": "no printed rows"})
                continue
            dates = pd.to_datetime(printed["datetime_utc"])
            n1y = int((dates >= as_of - pd.Timedelta(days=365)).sum())
            n2y = int((dates >= as_of - pd.Timedelta(days=730)).sum())
            nmax = len(dates)
            cadence = detect_cadence(dates) if len(dates) >= 2 else "unknown"
            cad_days = CADENCE_DAYS.get(cadence)
            last = dates.max()
            stale = bool(cad_days and (as_of - last).days > 2 * cad_days)

            note = []
            if stale:
                verdict = "BLOCKED"
                note.append(f"STALE (last={last.date()}, cadence={cadence})")
            elif nmax < 8:
                verdict = "BLOCKED"
            else:
                verdict = "FULL"
                if n1y < 8:
                    note.append("1y HIDDEN")
                if n2y < 8:
                    note.append("2y HIDDEN")
            if (ccy, key) in label_mismatches:
                if verdict != "BLOCKED":
                    verdict = "LABEL_PENDING"
                note.append("label MISMATCH (Bloc D, FAZA 0.6)")

            rows.append({"currency": ccy, "indicator_key": key, "n_1y": n1y, "n_2y": n2y,
                        "n_max": nmax, "verdict": verdict, "note": "; ".join(note)})
    rows.sort(key=lambda r: (indicators.get(r["indicator_key"], {}).get("category", "zzz"),
                            r["currency"], r["indicator_key"]))
    return rows


# Known label MISMATCH pairs from FAZA 0.6 Bloc D (impact-ordered list, all
# 21 hits) — carried forward as a literal set here since this branch does not
# have the measure/history-catalog-audit scripts; NOT re-derived/re-audited,
# per this task's own instruction not to re-run FAZA 0/0.5/0.6.
LABEL_MISMATCH_PAIRS = {
    ("GBP", "wage_growth"), ("CHF", "cpi_yoy"), ("JPY", "cpi_yoy"), ("CAD", "gdp_qoq"),
    ("GBP", "gdp_qoq"), ("USD", "wage_growth"), ("CAD", "cpi_yoy"), ("USD", "core_cpi"),
    ("NZD", "cpi_yoy"), ("CHF", "ppi_yoy"), ("USD", "core_pce"), ("CAD", "ppi_yoy"),
    ("EUR", "ppi_yoy"), ("JPY", "retail_sales"), ("USD", "ppi_yoy"), ("GBP", "ppi_yoy"),
    ("AUD", "core_cpi"), ("AUD", "ppi_yoy"), ("AUD", "wage_growth"), ("NZD", "wage_growth"),
}


def main() -> int:
    ind_cfg, inst_cfg = load_cfgs()
    matcher = build_matcher()
    cbz = load_can_be_zero(ind_cfg)
    flagged_bad = build_flagged_bad_lookup()

    ff = load_parquet()
    raw_uni = load_raw_universe()

    ghost_input_parquet = build_ghost_input_from_parquet(ff, matcher)
    ghost_input_raw = build_ghost_input_from_raw_universe(raw_uni, matcher)

    print("=" * 90)
    print("L1 — ghost rows (Class 1)")
    print("=" * 90)
    l1_parquet = l1_report(ghost_input_parquet)
    print(f"Parquet-level: condition (a) alone = {l1_parquet['n_condition_a_only']} pairs "
          f"(FAZA 0.6 reported 93 on this same test); intersection (a AND b) = "
          f"{l1_parquet['n_intersection']}; dropped by cadence check = "
          f"{l1_parquet['n_dropped_by_cadence_check']}")
    print(f"  Ianuarie 2023 cluster: condition-a-only={l1_parquet['n_jan2023_condition_a_only']}, "
          f"intersection={l1_parquet['n_jan2023_intersection']}")
    print("  Perechi CĂZUTE de intersecție (condiție (b) lipsă):")
    for d in l1_parquet["dropped_detail"]:
        print(f"    {d['canonical_id']:<28} {d['currency']} {d['release_dt']} gap={d['gap_days']}d")

    ghosts_parquet = detect_ghost_rows(ghost_input_parquet)
    ghosts_raw = detect_ghost_rows(ghost_input_raw)
    print(f"\nRaw-universe-level (archive+jb_raw, canonical_id=slug(currency,name_raw)): "
          f"intersection = {len(ghosts_raw)} rows")
    raw_only_ghosts = ghosts_raw[~ghosts_raw["release_dt"].isin(ghosts_parquet["release_dt"])]
    print(f"  din care NEALIASATE (nu apar în parquet, indicator_key=None): "
          f"{len(raw_only_ghosts[raw_only_ghosts['indicator_key'].isna()])}")
    for r in raw_only_ghosts[raw_only_ghosts["indicator_key"].isna()].itertuples():
        print(f"    {r.currency} {r.name_raw!r} {r.release_dt}  detail={r.detail}")

    print()
    print("=" * 90)
    print("L2 — implausible zeros pe serii de nivel (Class 2)")
    print("=" * 90)
    l2 = l2_report(ghost_input_parquet, flagged_bad)
    print(f"n_hits={l2['n_hits']}  caught_by_flagged_bad={l2['n_caught_by_flagged_bad']}  "
          f"missed_by_flagged_bad={l2['n_missed_by_flagged_bad']}")
    for r in l2["rows"]:
        print(f"  {r['currency']} {r['indicator_key']:<26} {r['release_dt']}  "
              f"flagged_bad={r['flagged_bad_signal']}  {r['detail']}")

    proposal = build_quarantine_proposal(ghosts_parquet, detect_implausible_zeros(ghost_input_parquet))
    print(f"\nQuarantine proposal (parquet-level, ce ar intra efectiv în excludere pentru scoring): "
          f"{len(proposal)} rânduri")

    quarantine_keys = set(zip(proposal["currency"], proposal["name_raw"],
                              proposal["release_dt"].astype(str)))
    ff_after = ff[~ff.apply(lambda r: (r["currency"], r["name_raw"], str(r["datetime_utc"]))
                           in quarantine_keys, axis=1)].copy()
    print(f"Parquet: {len(ff)} rânduri -> {len(ff_after)} după excludere ({len(ff) - len(ff_after)} eliminate)")

    print()
    print("=" * 90)
    print("IMPACT PE SCORINGUL CURENT")
    print("=" * 90)
    impact = impact_report(ff, ff_after, matcher, cbz, flagged_bad, ind_cfg, inst_cfg)
    print(f"Perechi (currency, indicator_key) verificate: {impact['n_indicator_pairs_checked']}")
    print(f"  sigma schimbată >5%: {impact['n_pairs_sigma_changed_5pct']}")
    print(f"  z-score schimbat: {impact['n_pairs_z_changed']}")
    print(f"  bucket schimbat: {impact['n_pairs_bucket_changed']}")
    print(f"  valute cu index agregat schimbat: {impact['n_currencies_index_changed']} / {len(CCYS)}")
    print(f"  ({impact['note']})")
    print("\n  Tabel (doar rândurile cu vreo schimbare), sigma% desc:")
    hdr = f"  {'CCY':<5}{'KEY':<28}{'sigma%chg':>10}{'z before':>10}{'z after':>10}{'bucket b':>9}{'bucket a':>9}"
    print(hdr)
    for r in impact["pairs"]:
        print(f"  {r['currency']:<5}{r['indicator_key']:<28}"
              f"{str(round(r['sigma_change_pct'],1)) if r['sigma_change_pct'] else '-':>10}"
              f"{str(round(r['z_before'],3)) if r['z_before'] is not None else '-':>10}"
              f"{str(round(r['z_after'],3)) if r['z_after'] is not None else '-':>10}"
              f"{str(r['bucket_before']):>9}{str(r['bucket_after']):>9}")
    print("\n  Per valută, index agregat (/strength), delta desc:")
    for r in impact["currencies"]:
        print(f"    {r['currency']:<5} {r['index_before']:>7} -> {r['index_after']:>7}  delta={r['delta']}")

    print()
    print("=" * 90)
    print("ANEXĂ — praguri de afișare UI (post-quarantine)")
    print("=" * 90)
    annex = annex_display_thresholds(ff_after, matcher, ind_cfg, LABEL_MISMATCH_PAIRS)
    print(f"{'CAT':<12}{'CCY':<5}{'KEY':<28}{'1y':>4}{'2y':>4}{'Max':>5}  {'VERDICT':<14}NOTE")
    indicators = ind_cfg.get("indicators", {}) or {}
    for r in annex:
        cat = indicators.get(r["indicator_key"], {}).get("category", "?")
        print(f"{cat:<12}{r['currency']:<5}{r['indicator_key']:<28}{r['n_1y']:>4}{r['n_2y']:>4}"
              f"{r['n_max']:>5}  {r['verdict']:<14}{r['note']}")

    REPORTS_DIR.mkdir(exist_ok=True)
    prop_json = REPORTS_DIR / "quarantine_proposal.json"
    prop_csv = REPORTS_DIR / "quarantine_proposal.csv"
    proposal.to_json(prop_json, orient="records", indent=2, date_format="iso")
    proposal.to_csv(prop_csv, index=False)
    print(f"\nWrote {prop_json.relative_to(ROOT)} and {prop_csv.relative_to(ROOT)} "
          f"({len(proposal)} rows)")

    payload = {
        "l1": l1_parquet,
        "l1_raw_universe_n_intersection": len(ghosts_raw),
        "l1_raw_universe_unaliased_hits": raw_only_ghosts[raw_only_ghosts["indicator_key"].isna()]
                                          .to_dict(orient="records"),
        "l2": l2, "impact": impact, "annex": annex,
    }
    report_json = REPORTS_DIR / "quarantine_impact_report.json"
    report_json.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {report_json.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
