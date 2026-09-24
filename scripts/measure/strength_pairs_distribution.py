"""measure — K for /strength.html once Strength is an aggregate of pairs (audit B1).

Same method as scripts/measure/strength_index_distribution.py (STRENGTH_PCT_K's
docstring): point-in-time reconstruction, weekly, over the trailing 12 months
(53 points), pooled over the 8 board currencies; K = 40 / p95(|score|), rounded
to the nearest 0.5.

The score reconstructed here is the NEW Strength score: for each currency, the
mean of the fundamental score of the 7 pairs it belongs to (+ as base, - as
quote), each pair score = macro_score_no_sentiment from compute_instrument
(D1=D intersection, no COT, no trend). Each week goes through the production
path: economic_render._load_calendar_frame (manual overrides at that as_of),
the policy rate from decisions.parquet, ff_scoring.scoring_view (R1), truncated
to release_dt <= as_of, rate scores and 2y-spread pair monetary (audit 5B) at as_of.

    .venv/bin/python scripts/measure/strength_pairs_distribution.py [--end ISO]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.economic_render as er  # noqa: E402
from src.economic_compute import build_payload  # noqa: E402
from src.ff_scoring import scoring_view  # noqa: E402
from src.rate_compute import compute_pair_spread_scores, compute_rate_scores  # noqa: E402

WEEKS = 52


def strength_scores(payload: dict) -> dict[str, float]:
    """The B1 aggregate from an economic payload (same function the page uses)."""
    return er.strength_from_pairs(payload)["scores"]


def reconstruct(end: pd.Timestamp) -> pd.DataFrame:
    ind = er._load_yaml(er.INDICATORS_YAML)
    inst = er._load_yaml(er.INSTRUMENTS_YAML)
    rates = pd.read_parquet(er.RATES_PARQUET) if er.RATES_PARQUET.exists() else pd.DataFrame()
    decisions = er._policy_rate_decisions()
    rows = []
    for i in range(WEEKS, -1, -1):
        as_of = end - pd.Timedelta(days=7 * i)
        cal = er._with_policy_rate(er._load_calendar_frame(as_of), decisions, as_of)
        cal = scoring_view(cal)
        cal = cal[pd.to_datetime(cal["release_dt"]) <= as_of]
        rs = compute_rate_scores(rates, as_of=as_of.date()) if len(rates) else {}
        # audit 5B: pair monetary on the 2y spread, as economic_render does
        fx = [(s, c["base"], c["quote"]) for s, c in inst["instruments"].items() if c.get("type") == "fx"]
        pm = compute_pair_spread_scores(rates, fx, as_of=as_of.date()) if len(rates) else {}
        p = build_payload(cal, ind, inst, as_of=as_of, rate_scores=rs or None, pair_monetary=pm or None)
        for ccy, s in strength_scores(p).items():
            rows.append({"as_of": as_of, "currency": ccy, "score": s})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    end = pd.Timestamp(a.end) if a.end else pd.Timestamp.utcnow().tz_localize(None).normalize()
    df = reconstruct(end)
    print(f"{len(df)} (currency, week) observations, {df['as_of'].nunique()} weeks, "
          f"{df['as_of'].min().date()} .. {df['as_of'].max().date()}")
    q = df["score"].quantile([0, .05, .25, .5, .75, .95, 1]).round(3).to_dict()
    print("global score quantiles:", q)
    for ccy, g in df.groupby("currency"):
        print(f"  {ccy}: min={g.score.min():+.2f} median={g.score.median():+.2f} max={g.score.max():+.2f}")
    sums = df.groupby("as_of")["score"].sum().abs().max()
    print(f"max |sum of 8 scores| over weeks = {sums:.2e}")
    p95 = float(df["score"].abs().quantile(0.95))
    k_raw = 40.0 / p95
    k = round(k_raw * 2) / 2
    print(f"p95(|score|) = {p95:.3f}  K_raw = {k_raw:.3f}  K = {k}")
    pct = (50 + df["score"] * k).clip(0, 100)
    print("pct quantiles with K:", pct.quantile([0, .05, .5, .95, 1]).round(1).to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
