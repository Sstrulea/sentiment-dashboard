"""Displayed policy rate (interest_rate_decision, weight 0) — audit 2026-09-23, 1.6.

Single source: data/cb/decisions.parquet, written by the official CB pipeline
(src/cb_compute). The FF/JBlanked `interest_rate_decision` prints are no longer
read: there a 0.00 is quarantined as a placeholder and rows are missing (CHF
showed 0.25% from March 2025 while the SNB was at 0.00%), and the conventions
differ (FF: ECB main refinancing rate, Fed upper bound).

Conventions are the decisions.parquet ones (= data/policy_rates.yaml, the
Carry page): Fed = midpoint of the target range (lower/upper kept for display),
ECB = deposit facility rate. One decision -> one calendar row:
  release_dt = decision_time_utc (naive UTC); when the pipeline has no time
               (BoJ, status 'statement'), meeting_date 00:00 UTC
  actual     = rate_after,  consensus = consensus,  previous = rate_before
  source     = "cb"
Rows after `as_of` are dropped (no lookahead).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DECISIONS_PARQUET = ROOT / "data" / "cb" / "decisions.parquet"
KEY = "interest_rate_decision"
SOURCE = "cb"


def load_decisions(path: Path = DECISIONS_PARQUET) -> pd.DataFrame:
    df = pd.read_parquet(path)
    t = pd.to_datetime(df["decision_time_utc"], utc=True).dt.tz_localize(None)
    df["time_known"] = t.notna()
    df["release_dt"] = t.fillna(pd.to_datetime(df["meeting_date"]))
    return df.sort_values(["currency", "release_dt"]).reset_index(drop=True)


def decision_rows(decisions: pd.DataFrame, as_of: pd.Timestamp,
                  columns: list[str]) -> pd.DataFrame:
    """decisions -> scoring-calendar rows (`columns` = ff_scoring.SCORING_COLUMNS)."""
    d = decisions[decisions["release_dt"] <= pd.Timestamp(as_of)]
    out = pd.DataFrame({
        "currency": d["currency"].to_numpy(), "indicator_key": KEY,
        "release_dt": d["release_dt"].to_numpy(),
        "actual": d["rate_after"].astype(float).to_numpy(),
        "consensus": d["consensus"].astype(float).to_numpy(),
        "previous": d["rate_before"].astype(float).to_numpy(),
        "source": SOURCE, "name_raw": d["bank"].str.upper().to_numpy(),
        "actual_origin": SOURCE,
    })
    return out[[c for c in columns if c in out.columns]]


def replace_in_calendar(cal: pd.DataFrame, decisions: pd.DataFrame,
                        as_of: pd.Timestamp) -> pd.DataFrame:
    """Drop every `interest_rate_decision` row of the scoring calendar (FF, JB,
    manual) and put the decisions.parquet rows in their place."""
    kept = cal[cal["indicator_key"] != KEY]
    rows = decision_rows(decisions, as_of, list(cal.columns))
    return pd.concat([kept, rows], ignore_index=True)


def display_fields(decisions: pd.DataFrame, currency: str, release_dt) -> dict:
    """Extra breakdown fields for the decision that produced the displayed entry:
    the Fed's target range, provenance, and the effective date."""
    m = decisions[(decisions["currency"] == currency)
                  & (decisions["release_dt"] == pd.Timestamp(release_dt))]
    if m.empty:
        return {}
    r = m.iloc[-1]
    out = {"source": "cb_decisions", "rate_source": str(r["rate_source"]),
           "decision_status": str(r["status"]),
           "effective_date": str(pd.Timestamp(r["effective_date"]).date()),
           "meeting_date": str(pd.Timestamp(r["meeting_date"]).date())}
    if not bool(r.get("time_known", True)):
        out["date_only"] = True       # no decision time: show the meeting date only (V2)
    if pd.notna(r["lower"]) and pd.notna(r["upper"]):
        out["range"] = {"lower": float(r["lower"]), "upper": float(r["upper"])}
    return out
