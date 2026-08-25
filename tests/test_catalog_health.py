"""FAZA 2C 5.3 — catalog health invariant: no entry in data/econ_catalog.yml
may be STALE or below the minimum print-count threshold. This is deliberately
a TEST, not a runtime UI banner (history_compute already has one of those,
FAZA 1G 3.2) — the point is to fail LOUDLY at generation/test time so a
series drifting into staleness or thinness is caught before anyone has to
notice it by eye on the page.

Uses TODAY's real time (not a frozen as_of) and the real, on-disk parquet —
deliberately not a synthetic fixture, because "still healthy right now" only
means something measured against real data. Skips (not xfails) when that
parquet isn't present, matching test_history_compute.py's own convention for
data-dependent tests.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src import history_compute as hc
from src.data_integrity import build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros
from src.ff_scoring import CCY2COUNTRY, build_matcher

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"

# FAZA 2C 5.1/5.2 — the same print-count bar applied throughout this
# cleanup (AUD Trimmed Mean CPI MoM removed at n=9; CHF GDP QoQ flagged at
# n=11 as ELIMINĂ_PROPUS): below this, a series is too young to trust as a
# catalog entry, regardless of whether it happens to already be has_data and
# non-stale today.
CATALOG_MIN_PRINTS = 12


def test_no_duplicate_selection_key_within_category_currency():
    """FAZA 2E-2 — history.js selects a series by indicator_key, unique per
    (category, currency) by contract. Before this phase it selected by
    `role` instead (market/secondary/policy) — a display-grouping label, not
    an identifier: labor/USD (unemployment_rate + wage_growth) and labor/GBP
    (unemployment_rate + employment_change) each carry TWO entries under
    role "secondary". Selecting by role meant both chips showed active
    simultaneously and resolveEntry always returned the first match — the
    second series was permanently unreachable by click, silently, for
    months (FAZA 2E). Pure structural check against the catalog file
    itself — no parquet needed, this is a catalog-authoring invariant, not
    a data-freshness one.
    """
    catalog = hc.load_catalog()
    failures = []
    for cat, ccys in catalog.get("categories", {}).items():
        for ccy, entries in ccys.items():
            keys = [e.get("indicator_key") for e in entries if e.get("indicator_key") is not None]
            dupes = {k for k in keys if keys.count(k) > 1}
            if dupes:
                failures.append(f"{cat}/{ccy}: indicator_key(s) {sorted(dupes)} appear more than once")
    assert not failures, "Duplicate selection key(s) found:\n" + "\n".join(failures)


def _build_quarantine_df(ff, matcher):
    df = ff.copy()
    df["indicator_key"] = df.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]), axis=1)
    df = df.rename(columns={"datetime_utc": "release_dt"})[
        ["currency", "indicator_key", "canonical_id", "name_raw", "release_dt",
         "actual", "forecast", "previous"]]
    ghosts = detect_ghost_rows(df)
    zeros = detect_implausible_zeros(df)
    return build_quarantine_proposal(ghosts, zeros)


@pytest.mark.skipif(not FF_PARQUET.exists(), reason="requires local data/economic_calendar_ff.parquet")
def test_no_catalog_series_is_stale_or_below_print_threshold():
    as_of = pd.Timestamp.now()
    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    ind_cfg = hc.load_indicators_cfg()
    catalog = hc.load_catalog()
    matcher = build_matcher()
    quarantine_df = _build_quarantine_df(ff, matcher)
    series_cache = hc.compute_catalog(ff, ind_cfg, catalog, quarantine_df, as_of=as_of)
    payload = hc.build_payload(catalog, series_cache, catalog_version="test", ind_cfg=ind_cfg, as_of=as_of)

    failures = []
    seen = set()
    for cat, ccys in catalog.get("categories", {}).items():
        for ccy, entries in ccys.items():
            for entry in entries:
                key = entry.get("indicator_key")
                if key is None or (ccy, key) in seen:
                    continue
                seen.add((ccy, key))
                label = entry.get("display_label") or key
                n_printed = int(series_cache[(ccy, key)]["actual"].notna().sum())

                # has_data/stale come straight from the payload — the exact
                # computation the page itself already uses (FAZA 1G 3.1),
                # not re-derived here.
                payload_entry = next(
                    e for e in payload["categories"][cat][ccy] if e.get("indicator_key") == key)
                if not payload_entry.get("has_data"):
                    failures.append(f"{cat}/{ccy}/{key} ({label}): has_data=False")
                elif payload_entry.get("stale"):
                    failures.append(
                        f"{cat}/{ccy}/{key} ({label}): STALE — last print "
                        f"{payload_entry.get('last_print_release_dt')}, "
                        f"{payload_entry.get('age_days')}d old")
                if n_printed < CATALOG_MIN_PRINTS:
                    failures.append(
                        f"{cat}/{ccy}/{key} ({label}): only {n_printed} real prints "
                        f"(< {CATALOG_MIN_PRINTS})")

    assert not failures, "Catalog health check failed:\n" + "\n".join(failures)
