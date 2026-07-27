"""Section K — blind horizon classification: autocorrelation half-life of each
component's OWN series. ZERO contact with forward returns — every input here
is the component's own historical values; nothing is matched against any
price/return series to pick a horizon.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.compute import compute_metrics, load_meta  # noqa: E402
from src.trend_score import (  # noqa: E402
    REGIME_MAP, SMA_MID, SMA_LONG, atr as _atr, hysteresis_step, load_history as trend_load_history,
)
from src.price_fetch import load_symbol_map  # noqa: E402
from scripts.diag.analysis_common import CATS, CCYS, DOCS_DIR, load_window  # noqa: E402

DATA = ROOT / "data"


def half_life(dates, values) -> dict:
    s = pd.Series(list(values), index=pd.to_datetime(list(dates))).sort_index()
    s = s[~s.index.duplicated(keep="last")].dropna()
    n = len(s)
    if n < 8:
        return {"n": n, "rho1": None, "median_gap_days": None, "half_life_days": None,
                "note": f"insufficient data (n={n} < 8)"}

    rho1 = s.autocorr(lag=1)
    gaps = s.index.to_series().diff().dt.days.dropna()
    median_gap = float(gaps.median()) if len(gaps) else float("nan")

    if rho1 is None or pd.isna(rho1):
        return {"n": n, "rho1": None, "median_gap_days": median_gap, "half_life_days": None,
                "note": "undefined (zero variance in window)"}
    if rho1 <= 0:
        return {"n": n, "rho1": float(rho1), "median_gap_days": median_gap,
                "half_life_days": median_gap, "note": "no persistence (rho1<=0) -> ~1 native step"}
    if rho1 >= 0.995:
        return {"n": n, "rho1": float(rho1), "median_gap_days": median_gap, "half_life_days": None,
                "note": "near-unit persistence; half-life not resolvable within this window (too short)"}

    hl = float(np.log(0.5) / np.log(rho1) * median_gap)
    return {"n": n, "rho1": float(rho1), "median_gap_days": median_gap, "half_life_days": hl, "note": "ok"}


# ---------------------------------------------------------------------------
# 1) FUND categories per currency (from the cached weekly window replay)
# ---------------------------------------------------------------------------

def fund_category_series() -> list[dict]:
    win = load_window()
    rows = []
    for ccy in CCYS:
        for cat in CATS:
            dates, vals = [], []
            for s in win["snapshots"]:
                cell = s["currencies"].get(ccy, {}).get("categories", {}).get(cat, {})
                if (cell.get("coverage") or 0) > 0:
                    dates.append(s["as_of"])
                    vals.append(cell["score_precise"])
            hl = half_life(dates, vals)
            rows.append({"component": f"FUND[{cat}]", "series": ccy, **hl})
    return rows


# ---------------------------------------------------------------------------
# 2) COT net + percentile per currency (native weekly report dates, full history)
# ---------------------------------------------------------------------------

def cot_series(window_start: pd.Timestamp, window_end: pd.Timestamp) -> list[dict]:
    hist = pd.read_parquet(DATA / "history.parquet")
    hist["report_date_as_yyyy_mm_dd"] = pd.to_datetime(hist["report_date_as_yyyy_mm_dd"])
    enriched = compute_metrics(hist)
    meta = load_meta()
    enriched["symbol"] = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("symbol"))
    cat = enriched["cftc_contract_market_code"].map(lambda c: meta.get(c, {}).get("category"))
    fx = enriched[cat == "fx"]

    rows = []
    for ccy in CCYS:
        if ccy == "USD":
            g = fx[fx["symbol"] == "DXY"]
        else:
            g = fx[fx["symbol"] == ccy]
        g = g[(g["report_date_as_yyyy_mm_dd"] >= window_start) & (g["report_date_as_yyyy_mm_dd"] <= window_end)]
        g = g.sort_values("report_date_as_yyyy_mm_dd")
        rows.append({"component": "COT[net]", "series": ccy,
                    **half_life(g["report_date_as_yyyy_mm_dd"], g["spec_net"])})
        rows.append({"component": "COT[percentile_6m]", "series": ccy,
                    **half_life(g["report_date_as_yyyy_mm_dd"], g["spec_extreme_6m"])})
    return rows


# ---------------------------------------------------------------------------
# 3) rate_exp 2y (raw yield level, per currency), real_yield 10y, net_liquidity
# ---------------------------------------------------------------------------

def macro_series_levels(window_start: pd.Timestamp, window_end: pd.Timestamp) -> list[dict]:
    rows = []
    rates = pd.read_parquet(DATA / "rates.parquet")
    rates["date"] = pd.to_datetime(rates["date"])
    for ccy in CCYS:
        g = rates[(rates["currency"] == ccy) & (rates["date"] >= window_start) & (rates["date"] <= window_end)]
        g = g.sort_values("date")
        rows.append({"component": "rate_exp_2y[level]", "series": ccy,
                    **half_life(g["date"], g["yield_pct"])})

    ry = pd.read_parquet(DATA / "real_yields.parquet")
    ry["date"] = pd.to_datetime(ry["date"])
    g = ry[(ry["series"] == "DFII10") & (ry["date"] >= window_start) & (ry["date"] <= window_end)].sort_values("date")
    rows.append({"component": "real_yield_10y[level]", "series": "DFII10", **half_life(g["date"], g["yield_pct"])})

    nl = pd.read_parquet(DATA / "net_liquidity.parquet")
    nl["date"] = pd.to_datetime(nl["date"])
    g = nl[(nl["date"] >= window_start) & (nl["date"] <= window_end)].sort_values("date")
    rows.append({"component": "net_liquidity[level]", "series": "NET_LIQUIDITY",
                **half_life(g["date"], g["net_liquidity"])})
    return rows


# ---------------------------------------------------------------------------
# 4) TREND regime + momentum per instrument (full daily series, vectorized)
# ---------------------------------------------------------------------------

def trend_component_series(window_start: pd.Timestamp, window_end: pd.Timestamp) -> list[dict]:
    df = trend_load_history()  # drops the forming bar; full history
    _, board_keys = load_symbol_map()

    rows = []
    for key in board_keys:
        g = df[df["symbol"] == key].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        closes = pd.to_numeric(g["close"], errors="coerce")
        if closes.dropna().shape[0] < SMA_LONG + 5:
            rows.append({"component": "TREND[regime]", "series": key, "n": 0, "rho1": None,
                        "median_gap_days": None, "half_life_days": None, "note": "insufficient price history"})
            rows.append({"component": "TREND[momentum]", "series": key, "n": 0, "rho1": None,
                        "median_gap_days": None, "half_life_days": None, "note": "insufficient price history"})
            continue

        sma_mid = closes.rolling(SMA_MID).mean()
        sma_long = closes.rolling(SMA_LONG).mean()
        bull_points = (closes > sma_mid).astype(int) + (closes > sma_long).astype(int) + (sma_mid > sma_long).astype(int)
        regime = bull_points.map(REGIME_MAP)

        slope = sma_mid - sma_mid.shift(20)
        atr_s = _atr(g).replace(0.0, np.nan)
        slope_atr = slope / (20 * atr_s)
        m = 0
        momentum_vals = []
        for sa in slope_atr:
            m = hysteresis_step(float(sa) if pd.notna(sa) else float("nan"), m)
            momentum_vals.append(m)
        momentum = pd.Series(momentum_vals, index=g.index)

        dates = pd.to_datetime(g["date"])
        mask = (dates >= window_start) & (dates <= window_end)
        rows.append({"component": "TREND[regime]", "series": key,
                    **half_life(dates[mask], regime[mask])})
        rows.append({"component": "TREND[momentum]", "series": key,
                    **half_life(dates[mask], momentum[mask])})
    return rows


def run() -> dict:
    win = load_window()
    window_start, window_end = win["window"][0], win["window"][-1]

    all_rows = []
    all_rows += fund_category_series()
    all_rows += cot_series(window_start, window_end)
    all_rows += macro_series_levels(window_start, window_end)
    all_rows += trend_component_series(window_start, window_end)

    df = pd.DataFrame(all_rows)
    df.to_csv(DOCS_DIR / "diag-K-halflife-raw.csv", index=False)

    resolved = df[df["half_life_days"].notna()].copy()
    resolved_sorted = resolved.sort_values("half_life_days")
    resolved_sorted.to_csv(DOCS_DIR / "diag-K-halflife-sorted.csv", index=False)

    # Summary: median half-life per component FAMILY (collapsing per-currency/
    # per-instrument replicates), so the report table stays a manageable size.
    df["family"] = df["component"].str.extract(r"^([A-Za-z_]+\[[a-z0-9_]+\])")[0].fillna(df["component"])
    family_summary = (
        df.groupby("family")
        .agg(n_series=("series", "count"),
            n_resolved=("half_life_days", lambda s: s.notna().sum()),
            median_half_life_days=("half_life_days", "median"),
            min_half_life_days=("half_life_days", "min"),
            max_half_life_days=("half_life_days", "max"))
        .sort_values("median_half_life_days")
    )
    family_summary.to_csv(DOCS_DIR / "diag-K-halflife-family-summary.csv")

    return {"df": df, "resolved_sorted": resolved_sorted, "family_summary": family_summary}


if __name__ == "__main__":
    res = run()
    print("Family summary (median half-life, days), sorted:")
    print(res["family_summary"].to_string())
    print(f"\n{len(res['resolved_sorted'])}/{len(res['df'])} series resolved to a half-life")
    print("\nFull sorted table (head 40):")
    print(res["resolved_sorted"].head(40).to_string(index=False))
