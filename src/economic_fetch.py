"""Economic-calendar acquisition from the MT5 calendar CSV.

Source: an MT5 Expert Advisor writes `$MT5_FILES_DIR/economic_calendar.csv`
every ~15 min — a TSV (tab-separated), UTF-16 encoded, 14 columns:

    event_id  server_time  gmt_offset_sec  country  currency  importance
    event  actual  forecast  previous  revised  unit  impact  period

`server_time` is "YYYY.MM.DD HH:MM:SS" in the broker server's timezone;
UTC = server_time - gmt_offset_sec. Values are already in natural units
(3.2 means 3.2, not 0.032); blank => absent => NaN. Speeches/auctions carry
empty actual/forecast and aren't in the indicator taxonomy, so they drop out
naturally.

This module does acquisition + normalization only — no scoring, no rendering.
It writes one parquet:

    data/economic_calendar.parquet

with schema: release_dt (UTC, tz-naive), country, currency, indicator_key,
actual, consensus, previous, unit, event_raw, source. Dedup is last-write-wins
on (currency, indicator_key, release_dt); the output is sorted deterministically
so unchanged data produces no git diff.

NOTE on release_dt: stored tz-naive (representing UTC). The pure compute layer
(`economic_compute.build_payload`) compares release_dt against a tz-naive
`as_of`, so a tz-aware column would raise; keeping it naive-UTC matches the rest
of the repo's parquet convention.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pandas as pd
import yaml

# Load .env once at import so local runs pick up MT5_FILES_DIR.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "economic_calendar.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"

CSV_ENCODING = "utf-16"
CSV_SEP = "\t"

CALENDAR_COLUMNS = [
    "release_dt",
    "country",
    "currency",
    "indicator_key",
    "actual",
    "consensus",
    "previous",
    "unit",
    "event_raw",
    "period",
    "source",
]


def _csv_path() -> Path:
    files_dir = os.environ.get("MT5_FILES_DIR")
    if not files_dir:
        raise RuntimeError(
            "MT5_FILES_DIR not set (env or .env); cannot locate economic_calendar.csv."
        )
    return Path(files_dir) / "economic_calendar.csv"


def _load_indicators_cfg() -> dict:
    with open(INDICATORS_YAML) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Raw load
# ---------------------------------------------------------------------------

def load_raw(csv_path: Path | None = None) -> pd.DataFrame:
    """Read the MT5 CSV and add a UTC (tz-naive) `release_dt`.

    Numeric columns are coerced; rows without a parseable release timestamp are
    dropped. Everything else (country/currency/event strings) is kept as-is.
    """
    path = csv_path or _csv_path()
    df = pd.read_csv(path, sep=CSV_SEP, encoding=CSV_ENCODING, dtype=str)

    for c in ("actual", "forecast", "previous", "revised"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    off = pd.to_numeric(df["gmt_offset_sec"], errors="coerce").fillna(0).astype("int64")
    st = pd.to_datetime(df["server_time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    # server_time is broker-local; subtract the offset to get UTC wall-clock.
    df["release_dt"] = st - pd.to_timedelta(off, unit="s")

    df = df.dropna(subset=["release_dt"]).reset_index(drop=True)
    for c in ("country", "currency", "event", "unit", "period"):
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Matcher (country-keyed) — see data/economic_indicators.yaml `matcher`.
# ---------------------------------------------------------------------------

class CompiledMatcher:
    """Precompiled country -> [(regex, indicator_key)] from the YAML matcher."""

    def __init__(self, matcher_cfg: dict) -> None:
        self._by_country: dict[str, list[tuple[re.Pattern, str]]] = {}
        for country, rules in (matcher_cfg or {}).items():
            compiled: list[tuple[re.Pattern, str]] = []
            for rule in rules or []:
                compiled.append(
                    (re.compile(rule["pattern"], re.IGNORECASE), rule["indicator"])
                )
            self._by_country[country] = compiled

    def match(self, country: str, event: str) -> str | None:
        if not event:
            return None
        for rx, indicator in self._by_country.get(country, []):
            if rx.search(event):
                return indicator
        return None

    @property
    def countries(self) -> list[str]:
        return list(self._by_country)


def match_event(country: str, event: str, matcher: CompiledMatcher) -> str | None:
    """Return the indicator_key for an event under `country`, else None."""
    return matcher.match(country, event)


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def normalize(df: pd.DataFrame, matcher: CompiledMatcher) -> pd.DataFrame:
    """Map raw MT5 rows to the calendar schema, dropping unmapped/out-of-scope.

    Returns a DataFrame with CALENDAR_COLUMNS (release_dt tz-naive UTC).
    """
    rows: list[dict] = []
    for r in df.itertuples(index=False):
        country = getattr(r, "country", "")
        event = getattr(r, "event", "")
        key = matcher.match(country, event)
        if key is None:
            continue
        period = str(getattr(r, "period", "") or "").strip()
        rows.append({
            "release_dt": getattr(r, "release_dt"),
            "country": country,
            "currency": getattr(r, "currency", ""),
            "indicator_key": key,
            "actual": getattr(r, "actual"),
            "consensus": getattr(r, "forecast"),
            "previous": getattr(r, "previous"),
            "unit": getattr(r, "unit", ""),
            "event_raw": event,
            "period": period,            # MT5 reference period → exact flash/final dedup
            "source": "mt5",
        })

    if not rows:
        return pd.DataFrame(columns=CALENDAR_COLUMNS)
    out = pd.DataFrame(rows, columns=CALENDAR_COLUMNS)
    out["release_dt"] = pd.to_datetime(out["release_dt"])
    return out


def count_unmapped(df: pd.DataFrame, matcher: CompiledMatcher) -> tuple[int, list[str]]:
    """Count + sample raw events that fall in a matched country but map to nothing.

    Restricted to the in-scope countries (others are intentionally dropped, not
    "coverage gaps"). Returns (count, up-to-15 distinct sample names).
    """
    scope = set(matcher.countries)
    seen: dict[str, int] = {}
    n = 0
    for r in df.itertuples(index=False):
        country = getattr(r, "country", "")
        if country not in scope:
            continue
        event = getattr(r, "event", "")
        if matcher.match(country, event) is not None:
            continue
        # Only count events that actually carry data (forecast or actual) — pure
        # speeches/auctions with no numbers aren't real coverage gaps.
        actual = getattr(r, "actual")
        forecast = getattr(r, "forecast")
        if pd.isna(actual) and pd.isna(forecast):
            continue
        n += 1
        label = f"{country} | {event}"
        seen[label] = seen.get(label, 0) + 1
    sample = sorted(seen, key=lambda k: -seen[k])[:15]
    return n, sample


# ---------------------------------------------------------------------------
# Merge to parquet
# ---------------------------------------------------------------------------

def merge(new: pd.DataFrame) -> pd.DataFrame:
    """Append `new` to the parquet, dedup last-write-wins, write deterministically."""
    if PARQUET.exists():
        existing = pd.read_parquet(PARQUET)
        existing["release_dt"] = pd.to_datetime(existing["release_dt"])
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new

    combined = (
        combined.sort_values(["currency", "indicator_key", "release_dt"])
        .drop_duplicates(["currency", "indicator_key", "release_dt"], keep="last")
        .reset_index(drop=True)
    )
    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(PARQUET, index=False)
    return combined


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_economic_calendar(days_back: int = 4) -> int:
    """Read the MT5 CSV, normalize, merge into parquet. Returns # rows written this run.

    `days_back` limits the freshly-ingested slice to recent releases (the CSV
    carries ~540 days of history; on each 15-min cron we only need the tail).
    A days_back < 0 ingests the full file (useful for a one-shot backfill). An
    empty/absent CSV is a hard error (the EA should always produce one); an
    empty *match* set is a no-op.
    """
    matcher = CompiledMatcher(_load_indicators_cfg().get("matcher", {}))

    raw = load_raw()
    if days_back is not None and days_back >= 0 and not raw.empty:
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days_back)
        raw = raw[raw["release_dt"] >= cutoff]

    new = normalize(raw, matcher)

    n_unmapped, sample = count_unmapped(raw, matcher)
    if n_unmapped:
        log.warning(
            "Unmapped in-scope events carrying data: %d (distinct sample: %s)",
            n_unmapped, "; ".join(sample) if sample else "—",
        )

    if new.empty:
        log.info("No matching releases in the ingested window; parquet unchanged.")
        return 0

    total = len(merge(new))
    log.info(
        "Normalized %d release(s); economic_calendar.parquet now has %d rows.",
        len(new), total,
    )
    return len(new)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    import argparse

    ap = argparse.ArgumentParser(description="Ingest the MT5 economic calendar CSV.")
    ap.add_argument("--days-back", type=int, default=4,
                    help="ingest releases newer than N days ago (-1 = full file)")
    args = ap.parse_args()
    n = update_economic_calendar(days_back=args.days_back)
    print(f"Wrote {n} normalized release(s).")
