"""PHASE 3 Task 2 — FRED cross-check on US FF actuals (retroactive safety net).

For the big US indicators with a FRED equivalent (CPI y/y, NFP, GDP q/q annualized),
compare the FF `actual` against the FRED-derived value for the same reference month.
A mismatch beyond tolerance QUARANTINES the print (excluded from scoring) with a loud
WARNING — the guard that would have caught the MT5 "3.2% m/m" corruption.

ADVISORY / RETROACTIVE: it validates already-published prints and FAILS OPEN (FRED
unreachable → no quarantine, logged). It never blocks the first ingest; if FRED later
contradicts a print, the next run quarantines it and the score recomputes.
Reuses the existing IPv4-pinned FRED client (rate_sources.FredSeriesSource).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import pandas as pd

log = logging.getLogger(__name__)

# US (currency, indicator_key) -> FRED series + transform to the FF actual convention.
#   cpi_yoy:            CPIAUCSL index -> YoY %                 (FF: CPI y/y %)
#   employment_change:  PAYEMS level (thousands) -> MoM change (FF: NFP, thousands)
#   gdp_qoq:            A191RL1Q225SBEA (real GDP % SAAR)       (FF: GDP q/q annualized %)
US_FRED = {
    ("USD", "cpi_yoy"): {"series": "CPIAUCSL", "transform": "yoy_pct", "tol": 0.2},
    ("USD", "employment_change"): {"series": "PAYEMS", "transform": "mom_diff", "tol": 60.0},  # ±60K (revisions)
    ("USD", "gdp_qoq"): {"series": "A191RL1Q225SBEA", "transform": "level", "tol": 0.4},
}


def _fred_value(series: pd.DataFrame, transform: str, ref_month: pd.Timestamp) -> Optional[float]:
    """FRED tidy (date, value) → the comparable value for the reference month."""
    d = series.dropna(subset=["value"]).copy()
    d["date"] = pd.to_datetime(d["date"]).dt.to_period("M").dt.to_timestamp()
    d = d.sort_values("date").set_index("date")["value"].astype(float)
    m = ref_month.to_period("M").to_timestamp()
    if transform == "level":
        return float(d.get(m)) if m in d.index else None
    if transform == "yoy_pct":
        prev = m - pd.DateOffset(years=1)
        if m in d.index and prev in d.index and d[prev] != 0:
            return (d[m] / d[prev] - 1.0) * 100.0
        return None
    if transform == "mom_diff":
        prevm = (m.to_period("M") - 1).to_timestamp()
        if m in d.index and prevm in d.index:
            return float(d[m] - d[prevm])
        return None
    return None


def crosscheck_us(ff_df: pd.DataFrame, *, fetcher: Callable[[str], Optional[pd.DataFrame]] | None = None,
                  now_utc: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Return quarantined prints (currency, indicator_key/canonical_id, datetime_utc,
    ff_actual, fred_value, diff) whose FF actual disagrees with FRED beyond tolerance.
    ff_df is the CANONICAL FF frame (needs currency, name_canonical/canonical_id,
    datetime_utc, actual, released). FAILS OPEN — a FRED failure yields no quarantine.
    """
    if fetcher is None:
        from .rate_sources import FredSeriesSource
        fetcher = lambda sid: FredSeriesSource(sid).fetch_series()  # noqa: E731

    # need indicator_key; derive via the matcher if the frame carries name_canonical
    from .ff_scoring import CCY2COUNTRY, build_matcher
    matcher = build_matcher()
    recs: list[dict] = []
    fred_cache: dict[str, Optional[pd.DataFrame]] = {}
    df = ff_df[ff_df.get("released", True) == True] if "released" in ff_df else ff_df  # noqa: E712

    for (ccy, ind), cfg in US_FRED.items():
        # latest released FF actual for this US indicator
        sub = df[df["currency"] == ccy].copy()
        if "name_canonical" in sub:
            sub["indicator_key"] = sub["name_canonical"].map(
                lambda n: matcher.match(CCY2COUNTRY.get(ccy, ""), n))
        elif "indicator_key" not in sub:
            continue
        sub = sub[sub["indicator_key"] == ind]
        sub["actual"] = pd.to_numeric(sub["actual"], errors="coerce")
        sub = sub[sub["actual"].notna()].sort_values("datetime_utc")
        if sub.empty:
            continue
        row = sub.iloc[-1]
        ff_actual = float(row["actual"])
        ref_month = pd.Timestamp(row["datetime_utc"])

        sid = cfg["series"]
        if sid not in fred_cache:
            try:
                fred_cache[sid] = fetcher(sid)
            except Exception as e:  # noqa: BLE001 — fail open
                log.warning("FRED cross-check unavailable for %s (%s); skipping.", sid, str(e)[:60])
                fred_cache[sid] = None
        fred = fred_cache[sid]
        if fred is None or len(fred) == 0:
            continue
        fv = _fred_value(fred, cfg["transform"], ref_month)
        if fv is None:
            continue  # reference month not yet in FRED (publication lag) — retroactive
        diff = abs(ff_actual - fv)
        if diff > cfg["tol"]:
            log.warning("FRED cross-check QUARANTINE %s %s @ %s: FF=%.2f vs FRED=%.2f (|Δ|=%.2f > %.2f)",
                        ccy, ind, ref_month.date(), ff_actual, fv, diff, cfg["tol"])
            recs.append({"currency": ccy, "indicator_key": ind, "datetime_utc": ref_month,
                         "ff_actual": ff_actual, "fred_value": round(fv, 3), "diff": round(diff, 3),
                         "tol": cfg["tol"]})
    return pd.DataFrame(recs, columns=["currency", "indicator_key", "datetime_utc",
                                       "ff_actual", "fred_value", "diff", "tol"])
