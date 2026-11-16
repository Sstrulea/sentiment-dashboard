"""Fetch VIX/VIX3M daily closes (FRED API: VIXCLS / VXVCLS) and CBOE Put/Call ratios (cdn.cboe.com, once a day).

VIX / VIX3M come through the FRED API since fix/sources-compliance (2026-11-16): identical to the Cboe CSV on the whole stored
history; FRED marks them "Copyright, 2016, Chicago Board Options Exchange, Inc. Reprinted with permission" -> scope personal_only
in config/fred_series.yaml. The P/C ratios stay on Cboe (no official alternative; personal, non-commercial use - license_note).

This module does data acquisition only — no analytics, no template rendering.
It writes two parquet files alongside the existing COT data:

    data/sentiment_history.parquet  — daily VIX/VIX3M + ratio
    data/pc_history.parquet         — daily Put/Call ratios (5 variants)
"""
from __future__ import annotations

import datetime as dt
import io
import logging
from pathlib import Path

import pandas as pd
import requests

from . import http_policy as HP

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SENTIMENT_FILE = ROOT / "data" / "sentiment_history.parquet"
PC_FILE = ROOT / "data" / "pc_history.parquet"

FRED_VIX = {"VIX": "VIXCLS", "VIX3M": "VXVCLS"}          # FRED API series (Cboe copyright, personal_only)
PC_URL_TEMPLATE = (
    "https://cdn.cboe.com/data/us/options/market_statistics/daily/"
    "{date}_daily_options"
)

HTTP_TIMEOUT = 10

PC_RATIO_MAP = {
    "total": "TOTAL PUT/CALL RATIO",
    "equity": "EQUITY PUT/CALL RATIO",
    "index": "INDEX PUT/CALL RATIO",
    "spx_spxw": "SPX + SPXW PUT/CALL RATIO",
    "vix": "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO",
}

PC_COLUMNS = ["date", "total", "equity", "index", "spx_spxw", "vix"]


def fetch_vix_history(symbol: str) -> pd.DataFrame:
    """Full VIX or VIX3M daily history from the FRED API (VIXCLS / VXVCLS).

    Returns a DataFrame with columns `date` (datetime64[ns]) and `close` (float64),
    sorted ascending by date with missing days (FRED ".") dropped.
    """
    from . import fred_api
    sym = symbol.upper()
    if sym not in FRED_VIX:
        raise ValueError(f"Unknown symbol: {symbol!r} (expected 'VIX' or 'VIX3M')")
    rows = fred_api.observations(FRED_VIX[sym])
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[["date", "close"]].dropna().sort_values("date").reset_index(drop=True)
    return df


def fetch_pc_daily(date: dt.date) -> dict | None:
    """Fetch a single day of Put/Call ratios from the CBOE daily stats endpoint.

    Returns None on 404 (weekend, holiday, not-yet-published) or on any
    recoverable parse failure. Raises only for unexpected programming errors
    (e.g., wrong type passed for `date`).
    """
    if not isinstance(date, dt.date):
        raise ValueError(f"date must be a datetime.date, got {type(date).__name__}")

    url = PC_URL_TEMPLATE.format(date=date.strftime("%Y-%m-%d"))
    try:
        r = HP.get(url, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning("Network error fetching P/C for %s: %s", date, e)
        return None

    if r.status_code == 404:
        return None
    if r.status_code != 200:
        log.warning("P/C fetch for %s returned HTTP %d", date, r.status_code)
        return None

    try:
        payload = r.json()
    except ValueError as e:
        log.warning("P/C response for %s is not valid JSON: %s", date, e)
        return None

    ratios = payload.get("ratios") if isinstance(payload, dict) else None
    if not isinstance(ratios, list):
        log.warning("P/C response for %s missing 'ratios' list", date)
        return None

    by_name: dict[str, str] = {}
    for item in ratios:
        if isinstance(item, dict) and "name" in item and "value" in item:
            by_name[item["name"].strip().upper()] = str(item["value"]).strip()

    out: dict = {"date": date}
    for key, cboe_name in PC_RATIO_MAP.items():
        raw = by_name.get(cboe_name.upper())
        if raw is None or raw == "":
            out[key] = None
            continue
        try:
            out[key] = float(raw)
        except (TypeError, ValueError):
            out[key] = None
    return out


def build_sentiment_parquet() -> pd.DataFrame:
    """Fetch VIX + VIX3M, inner-join on date, compute VIX/VIX3M ratio, save parquet."""
    vix = fetch_vix_history("VIX").rename(columns={"close": "vix_close"})
    vix3m = fetch_vix_history("VIX3M").rename(columns={"close": "vix3m_close"})

    df = vix.merge(vix3m, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df["ratio"] = df["vix_close"] / df["vix3m_close"]
    df = df[["date", "vix_close", "vix3m_close", "ratio"]]
    if SENTIMENT_FILE.exists():                      # the stored rows (Cboe CSV until 2026-11-16) stay as they are: only newer days
        old = pd.read_parquet(SENTIMENT_FILE)        # are appended (FRED is identical on the 4315 shared days; it has no
        old["date"] = pd.to_datetime(old["date"])    # VIX3M before 2009-09-18 in the stored file and is published a day later)
        df = pd.concat([old, df[df["date"] > old["date"].max()]], ignore_index=True)[["date", "vix_close", "vix3m_close", "ratio"]]

    SENTIMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SENTIMENT_FILE, index=False)
    log.info("Wrote %d rows to %s", len(df), SENTIMENT_FILE)
    return df


def append_pc_daily(date: dt.date) -> bool:
    """Fetch a single day of P/C ratios and append to pc_history.parquet.

    Returns True if data was found and written, False if the fetch yielded
    None (weekend, holiday, not-yet-published, or recoverable error).
    """
    row = fetch_pc_daily(date)
    if row is None:
        log.warning("No P/C data for %s", date)
        return False

    new_df = pd.DataFrame([row], columns=PC_COLUMNS)
    new_df["date"] = pd.to_datetime(new_df["date"])

    if PC_FILE.exists():
        existing = pd.read_parquet(PC_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = (
        combined.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )

    PC_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(PC_FILE, index=False)
    log.info("Appended %s to %s (%d total rows)", date, PC_FILE, len(combined))
    return True


def check_pc_backfill_availability() -> dict:
    """Probe 10 historical/recent dates to decide whether backfill is feasible.

    Does NOT write anything to disk — this is a read-only diagnostic.
    """
    today = dt.date.today()
    probes: list[dt.date] = [
        today - dt.timedelta(days=7),
        today - dt.timedelta(days=30),
        today - dt.timedelta(days=90),
        dt.date(2025, 1, 15),
        dt.date(2024, 6, 10),
        dt.date(2023, 3, 22),
        dt.date(2022, 9, 1),
        dt.date(2021, 5, 14),
        dt.date(2020, 3, 18),
        dt.date(2019, 12, 2),
    ]
    historical_dates = probes[3:]

    results: list[dict] = []
    historical_successes: list[dt.date] = []

    for probed in probes:
        url = PC_URL_TEMPLATE.format(date=probed.strftime("%Y-%m-%d"))
        try:
            r = HP.get(url, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            results.append(
                {
                    "date": probed.isoformat(),
                    "status": "error",
                    "detail": str(e),
                    "sample": None,
                }
            )
            continue

        if r.status_code == 404:
            results.append(
                {
                    "date": probed.isoformat(),
                    "status": "404",
                    "detail": None,
                    "sample": None,
                }
            )
            continue
        if r.status_code != 200:
            results.append(
                {
                    "date": probed.isoformat(),
                    "status": f"http_{r.status_code}",
                    "detail": None,
                    "sample": None,
                }
            )
            continue

        row = fetch_pc_daily(probed)
        if row is None:
            results.append(
                {
                    "date": probed.isoformat(),
                    "status": "error",
                    "detail": "parse failed",
                    "sample": None,
                }
            )
            continue

        sample = {k: row[k] for k in ("total", "equity", "index", "spx_spxw", "vix")}
        results.append(
            {
                "date": probed.isoformat(),
                "status": "success",
                "detail": None,
                "sample": sample,
            }
        )
        if probed in historical_dates:
            historical_successes.append(probed)

    supports_backfill = len(historical_successes) >= 7
    if supports_backfill:
        earliest = min(historical_successes)
        recommendation = (
            f"Backfill supported from at least {earliest.isoformat()} "
            f"({len(historical_successes)}/{len(historical_dates)} historical probes succeeded)"
        )
    else:
        recommendation = (
            "Endpoint is forward-only; use warmup strategy "
            f"({len(historical_successes)}/{len(historical_dates)} historical probes succeeded)"
        )

    return {
        "supports_backfill": supports_backfill,
        "results": results,
        "recommendation": recommendation,
    }
