"""FRED - the ONLY way this repo reads FRED data: the official API with a key.

The FRED terms forbid automated downloads of the CSV download links of the FRED website and the incorporation of FRED content into another database
outside the API (fred.stlouisfed.org/legal); applications must use the FRED API under its terms
(https://fred.stlouisfed.org/docs/api/terms_of_use.html). Every FRED reader of the repo (src/rate_sources,
src/cb_sources/official.py, scripts/cb_nzd_bkbm_spread.py, the probes, scripts/measure/) delegates here.

* endpoint v1 `fred/series/observations` (series_id, file_type=json, observation_start); native frequency and units,
  no transformation; the FRED missing marker "." becomes None - never 0 (a real zero is a value);
* the key comes ONLY from the environment (FRED_API_KEY: the repo's .env locally through python-dotenv, the
  `secrets.FRED_API_KEY` secret in CI). No key -> `FredKeyMissing`; there is no fallback to the FRED CSV download;
* the key never leaves this module: every message, exception and URL handed back is redacted (`redact`), exceptions
  from requests are re-raised without their chain (their text carries the request URL);
* provenance = the series page (`series_page`) + "via FRED API";
* polite: one request at a time with a minimum interval (FRED allows 120 requests a minute), timeout, retry with
  exponential backoff on 429 / 5xx / network errors.

Notice the terms require on every page that shows the data (templates/_footer.html.j2): NOTICE.
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import requests

API_BASE = "https://api.stlouisfed.org/fred"
SERIES_PAGE = "https://fred.stlouisfed.org/series/{sid}"
TERMS_URL = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
NOTICE = "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."
PROVENANCE = "via FRED API"
ENV_KEY = "FRED_API_KEY"
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 0.6                 # <= 100 requests a minute (FRED's limit is 120)
RETRIES = 4                          # on 429 / 5xx / network errors
BACKOFF_S = 2.0                      # 2, 4, 8, 16 s
RETRY_STATUS = {429, 500, 502, 503, 504}

ROOT = Path(__file__).resolve().parents[1]
_lock = threading.Lock()
_last_call = [0.0]


class FredApiError(RuntimeError):
    """A FRED API failure; the message is always redacted."""


class FredKeyMissing(FredApiError):
    pass


# ---------------------------------------------------------------------------
# Key handling and redaction
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:                                   # CI sets the variable directly
        return
    load_dotenv(ROOT / ".env", override=False)


def api_key() -> str:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        _load_dotenv()
        key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise FredKeyMissing(f"{ENV_KEY} is not set (.env locally, secrets.{ENV_KEY} in CI): FRED data not fetched, "
                             "stored data left untouched")
    return key


_KEY_RX = re.compile(r"(api_key=)[^&\s'\"<>]+", re.I)


def redact(text: object) -> str:
    """The text with any api_key=... and any occurrence of the configured key replaced."""
    s = _KEY_RX.sub(r"\1REDACTED", str(text))
    key = os.environ.get(ENV_KEY, "").strip()
    if key and len(key) >= 8:
        s = s.replace(key, "REDACTED")
    return s


def series_page(series_id: str) -> str:
    return SERIES_PAGE.format(sid=series_id)


def provenance(series_id: str) -> str:
    return f"{series_page(series_id)} ({PROVENANCE})"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _throttle(sleep: Callable[[float], None]) -> None:
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call[0])
        if wait > 0:
            sleep(wait)
        _last_call[0] = time.monotonic()


def _call(endpoint: str, params: dict, *, session=None, sleep: Optional[Callable[[float], None]] = None) -> dict:
    sleep = sleep or time.sleep
    key = api_key()
    get = (session or requests).get
    q = dict(params, api_key=key, file_type="json")
    last = ""
    for attempt in range(RETRIES + 1):
        _throttle(sleep)
        try:
            r = get(f"{API_BASE}/{endpoint}", params=q, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            last = f"network: {type(e).__name__}: {redact(e)}"
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise FredApiError(f"FRED API {endpoint} {params.get('series_id', '')}: response is not JSON") from None
            msg = ""
            try:
                msg = (r.json() or {}).get("error_message", "")
            except ValueError:
                pass
            last = f"HTTP {r.status_code}" + (f": {redact(msg)}" if msg else "")
            if r.status_code not in RETRY_STATUS:
                break
        if attempt < RETRIES:
            sleep(BACKOFF_S * 2 ** attempt)
    raise FredApiError(redact(f"FRED API {endpoint} {params.get('series_id', '')}: {last}"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_observations(payload: dict) -> list:
    """[(date, value | None)] from a fred/series/observations JSON; "." (FRED's missing marker) -> None."""
    out = []
    for o in payload.get("observations") or []:
        d = o.get("date")
        if not d:
            continue
        v = o.get("value")
        try:
            val = None if v in (None, "", ".") else float(v)
        except (TypeError, ValueError):
            val = None
        out.append((date.fromisoformat(d), val))
    return out


def observations(series_id: str, start: Optional[date | str] = None, *, session=None,
                 sleep: Optional[Callable[[float], None]] = None) -> list:
    """Every observation of a series (native frequency and units) from `start` on: [(date, value | None)]."""
    params = {"series_id": series_id}
    if start:
        params["observation_start"] = str(start)
    out: list = []
    offset = 0
    while True:                                           # the endpoint pages at 100 000 observations
        payload = _call("series/observations", dict(params, offset=offset, limit=100000), session=session, sleep=sleep)
        rows = parse_observations(payload)
        out += rows
        total = int(payload.get("count") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            return out


def series_info(series_id: str, *, session=None, sleep: Optional[Callable[[float], None]] = None) -> dict:
    """The fred/series record (title, frequency, units, notes - the copyright notice of third-party series is there)."""
    payload = _call("series", {"series_id": series_id}, session=session, sleep=sleep)
    s = payload.get("seriess") or []
    return s[0] if s else {}
