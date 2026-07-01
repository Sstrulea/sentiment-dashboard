"""Guarded FRED fallback for the MT5 economic calendar.

PRINCIPLE: MT5 is PRIMARY. FRED fills ONLY a genuine source gap — a period whose
MT5 `actual` is missing/NaN — and ONLY when the FRED series provably CONTINUES the
MT5 series. FRED never overrides a present MT5 actual and never fabricates.

CONTINUITY GUARD (per series, every refresh):
  - Build the MT5 ground-truth from the parquet itself: {period -> published actual}
    for this (currency, indicator). No hardcoded values.
  - For each candidate FRED series, transform to the MT5 unit (QoQ%/MoM%) and align
    on the overlapping periods.
  - ACCEPT a candidate only if it overlaps the MT5 series on >= MIN_OVERLAP periods
    AND every overlapping point matches within TOL_PP (0.1pp). Among accepting
    candidates pick the closest (auto-selects variants, e.g. CHF sporting-event-
    adjusted vs not).
  - Then fill ONLY the gap periods (MT5 actual NaN) from the accepted series, reusing
    the scheduled MT5 row's release_dt + consensus. If no candidate accepts -> REFUSE,
    log the reason, leave the gap (accept-degradation). Idempotent (dedup last-write-wins).

Reuses the existing IPv4-pinned FRED client (rate_sources.FredSeriesSource).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .rate_sources import FredSeriesSource

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "economic_calendar.parquet"

TOL_PP = 0.1          # max allowed |MT5 − FRED| on overlapping points (percentage points)
MIN_OVERLAP = 4       # need at least this many overlapping published periods to trust a match

CALENDAR_COLUMNS = [
    "release_dt", "country", "currency", "indicator_key", "actual",
    "consensus", "previous", "unit", "event_raw", "period", "source",
]


@dataclass
class Candidate:
    series_id: str
    transform: str  # "growth_as_is" | "level_qoq" | "level_mom"
    label: str = ""


@dataclass
class Target:
    currency: str
    indicator_key: str
    candidates: list[Candidate]
    # human note for residual reporting when refused
    note: str = ""


# --- Targets: only genuine MT5 source gaps (see /tmp/inspectie_growth.md) -------
# CHF lists the sporting-event-ADJUSTED variant first (auto-select prefers the
# closest match); the unadjusted is the fallback variant.
TARGETS: list[Target] = [
    Target("JPY", "gdp_qoq", [
        Candidate("NAEXKP01JPQ657S", "growth_as_is", "OECD Real GDP QoQ"),
        Candidate("JPNRGDPEXP", "level_qoq", "Real GDP (expenditure) levels"),
    ], note="Japan final GDP q/q"),
    Target("CHF", "gdp_qoq", [
        Candidate("CLVMNACSCAB1GQCH", "level_qoq", "SECO Real GDP SA levels (unadjusted)"),
        Candidate("NAEXKP01CHQ657S", "growth_as_is", "OECD Real GDP QoQ (unadjusted)"),
    ], note="Switzerland GDP q/q (sporting-event-adjusted variant not on FRED)"),
    Target("AUD", "retail_sales", [
        Candidate("AUSSARTMISMEI", "level_mom", "OECD Retail Trade index"),
    ], note="Australia Retail Sales m/m (MT5 series discontinued after 2025-07)"),
]


# ---------------------------------------------------------------------------
# Pure transforms + guard
# ---------------------------------------------------------------------------

def _to_period_value(df: pd.DataFrame, transform: str) -> dict[pd.Timestamp, float]:
    """FRED tidy frame (date, value) -> {period_start -> value in MT5 units}."""
    d = df.dropna(subset=["value"]).copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d = d.sort_values("date")
    vals = pd.to_numeric(d["value"], errors="coerce")
    if transform == "growth_as_is":
        out = vals
        idx = d["date"]
    elif transform in ("level_qoq", "level_mom"):
        out = vals.pct_change() * 100.0
        idx = d["date"]
    else:
        raise ValueError(f"unknown transform {transform!r}")
    return {pd.Timestamp(t).normalize(): float(v)
            for t, v in zip(idx, out) if pd.notna(v)}


def mt5_ground_truth(sub: pd.DataFrame) -> dict[pd.Timestamp, float]:
    """{period_start -> published MT5 actual} for one (currency, indicator)."""
    out: dict[pd.Timestamp, float] = {}
    for _, r in sub.iterrows():
        per = _parse_period(r.get("period"))
        act = pd.to_numeric(r.get("actual"), errors="coerce")
        if per is not None and pd.notna(act):
            out[per] = float(act)  # last wins (parquet already deduped/sorted)
    return out


def _parse_period(p) -> Optional[pd.Timestamp]:
    if p is None:
        return None
    s = str(p).strip()
    if not s or s.lower() in {"nan", "nat", "none", "0", "0.0"}:
        return None
    ts = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(ts) else pd.Timestamp(ts).normalize()


def evaluate_candidate(mt5: dict[pd.Timestamp, float],
                       fred: dict[pd.Timestamp, float],
                       tol: float = TOL_PP,
                       min_overlap: int = MIN_OVERLAP) -> dict:
    """Return {accepted, n_overlap, max_diff} for one candidate vs the MT5 series."""
    common = sorted(set(mt5) & set(fred))
    if len(common) < min_overlap:
        return {"accepted": False, "n_overlap": len(common), "max_diff": None,
                "reason": f"only {len(common)} overlapping periods (<{min_overlap})"}
    diffs = [abs(mt5[p] - fred[p]) for p in common]
    max_diff = max(diffs)
    accepted = max_diff <= tol
    return {"accepted": accepted, "n_overlap": len(common), "max_diff": max_diff,
            "reason": (f"max |Δ|={max_diff:.3f}pp "
                       f"{'≤' if accepted else '>'} {tol}pp over {len(common)} periods")}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _gap_periods(sub: pd.DataFrame, mt5: dict) -> dict[pd.Timestamp, pd.Series]:
    """Periods that have an MT5 SCHEDULED row (actual NaN) but no published actual —
    the fillable gaps. Returns {period -> the scheduled row} (latest per period)."""
    gaps: dict[pd.Timestamp, pd.Series] = {}
    for _, r in sub.sort_values("release_dt").iterrows():
        per = _parse_period(r.get("period"))
        act = pd.to_numeric(r.get("actual"), errors="coerce")
        if per is not None and pd.isna(act) and per not in mt5:
            gaps[per] = r  # keep latest scheduled row for that period
    return gaps


def apply_fred_fallback(parquet_path: Path = PARQUET,
                        targets: list[Target] = TARGETS,
                        fetcher: Callable[[str], Optional[pd.DataFrame]] | None = None,
                        ) -> dict:
    """Fill genuine MT5 gaps from guard-passing FRED series. Returns a report dict.
    Never overrides MT5 actuals; refuses (logs) when no candidate continues the series.
    """
    if fetcher is None:
        fetcher = lambda sid: FredSeriesSource(sid).fetch_series()  # noqa: E731

    if not parquet_path.exists():
        log.warning("calendar parquet missing; FRED fallback skipped.")
        return {"targets": [], "filled": 0}

    df = pd.read_parquet(parquet_path)
    df["release_dt"] = pd.to_datetime(df["release_dt"])
    new_rows: list[dict] = []
    report: list[dict] = []

    for t in targets:
        sub = df[(df["currency"] == t.currency) & (df["indicator_key"] == t.indicator_key)]
        mt5 = mt5_ground_truth(sub)
        gaps = _gap_periods(sub, mt5)
        entry = {"currency": t.currency, "indicator": t.indicator_key,
                 "mt5_points": len(mt5), "gaps": [str(p.date()) for p in sorted(gaps)],
                 "candidates": [], "decision": "REFUSED", "chosen": None, "filled": 0,
                 "note": t.note}

        evals = []
        for c in t.candidates:
            raw = fetcher(c.series_id)
            if raw is None or len(raw) == 0:
                evals.append((c, {"accepted": False, "n_overlap": 0, "max_diff": None,
                                  "reason": "fetch failed / empty"}, {}))
                entry["candidates"].append(f"{c.series_id} ({c.label}): fetch failed")
                continue
            fred = _to_period_value(raw, c.transform)
            ev = evaluate_candidate(mt5, fred)
            evals.append((c, ev, fred))
            entry["candidates"].append(f"{c.series_id} ({c.label}): {ev['reason']}")

        accepted = [(c, ev, fred) for (c, ev, fred) in evals if ev["accepted"]]
        if not accepted:
            log.warning("FRED fallback REFUSED %s %s — no candidate continues the MT5 "
                        "series within %.2fpp.", t.currency, t.indicator_key, TOL_PP)
            report.append(entry)
            continue

        # auto-select the closest-matching variant
        c, ev, fred = min(accepted, key=lambda x: x[1]["max_diff"])
        entry["decision"] = "ACCEPTED"
        entry["chosen"] = f"{c.series_id} ({c.label})"
        for per, sched in gaps.items():
            if per in fred:
                row = {k: sched.get(k) for k in CALENDAR_COLUMNS}
                row["actual"] = round(float(fred[per]), 6)
                row["source"] = "fred"
                row["release_dt"] = pd.Timestamp(sched["release_dt"])
                new_rows.append(row)
                entry["filled"] += 1
        log.info("FRED fallback ACCEPTED %s %s via %s (%s); filled %d gap(s).",
                 t.currency, t.indicator_key, c.series_id, ev["reason"], entry["filled"])
        report.append(entry)

    filled = len(new_rows)
    if filled:
        merged = pd.concat([df, pd.DataFrame(new_rows, columns=CALENDAR_COLUMNS)], ignore_index=True)
        merged = (merged.sort_values(["currency", "indicator_key", "release_dt"])
                  .drop_duplicates(["currency", "indicator_key", "release_dt"], keep="last")
                  .reset_index(drop=True))
        merged.to_parquet(parquet_path, index=False)
        log.info("FRED fallback wrote %d filled row(s) to %s.", filled, parquet_path)

    return {"targets": report, "filled": filled}


def _print_report(rep: dict) -> None:
    print(f"FRED fallback — {rep['filled']} gap row(s) filled\n")
    for e in rep["targets"]:
        print(f"{e['currency']} {e['indicator']}: {e['decision']}"
              f"{' via ' + e['chosen'] if e['chosen'] else ''} "
              f"(MT5 pts={e['mt5_points']}, gaps={e['gaps'] or '—'}, filled={e['filled']})")
        for c in e["candidates"]:
            print(f"    - {c}")
        if e["decision"] == "REFUSED":
            print(f"    → REFUSED (kept empty): {e['note']}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    ap = argparse.ArgumentParser(description="Guarded FRED fallback for MT5 calendar gaps")
    ap.parse_args(argv)
    rep = apply_fred_fallback()
    _print_report(rep)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
