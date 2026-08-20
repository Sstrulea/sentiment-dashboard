#!/usr/bin/env python3
"""FAZA 0 — read-only audit of the historical economic-calendar catalog.

Answers, from REAL local data (data/economic_calendar_ff.parquet + the raw
JBlanked/FF JSON captures under data/jb_raw, data/archive, data/ff_raw), which
canonical (category, currency) primary/secondary series actually exist, how
deep/clean their history is, and whether their configured frequency and
name_raw are what the config claims.

Zero network. Zero writes under data/. Zero changes to src/. Output: a
fixed-width table + flags section on stdout, plus reports/history_catalog_audit
.json/.md for full detail (git-ignored, not committed).

SCHEMA NOTE (see docs delivered alongside this script for the full writeup):
indicator_key is NOT a column of the raw historical parquet — it is derived
downstream (ff_scoring.to_scoring_frame) by regex-matching name_canonical
against data/economic_indicators.yaml's `matcher` section. Many raw name_raw
values that exist in JBlanked's feed never reach an indicator_key at all
(no matcher rule), and some never even reach the parquet (no entry in
config/ff_aliases.yaml gates ingestion at canonicalization time — see
econ_calendar_ff._canonicalize). This script therefore resolves candidates
against THREE pools per currency, in this priority order:
  1. name_canonical values already in the parquet (production-aliased)
  2. name_raw values already in the parquet (production-aliased)
  3. name_raw values seen ONLY in the raw JSON captures, never aliased
A hit in pool 3 is reported as UNRESOLVED even at a high text-match score:
it is evidence the series exists upstream, not evidence it is usable today.
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import (  # noqa: E402
    canonical_id as mk_canonical_id,
    extract_period_suffix,
    iso_to_utc,
    jblanked_to_utc,
)
from src.economic_compute import effective_frequency  # noqa: E402
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, detect_cadence  # noqa: E402
from src.jb_actuals import ARCHIVE_JSON, RAW_DIR, build_flagged_bad_lookup  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
FF_RAW_DIR = ROOT / "data" / "ff_raw"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
REPORTS_DIR = ROOT / "reports"

CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]

MATCH_SCORE_RESOLVE = 55.0   # candidate text-match score below which we call it UNRESOLVED
REV_EPS_REL = 0.02           # relative epsilon for "this pair is a real revision"
REV_EPS_ABS = 1e-6
THIN_N = 20                  # < this many prints -> THIN flag

# ---------------------------------------------------------------------------
# The 8-currency x 4-category candidate list, verbatim from the FAZA 0 brief.
# (cat, ccy, rol, description) — description is a SEMANTIC label, matched by
# text-similarity against real name_raw/name_canonical, never assumed exact.
# ---------------------------------------------------------------------------
CANDIDATES: list[tuple[str, str, str, str]] = []


def _add(cat: str, ccy: str, prim: str, secs: list[str]) -> None:
    CANDIDATES.append((cat, ccy, "PRIM", prim))
    for s in secs:
        CANDIDATES.append((cat, ccy, "SEC", s))


_add("inflation", "USD", "CPI y/y", ["Core CPI y/y"])
_add("inflation", "EUR", "HICP Flash y/y", ["Core HICP Flash y/y"])
_add("inflation", "GBP", "CPI y/y", ["Core CPI y/y"])
_add("inflation", "JPY", "National Core CPI y/y", ["Tokyo Core CPI y/y"])
_add("inflation", "CAD", "Median CPI y/y", ["Trimmed Mean CPI y/y", "CPI m/m"])
_add("inflation", "AUD", "CPI y/y", ["Trimmed Mean CPI q/q"])
_add("inflation", "NZD", "CPI q/q", [])
_add("inflation", "CHF", "CPI m/m", ["CPI y/y"])

_add("labor", "USD", "Non-Farm Payrolls", ["Unemployment Rate", "Average Hourly Earnings m/m"])
_add("labor", "CAD", "Employment Change", ["Unemployment Rate"])
_add("labor", "AUD", "Employment Change", ["Unemployment Rate"])
_add("labor", "NZD", "Employment Change q/q", ["Unemployment Rate"])
_add("labor", "GBP", "Average Earnings 3m/y", ["Unemployment Rate", "Claimant Count Change"])
_add("labor", "EUR", "Unemployment Rate", ["German Unemployment Change"])
_add("labor", "JPY", "Unemployment Rate", [])
_add("labor", "CHF", "Unemployment Rate", [])

_add("growth", "USD", "GDP q/q Advance", ["Retail Sales m/m"])
_add("growth", "GBP", "GDP m/m", ["GDP q/q Prelim"])
_add("growth", "CAD", "GDP m/m", [])
_add("growth", "EUR", "GDP Flash q/q", ["Retail Sales m/m"])
_add("growth", "JPY", "GDP Prelim q/q", [])
_add("growth", "AUD", "GDP q/q", ["Retail Sales m/m"])
_add("growth", "NZD", "GDP q/q", [])
_add("growth", "CHF", "GDP q/q", [])

_add("rates", "USD", "Federal Funds Rate", [])
_add("rates", "EUR", "Main Refinancing Rate", [])
_add("rates", "GBP", "Official Bank Rate", [])
_add("rates", "JPY", "BOJ Policy Rate", [])
_add("rates", "CAD", "Overnight Rate", [])
_add("rates", "AUD", "Cash Rate", [])
_add("rates", "NZD", "Official Cash Rate", [])
_add("rates", "CHF", "SNB Policy Rate", [])


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_indicators_cfg() -> dict:
    with open(INDICATORS_YAML) as f:
        return yaml.safe_load(f) or {}


def load_parquet() -> pd.DataFrame:
    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    return df


def _read_jb_shaped(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — audit tool, never crash on one bad file
        return []
    out = []
    for e in data:
        ccy = str(e.get("Currency", "")).strip()
        if ccy not in CCYS:
            continue
        dt = jblanked_to_utc(e.get("Date", ""))
        if dt is None:
            continue
        out.append({"currency": ccy, "name_raw": str(e.get("Name", "")).strip(), "dt": dt,
                    "actual_raw": e.get("Actual"), "forecast_raw": e.get("Forecast"),
                    "previous_raw": e.get("Previous"), "src": path.name})
    return out


def _read_ff_shaped(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in data:
        ccy = str(e.get("country", "")).strip()
        if ccy not in CCYS:
            continue
        dt = iso_to_utc(e.get("date", ""))
        if dt is None:
            continue
        out.append({"currency": ccy, "name_raw": str(e.get("title", "")).strip(), "dt": dt,
                    "actual_raw": e.get("actual"), "forecast_raw": e.get("forecast"),
                    "previous_raw": e.get("previous"), "src": path.name})
    return out


def load_raw_universe() -> pd.DataFrame:
    """Pre-alias-filter name census: everything JBlanked/FF ever sent us that we
    have a local copy of, regardless of whether config/ff_aliases.yaml maps it.
    Sources: data/archive/ff_calendar_range.json (backfill, 2023-01-01..2026-07-03),
    data/jb_raw/*.json (rolling ~14-day JBlanked captures), data/ff_raw/*.json
    (rolling FF weekly captures, actual-less). KNOWN GAP: no local raw capture
    covers 2026-07-04..2026-08-06 (between the archive backfill and the oldest
    retained jb_raw file) — a candidate unresolved solely on raw-universe
    evidence from that window is inconclusive, not necessarily absent upstream.
    """
    recs: list[dict] = []
    if ARCHIVE_JSON.exists():
        recs += _read_jb_shaped(ARCHIVE_JSON)
    for p in sorted(RAW_DIR.glob("jb_range_*.json")):
        recs += _read_jb_shaped(p)
    for p in sorted(FF_RAW_DIR.glob("*.json")):
        recs += _read_ff_shaped(p)
    if not recs:
        return pd.DataFrame(columns=["currency", "name_raw", "dt", "actual_raw",
                                     "forecast_raw", "previous_raw", "src"])
    df = pd.DataFrame(recs)
    df["dt"] = pd.to_datetime(df["dt"])
    return df


def _safe_normalize(raw) -> float:
    from src.econ_calendar_ff import normalize_ff_value
    try:
        return normalize_ff_value(raw)
    except (ValueError, TypeError):
        return float("nan")


# ---------------------------------------------------------------------------
# Section A — discovery inventory (safety net)
# ---------------------------------------------------------------------------

def discovery_by_indicator_key(ff: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    ff = ff.copy()
    ff["indicator_key"] = ff.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    rows = []
    for (ccy, key), g in ff.groupby(["currency", "indicator_key"], dropna=False):
        if key is None:
            continue
        printed = g[g["actual"].notna()]
        if printed.empty:
            continue
        names = printed["name_raw"].value_counts()
        rows.append({
            "currency": ccy, "indicator_key": key, "n": len(printed),
            "from": printed["datetime_utc"].min().date().isoformat(),
            "to": printed["datetime_utc"].max().date().isoformat(),
            "distinct_name_raw": len(names),
            "name_raw_counts": names.to_dict(),
            "fcst_pct": round(100 * printed["forecast"].notna().mean(), 1),
        })
    return pd.DataFrame(rows).sort_values(["currency", "indicator_key"]).reset_index(drop=True)


def discovery_raw_census(ff: pd.DataFrame, raw_uni: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for ccy in CCYS:
        rows = []
        psub = ff[ff["currency"] == ccy]
        for nm, g in psub.groupby("name_raw"):
            printed = g[g["actual"].notna()]
            rows.append({"currency": ccy, "name_raw": nm, "n": len(printed),
                        "from": printed["datetime_utc"].min().date().isoformat() if len(printed) else None,
                        "to": printed["datetime_utc"].max().date().isoformat() if len(printed) else None,
                        "fcst_pct": round(100 * printed["forecast"].notna().mean(), 1) if len(printed) else None,
                        "in_parquet": True})
        if len(raw_uni):
            rsub = raw_uni[raw_uni["currency"] == ccy]
            aliased_names = set(psub["name_raw"].unique())
            for nm, g in rsub.groupby("name_raw"):
                if nm in aliased_names:
                    continue
                rows.append({"currency": ccy, "name_raw": nm, "n": len(g),
                            "from": g["dt"].min().date().isoformat(),
                            "to": g["dt"].max().date().isoformat(),
                            "fcst_pct": round(100 * g["forecast_raw"].apply(
                                lambda v: v is not None and str(v).strip() != "").mean(), 1),
                            "in_parquet": False})
        out[ccy] = pd.DataFrame(rows).sort_values("n", ascending=False).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Section B — candidate resolution
# ---------------------------------------------------------------------------

def _score(query: str, candidate: str) -> float:
    q, c = query.lower().strip(), candidate.lower().strip()
    if q == c:
        return 100.0
    ratio = difflib.SequenceMatcher(None, q, c).ratio() * 100
    if q in c or c in q:
        ratio = max(ratio, 85.0)
    return ratio


def build_pool(ccy: str, ff: pd.DataFrame, raw_uni: pd.DataFrame) -> list[dict]:
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sub = ff[ff["currency"] == ccy]
    for txt in sub["name_canonical"].dropna().unique():
        k = ("canonical", txt)
        if k in seen:
            continue
        seen.add(k)
        pool.append({"text": txt, "kind": "canonical"})
    for txt in sub["name_raw"].dropna().unique():
        k = ("parquet_raw", txt)
        if k in seen:
            continue
        seen.add(k)
        pool.append({"text": txt, "kind": "parquet_raw"})
    if len(raw_uni):
        rsub = raw_uni[raw_uni["currency"] == ccy]
        aliased = set(sub["name_raw"].unique())
        for txt in rsub["name_raw"].dropna().unique():
            if txt in aliased:
                continue
            k = ("raw_only", txt)
            if k in seen:
                continue
            seen.add(k)
            pool.append({"text": txt, "kind": "raw_only"})
    return pool


def rank_pool(desc: str, pool: list[dict]) -> list[tuple[float, dict]]:
    scored = [(_score(desc, p["text"]), p) for p in pool]
    scored.sort(key=lambda t: -t[0])
    return scored


# ---------------------------------------------------------------------------
# Series metrics (works on a parquet-backed subset OR a raw-only subset)
# ---------------------------------------------------------------------------

def compute_metrics(sub: pd.DataFrame, ccy: str, is_raw_only: bool,
                    flagged_bad: dict, expected_suffix: str) -> dict:
    if is_raw_only:
        sub = sub.copy()
        sub["actual_n"] = sub["actual_raw"].apply(_safe_normalize)
        sub["forecast_n"] = sub["forecast_raw"].apply(_safe_normalize)
        sub["previous_n"] = sub["previous_raw"].apply(_safe_normalize)
        dtcol, acol, fcol, pcol = "dt", "actual_n", "forecast_n", "previous_n"
    else:
        dtcol, acol, fcol, pcol = "datetime_utc", "actual", "forecast", "previous"

    sub = sub.sort_values(dtcol)
    printed = sub[sub[acol].notna()]
    n = len(printed)
    if n == 0:
        return {"n": 0, "from": None, "to": None, "fcst_pct": None, "prev_pct": None,
                "zero_pct": None, "bad_pct": None, "revder_pct": None, "revrate_pct": None,
                "rev_abs": {}, "rev_sigma": {}, "name_ok": "OK", "distinct_names": {},
                "freq_emp": "unknown", "xform_mismatch_pct": None}

    distinct_names = printed["name_raw"].value_counts().to_dict()
    name_ok = "OK" if len(distinct_names) == 1 else f"{len(distinct_names)}x"
    fcst_pct = round(100 * printed[fcol].notna().mean(), 1)
    prev_pct = round(100 * printed[pcol].notna().mean(), 1)
    zero_pct = round(100 * (printed[acol] == 0.0).mean(), 1)
    freq_emp = detect_cadence(printed[dtcol])

    sig = []
    for r in printed.itertuples():
        d = pd.Timestamp(getattr(r, dtcol)).date()
        nr = getattr(r, "name_raw")
        sig.append(flagged_bad.get((ccy, nr, d)))
    present = [s for s in sig if s is not None]
    bad_pct = round(100 * sum(present) / len(present), 1) if present else None

    xform_mismatches = 0
    xform_checked = 0
    if expected_suffix and expected_suffix != "none":
        for nr in printed["name_raw"]:
            try:
                real_suffix = extract_period_suffix(nr)
            except ValueError:
                continue
            if real_suffix == "none":
                continue
            xform_checked += 1
            if real_suffix != expected_suffix:
                xform_mismatches += 1
    xform_pct = round(100 * xform_mismatches / xform_checked, 1) if xform_checked else None

    revder_pct = revrate_pct = None
    rev_abs: dict = {}
    rev_sigma: dict = {}
    if not is_raw_only:
        both = printed[printed["actual"].notna() & printed["forecast"].notna()]
        sigma_full = float((both["actual"] - both["forecast"]).std(ddof=1)) if len(both) >= 2 else None
        rows = printed.reset_index(drop=True)
        n_pairs = 0
        n_derivable = 0
        n_revised = 0
        magnitudes = []
        for i in range(len(rows) - 1):
            a_n = rows.loc[i, "actual"]
            prev_next = rows.loc[i + 1, "previous"]
            n_pairs += 1
            if pd.isna(a_n) or pd.isna(prev_next):
                continue
            d_n = pd.Timestamp(rows.loc[i, "datetime_utc"]).date()
            d_n1 = pd.Timestamp(rows.loc[i + 1, "datetime_utc"]).date()
            if flagged_bad.get((ccy, rows.loc[i, "name_raw"], d_n)) is True:
                continue
            if flagged_bad.get((ccy, rows.loc[i + 1, "name_raw"], d_n1)) is True:
                continue
            n_derivable += 1
            diff = float(prev_next) - float(a_n)
            thresh = max(REV_EPS_ABS, REV_EPS_REL * abs(float(a_n)))
            if abs(diff) > thresh:
                n_revised += 1
                magnitudes.append(abs(diff))
        if n_pairs:
            revder_pct = round(100 * n_derivable / n_pairs, 1)
        if n_derivable:
            revrate_pct = round(100 * n_revised / n_derivable, 1)
        if magnitudes:
            s = pd.Series(magnitudes)
            rev_abs = {"min": round(s.min(), 4), "median": round(s.median(), 4),
                      "p90": round(s.quantile(0.9), 4), "max": round(s.max(), 4)}
            if sigma_full and sigma_full > 0:
                s_sigma = s / sigma_full
                rev_sigma = {"min": round(s_sigma.min(), 3), "median": round(s_sigma.median(), 3),
                            "p90": round(s_sigma.quantile(0.9), 3), "max": round(s_sigma.max(), 3)}

    return {"n": n, "from": printed[dtcol].min().date().isoformat(),
            "to": printed[dtcol].max().date().isoformat(),
            "fcst_pct": fcst_pct, "prev_pct": prev_pct, "zero_pct": zero_pct,
            "bad_pct": bad_pct, "revder_pct": revder_pct, "revrate_pct": revrate_pct,
            "rev_abs": rev_abs, "rev_sigma": rev_sigma, "name_ok": name_ok,
            "distinct_names": distinct_names, "freq_emp": freq_emp,
            "xform_mismatch_pct": xform_pct}


def _expected_suffix(desc: str) -> str:
    try:
        return extract_period_suffix(desc)
    except ValueError:
        return "none"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def resolve_one(cat: str, ccy: str, rol: str, desc: str, ff: pd.DataFrame,
                raw_uni: pd.DataFrame, pools: dict, matcher: CompiledMatcher,
                indicators: dict, defaults: dict, flagged_bad: dict) -> dict:
    """Resolve ONE (cat, ccy, rol, desc) candidate against the currency's pool
    and compute its full metrics. Pulled out of `run()` (FAZA 0.5, docs/) so a
    later script can reuse the IDENTICAL resolution path via import instead of
    re-deriving it — `_subset`/`_is_raw_only` are extra (leading-underscore,
    stripped before JSON serialization) so a caller that needs the resolved
    row-level data for further analysis doesn't have to re-run the pool search."""
    ranked = rank_pool(desc, pools[ccy])
    top3 = [{"text": p["text"], "kind": p["kind"], "score": round(sc, 1)}
            for sc, p in ranked[:3]]
    if not ranked:
        return {"cat": cat, "ccy": ccy, "rol": rol, "desc": desc,
                "key": None, "resolved": False, "top3": top3,
                "metrics": compute_metrics(pd.DataFrame(), ccy, False, {}, "none"),
                "_subset": pd.DataFrame(), "_is_raw_only": False}

    best_score, best = ranked[0]
    is_raw_only = best["kind"] == "raw_only"
    resolved = (best_score >= MATCH_SCORE_RESOLVE) and not is_raw_only

    if best["kind"] in ("canonical", "parquet_raw"):
        if best["kind"] == "canonical":
            winning_canonical = best["text"]
        else:
            matches = ff[(ff["currency"] == ccy) & (ff["name_raw"] == best["text"])]
            winning_canonical = matches["name_canonical"].iloc[0]
        subset = ff[(ff["currency"] == ccy) & (ff["name_canonical"] == winning_canonical)]
        indicator_key = matcher.match(CCY2COUNTRY.get(ccy, ""), winning_canonical)
        key = indicator_key or f"canonical:{mk_canonical_id(ccy, winning_canonical)}"
    else:
        subset = raw_uni[(raw_uni["currency"] == ccy) & (raw_uni["name_raw"] == best["text"])]
        indicator_key = None
        key = f"raw:{mk_canonical_id(ccy, best['text'])}"

    expected_suffix = _expected_suffix(desc)
    metrics = compute_metrics(subset, ccy, is_raw_only, flagged_bad, expected_suffix)

    freq_decl = "-"
    if indicator_key and indicator_key in indicators:
        freq_decl = effective_frequency(indicators[indicator_key], defaults, ccy) or "-"

    flags = []
    if not resolved:
        flags.append("UNRESOLVED")
    else:
        if metrics["name_ok"] != "OK":
            flags.append("NAME_HETERO")
        if freq_decl != "-" and metrics["freq_emp"] not in ("unknown",) and freq_decl != metrics["freq_emp"]:
            flags.append("FREQ_MISMATCH")
        if metrics["n"] and metrics["n"] < THIN_N:
            flags.append("THIN")
        if metrics["xform_mismatch_pct"]:
            flags.append("XFORM")
    if not flags:
        flags.append("OK")

    return {
        "cat": cat, "ccy": ccy, "rol": rol, "desc": desc, "key": key,
        "indicator_key": indicator_key, "resolved": resolved, "score": round(best_score, 1),
        "match_kind": best["kind"], "matched_text": best["text"], "top3": top3,
        "freq_decl": freq_decl, "flags": flags, "metrics": metrics,
        "_subset": subset, "_is_raw_only": is_raw_only,
    }


def run() -> dict:
    ind_cfg = load_indicators_cfg()
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}) or {})
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    flagged_bad = build_flagged_bad_lookup()

    ff = load_parquet()
    raw_uni = load_raw_universe()

    pools = {ccy: build_pool(ccy, ff, raw_uni) for ccy in CCYS}

    results = [resolve_one(cat, ccy, rol, desc, ff, raw_uni, pools, matcher,
                          indicators, defaults, flagged_bad)
              for cat, ccy, rol, desc in CANDIDATES]

    discovery_key = discovery_by_indicator_key(ff, matcher)
    discovery_raw = discovery_raw_census(ff, raw_uni)

    return {"results": results, "discovery_key": discovery_key, "discovery_raw": discovery_raw,
            "ff_parquet_rows": len(ff), "raw_universe_rows": len(raw_uni)}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

COLS = ["CAT", "CCY", "ROL", "KEY", "NAME_OK", "N", "FROM", "TO", "FCST%", "PREV%",
        "ZERO%", "BAD%", "REVDER%", "REVRATE%", "FREQ_DECL", "FREQ_EMP", "FLAG"]
WIDTHS = [11, 5, 6, 28, 9, 6, 12, 12, 7, 7, 7, 7, 9, 10, 11, 11, 22]


def _fmt(v, w):
    s = "-" if v is None else str(v)
    return s[:w - 1].ljust(w) if len(s) >= w else s.ljust(w)


def _row_cells(r: dict) -> list:
    m = r["metrics"]
    return [r["cat"], r["ccy"], r["rol"], r.get("key") or "UNRESOLVED", m["name_ok"],
            m["n"] or 0, m["from"] or "-", m["to"] or "-", m["fcst_pct"], m["prev_pct"],
            m["zero_pct"], m["bad_pct"], m["revder_pct"], m["revrate_pct"],
            r.get("freq_decl", "-"), m["freq_emp"], ",".join(r.get("flags", ["UNRESOLVED"]))]


def print_table(results: list[dict]) -> None:
    header = "".join(_fmt(c, w) for c, w in zip(COLS, WIDTHS))
    print(header)
    print("-" * len(header))
    for r in results:
        print("".join(_fmt(c, w) for c, w in zip(_row_cells(r), WIDTHS)))


def print_flags_section(results: list[dict]) -> None:
    print()
    print("=" * 80)
    print("UNRESOLVED / FLAG != OK")
    print("=" * 80)
    for r in results:
        flags = r.get("flags", ["UNRESOLVED"])
        if flags == ["OK"]:
            continue
        print(f"\n{r['cat']}/{r['ccy']}/{r['rol']}  \"{r['desc']}\"  flags={','.join(flags)}")
        if r.get("key"):
            print(f"  resolved -> key={r['key']}  matched_text={r.get('matched_text')!r} "
                  f"(kind={r.get('match_kind')}, score={r.get('score')})")
        print("  top3 alternatives:")
        for alt in r["top3"]:
            print(f"    [{alt['score']:5.1f}] ({alt['kind']:>11}) {alt['text']!r}")


def write_reports(audit: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    gi = ROOT / ".gitignore"
    gi_text = gi.read_text() if gi.exists() else ""
    if "reports/" not in gi_text.split("\n"):
        with open(gi, "a") as f:
            if gi_text and not gi_text.endswith("\n"):
                f.write("\n")
            f.write("reports/\n")

    json_path = REPORTS_DIR / "history_catalog_audit.json"
    clean_results = [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in audit["results"]]
    serializable = {
        "ff_parquet_rows": audit["ff_parquet_rows"],
        "raw_universe_rows": audit["raw_universe_rows"],
        "results": clean_results,
        "discovery_by_indicator_key": audit["discovery_key"].to_dict(orient="records"),
        "discovery_raw_census": {ccy: df.to_dict(orient="records")
                                 for ccy, df in audit["discovery_raw"].items()},
    }
    json_path.write_text(json.dumps(serializable, indent=2, default=str))

    md_path = REPORTS_DIR / "history_catalog_audit.md"
    lines = ["# History catalog audit\n"]
    lines.append(f"FF parquet rows: {audit['ff_parquet_rows']} | raw universe rows: {audit['raw_universe_rows']}\n")
    lines.append("\n## Candidate resolution\n")
    lines.append("| CAT | CCY | ROL | KEY | N | FROM | TO | FLAG |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in audit["results"]:
        m = r["metrics"]
        lines.append(f"| {r['cat']} | {r['ccy']} | {r['rol']} | {r.get('key') or 'UNRESOLVED'} "
                    f"| {m['n'] or 0} | {m['from'] or '-'} | {m['to'] or '-'} "
                    f"| {','.join(r.get('flags', ['UNRESOLVED']))} |")
    lines.append("\n## Existing indicator_key inventory (from production matcher)\n")
    lines.append("| CCY | indicator_key | N | FROM | TO | distinct name_raw | FCST% |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in audit["discovery_key"].to_dict(orient="records"):
        lines.append(f"| {row['currency']} | {row['indicator_key']} | {row['n']} "
                    f"| {row['from']} | {row['to']} | {row['distinct_name_raw']} | {row['fcst_pct']} |")
    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {json_path.relative_to(ROOT)} and {md_path.relative_to(ROOT)}")


def main() -> int:
    audit = run()
    print_table(audit["results"])
    print_flags_section(audit["results"])
    write_reports(audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
