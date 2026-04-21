"""Compute weekly COT metrics: exposures, net positions, and trailing percentile ranks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_FILE = ROOT / "data" / "contracts.yaml"


def load_meta() -> dict[str, dict]:
    with open(CONTRACTS_FILE) as f:
        cfg = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for cat_key, cat in cfg["categories"].items():
        for inst in cat["instruments"]:
            out[inst["cftc_code"]] = {
                "symbol": inst["symbol"],
                "name": inst["name"],
                "category": cat_key,
                "category_label": cat["label"],
            }
    return out


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return np.where(b != 0, a / b, np.nan)


def _trailing_rank(series: pd.Series, window: int) -> pd.Series:
    """Return the percentile rank (0..1) of each value within its trailing window.

    Uses pandas `rank(pct=True)` with `method='average'`. When the window is
    not yet full, returns NaN for that row (we avoid lookahead and also avoid
    unstable ranks on tiny samples).
    """
    def _r(x: np.ndarray) -> float:
        s = pd.Series(x)
        return s.rank(method="average", pct=True).iloc[-1]

    return series.rolling(window=window, min_periods=window).apply(_r, raw=True)


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Given raw CFTC rows, return a frame enriched with metrics.

    Expects columns: report_date_as_yyyy_mm_dd, cftc_contract_market_code,
    noncomm_positions_long_all, noncomm_positions_short_all,
    comm_positions_long_all, comm_positions_short_all.
    """
    if df.empty:
        return df.copy()

    df = df.copy()
    df["report_date_as_yyyy_mm_dd"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    df = df.sort_values(["cftc_contract_market_code", "report_date_as_yyyy_mm_dd"])

    spec_long = df["noncomm_positions_long_all"]
    spec_short = df["noncomm_positions_short_all"]
    comm_long = df["comm_positions_long_all"]
    comm_short = df["comm_positions_short_all"]

    df["spec_long"] = spec_long
    df["spec_short"] = spec_short
    df["comm_long"] = comm_long
    df["comm_short"] = comm_short

    df["spec_exp_long"] = _safe_div(spec_long, spec_long + spec_short)
    df["spec_exp_short"] = 1 - df["spec_exp_long"]
    df["spec_net"] = spec_long - spec_short

    df["comm_exp_long"] = _safe_div(comm_long, comm_long + comm_short)
    df["comm_exp_short"] = 1 - df["comm_exp_long"]
    df["comm_net"] = comm_long - comm_short

    out_parts = []
    for _, g in df.groupby("cftc_contract_market_code", sort=False):
        g = g.copy()
        g["spec_extreme_6m"] = _trailing_rank(g["spec_net"], 26)
        g["spec_extreme_3y"] = _trailing_rank(g["spec_net"], 156)
        g["comm_extreme_6m"] = _trailing_rank(g["comm_net"], 26)
        g["comm_extreme_3y"] = _trailing_rank(g["comm_net"], 156)
        out_parts.append(g)
    return pd.concat(out_parts, ignore_index=True)


def build_latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Return a one-row-per-instrument snapshot for the most recent week,
    annotated with category metadata and flip detection.
    """
    enriched = compute_metrics(df)
    if enriched.empty:
        return enriched

    meta = load_meta()
    enriched["symbol"] = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("symbol"))
    enriched["name"] = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("name"))
    enriched["category"] = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("category"))
    enriched["category_label"] = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("category_label"))

    # Keep only contracts referenced in contracts.yaml.
    enriched = enriched[enriched["symbol"].notna()].copy()

    latest_date = enriched["report_date_as_yyyy_mm_dd"].max()
    latest = enriched[enriched["report_date_as_yyyy_mm_dd"] == latest_date].copy()

    # Previous week per contract for flip detection.
    prev = (
        enriched[enriched["report_date_as_yyyy_mm_dd"] < latest_date]
        .sort_values("report_date_as_yyyy_mm_dd")
        .groupby("cftc_contract_market_code")
        .tail(1)[
            [
                "cftc_contract_market_code",
                "spec_extreme_3y",
                "comm_extreme_3y",
            ]
        ]
        .rename(columns={
            "spec_extreme_3y": "spec_extreme_3y_prev",
            "comm_extreme_3y": "comm_extreme_3y_prev",
        })
    )
    latest = latest.merge(prev, on="cftc_contract_market_code", how="left")

    def _flipped(cur: pd.Series, prev_: pd.Series) -> pd.Series:
        cur_extreme = (cur >= 0.95) | (cur <= 0.05)
        prev_normal = (prev_ > 0.05) & (prev_ < 0.95)
        return cur_extreme & prev_normal.fillna(False)

    latest["spec_flipped"] = _flipped(latest["spec_extreme_3y"], latest["spec_extreme_3y_prev"])
    latest["comm_flipped"] = _flipped(latest["comm_extreme_3y"], latest["comm_extreme_3y_prev"])
    latest["any_flipped"] = latest["spec_flipped"] | latest["comm_flipped"]

    # Mark top-3 |Net| per category for coloring.
    latest["spec_net_rank_in_cat"] = (
        latest.groupby("category")["spec_net"].transform(lambda s: s.abs().rank(ascending=False, method="min"))
    )
    latest["comm_net_rank_in_cat"] = (
        latest.groupby("category")["comm_net"].transform(lambda s: s.abs().rank(ascending=False, method="min"))
    )
    return latest.sort_values(["category", "symbol"]).reset_index(drop=True)
