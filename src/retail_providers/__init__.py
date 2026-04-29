"""Retail sentiment provider implementations.

Each provider exposes a `RetailSentimentProvider` (Protocol) implementation
that returns a `FetchResult` describing per-symbol sentiment plus provider
meta-statistics.
"""
from .base import (
    FetchResult,
    GeneralStats,
    RetailAuthError,
    RetailFetchError,
    RetailParseError,
    RetailSentimentProvider,
    RetailSnapshot,
)
from .myfxbook import MyfxbookProvider

__all__ = [
    "FetchResult",
    "GeneralStats",
    "MyfxbookProvider",
    "RetailAuthError",
    "RetailFetchError",
    "RetailParseError",
    "RetailSentimentProvider",
    "RetailSnapshot",
]
