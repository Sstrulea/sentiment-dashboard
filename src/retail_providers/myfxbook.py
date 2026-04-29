"""Myfxbook Community Outlook provider.

Auth flow:
    1) GET login.json?email=&password=  -> returns {error: bool, session: str}
    2) GET get-community-outlook.json?session=...
    3) GET logout.json?session=...      (always, in finally)

Sessions are IP-bound and have a 1-month TTL, but each fetch run does a
fresh login because GitHub Actions IPs rotate.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
import yaml

from .base import (
    FetchResult,
    GeneralStats,
    RetailAuthError,
    RetailFetchError,
    RetailParseError,
    RetailSnapshot,
)

# Load .env once at module import so local dev picks up MYFXBOOK_* vars.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    # python-dotenv not installed or .env missing — fine in CI where env vars are set directly.
    pass

log = logging.getLogger(__name__)

API_BASE = "https://www.myfxbook.com/api"
HTTP_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS_YAML = ROOT / "data" / "retail_symbols.yaml"


def _strip_number(v: Any) -> float | None:
    """Coerce strings like '35,914,737.56' or numerics to float; return None on failure."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _strip_int(v: Any) -> int | None:
    f = _strip_number(v)
    return None if f is None else int(round(f))


def _build_alias_map(provider_aliases: dict | None) -> dict[str, str]:
    """Reverse-map every provider alias -> universal symbol.

    `provider_aliases` shape:  {universal: [alias1, alias2, ...]}
    Returns:                   {alias_upper: universal}
    """
    out: dict[str, str] = {}
    if not provider_aliases:
        return out
    for universal, aliases in provider_aliases.items():
        for a in aliases or []:
            out[a.upper()] = universal
    return out


class MyfxbookProvider:
    """Implementation of `RetailSentimentProvider` for Myfxbook Community Outlook."""

    name = "myfxbook"

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        symbols_yaml: Path = SYMBOLS_YAML,
    ) -> None:
        self.email = email if email is not None else os.environ.get("MYFXBOOK_EMAIL")
        self.password = password if password is not None else os.environ.get("MYFXBOOK_PASSWORD")
        if not self.email or not self.password:
            raise RetailAuthError(
                "Myfxbook credentials missing. Set MYFXBOOK_EMAIL and "
                "MYFXBOOK_PASSWORD in environment or .env file."
            )

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        })

        # Universal symbol set + alias map for symbol normalization.
        cfg = self._load_symbols(symbols_yaml)
        self._universal_symbols: set[str] = set(cfg.get("symbols", {}).keys())
        provider_aliases = (cfg.get("provider_aliases") or {}).get(self.name, {})
        # Make sure the canonical universal symbol resolves to itself.
        for u in self._universal_symbols:
            provider_aliases.setdefault(u, [u])
        self._alias_map = _build_alias_map(provider_aliases)

    # ------------------------------------------------------------------
    # Loading config
    # ------------------------------------------------------------------

    @staticmethod
    def _load_symbols(path: Path) -> dict:
        if not path.exists():
            log.warning("retail_symbols.yaml not found at %s — provider will accept all symbols", path)
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get_json(self, url: str, params: dict, retries: int = 1) -> dict:
        """GET a JSON endpoint with one retry on transient failure."""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.RequestException as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.0)
                    continue
                raise RetailFetchError(f"Network error calling {url}: {e}") from e

            if r.status_code != 200:
                if attempt < retries and 500 <= r.status_code < 600:
                    time.sleep(1.0)
                    continue
                raise RetailFetchError(f"HTTP {r.status_code} from {url}: {r.text[:200]!r}")

            try:
                return r.json()
            except ValueError as e:
                raise RetailParseError(f"Non-JSON response from {url}: {e}") from e

        # Unreachable, but keeps type-checkers happy.
        raise RetailFetchError(f"Exhausted retries for {url}: {last_err}")

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _login(self) -> str:
        payload = self._get_json(
            f"{API_BASE}/login.json",
            params={"email": self.email, "password": self.password},
        )
        if payload.get("error", True):
            msg = payload.get("message") or "unknown error"
            raise RetailAuthError(f"Myfxbook login failed: {msg}")
        session_id = payload.get("session")
        if not session_id:
            raise RetailAuthError("Myfxbook login returned no session id")
        # Myfxbook returns the session URL-encoded (contains %2B, %2F, %3D).
        # If we hand it back to requests.params= as-is, the % signs get
        # double-encoded as %25 and the next API call sees an invalid session.
        return unquote(str(session_id))

    def _logout(self, session_id: str) -> None:
        try:
            self._get_json(f"{API_BASE}/logout.json", params={"session": session_id})
        except Exception as e:
            # Logout failure is not fatal; just log and move on so we don't
            # mask the real error in the caller.
            log.warning("Myfxbook logout failed (ignored): %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Verify credentials by logging in and out. Returns True on success."""
        try:
            session_id = self._login()
        except RetailAuthError:
            return False
        try:
            return True
        finally:
            self._logout(session_id)

    def fetch_all(self) -> FetchResult:
        session_id = self._login()
        try:
            payload = self._get_json(
                f"{API_BASE}/get-community-outlook.json",
                params={"session": session_id},
            )
            if payload.get("error", True):
                raise RetailFetchError(
                    f"Myfxbook outlook error: {payload.get('message', 'unknown')}"
                )

            snapshots = self._parse_symbols(payload)
            general = self._parse_general(payload)

            return FetchResult(
                fetched_at=datetime.now(timezone.utc),
                provider=self.name,
                snapshots=snapshots,
                general=general,
            )
        finally:
            self._logout(session_id)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _normalize_symbol(self, provider_name: str) -> str | None:
        """Map a provider symbol to the universal symbol, if configured."""
        if not provider_name:
            return None
        key = provider_name.strip().upper()
        return self._alias_map.get(key)

    def _parse_symbols(self, payload: dict) -> list[RetailSnapshot]:
        items = payload.get("symbols")
        if not isinstance(items, list):
            raise RetailParseError("Myfxbook outlook payload missing 'symbols' list")

        out: list[RetailSnapshot] = []
        unmapped: list[str] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            provider_sym = str(item.get("name", "")).strip()
            universal = self._normalize_symbol(provider_sym)
            if universal is None:
                unmapped.append(provider_sym)
                continue

            long_pct = _strip_number(item.get("longPercentage"))
            short_pct = _strip_number(item.get("shortPercentage"))
            if long_pct is None or short_pct is None:
                log.warning("Skipping %s: missing long/short percentage", provider_sym)
                continue

            total = long_pct + short_pct
            if total < 95.0 or total > 105.0:
                log.warning(
                    "Skipping %s: long+short=%.2f%% outside 95-105%% tolerance",
                    provider_sym, total,
                )
                continue

            out.append(
                RetailSnapshot(
                    symbol=universal,
                    long_pct=float(long_pct),
                    short_pct=float(short_pct),
                    long_volume=_strip_number(item.get("longVolume")),
                    short_volume=_strip_number(item.get("shortVolume")),
                    long_positions=_strip_int(item.get("longPositions")),
                    short_positions=_strip_int(item.get("shortPositions")),
                    total_positions=_strip_int(item.get("totalPositions")),
                    avg_long_price=_strip_number(item.get("avgLongPrice")),
                    avg_short_price=_strip_number(item.get("avgShortPrice")),
                    raw=dict(item),
                )
            )

        if unmapped:
            log.info(
                "Myfxbook: %d unmapped symbols ignored (e.g. %s)",
                len(unmapped),
                ", ".join(sorted(set(unmapped))[:10]),
            )

        return out

    @staticmethod
    def _parse_general(payload: dict) -> GeneralStats:
        g = payload.get("general")
        if not isinstance(g, dict):
            return GeneralStats(None, None, None, None, None, None)
        return GeneralStats(
            real_account_pct=_strip_number(g.get("realAccountsPercentage")),
            demo_account_pct=_strip_number(g.get("demoAccountsPercentage")),
            profitable_pct=_strip_number(g.get("profitablePercentage")),
            nonprofitable_pct=_strip_number(g.get("nonprofitablePercentage")),
            total_funds_usd=_strip_number(g.get("totalFunds")),
            average_deposit_usd=_strip_number(g.get("averageDeposit")),
        )
