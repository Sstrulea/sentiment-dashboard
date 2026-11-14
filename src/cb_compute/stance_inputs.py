"""Stance inputs (phase S0a) - pure dataclasses and the pure selection helpers. No I/O (the loaders are in
src/cb_datasets.py), no stance computation (phase S1).

A `StanceInputs` per bank gathers, for an as-of date: the current mandate version, the latest official observations of
the headline / underlying / secondary / unemployment series (y/y already derived for index series), the neutral estimates,
the projection publication (manual or Fed SEP) and the slack estimate - each with its provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

ROLES = ("headline", "underlying", "secondary", "unemployment", "output_gap", "rate_assumption", "nairu")
TRANSFORMS = ("annual_average", "q4_q4", "yoy_quarter", "fiscal_year", "quarter_level", "year_average_march", "horizon_level")
CONDITIONING = ("own_path", "market", "constant_rate", "unpublished")
NEUTRAL_FLAGS = ("BANK_ESTIMATE", "STAFF_ESTIMATE", "SEP_LONGER_RUN", "MEMBERS_VIEW", "NOT_PUBLISHED")
SLACK_TYPES = ("unemployment_gap", "output_gap", "none")
MANDATE_TYPES = ("price_stability", "dual", "hierarchical")
CHANGE_REASONS = ("bank_framework", "bank_measures", "dashboard_choice")


@dataclass(frozen=True)
class Target:
    kind: str                          # point | band
    value: Optional[float] = None      # point
    low: Optional[float] = None        # band
    high: Optional[float] = None
    midpoint: Optional[float] = None   # only when the bank declares it
    measure: str = ""


@dataclass(frozen=True)
class MandateVersion:
    currency: str
    effective_from: date
    effective_to: Optional[date]
    target: Target
    mandate_type: str
    horizon_text: str
    forward_looking: bool
    headline: str
    underlying: tuple                  # series ids (CAD: trim + median)
    underlying_combine: Optional[str]
    secondary: tuple
    rule_applied: int
    slack_type: str
    neutral_source: str
    neutral_flag: str
    forecasts_source: str
    conditioning: str
    last_verified: Optional[date]

    def current_at(self, d: date) -> bool:
        return self.effective_from <= d and (self.effective_to is None or d <= self.effective_to)


@dataclass(frozen=True)
class Observation:
    series_id: str
    period: str
    value: float                       # as published
    published_as: str                  # index | yoy | rate
    yoy: Optional[float]               # the 12-month rate (published or derived); None for a rate series
    release_date: Optional[date]
    source_url: str


@dataclass(frozen=True)
class NeutralEstimate:
    label: str
    basis: str                         # nominal | real
    point: Optional[float]
    low: Optional[float]
    high: Optional[float]
    published: date
    member: Optional[str] = None
    url: str = ""
    page: str = ""


@dataclass(frozen=True)
class ForecastPoint:
    role: str
    variable: str
    transform: str
    horizon: str
    value: Optional[float]
    low: Optional[float] = None
    high: Optional[float] = None


@dataclass(frozen=True)
class Projection:
    currency: str
    publication: str
    published: date
    conditioning: str
    cutoff: Optional[date]
    source: str                        # manual | fed_sep
    points: tuple                      # ForecastPoint
    url: str = ""


@dataclass
class StanceInputs:
    currency: str
    asof: date
    mandate: Optional[MandateVersion]
    observations: dict = field(default_factory=dict)       # role -> [Observation] (latest per series)
    neutral_status: str = ""
    neutral: list = field(default_factory=list)            # NeutralEstimate
    projection: Optional[Projection] = None
    pending: list = field(default_factory=list)            # human-readable pending items


def current_version(versions: list, d: date) -> Optional[MandateVersion]:
    """The single version valid at `d` (None when none is)."""
    cur = [v for v in versions if v.current_at(d)]
    return cur[0] if len(cur) == 1 else None


def overlaps(versions: list) -> list:
    """Pairs of versions whose [from, to] intervals intersect."""
    out = []
    vs = sorted(versions, key=lambda v: v.effective_from)
    for a, b in zip(vs, vs[1:]):
        if a.effective_to is None or b.effective_from <= a.effective_to:
            out.append((a.effective_from, b.effective_from))
    return out
