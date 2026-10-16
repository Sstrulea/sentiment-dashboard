"""Shared constants and paths of the scoring tournament."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "scripts" / "measure"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Everything heavy (weekly scores, downloads) lives OUTSIDE the repo.
CACHE = Path.home() / ".cache" / "macro-tournament"
FRED_DIR = CACHE / "fred"
YAHOO_DIR = CACHE / "yahoo"
CFTC_DIR = CACHE / "cftc"
DOWNLOADS = Path.home() / "Downloads"

CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
INDICES = ("SP500", "NASDAQ", "DJIA", "DAX", "NIKKEI", "FTSE100")
METALS = ("GOLD", "SILVER")
CROSS = INDICES + METALS
HORIZONS = (1, 2, 4)

# Yahoo Finance tickers (indices) — Close column, cached under YAHOO_DIR.
YAHOO = {"SP500": "^GSPC", "NASDAQ": "^IXIC", "DJIA": "^DJI", "DAX": "^GDAXI",
         "NIKKEI": "^N225", "FTSE100": "^FTSE"}
# Stooq daily CSVs (metals) — the user's downloads, read as they are.
STOOQ = {"GOLD": DOWNLOADS / "xauusd_d.csv", "SILVER": DOWNLOADS / "xagusd_d.csv"}
# FRED cross-check of the Yahoo indices (same index, different vendor).
FRED_INDEX = {"SP500": "SP500", "NASDAQ": "NASDAQCOM", "DJIA": "DJIA", "NIKKEI": "NIKKEI225"}
CORR_MIN = 0.99

# The 2Y used by each cross instrument (B1 index/metal rates, D2 indices).
HOME_2Y = {"SP500": "USD", "NASDAQ": "USD", "DJIA": "USD", "DAX": "EUR", "NIKKEI": "JPY",
           "FTSE100": "GBP", "GOLD": "USD", "SILVER": "USD"}


def fridays(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    """Fridays 21:00 UTC from `first` to `last` inclusive (both Friday 21:00)."""
    out, t = [], pd.Timestamp(first)
    while t <= last:
        out.append(t)
        t += pd.Timedelta(days=7)
    return out
