"""Displayed policy rate (interest_rate_decision, weight 0) — audit 2026-09-23, 1.6.

Single source: data/cb/decisions.parquet, written by the official CB pipeline
(src/cb_compute). The FF/JBlanked `interest_rate_decision` prints are no longer
read: there a 0.00 is quarantined as a placeholder and rows are missing (CHF
showed 0.25% from March 2025 while the SNB was at 0.00%), and the conventions
differ (FF: ECB main refinancing rate, Fed upper bound).

Conventions are the decisions.parquet ones (= data/policy_rates.yaml, now only
the Carry page's fallback): Fed = midpoint of the target range (lower/upper kept for display),
ECB = deposit facility rate. One decision -> one calendar row:
  release_dt = decision_time_utc (naive UTC); when the pipeline has no time
               (BoJ, status 'statement'), meeting_date 00:00 UTC
  actual     = rate_after,  consensus = consensus,  previous = rate_before
  source     = "cb"
Rows after `as_of` are dropped (no lookahead).

`policy_rates_at` gives the rate in force per currency at `as_of` (the latest
decision at or before it) — the /carry page's source (2026-10-07).
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


def policy_rates_at(decisions: pd.DataFrame | None, as_of: pd.Timestamp) -> dict[str, dict]:
    """Per currency, the policy rate in force at `as_of`: rate_after of the
    latest decision with release_dt <= as_of — the same rule as the /economic
    display (decision_rows). Pure, no rendering; reused by /carry and, later,
    by the carry factor in the score.

    {CCY: {"rate_pct", "release_dt", "meeting_date", "effective_date",
           "rate_source", "status", "bank", ["lower", "upper"]}}
    A currency with no decision at or before `as_of` is absent. A 0.00 rate
    (CHF) is a real rate, kept as 0.0."""
    if decisions is None or decisions.empty:
        return {}
    d = decisions[decisions["release_dt"] <= pd.Timestamp(as_of)]
    out: dict[str, dict] = {}
    for ccy, g in d.groupby("currency", sort=True):
        r = g.sort_values("release_dt").iloc[-1]
        if pd.isna(r["rate_after"]):
            continue
        entry = {"rate_pct": float(r["rate_after"]),
                 "release_dt": pd.Timestamp(r["release_dt"]).isoformat(),
                 "meeting_date": str(pd.Timestamp(r["meeting_date"]).date()),
                 "effective_date": str(pd.Timestamp(r["effective_date"]).date()),
                 "rate_source": str(r["rate_source"]), "status": str(r["status"]),
                 "bank": str(r["bank"])}
        if pd.notna(r.get("lower")) and pd.notna(r.get("upper")):
            entry["lower"], entry["upper"] = float(r["lower"]), float(r["upper"])
        out[str(ccy)] = entry
    return out


def policy_rates_with_fallback(decisions: pd.DataFrame | None, as_of: pd.Timestamp,
                               manual: dict | None) -> dict[str, float]:
    """{CCY: rate} — policy_rates_at (decisions.parquet), and for a currency it
    cannot give, the manual data/policy_rates.yaml leg (`manual` = its `rates`
    mapping) — the same per-currency rule as /carry. 0.00 is a rate."""
    out = {c: d["rate_pct"] for c, d in policy_rates_at(decisions, as_of).items()}
    for c, leg in (manual or {}).items():
        if c not in out and (leg or {}).get("rate_pct") is not None:
            out[c] = float(leg["rate_pct"])
    return out


# FF calendar conventions -> decisions.parquet conventions (policy_rate_history).
FED_MIDPOINT_OFFSET = 0.125                 # FF shows the Fed's upper bound
ECB_DFR_SWITCH = pd.Timestamp("2024-09-12")  # the ECB narrows MRO - DFR from 0.50 to 0.15
ECB_MRO_TO_DFR = (0.50, 0.15)               # (before the switch, from the switch on)


def policy_rate_history(ff: pd.DataFrame, decisions: pd.DataFrame | None = None) -> pd.DataFrame:
    """The policy-rate decisions per currency over time — pure; never reads
    data/policy_rates.yaml. Columns: currency, release_dt, rate, source.

    The interest_rate_decision rows of the FF calendar parquet (a missing
    actual takes the next row's `previous`), converted to the decisions.parquet
    conventions — Fed: upper bound − 0.125 (midpoint); ECB: main refinancing
    rate − 0.50 for decisions announced before 2024-09-12, − 0.15 from the
    2024-09-12 decision on (deposit facility rate) — and replaced by
    data/cb/decisions.parquet (load_decisions frame) from each currency's
    first decision there. See rate_at() for the rate in force at t."""
    f = ff[ff["canonical_id"].astype(str).str.endswith("interest_rate_decision")].copy()
    f["release_dt"] = pd.to_datetime(f["datetime_utc"])
    f = f.sort_values(["currency", "release_dt"])
    nxt_prev = f.groupby("currency")["previous"].shift(-1)
    f["rate"] = f["actual"].where(f["actual"].notna(), nxt_prev).astype(float)
    usd = f["currency"] == "USD"
    f.loc[usd, "rate"] = f.loc[usd, "rate"] - FED_MIDPOINT_OFFSET
    eur = f["currency"] == "EUR"
    before = f["release_dt"] < ECB_DFR_SWITCH
    f.loc[eur & before, "rate"] = f.loc[eur & before, "rate"] - ECB_MRO_TO_DFR[0]
    f.loc[eur & ~before, "rate"] = f.loc[eur & ~before, "rate"] - ECB_MRO_TO_DFR[1]
    f = f[f["rate"].notna()]
    out = f[["currency", "release_dt", "rate"]].assign(source="ff")
    if decisions is not None and not decisions.empty:
        d = decisions[decisions["rate_after"].notna()]
        first = d.groupby("currency")["release_dt"].min()
        cut = out["currency"].map(first)
        out = out[cut.isna() | (out["release_dt"] < cut)]
        dec = pd.DataFrame({"currency": d["currency"].to_numpy(), "release_dt": d["release_dt"].to_numpy(),
                            "rate": d["rate_after"].astype(float).to_numpy(), "source": "cb_decisions"})
        out = pd.concat([out, dec], ignore_index=True)
    return out.sort_values(["currency", "release_dt"], kind="stable").reset_index(drop=True)


def rate_at(history: pd.DataFrame, t: pd.Timestamp) -> dict[str, float]:
    """{CCY: rate} — the last decision announced at or before t."""
    h = history[history["release_dt"] <= pd.Timestamp(t)]
    if h.empty:
        return {}
    last = h.sort_values("release_dt", kind="stable").groupby("currency").tail(1)
    return {str(c): float(r) for c, r in zip(last["currency"], last["rate"])}


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
        "actual_origin": SOURCE, "publication": "scored",
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
