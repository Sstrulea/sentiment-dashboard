"""Provider abstraction for retail sentiment data sources.

Concrete providers (Myfxbook, future alternatives) implement
`RetailSentimentProvider` and return a `FetchResult` containing per-symbol
snapshots in a normalized shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class RetailAuthError(RuntimeError):
    """Raised when authentication with the provider fails (bad credentials, locked account)."""


class RetailFetchError(RuntimeError):
    """Raised on network or transient API failure (retryable)."""


class RetailParseError(RuntimeError):
    """Raised when the provider response cannot be parsed into the normalized shape."""


@dataclass(frozen=True)
class RetailSnapshot:
    """Per-symbol retail sentiment snapshot, normalized across providers."""

    symbol: str                      # universal symbol (e.g. "EURUSD")
    long_pct: float                  # 0-100, % of positions long
    short_pct: float                 # 0-100
    long_volume: float | None        # lot-volume USD
    short_volume: float | None
    long_positions: int | None
    short_positions: int | None
    total_positions: int | None
    avg_long_price: float | None
    avg_short_price: float | None
    raw: dict = field(default_factory=dict)  # original payload for forensics


@dataclass(frozen=True)
class GeneralStats:
    """Provider-level meta-statistics (one per fetch)."""

    real_account_pct: float | None
    demo_account_pct: float | None
    profitable_pct: float | None
    nonprofitable_pct: float | None
    total_funds_usd: float | None
    average_deposit_usd: float | None


@dataclass(frozen=True)
class FetchResult:
    fetched_at: datetime             # UTC
    provider: str
    snapshots: list[RetailSnapshot]
    general: GeneralStats


class RetailSentimentProvider(Protocol):
    name: str

    def fetch_all(self) -> FetchResult: ...

    def health_check(self) -> bool: ...
