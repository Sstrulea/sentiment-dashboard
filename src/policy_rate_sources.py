"""BIS Central bank policy rates — keyless SDMX source for all 8 board
currencies, ONE HTTP request. Read-only network I/O; never writes to disk,
never raises.

    fetch_policy_rates() -> dict[currency, PolicyRateObservation]

Validated live by `src.policy_rate_probe` (kept in the repo as the
diagnostic that proved this design out): all 8 currencies resolve from a
single call, BIS's conventions are the ones we want (Fed midpoint, GBP Bank
Rate, CAD overnight target — not Bank Rate or the deposit rate, CHF SNB
policy rate — not discount rate or SARON), and BIS places every value
change on the real effective date, not some lagged publication date.

Endpoint: WS_CBPOL (NOT "WS_CBPOL_D" — that dataflow ID 404s; confirmed by
the probe, don't reintroduce it).

    https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.<ref_areas>?lastNObservations=N&format=csv

The task's reference URL omits `lastNObservations`; that is deliberately NOT
what this module sends — the unbounded default returns EVERY observation
since each series' inception (checked live: 157,300 rows / 113 MB across the
8 currencies, some series back to 1946). `LOOKBACK_OBSERVATIONS` bounds it to
the most recent 500 REAL observations per currency (~1.5-2 years even for
the sparsest series), still ONE request, comfortably covering the most
recent value change for every currency observed live (the longest gap seen,
CHF's 2025-06-20 change, sits ~14 months back — well inside the window).
"""
from __future__ import annotations

import csv
import io
import logging
import math
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://stats.bis.org/api/v2/data/dataflow"
AGENCY = "BIS"
DATAFLOW = "WS_CBPOL"

# Board currency -> BIS REF_AREA code (BIS's CL_BIS_GL_REF_AREA codelist).
# XM = euro area (the ECB deposit facility rate is published under this
# code, not any single euro-area member country). Order here is the
# canonical currency order used throughout this module and by
# src.policy_rate_fetch's YAML output.
CCY_REF_AREA = {
    "USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP",
    "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH",
}
CURRENCY_ORDER = list(CCY_REF_AREA)

LOOKBACK_OBSERVATIONS = 500

# Some statistical-agency servers 403 a bare requests/urllib UA — see
# src/rate_sources/__init__.py's UA constant for the same fix applied to
# RBA/BoE/Stooq. Reused verbatim, not re-derived.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 15
RETRIES = 2
BACKOFF_S = 1.0


# ---------------------------------------------------------------------------
# Allowlist parser — accept ONLY a clean, finite float. Everything else
# (including forms never observed live) is rejected as missing, never
# guessed at or half-parsed.
# ---------------------------------------------------------------------------

def parse_obs_value(raw: Optional[str]) -> Optional[float]:
    """Accept a value iff it parses as a finite float. Rejects: None, empty/
    whitespace-only, and anything float() can't parse ("", ".", "-", "null",
    "1,25", or any other form — no allowlist of REJECTED strings is kept,
    because a blocklist can only ever cover forms already seen).

    Two traps this exists specifically to dodge:
      - float("NaN") / float("nan") / float("+inf") all SUCCEED in Python
        and return a float — `math.isfinite()` on the RESULT is what catches
        these, not the parse itself failing. `src.policy_rate_probe` found
        BIS emits literal "NaN" for missing observations; if this function
        ever stopped catching it, a `nan` would silently reach `rate_pct`
        and poison every downstream comparison (nan != nan).
      - 0.0 is a real, legitimate rate (CHF's SNB policy rate has been 0.00%
        since 2025-06-20) — this function does no truthiness check anywhere,
        only `is None` / parse-success checks, so a clean "0" or "0.0" is
        accepted exactly like any other clean number.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        v = float(s)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _to_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTTP + CSV
# ---------------------------------------------------------------------------

def _get(url: str) -> Optional[requests.Response]:
    """GET with limited retries. None on any network failure or non-200
    status after retries are exhausted. Never raises."""
    headers = {"User-Agent": UA, "Accept": "text/csv, application/json;q=0.5, */*;q=0.1"}
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            log.warning("BIS fetch attempt %d/%d failed: %s: %s",
                       attempt + 1, RETRIES + 1, type(e).__name__, e)
        else:
            if r.status_code == 200:
                return r
            log.warning("BIS fetch attempt %d/%d: HTTP %d", attempt + 1, RETRIES + 1, r.status_code)
            if r.status_code < 500:
                return None  # 4xx won't change on retry
        if attempt < RETRIES:
            time.sleep(BACKOFF_S)
    return None


def _data_url(dataflow: str, key: str, last_n: int) -> str:
    return f"{BASE_URL}/{AGENCY}/{dataflow}/1.0/{key}?lastNObservations={last_n}&format=csv"


def _parse_csv(text: str) -> tuple[list[tuple[str, str, Optional[float]]], dict[str, str]]:
    """Returns ([(ref_area, time_period, clean_value_or_None), ...],
    {ref_area: SOURCE_REF}) in CSV row order. ([], {}) on any parse failure
    — never raises."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or "OBS_VALUE" not in reader.fieldnames:
            return [], {}
        rows: list[tuple[str, str, Optional[float]]] = []
        source_ref_by_area: dict[str, str] = {}
        for row in reader:
            ref_area = (row.get("REF_AREA") or "").strip()
            time_period = (row.get("TIME_PERIOD") or "").strip()
            value = parse_obs_value(row.get("OBS_VALUE"))
            rows.append((ref_area, time_period, value))
            if ref_area and ref_area not in source_ref_by_area:
                source_ref = (row.get("SOURCE_REF") or "").strip()
                if source_ref:
                    source_ref_by_area[ref_area] = source_ref
        return rows, source_ref_by_area
    except Exception as e:  # noqa: BLE001 — never raise on a malformed payload
        log.warning("BIS CSV parse failed: %s: %s", type(e).__name__, e)
        return [], {}


# ---------------------------------------------------------------------------
# Pure aggregation — testable independently of any network call.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyRateObservation:
    currency: str
    ref_area: str
    rate_pct: float
    verified: date              # date of the latest VALID observation
    effective: Optional[date]   # date of the latest VALUE CHANGE; None if the
                                 # window shows no change (value was already
                                 # constant at the window's start — caller
                                 # should then preserve whatever `effective`
                                 # is already on file, not guess one)
    bis_source_ref: str = ""    # BIS's own SOURCE_REF, e.g. "European Central Bank"


def build_observation(currency: str, ref_area: str, obs: list[tuple[date, float]],
                       source_ref: str = "") -> Optional[PolicyRateObservation]:
    """Pure: `obs` is a list of (date, value) REAL observations for one
    currency, in any order (sorted here). None if `obs` is empty.

    `verified` = the latest observation's date. `effective` = the date of
    the most recent VALUE CHANGE walking back from the tail — these are
    genuinely different questions: a currency held steady for the whole
    fetch window has a fresh `verified` (BIS published today) but an
    `effective` from before the window even started, which we can't know
    from this data — so `effective` comes back None in that case rather
    than a guessed date.
    """
    if not obs:
        return None
    ordered = sorted(obs, key=lambda t: t[0])
    verified_date, rate_pct = ordered[-1]

    effective_date: Optional[date] = None
    for i in range(len(ordered) - 1, 0, -1):
        if ordered[i][1] != ordered[i - 1][1]:
            effective_date = ordered[i][0]
            break

    return PolicyRateObservation(
        currency=currency, ref_area=ref_area, rate_pct=rate_pct,
        verified=verified_date, effective=effective_date, bis_source_ref=source_ref,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch_policy_rates(ref_areas: Optional[dict[str, str]] = None) -> dict[str, PolicyRateObservation]:
    """One BIS request for every currency in `ref_areas` (default: the 8
    board currencies). Returns {currency: PolicyRateObservation} — a
    currency simply doesn't appear in the result if the whole fetch failed,
    the payload was empty/unparseable, or that specific currency had zero
    clean observations in the window. Never raises, never returns a partial
    or garbage entry for any currency."""
    ref_areas = ref_areas or CCY_REF_AREA
    key = "D." + "+".join(ref_areas.values())
    url = _data_url(DATAFLOW, key, LOOKBACK_OBSERVATIONS)

    r = _get(url)
    if r is None:
        log.warning("BIS policy-rate fetch failed (network/HTTP): %s", url)
        return {}

    rows, source_ref_by_area = _parse_csv(r.text)
    if not rows:
        log.warning("BIS policy-rate payload parsed to zero usable rows.")
        return {}

    area_to_ccy = {v: k for k, v in ref_areas.items()}
    by_area: dict[str, list[tuple[date, float]]] = {}
    for ref_area, time_period, value in rows:
        if value is None or ref_area not in area_to_ccy:
            continue
        d = _to_date(time_period)
        if d is None:
            continue
        by_area.setdefault(ref_area, []).append((d, value))

    out: dict[str, PolicyRateObservation] = {}
    for ref_area, ccy in area_to_ccy.items():
        obs = build_observation(ccy, ref_area, by_area.get(ref_area, []),
                                source_ref_by_area.get(ref_area, ""))
        if obs is not None:
            out[ccy] = obs
    return out
