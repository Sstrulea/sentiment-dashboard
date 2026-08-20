#!/usr/bin/env python3
"""FAZA 0.5 — forensics on flagged_bad + POLICY-role resolution (read-only).

Reuses audit_history_catalog.py's resolution helpers BY IMPORT (build_pool,
rank_pool, resolve_one, compute_metrics, load_parquet, load_raw_universe,
CANDIDATES, ...) — no resolution logic is re-derived here. This script only
adds NEW analysis on top: raw-field forensics for flagged_bad (Block A),
POLICY-role candidate resolution across all 3 raw layers (Block B), 4
targeted gap investigations (Block C), and a depth/staleness calibration
pass (Block D).

Zero writes under data/, zero src/ changes, zero network, zero merge.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_history_catalog as ph0  # noqa: E402

from src.econ_calendar_ff import extract_period_suffix, iso_to_utc, jblanked_to_utc  # noqa: E402
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.ff_scoring import detect_cadence  # noqa: E402
from src.jb_actuals import ARCHIVE_JSON, RAW_DIR, build_flagged_bad_lookup  # noqa: E402

FF_RAW_DIR = ROOT / "data" / "ff_raw"
REPORTS_DIR = ROOT / "reports"


# ---------------------------------------------------------------------------
# Shared setup — resolves the FAZA 0 candidates once via ph0.resolve_one
# ---------------------------------------------------------------------------

def setup() -> dict:
    ind_cfg = ph0.load_indicators_cfg()
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}) or {})
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    flagged_bad = build_flagged_bad_lookup()
    ff = ph0.load_parquet()
    raw_uni = ph0.load_raw_universe()
    pools = {ccy: ph0.build_pool(ccy, ff, raw_uni) for ccy in ph0.CCYS}
    resolved = {}
    for cat, ccy, rol, desc in ph0.CANDIDATES:
        r = ph0.resolve_one(cat, ccy, rol, desc, ff, raw_uni, pools, matcher,
                            indicators, defaults, flagged_bad)
        resolved[(cat, ccy, rol)] = r
    return {"ind_cfg": ind_cfg, "matcher": matcher, "defaults": defaults,
            "indicators": indicators, "flagged_bad": flagged_bad, "ff": ff,
            "raw_uni": raw_uni, "pools": pools, "resolved": resolved}


def load_raw_events() -> dict[str, list[dict]]:
    def _load(paths):
        out = []
        for p in paths:
            try:
                out += json.loads(Path(p).read_text())
            except Exception:  # noqa: BLE001
                pass
        return out
    return {
        "archive": _load([ARCHIVE_JSON]),
        "jb_raw": _load(sorted(RAW_DIR.glob("jb_range_*.json"))),
        "ff_raw": _load(sorted(FF_RAW_DIR.glob("*.json"))),
    }


def build_raw_key_index(raw_events: dict) -> dict:
    """(currency, name_raw, UTC date) -> [(Quality, Strength, source_file), ...].
    Scans ONLY archive + jb_raw — the same two sources build_flagged_bad_lookup
    itself scans (ff_raw carries no Quality/Strength field at all)."""
    idx: dict = defaultdict(list)
    for src_name in ("archive", "jb_raw"):
        for e in raw_events[src_name]:
            dt = jblanked_to_utc(e.get("Date", ""))
            if dt is None:
                continue
            ccy = str(e.get("Currency", "")).strip()
            nr = str(e.get("Name", "")).strip()
            idx[(ccy, nr, dt.date())].append((e.get("Quality"), e.get("Strength"), src_name))
    return idx


# ---------------------------------------------------------------------------
# BLOCK A — flagged_bad forensics
# ---------------------------------------------------------------------------

def a1_report() -> str:
    return """
A1. Unde se construiește flagged_bad:
    src/jb_actuals.py:227 build_flagged_bad_lookup(raw_dir=RAW_DIR, archive_path=ARCHIVE_JSON)
    Surse: data/archive/ff_calendar_range.json + data/jb_raw/jb_range_*.json (NU data/ff_raw/ —
    FF weekly n-are Quality/Strength deloc, confirmat empiric mai jos).
    Camp brut consultat: "Quality" si "Strength" (jb_actuals.py:270-273).
    Valori tratate ca BAD (jb_actuals.py:200-203, whitelist, fail-loud pe orice altă valoare):
        QUALITY_ACCEPTED = {"Good Data"}
        QUALITY_FLAGGED  = {"Bad Data", "Data Not Loaded"}
        STRENGTH_ACCEPTED = {"Strong Data", "Weak Data"}
        STRENGTH_FLAGGED  = {"Data Not Loaded"}
    Regula per eveniment (jb_actuals.py:206-224 _field_verdict + 263-279):
        bad = (Quality in FLAGGED) OR (Strength in FLAGGED)
        cheie lookup = (currency, name_raw, UTC release date)
        MISSING key -> "no signal" (nu False) — consumatorul decide default-ul.
"""


def a2_field_census(raw_events: dict) -> dict:
    def field_census(events, field):
        present = Counter()
        absent = nullc = emptyc = 0
        for e in events:
            if field not in e:
                absent += 1
            else:
                v = e[field]
                if v is None:
                    nullc += 1
                elif isinstance(v, str) and v.strip() == "":
                    emptyc += 1
                else:
                    present[v] += 1
        return present, absent, nullc, emptyc

    out = {}
    for src_name, evs in raw_events.items():
        out[src_name] = {"n_events": len(evs)}
        for field in ("Quality", "Strength"):
            present, absent, nullc, emptyc = field_census(evs, field)
            out[src_name][field] = {"present": dict(present), "absent": absent,
                                    "null": nullc, "empty_string": emptyc}
    cross = Counter()
    for e in raw_events["archive"] + raw_events["jb_raw"]:
        cross[(e.get("Quality"), e.get("Strength"))] += 1
    out["cross_tab_archive_plus_jbraw"] = {f"{q}|{s}": n for (q, s), n in
                                           sorted(cross.items(), key=lambda kv: -kv[1])}

    # sentiment-vs-corruption check: does Quality correlate with Outcome direction?
    outcome_by_quality: dict = defaultdict(Counter)
    for e in raw_events["archive"]:
        outcome_by_quality[e.get("Quality")][e.get("Outcome", "")] += 1
    out["outcome_by_quality_archive_top6"] = {
        q: dict(c.most_common(6)) for q, c in outcome_by_quality.items()
    }
    return out


def a3_denominator(ctx: dict, raw_events: dict) -> dict:
    ff = ctx["ff"]
    flagged_bad = ctx["flagged_bad"]
    raw_idx = build_raw_key_index(raw_events)
    n_total = len(ff)
    n_signal = 0
    n_in_raw_idx = 0
    for r in ff.itertuples():
        key = (r.currency, r.name_raw, pd.Timestamp(r.datetime_utc).date())
        if key in raw_idx:
            n_in_raw_idx += 1
        if flagged_bad.get(key) is not None:
            n_signal += 1
    return {
        "n_total_parquet_rows": n_total,
        "n_with_matching_raw_event": n_in_raw_idx,
        "n_with_quality_signal": n_signal,
        "join_rate_pct": round(100 * n_in_raw_idx / n_total, 1),
        "signal_rate_pct": round(100 * n_signal / n_total, 1),
        "join_key": "(currency, name_raw, UTC release date)",
    }


def a4_consumers() -> str:
    return """
A4. Consumatori (grep flagged_bad in src/ — EXACT 2 fisiere il consuma):

  1) src/ff_scoring.py to_scoring_frame() — calea de scoring principala.
     - linia 180-189 (actual==0.0 CANDIDATE): gate-ul se aplica DOAR randurilor cu
       actual==0.0 (fie can_be_zero=true, fie suffix m/m|q/q widening). Un rand cu
       actual NENUL nu atinge NICIODATA flagged_bad.
     - linia 197-213 (consensus==0.0 CANDIDATE): identic, DOAR pentru consensus==0.0.
     => (c) DOAR gate pe calea de zero-widening. NU (a) sigma, NU (b) scor general.

  2) src/manual_actuals.py _zero_passes_widening() (linia 136-157), consumat de:
     - _raw_actionable_rows() linia 183-188: DOAR cand r.actual==0.0 (stare ZERO_CONFIRM
       intr-un panou de "randuri care necesita actiune manuala").
     - _has_valid_sibling_same_day() linia 217-221: DOAR cand un rand-sibling are
       actual==0.0, ca sa decida daca acel sibling e "valid".
     => Acelasi gate (c), alta cale de consum (panoul de override manual), tot
        restrans strict la actual==0.0.

  VERDICT: flagged_bad NU exclude NICIODATA un rand din sigma (fereastra de 12
  printuri) sau din scorul afisat, pentru NICIUN print cu actual != 0.0 — ceea ce
  inseamna marea majoritate a datelor. Metrica BAD% din FAZA 0, calculata pe TOATE
  randurile seriei indiferent de valoare, masoara altceva decat ceea ce productia
  chiar consulta.
"""


def bad_pct_variants(subset: pd.DataFrame, ccy: str, flagged_bad: dict):
    printed = subset[subset["actual"].notna()]
    if printed.empty:
        return None, None, 0, 0
    sig = [flagged_bad.get((ccy, nr, pd.Timestamp(d).date()))
          for nr, d in zip(printed["name_raw"], printed["datetime_utc"])]
    present = [s for s in sig if s is not None]
    bad_signal = round(100 * sum(present) / len(present), 1) if present else None
    bad_all = round(100 * sum(1 for s in sig if s is True) / len(sig), 1)
    return bad_signal, bad_all, len(printed), len(present)


def a5_contrafactual(ctx: dict) -> list[dict]:
    rows = []
    for (cat, ccy, rol), r in ctx["resolved"].items():
        if r["_is_raw_only"] or r["metrics"]["n"] == 0:
            continue
        subset = r["_subset"]
        bad_signal, bad_all, n, n_signal = bad_pct_variants(subset, ccy, ctx["flagged_bad"])
        m_gated = r["metrics"]
        expected_suffix = ph0._expected_suffix(r["desc"])
        m_ungated = ph0.compute_metrics(subset, ccy, False, {}, expected_suffix)
        rows.append({
            "cat": cat, "ccy": ccy, "rol": rol, "key": r["key"], "n": n,
            "zero_pct": m_gated["zero_pct"],
            "bad_pct_signal_denom": bad_signal, "n_with_signal": n_signal,
            "bad_pct_all_denom": bad_all,
            "revder_pct_gated": m_gated["revder_pct"],
            "revrate_pct_gated": m_gated["revrate_pct"],
            "revder_pct_ungated": m_ungated["revder_pct"],
            "revrate_pct_ungated": m_ungated["revrate_pct"],
        })
    return rows


def a6_sample_rows(ctx: dict, raw_events: dict) -> dict:
    raw_idx = build_raw_key_index(raw_events)
    out = {}
    for label, rkey in (("USD cpi_yoy", ("inflation", "USD", "PRIM")),
                        ("USD core_cpi", ("inflation", "USD", "SEC"))):
        r = ctx["resolved"][rkey]
        subset = r["_subset"].sort_values("datetime_utc")
        printed = subset[subset["actual"].notna()]
        rows = []
        for row in printed.itertuples():
            d = pd.Timestamp(row.datetime_utc).date()
            key = ("USD", row.name_raw, d)
            bad = ctx["flagged_bad"].get(key)
            if bad is True:
                hits = raw_idx.get(key, [])
                rows.append({
                    "release_dt": str(row.datetime_utc), "actual": row.actual,
                    "forecast": row.forecast, "previous": row.previous,
                    "name_raw": row.name_raw,
                    "raw_quality_strength_source": hits,
                })
        out[label] = rows
    return out


# ---------------------------------------------------------------------------
# BLOCK B — POLICY role resolution (all 3 raw layers)
# ---------------------------------------------------------------------------

POLICY_KEYWORDS = {
    "USD": ["PCE"], "EUR": ["HICP", "CPI"], "GBP": ["CPI"], "JPY": ["CPI"],
    "CAD": ["CPI"], "AUD": ["CPI"], "NZD": ["CPI"], "CHF": ["CPI"],
}
POLICY_EXCLUDE_TERMS = ("core", "median", "trimmed", "common", "tokyo", "boj core")


def all_names_for_currency(ccy: str, ff: pd.DataFrame, raw_uni: pd.DataFrame) -> set[str]:
    names = set(ff[ff["currency"] == ccy]["name_raw"].dropna().unique())
    names |= set(ff[ff["currency"] == ccy]["name_canonical"].dropna().unique())
    if len(raw_uni):
        names |= set(raw_uni[raw_uni["currency"] == ccy]["name_raw"].dropna().unique())
    return names


def _name_stats(ccy: str, name: str, ff: pd.DataFrame, raw_uni: pd.DataFrame) -> dict:
    in_parquet_raw = name in set(ff[ff["currency"] == ccy]["name_raw"])
    in_parquet_canon = name in set(ff[ff["currency"] == ccy]["name_canonical"])
    if in_parquet_raw:
        sub = ff[(ff["currency"] == ccy) & (ff["name_raw"] == name)]
        layer = "parquet(aliased,raw)"
        feeding_raw_names = [name]
    elif in_parquet_canon:
        sub = ff[(ff["currency"] == ccy) & (ff["name_canonical"] == name)]
        layer = "parquet(aliased,canonical)"
        feeding_raw_names = sorted(sub["name_raw"].dropna().unique().tolist())
    else:
        sub = None
        layer = "raw_only(never aliased)"
        feeding_raw_names = [name]

    if sub is not None:
        printed = sub[sub["actual"].notna()]
        n = len(printed)
        stats = {
            "n": n,
            "from": printed["datetime_utc"].min().date().isoformat() if n else None,
            "to": printed["datetime_utc"].max().date().isoformat() if n else None,
            "actual_min": round(float(printed["actual"].min()), 3) if n else None,
            "actual_median": round(float(printed["actual"].median()), 3) if n else None,
            "actual_max": round(float(printed["actual"].max()), 3) if n else None,
            "freq_emp": detect_cadence(printed["datetime_utc"]) if n >= 2 else "unknown",
        }
    else:
        rsub = raw_uni[(raw_uni["currency"] == ccy) & (raw_uni["name_raw"] == name)]
        n = len(rsub)
        stats = {
            "n": n,
            "from": rsub["dt"].min().date().isoformat() if n else None,
            "to": rsub["dt"].max().date().isoformat() if n else None,
            "actual_min": None, "actual_median": None, "actual_max": None,
            "freq_emp": detect_cadence(rsub["dt"]) if n >= 2 else "unknown",
        }
    try:
        suffix = extract_period_suffix(name)
    except ValueError:
        suffix = "unknown"
    return {"name": name, "layer": layer, "suffix": suffix,
            "feeding_raw_names": feeding_raw_names, **stats}


def _is_headline(d: dict) -> bool:
    """True iff neither the searched name NOR any raw name_raw actually feeding
    it (relevant when `name` is a canonical label — the canonical text can read
    as headline while the real underlying print is a core/median/trimmed/tokyo
    measure, e.g. JPY canonical 'CPI y/y' fed by raw 'National Core CPI y/y')
    contains a core/median/trimmed/common measure-modifier term."""
    texts = [d["name"]] + d["feeding_raw_names"]
    return not any(t in txt.lower() for txt in texts for t in POLICY_EXCLUDE_TERMS)


def _is_true_yoy(d: dict) -> bool:
    """True iff EVERY raw name_raw actually feeding this entry is ALSO y/y by its
    own real transform (extract_period_suffix) — catches a canonical label that
    reads 'CPI y/y' while its only real feed is an m/m or q/q print (CAD cpi_yoy
    fed by 'CPI m/m', NZD's canonical 'CPI y/y' fed only by 'CPI q/q'). A
    raw-layer entry has feeding_raw_names == [name], so this reduces to
    checking the entry's own suffix."""
    for nm in d["feeding_raw_names"]:
        try:
            if extract_period_suffix(nm) != "y/y":
                return False
        except ValueError:
            return False
    return True


def band_verdict(detail: list[dict]) -> tuple[str, dict | None]:
    candidates = [d for d in detail if d["suffix"] == "y/y"
                 and d["layer"].startswith("parquet(aliased")
                 and _is_headline(d) and _is_true_yoy(d)]
    if not candidates:
        return "BAND_TEXT_ONLY", None
    best = max(candidates, key=lambda d: d["n"] or 0)
    if not best["from"] or not best["to"]:
        return "BAND_TEXT_ONLY", best
    yrs = (date.fromisoformat(best["to"]) - date.fromisoformat(best["from"])).days / 365.25
    return ("BAND_OK" if yrs >= 3.0 else "BAND_TEXT_ONLY"), best


def block_b(ctx: dict) -> dict:
    ff, raw_uni = ctx["ff"], ctx["raw_uni"]
    out = {}
    for ccy, kws in POLICY_KEYWORDS.items():
        names = all_names_for_currency(ccy, ff, raw_uni)
        hits = sorted(n for n in names if any(kw.lower() in n.lower() for kw in kws))
        detail = [_name_stats(ccy, n, ff, raw_uni) for n in hits]
        verdict, best = band_verdict(detail)
        out[ccy] = {"detail": detail, "verdict": verdict, "best": best}
    return out


# ---------------------------------------------------------------------------
# BLOCK C — targeted gaps
# ---------------------------------------------------------------------------

def c1_chf_rate_scan(raw_events: dict) -> dict:
    cutoff = "2025.06.19"
    hits = defaultdict(str)  # name -> last seen date string
    for src_name, evs in raw_events.items():
        ccy_field, name_field, date_field = (
            ("Currency", "Name", "Date") if src_name != "ff_raw" else ("country", "title", "date"))
        for e in evs:
            if str(e.get(ccy_field, "")).strip() != "CHF":
                continue
            n = str(e.get(name_field, "")).strip()
            d = str(e.get(date_field, ""))
            d_norm = d.replace("-", ".").replace("T", " ")[:10] if src_name == "ff_raw" else d[:10]
            if d_norm > hits[n]:
                hits[n] = d_norm
    rate_related = {n: d for n, d in hits.items()
                    if d > cutoff and ("rate" in n.lower() or "snb" in n.lower())}
    return {"cutoff": cutoff, "rate_or_snb_related_names_after_cutoff": rate_related,
            "snb_policy_rate_last_seen": hits.get("SNB Policy Rate")}


def c2_usd_gdp_census(ctx: dict) -> list[dict]:
    r = ctx["resolved"][("growth", "USD", "PRIM")]
    subset = r["_subset"].sort_values("datetime_utc")
    printed = subset[subset["actual"].notna()]
    rows = []
    for nr, g in printed.groupby("name_raw"):
        rows.append({"name_raw": nr, "n": len(g),
                    "from": g["datetime_utc"].min().date().isoformat(),
                    "to": g["datetime_utc"].max().date().isoformat()})
    dates = sorted(printed["datetime_utc"].tolist())
    gaps = []
    for i in range(1, len(dates)):
        gap_days = (dates[i] - dates[i - 1]).days
        if gap_days > 130:  # > ~1 quarter+slack -> a missed quarter
            gaps.append({"after": str(dates[i - 1]), "before": str(dates[i]), "gap_days": gap_days})
    return {"by_name_raw": rows, "suspicious_gaps_gt_130d": gaps}


def c3_aud_trimmed_mean_monthly(ctx: dict) -> dict:
    ff = ctx["ff"]
    matcher = ctx["matcher"]
    from src.ff_scoring import CCY2COUNTRY
    aud = ff[ff["currency"] == "AUD"].copy()
    aud["indicator_key"] = aud["name_canonical"].apply(
        lambda nc: matcher.match(CCY2COUNTRY["AUD"], nc))
    subset = aud[aud["indicator_key"] == "trimmed_mean_cpi_monthly"]
    metrics = ph0.compute_metrics(subset, "AUD", False, ctx["flagged_bad"], "m/m")
    return metrics


def c4_revision_validation(ctx: dict) -> dict:
    def revision_pair_detail(subset: pd.DataFrame) -> list[dict]:
        printed = subset[subset["actual"].notna()].sort_values("datetime_utc").reset_index(drop=True)
        pairs = []
        for i in range(len(printed) - 1):
            a_n = printed.loc[i, "actual"]
            prev_next = printed.loc[i + 1, "previous"]
            if pd.isna(a_n) or pd.isna(prev_next):
                continue
            delta = float(prev_next) - float(a_n)
            rel = delta / abs(float(a_n)) if a_n != 0 else None
            pairs.append({
                "release_dt_N": str(printed.loc[i, "datetime_utc"]),
                "release_dt_N1": str(printed.loc[i + 1, "datetime_utc"]),
                "actual_N": round(float(a_n), 4), "previous_N1": round(float(prev_next), 4),
                "delta_abs": round(delta, 4),
                "delta_rel_pct": round(rel * 100, 1) if rel is not None else None,
            })
        pairs.sort(key=lambda p: -abs(p["delta_abs"]))
        return pairs

    flagged_watch = {"employment_change", "retail_sales", "gdp_qoq"}
    out = {}
    zero_rev_flags = []
    for (cat, ccy, rol), r in ctx["resolved"].items():
        if r["_is_raw_only"] or r["metrics"]["n"] == 0:
            continue
        pairs = revision_pair_detail(r["_subset"])
        out[f"{cat}/{ccy}/{rol} ({r['key']})"] = pairs[:5]
        if r.get("indicator_key") in flagged_watch and (r["metrics"]["revrate_pct"] == 0.0):
            zero_rev_flags.append({
                "series": f"{cat}/{ccy}/{rol}", "key": r["key"],
                "revder_pct": r["metrics"]["revder_pct"],
                "revrate_pct": r["metrics"]["revrate_pct"], "n": r["metrics"]["n"],
            })
    return {"top5_by_series": out, "zero_revrate_on_watchlist": zero_rev_flags}


# ---------------------------------------------------------------------------
# BLOCK D — depth / staleness calibration
# ---------------------------------------------------------------------------

CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365}


def d_depth_calibration(ctx: dict) -> list[dict]:
    as_of = pd.Timestamp.now().normalize()
    rows = []
    for (cat, ccy, rol), r in ctx["resolved"].items():
        m = r["metrics"]
        if m["n"] == 0:
            rows.append({"cat": cat, "ccy": ccy, "rol": rol, "key": r["key"],
                        "n_total": 0, "note": "no printed rows resolved"})
            continue
        subset = r["_subset"]
        dtcol = "dt" if r["_is_raw_only"] else "datetime_utc"
        acol = "actual_raw" if r["_is_raw_only"] else "actual"
        printed = subset[subset[acol].notna()] if not r["_is_raw_only"] else \
            subset[subset[acol].apply(lambda v: v is not None and str(v).strip() != "")]
        dates = pd.to_datetime(printed[dtcol])
        first, last = dates.min(), dates.max()
        depth_months = round((last - first).days / 30.44, 1)
        n12 = int((dates >= as_of - pd.Timedelta(days=365)).sum())
        n24 = int((dates >= as_of - pd.Timedelta(days=730)).sum())
        n36 = int((dates >= as_of - pd.Timedelta(days=1095)).sum())
        cad_days = CADENCE_DAYS.get(m["freq_emp"])
        stale = None
        if cad_days:
            stale = bool((as_of - last).days > 2 * cad_days)
        rows.append({
            "cat": cat, "ccy": ccy, "rol": rol, "key": r["key"],
            "first": first.date().isoformat(), "last": last.date().isoformat(),
            "depth_months": depth_months, "n_total": len(printed),
            "n_last12mo": n12, "n_last24mo": n24, "n_last36mo": n36,
            "freq_emp": m["freq_emp"], "stale": stale,
        })
    return rows


def d_absolute_floor(raw_events: dict, ff: pd.DataFrame) -> dict:
    archive_dts = [jblanked_to_utc(e.get("Date", "")) for e in raw_events["archive"]]
    archive_dts = [d for d in archive_dts if d is not None]
    jbraw_dts = [jblanked_to_utc(e.get("Date", "")) for e in raw_events["jb_raw"]]
    jbraw_dts = [d for d in jbraw_dts if d is not None]
    ffraw_dts = [iso_to_utc(e.get("date", "")) for e in raw_events["ff_raw"]]
    ffraw_dts = [d for d in ffraw_dts if d is not None]
    candidates = [ff["datetime_utc"].min()] + archive_dts + jbraw_dts + ffraw_dts
    return {"absolute_earliest_local_date": min(candidates).date().isoformat()}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_block_a(a2, a3, a5, a6):
    print("=" * 90)
    print("BLOC A — flagged_bad forensics")
    print("=" * 90)
    print(a1_report())

    print("A2. Census pe campurile brute (per sursa):")
    for src_name in ("archive", "jb_raw", "ff_raw"):
        d = a2[src_name]
        print(f"  --- {src_name} (n={d['n_events']}) ---")
        for field in ("Quality", "Strength"):
            f = d[field]
            print(f"    {field}: present={f['present']}")
            print(f"      absent={f['absent']}  null={f['null']}  empty_str={f['empty_string']}")
    print("\n  Cross-tab Quality x Strength (archive+jb_raw):")
    for k, v in a2["cross_tab_archive_plus_jbraw"].items():
        print(f"    {k}: {v}")
    print("\n  Outcome text by Quality (archive, top6) — verifica daca Bad Data ~ 'a ratat forecast':")
    for q, outc in a2["outcome_by_quality_archive_top6"].items():
        print(f"    Quality={q}:")
        for o, n in outc.items():
            print(f"      {n:6d}  {o!r}")

    print("\nA3. Denominator / rata de join parquet<->raw:")
    for k, v in a3.items():
        print(f"    {k}: {v}")

    print("\nA5. Contrafactual BAD%/REVDER%/REVRATE% (gated vs ungated), per serie PRIM/SEC:")
    hdr = f"{'CAT/CCY/ROL':<22}{'KEY':<24}{'N':>5}{'BAD%sig':>9}{'BAD%all':>9}{'RVD%g':>8}{'RVR%g':>8}{'RVD%u':>8}{'RVR%u':>8}"
    print("  " + hdr)
    for row in a5:
        label = f"{row['cat']}/{row['ccy']}/{row['rol']}"
        print("  " + f"{label:<22}{str(row['key'])[:23]:<24}{row['n']:>5}"
              f"{str(row['bad_pct_signal_denom']):>9}{str(row['bad_pct_all_denom']):>9}"
              f"{str(row['revder_pct_gated']):>8}{str(row['revrate_pct_gated']):>8}"
              f"{str(row['revder_pct_ungated']):>8}{str(row['revrate_pct_ungated']):>8}")

    print("\nA6. Toate randurile FLAGGED (Bad) pentru USD cpi_yoy si USD core_cpi:")
    for label, rows in a6.items():
        print(f"  --- {label} ({len(rows)} randuri flagate) ---")
        for row in rows:
            print(f"    {row['release_dt']}  A={row['actual']} F={row['forecast']} P={row['previous']}"
                  f"  name_raw={row['name_raw']!r}  raw_hits={row['raw_quality_strength_source']}")


def print_block_b(ctx, b):
    print()
    print("=" * 90)
    print("BLOC B — POLICY role resolution")
    print("=" * 90)
    for ccy, d in b.items():
        print(f"\n--- {ccy}  verdict={d['verdict']} ---")
        if d["best"]:
            print(f"  best: {d['best']}")
        for entry in d["detail"]:
            fed_by = "" if entry["feeding_raw_names"] == [entry["name"]] else \
                f"  fed_by={entry['feeding_raw_names']}"
            print(f"    [{entry['layer']:<24}] suffix={entry['suffix']:<8} n={entry['n']:>3} "
                  f"{entry['from']}..{entry['to']}  actual[min/med/max]="
                  f"{entry['actual_min']}/{entry['actual_median']}/{entry['actual_max']}  "
                  f"{entry['name']!r}{fed_by}")

    print("\nMatrice finala: 8 valute x {market resolvat, policy resolvat, verdict banda}")
    print(f"  {'CCY':<5}{'MARKET_KEY (inflation PRIM)':<30}{'POLICY_VERDICT':<16}{'POLICY_BEST_NAME'}")
    for ccy in ph0.CCYS:
        market = ctx["resolved"].get(("inflation", ccy, "PRIM"))
        market_key = market["key"] if market else "-"
        d = b[ccy]
        best_name = d["best"]["name"] if d["best"] else "-"
        print(f"  {ccy:<5}{str(market_key):<30}{d['verdict']:<16}{best_name!r}")


def print_block_c(c1, c2, c3, c4):
    print()
    print("=" * 90)
    print("BLOC C — goluri tintite")
    print("=" * 90)
    print("\nC1. CHF interest_rate_decision — scan toate straturile dupa 2025-06-19:")
    print(f"  SNB Policy Rate ultima aparitie: {c1['snb_policy_rate_last_seen']}")
    print("  Nume conexe Rate/SNB vazute DUPA cutoff (orice strat):")
    for n, d in sorted(c1["rate_or_snb_related_names_after_cutoff"].items(), key=lambda kv: -1):
        print(f"    {n!r}: ultima aparitie {d}")

    print("\nC2. USD gdp_qoq — census name_raw + gap-uri suspecte:")
    for row in c2["by_name_raw"]:
        print(f"    {row['name_raw']!r}: n={row['n']}  {row['from']}..{row['to']}")
    if c2["suspicious_gaps_gt_130d"]:
        for g in c2["suspicious_gaps_gt_130d"]:
            print(f"    GAP: {g['after']} -> {g['before']} ({g['gap_days']} zile)")
    else:
        print("    (niciun gap > 130 zile)")

    print("\nC3. AUD trimmed_mean_cpi_monthly (inlocuitor live gasit in FAZA 0):")
    for k, v in c3.items():
        if k not in ("distinct_names", "rev_abs", "rev_sigma"):
            print(f"    {k}: {v}")

    print("\nC4. Top 5 revizii ca magnitudine, per serie (doar cele cu >=1 pereche):")
    for label, pairs in c4["top5_by_series"].items():
        if not pairs:
            continue
        print(f"  --- {label} ---")
        for p in pairs:
            print(f"    N={p['release_dt_N']}  actual[N]={p['actual_N']}  "
                  f"-> previous[N+1] ({p['release_dt_N1']})={p['previous_N1']}  "
                  f"delta={p['delta_abs']} ({p['delta_rel_pct']}%)")
    print("\n  Serii pe watchlist (NFP/employment/retail/GDP) cu REVRATE=0%:")
    if not c4["zero_revrate_on_watchlist"]:
        print("    (niciuna)")
    for f in c4["zero_revrate_on_watchlist"]:
        print(f"    {f['series']} ({f['key']}): REVDER%={f['revder_pct']} REVRATE%={f['revrate_pct']} n={f['n']}")


def print_block_d(d_rows, d_floor):
    print()
    print("=" * 90)
    print("BLOC D — adancime reala / calibrare")
    print("=" * 90)
    hdr = (f"{'CAT/CCY/ROL':<23} {'KEY':<25} {'FIRST':<11} {'LAST':<11} "
          f"{'MO':>6} {'N':>4} {'N12':>4} {'N24':>4} {'N36':>4} {'FREQ':<10} {'STALE'}")
    print(hdr)
    for r in d_rows:
        label = f"{r['cat']}/{r['ccy']}/{r['rol']}"
        if r.get("n_total", 0) == 0 and "first" not in r:
            print(f"{label:<23} {str(r['key'])[:25]:<25} -- no printed rows --")
            continue
        print(f"{label:<23} {str(r['key'])[:25]:<25} {r['first']:<11} {r['last']:<11} "
              f"{r['depth_months']:>6} {r['n_total']:>4} {r['n_last12mo']:>4} {r['n_last24mo']:>4} "
              f"{r['n_last36mo']:>4} {r['freq_emp']:<10} {r['stale']}")
    print(f"\nPlafon absolut de istoric local (orice strat): {d_floor['absolute_earliest_local_date']}")


def write_reports(payload: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "history_phase05_audit.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {path.relative_to(ROOT)}")


def main() -> int:
    ctx = setup()
    raw_events = load_raw_events()

    a2 = a2_field_census(raw_events)
    a3 = a3_denominator(ctx, raw_events)
    a5 = a5_contrafactual(ctx)
    a6 = a6_sample_rows(ctx, raw_events)
    print_block_a(a2, a3, a5, a6)

    b = block_b(ctx)
    print_block_b(ctx, b)

    c1 = c1_chf_rate_scan(raw_events)
    c2 = c2_usd_gdp_census(ctx)
    c3 = c3_aud_trimmed_mean_monthly(ctx)
    c4 = c4_revision_validation(ctx)
    print_block_c(c1, c2, c3, c4)

    d_rows = d_depth_calibration(ctx)
    d_floor = d_absolute_floor(raw_events, ctx["ff"])
    print_block_d(d_rows, d_floor)

    payload = {
        "block_a": {"a2_field_census": a2, "a3_denominator": a3, "a5_contrafactual": a5, "a6_samples": a6},
        "block_b": b,
        "block_c": {"c1_chf_rate_scan": c1, "c2_usd_gdp_census": c2, "c3_aud_trimmed_mean_monthly": c3,
                    "c4_revision_validation": c4},
        "block_d": {"depth_calibration": d_rows, "absolute_floor": d_floor},
    }
    write_reports(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
