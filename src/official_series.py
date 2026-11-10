"""Official-series layer — store + quarter/percent arithmetic (no network).

DISPLAY-ONLY. Official statistics-agency series (e.g. Stats NZ CPI index) used
to show a transform the FF/JBlanked feed does not carry (NZD CPI y/y). Nothing
here is scored: this module is never imported by economic_compute,
economic_render or ff_scoring (guarded by tests/test_official_series.py).

Store: data/official_series.parquet — one row per (series_id, period):
    series_id, period ("2026Q3"), value, value_kind ("index"), source_url,
    fetched_at_utc
APPEND-ONLY: a (series_id, period) once written is never rewritten.
`append_rows` refuses to overwrite and only adds new periods.

Network fetching lives in src/official_sources.py (adapters) and
src/official_fetch.py (the guarded fetcher); this module stays dependency-light
so history_compute can read the store without pulling in any fetch code.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / "data" / "official_series.parquet"
STORE_COLUMNS = ["series_id", "period", "value", "value_kind", "source_url", "fetched_at_utc"]
D1 = Decimal("0.1")


@dataclass(frozen=True)
class OfficialSeriesMeta:
    """How an official series attaches to the scored feed.

    `currency`/`anchor_indicator_key` name the FF series whose publications
    anchor each official point (one point per real anchor print); the anchor's
    reference period is the calendar quarter BEFORE its UTC release date.
    `anchor_transform` is what the anchor's own `actual` measures (checked by
    the fetcher's G1 guard against the same official index)."""
    series_id: str
    currency: str
    anchor_indicator_key: str
    anchor_transform: str          # "QoQ" — what the anchor's actual is
    display_transform: str         # "YoY" — what the official point shows
    value_kind: str                # "index"
    source_ref: str                # display-only provenance host


SERIES_META: dict[str, OfficialSeriesMeta] = {
    "statsnz_cpi": OfficialSeriesMeta(
        series_id="statsnz_cpi", currency="NZD", anchor_indicator_key="cpi_yoy",
        anchor_transform="QoQ", display_transform="YoY", value_kind="index",
        source_ref="stats.govt.nz"),
}


# ------------------------------------------------------------- quarters -----

def quarter_shift(q: str, k: int) -> str:
    """'2026Q3' shifted by k quarters."""
    y, n = int(q[:4]), int(q[-1])
    i = y * 4 + (n - 1) + k
    return f"{i // 4}Q{i % 4 + 1}"


def reference_quarter(release_dt) -> str:
    """Reference quarter of a quarterly publication = the calendar quarter
    before its UTC release date (2023-01-24 -> 2022Q4)."""
    return str(pd.Timestamp(release_dt).to_period("Q") - 1)


# -------------------------------------------------------------- percent -----

def pct_change(cur, base) -> Optional[Decimal]:
    """(cur/base - 1) * 100, one decimal, Decimal ROUND_HALF_UP (never round())."""
    if cur is None or base is None:
        return None
    c, b = Decimal(str(cur)), Decimal(str(base))
    if b == 0:
        return None
    return ((c / b - 1) * 100).quantize(D1, rounding=ROUND_HALF_UP)


def transform(index: dict[str, object], q: str, kind: str) -> Optional[Decimal]:
    lag = {"QoQ": 1, "YoY": 4}[kind]
    return pct_change(index.get(q), index.get(quarter_shift(q, -lag)))


def as_1dp(v) -> Optional[Decimal]:
    """A feed value (float) as a 1-dp Decimal for exact comparison."""
    if v is None or pd.isna(v):
        return None
    return Decimal(str(v)).quantize(D1, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- store -----

def read_store(path: Path = STORE_PATH) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    return pd.read_parquet(path)


def index_map(store: pd.DataFrame, series_id: str) -> dict[str, float]:
    sub = store[store["series_id"] == series_id]
    return dict(zip(sub["period"], sub["value"]))


def append_rows(new_rows: pd.DataFrame, path: Path = STORE_PATH) -> int:
    """Append ONLY (series_id, period) pairs not already stored. Existing rows
    are never rewritten, whatever `new_rows` says about them. Returns the
    number of rows added."""
    store = read_store(path)
    have = set(zip(store["series_id"], store["period"]))
    add = new_rows[[(s, p) not in have for s, p in zip(new_rows["series_id"], new_rows["period"])]]
    add = add.drop_duplicates(subset=["series_id", "period"])[STORE_COLUMNS]
    if add.empty:
        return 0
    out = pd.concat([store[STORE_COLUMNS], add], ignore_index=True) if len(store) else add.reset_index(drop=True)
    out = out.astype({"series_id": str, "period": str, "value": float, "value_kind": str,
                      "source_url": str, "fetched_at_utc": str})
    out.to_parquet(path, index=False)
    return len(add)
