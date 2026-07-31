"""FAZA 4 — country/local-time ingest guard (docs/proposal-pmi-ingest-guard.md).

Catches the SAME failure mode as the 2026-07-30 PMI decontamination (GBP/CAD
tagged on other countries' generic-titled PMI releases) at ingest, not years
later in an audit: convert each row's `datetime_utc` to ITS OWN currency's
timezone, compare against that series' trailing dominant local time, and
quarantine (never silently drop) anything that deviates too far.

Mirrors `ff_fred_crosscheck.py`'s architecture exactly: a PURE function that
returns quarantine candidates (currency, canonical_id, datetime_utc,
local_hm, dominant_local_hm, deviation_hours, reason) — it does not write
`data/ff_quarantine.parquet` itself. Wiring the write call into the live
refresh pipeline is `src/ff_refresh.py`'s job (mirroring how `crosscheck_us`
is wired in there today) — OUT OF SCOPE here (`src/ff_refresh.py` is
explicitly excluded from this task). This module is complete, tested, and
ready to be wired in by a future, separate change.

Local hour (not UTC) is the whole point: it stays constant across a DST
transition (verified empirically for all 8 currencies in Faza 3a/3c — CHF/
JPY/EUR/GBP/CAD's own correctly-labeled series all hold a fixed local
release time year-round), so a trailing MODE of local hour never needs a
DST correction and never flags a normal DST shift as a deviation.

KNOWN GAP — the 76-row US S&P Global Final PMI cluster (09:45 America/
New_York, docs/pmi-reattribution-before-after.md) is NOT covered by this
guard, and can never be, as currently designed: the guard flags a deviation
from a series' OWN trailing dominant local hour — it has nothing to compare
against when USD has never had a single correctly-labeled S&P Global PMI
print to begin with (unlike EUR/JPY/CHF, whose own 2026 copies gave this
guard a real baseline). If JBlanked ever resumes mislabeling that same
09:45-ET pattern under GBP/CAD's currency tag again, this guard stays
silent — there is no USD baseline for the row to deviate from, and the
GBP/CAD side may itself show a plausible (if wrong) dominant hour once
enough contaminated rows accumulate. Closing this gap requires the same
taxonomy decision noted in FAZA 3 (a USD S&P Global PMI indicator_key,
separate from ISM) — until then, this is a real, standing blind spot, not
a hypothetical one.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

# Same 8-currency map validated (against real series, zero false positives)
# in the PMI decontamination measurement (docs/pmi-decontamination-before-
# after.md) and reused unchanged here.
CCY_TIMEZONE: dict[str, str] = {
    "GBP": "Europe/London",
    "EUR": "Europe/Berlin",
    "USD": "America/New_York",
    "CAD": "America/Toronto",
    "JPY": "Asia/Tokyo",
    "AUD": "Australia/Sydney",
    "NZD": "Pacific/Auckland",
    "CHF": "Europe/Zurich",
}

# interest_rate_decision has no fixed announcement time (central bank
# decisions), so it has no meaningful "dominant" local hour to compare
# against — excluded entirely, per the proposal's risk analysis. Every
# in-scope currency's rate-decision canonical_id ends this way (e.g.
# 'usd_fed_interest_rate_decision', 'cad_boc_interest_rate_decision') —
# matching on the suffix keeps this module independent of
# data/economic_indicators.yaml (econ_calendar_ff.py, whose schema this
# mirrors, has no dependency on that file either).
_RATE_DECISION_SUFFIX = "interest_rate_decision"

DEFAULT_TRAILING_N = 24         # trailing window (released prints), per the proposal
DEFAULT_DEVIATION_HOURS = 2.0   # flag threshold: 3 confirmed contamination cases were 4-14h+ off;
                                # the 3 exonerated legitimate schedule-drift cases were within this band
DEFAULT_MIN_PRINTS = 8          # floor below which a trailing mode isn't reliable (thin-baseline series)


def _local_hm(dt_utc: pd.Timestamp, tz_name: str) -> float:
    """UTC timestamp -> local hour-of-day as a float (e.g. 9:30 -> 9.5)."""
    ts = pd.Timestamp(dt_utc)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    local = ts.tz_convert(ZoneInfo(tz_name))
    return local.hour + local.minute / 60.0


def _circular_diff(a: float, b: float, period: float = 24.0) -> float:
    """Shortest distance between two hour-of-day values across midnight
    (e.g. 23:45 vs 00:15 is 0.5h apart, not 23.5h)."""
    d = abs(a - b) % period
    return min(d, period - d)


def _mode_hm(values: list[float]) -> float | None:
    """Mode of a list of local-hour floats, rounded to the minute to absorb
    float noise before counting. None if empty."""
    if not values:
        return None
    rounded = [round(v * 60) / 60 for v in values]
    counts: dict[float, int] = {}
    for v in rounded:
        counts[v] = counts.get(v, 0) + 1
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def country_hour_guard(
    df: pd.DataFrame,
    *,
    trailing_n: int = DEFAULT_TRAILING_N,
    deviation_hours: float = DEFAULT_DEVIATION_HOURS,
    min_prints: int = DEFAULT_MIN_PRINTS,
    ccy_timezone: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Pure. `df`: canonical schema (currency, canonical_id, datetime_utc,
    released, actual — same columns `_canonicalize` produces / `crosscheck_us`
    consumes). For each RELEASED row, compare its own-currency local hour
    against the MODE of local hour over the trailing `trailing_n` prior
    released rows in the SAME `canonical_id`. A deviation beyond
    `deviation_hours` (circular, DST-proof) is returned as a quarantine
    candidate — never dropped here, never silently excluded; the caller
    decides what to do with the returned rows (mirrors `crosscheck_us`).

    Skipped, not flagged:
      - unreleased rows (no actual yet — nothing to check)
      - currencies without a timezone mapping (out-of-scope/unknown)
      - any canonical_id ending in "interest_rate_decision" (no fixed time)
      - rows whose series has fewer than `min_prints` PRIOR released prints
        (a trailing mode from a handful of prints isn't reliable)
    """
    tz_map = ccy_timezone if ccy_timezone is not None else CCY_TIMEZONE
    if df is None or df.empty:
        return pd.DataFrame(columns=["currency", "canonical_id", "datetime_utc",
                                     "local_hm", "dominant_local_hm", "deviation_hours", "reason"])

    work = df.copy()
    work["datetime_utc"] = pd.to_datetime(work["datetime_utc"])
    if "released" in work.columns:
        work = work[work["released"].astype(bool)]
    if "actual" in work.columns:
        work = work[pd.to_numeric(work["actual"], errors="coerce").notna()]

    recs: list[dict] = []
    for cid, g in work.groupby("canonical_id", sort=False):
        if cid.endswith(_RATE_DECISION_SUFFIX):
            continue
        g = g.sort_values("datetime_utc")
        currency = g["currency"].iloc[0]
        tz_name = tz_map.get(currency)
        if tz_name is None:
            continue

        local_hms = [_local_hm(dt, tz_name) for dt in g["datetime_utc"]]
        for i in range(len(g)):
            prior = local_hms[max(0, i - trailing_n):i]
            if len(prior) < min_prints:
                continue
            dominant = _mode_hm(prior)
            if dominant is None:
                continue
            deviation = _circular_diff(local_hms[i], dominant)
            if deviation > deviation_hours:
                row = g.iloc[i]
                recs.append({
                    "currency": currency,
                    "canonical_id": cid,
                    "datetime_utc": row["datetime_utc"],
                    "local_hm": round(local_hms[i], 4),
                    "dominant_local_hm": round(dominant, 4),
                    "deviation_hours": round(deviation, 4),
                    "reason": "country_mismatch",
                })

    return pd.DataFrame(recs, columns=["currency", "canonical_id", "datetime_utc",
                                       "local_hm", "dominant_local_hm", "deviation_hours", "reason"])
