"""Point-in-time signals of the OOS check (docs/eval/oos-cot-rates-prereg.md, D1–D2).

H1: L / F / C per currency (USD = DXY) from the full CFTC history of the 8 FX
    contracts, through src.compute.compute_metrics and src.cot_score.
H2: R per index = mean of the present v3_yield_signals (home 2Y, REAL10), with the
    EUR/GBP 2Y extended from the repo's own sources (yields_ext.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "scripts" / "measure", ROOT / "scripts" / "measure" / "tournament",
          ROOT / "scripts" / "measure" / "oos"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.compute import compute_metrics  # noqa: E402
from src.cot_score import cot_cell, flow_score, level_score  # noqa: E402
from src.crossasset_compute import v3_yield_signals  # noqa: E402

CFTC_DIR = Path.home() / ".cache" / "macro-tournament" / "cftc"
CODES = {"EUR": "099741", "GBP": "096742", "JPY": "097741", "AUD": "232741", "NZD": "112741",
         "CAD": "090741", "CHF": "092741", "USD": "098662"}      # USD = DXY
CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
DATE = "report_date_as_yyyy_mm_dd"
H1_START, H1_END = pd.Timestamp("2009-07-03 21:00"), pd.Timestamp("2023-09-15 21:00")
H2_START, H2_END = pd.Timestamp("2014-01-03 21:00"), pd.Timestamp("2023-09-15 21:00")
H2_HALF2 = pd.Timestamp("2018-11-02 21:00")
INDICES = ("SP500", "NASDAQ", "DJIA", "DAX", "NIKKEI", "FTSE100")
HOME = {"SP500": "USD", "NASDAQ": "USD", "DJIA": "USD", "DAX": "EUR", "NIKKEI": "JPY", "FTSE100": "GBP"}


def fridays(a: pd.Timestamp, b: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(a, b, freq="7D"))


# ---------------------------------------------------------------------------
# H1
# ---------------------------------------------------------------------------

def cftc_raw() -> pd.DataFrame:
    parts = []
    for c, code in CODES.items():
        p = CFTC_DIR / f"cot_full_{code}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"CFTC history missing for {c}: {p}")
        parts.append(pd.read_parquet(p))
    d = pd.concat(parts, ignore_index=True)
    d[DATE] = pd.to_datetime(d[DATE])
    return d


def enrich(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """{currency: enriched rows sorted by report date} (compute_metrics as in production)."""
    e = compute_metrics(raw)
    inv = {v: k for k, v in CODES.items()}
    return {inv[code]: g.sort_values(DATE).reset_index(drop=True)
            for code, g in e.groupby("cftc_contract_market_code") if code in inv}


def lfc_at(en: dict[str, pd.DataFrame], t: pd.Timestamp) -> dict[str, tuple]:
    """{currency: (L, F, C)} at t — None when the latest report has no ext_6m."""
    out = {}
    for c in CCYS:
        g = en[c][en[c][DATE] <= t]
        if g.empty or pd.isna(g["spec_extreme_6m"].iloc[-1]):
            out[c] = (None, None, None)
            continue
        e6, e3 = g["spec_extreme_6m"].iloc[-1], g["spec_extreme_3y"].iloc[-1]
        L, _ = level_score(e6, e3)
        F, fm = flow_score(g["spec_net"])
        C, _ = cot_cell(e6, e3, g["spec_net"])
        out[c] = (float(L), None if fm["basis"] != "ok" else float(F), float(C))
    return out


def h1_scores(en, weeks) -> pd.DataFrame:
    rows = []
    for t in weeks:
        for c, (L, F, C) in lfc_at(en, t).items():
            rows += [("L", t, "ccy", c, L), ("F", t, "ccy", c, F), ("C", t, "ccy", c, C)]
    df = pd.DataFrame(rows, columns=["variant", "week", "view", "unit", "score"])
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# H2
# ---------------------------------------------------------------------------

def rates_ext() -> pd.DataFrame:
    """data/rates.parquet + the EUR/GBP history before its first day (same source labels)."""
    import yields_ext as Y
    rates = pd.read_parquet(ROOT / "data" / "rates.parquet")
    add = []
    for ccy, fn, src in (("EUR", Y.fetch_eur, "ecb"), ("GBP", Y.fetch_gbp, "boe_glc")):
        first = rates.loc[rates["currency"] == ccy, "date"].min()
        s = fn()
        s = s[s.index < first]
        add.append(pd.DataFrame({"currency": ccy, "date": s.index, "tenor": "2y", "yield_pct": s.values,
                                 "source": src}))
    return pd.concat([rates] + add, ignore_index=True).sort_values(["currency", "date"]).reset_index(drop=True)


def sigma_y() -> dict:
    return yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())["v3"]["sigma_y"]


def r_at(rates: pd.DataFrame, real: pd.DataFrame, t: pd.Timestamp, sig: dict) -> dict[str, float | None]:
    ys = v3_yield_signals(rates, real, t.date(), sig)
    out = {}
    for sym in INDICES:
        vals = [(ys.get(n) or {}).get("signal") for n in (HOME[sym], "REAL10")]
        vals = [v for v in vals if v is not None]
        out[sym] = float(np.mean(vals)) if vals else None
    return out


def h2_scores(rates, real, weeks) -> pd.DataFrame:
    sig = sigma_y()
    rows = [(t, s, v) for t in weeks for s, v in r_at(rates, real, t, sig).items()]
    df = pd.DataFrame(rows, columns=["week", "unit", "R"])
    df["R"] = pd.to_numeric(df["R"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Concordance with data/history.parquet (test + report)
# ---------------------------------------------------------------------------

def hist_concordance() -> dict:
    """Raw rows identical on the common (report, contract) pairs; L/F/C identical on
    the Fridays where history.parquet's own trailing windows are complete
    (>= 156 reports for L and C, >= 57 for F)."""
    from src.fetch import NUMERIC_COLS
    raw = cftc_raw()
    hist = pd.read_parquet(ROOT / "data" / "history.parquet")
    hist = hist[hist["cftc_contract_market_code"].isin(CODES.values())].copy()
    hist[DATE] = pd.to_datetime(hist[DATE])
    key = [DATE, "cftc_contract_market_code"]
    j = hist.merge(raw, on=key, suffixes=("_h", "_d"))
    raw_mismatch = int(sum((j[f"{c}_h"].astype(float) != j[f"{c}_d"].astype(float)).sum() for c in NUMERIC_COLS))
    missing = int(len(hist) - len(j))
    en_full, en_hist = enrich(raw), enrich(hist)
    weeks = fridays(pd.Timestamp("2023-03-31 21:00"), hist[DATE].max() + pd.Timedelta(days=3, hours=21))
    comparable = mism = 0
    for t in weeks:
        a, b = lfc_at(en_full, t), lfc_at(en_hist, t)
        for c in CCYS:
            n = int((en_hist[c][DATE] <= t).sum())
            for i, need in ((0, 156), (1, 57), (2, 156)):
                if n >= need:
                    comparable += 1
                    if a[c][i] != b[c][i]:
                        mism += 1
    return {"common_reports": int(len(j)), "raw_mismatches": raw_mismatch, "hist_rows_not_downloaded": missing,
            "comparable_weeks": len([t for t in weeks if all(int((en_hist[c][DATE] <= t).sum()) >= 57 for c in CCYS)]),
            "comparable_cells": comparable, "mismatches": mism}
