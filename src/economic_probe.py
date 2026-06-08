"""Read-only coverage probe for the MT5 economic-calendar CSV.

No parquet writes, no scoring — diagnostics only. Reports, per in-scope
currency: event volume, forecast (consensus) availability among importance>=2
releases, and how many target indicators the matcher actually resolves. With
`--dump-events` it lists the DISTINCT event-name strings per country so the
country-keyed matcher in data/economic_indicators.yaml can be reconciled. Also
prints the EUR-by-country breakdown to confirm euro-area scoping (only
"European Union" should feed EUR; Germany/France/etc. drop).

    python -m src.economic_probe
    python -m src.economic_probe --dump-events
    python -m src.economic_probe --country "United States"
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict

import pandas as pd

from .economic_fetch import (
    CompiledMatcher,
    _load_indicators_cfg,
    load_raw,
)

log = logging.getLogger(__name__)

# Currencies in v1 scope.
SCOPE_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


def _importance(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df.get("importance"), errors="coerce").fillna(0).astype(int)


def summarize_coverage(df: pd.DataFrame) -> dict:
    """Per-currency: total events, importance>=2 count, forecast% among those."""
    imp = _importance(df)
    out: dict = {}
    for ccy in SCOPE_CURRENCIES:
        sub = df[df["currency"] == ccy]
        sub_imp = imp[df["currency"] == ccy]
        med_hi = sub[sub_imp >= 2]
        n_hi = int(len(med_hi))
        with_fc = int(med_hi["forecast"].notna().sum()) if n_hi else 0
        out[ccy] = {
            "events": int(len(sub)),
            "med_hi": n_hi,
            "with_forecast": with_fc,
            "forecast_pct": (100.0 * with_fc / n_hi) if n_hi else 0.0,
        }
    return out


def summarize_mapped(df: pd.DataFrame, matcher: CompiledMatcher) -> dict:
    """Per-currency: indicator_key -> # rows the matcher resolves (any importance)."""
    per_ccy: dict[str, Counter] = defaultdict(Counter)
    for r in df.itertuples(index=False):
        key = matcher.match(getattr(r, "country", ""), getattr(r, "event", ""))
        if key is None:
            continue
        per_ccy[getattr(r, "currency", "")][key] += 1
    return {ccy: dict(per_ccy.get(ccy, {})) for ccy in SCOPE_CURRENCIES}


def eur_country_breakdown(df: pd.DataFrame) -> dict[str, int]:
    """Row counts per country among currency==EUR (scoping confirmation)."""
    sub = df[df["currency"] == "EUR"]
    return dict(sub["country"].value_counts())


def distinct_events_by_country(df: pd.DataFrame, countries: list[str]) -> dict[str, Counter]:
    out: dict[str, Counter] = {}
    for c in countries:
        sub = df[df["country"] == c]
        out[c] = Counter(sub["event"].tolist())
    return out


def _print_report(df: pd.DataFrame, matcher: CompiledMatcher, dump_events: bool) -> None:
    coverage = summarize_coverage(df)
    mapped = summarize_mapped(df, matcher)

    span = "—"
    if not df.empty:
        span = f"{df['release_dt'].min().date()} → {df['release_dt'].max().date()}"

    print("\n=== MT5 Economic Calendar — Coverage Probe ===")
    print(f"Total rows: {len(df)}   |   release_dt span (UTC): {span}\n")

    print(f"{'CCY':<5}{'events':>8}{'imp>=2':>8}{'fc':>7}{'fc%':>7}   mapped indicators")
    for ccy in SCOPE_CURRENCIES:
        c = coverage[ccy]
        m = mapped[ccy]
        n_keys = len(m)
        print(
            f"{ccy:<5}{c['events']:>8}{c['med_hi']:>8}{c['with_forecast']:>7}"
            f"{c['forecast_pct']:>6.0f}%   {n_keys} keys: "
            f"{', '.join(f'{k}={v}' for k, v in sorted(m.items()))}"
        )

    # GATE: is non-US forecast effectively present?
    non_us = [c for c in SCOPE_CURRENCIES if c != "USD"]
    non_us_fc = sum(coverage[c]["with_forecast"] for c in non_us)
    non_us_hi = sum(coverage[c]["med_hi"] for c in non_us)
    print("\n--- GATE: non-US consensus ---")
    print(f"US forecast (imp>=2): {coverage['USD']['forecast_pct']:.0f}% "
          f"({coverage['USD']['with_forecast']}/{coverage['USD']['med_hi']})")
    print(f"Non-US forecast (imp>=2, all): {non_us_fc}/{non_us_hi} "
          f"= {(100.0*non_us_fc/non_us_hi if non_us_hi else 0):.0f}%  "
          f"-> {'PRESENT' if non_us_fc > 0 else 'ABSENT'}")

    print("\n--- EUR scoping (currency==EUR by country) ---")
    for country, n in eur_country_breakdown(df).items():
        flag = "  <- scored" if country == "European Union" else "  (dropped: not in matcher)"
        print(f"  {country:<18}{n:>6}{flag}")

    if dump_events:
        names = distinct_events_by_country(df, matcher.countries)
        print("\n=== Distinct event strings per in-scope country (count) ===")
        for country in matcher.countries:
            ctr = names.get(country, Counter())
            print(f"\n--- {country} ({len(ctr)} distinct) ---")
            for name, n in ctr.most_common():
                print(f"  {n:>4}  {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only MT5 economic-calendar coverage probe."
    )
    parser.add_argument("--country", type=str, default=None,
                        help="restrict the report to a single MT5 country name")
    parser.add_argument("--dump-events", action="store_true",
                        help="list distinct event-name strings per in-scope country")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    try:
        df = load_raw()
    except Exception as e:
        log.error("Could not read MT5 calendar CSV: %s", e)
        return 2

    if args.country:
        df = df[df["country"] == args.country].reset_index(drop=True)
        if df.empty:
            log.warning("No rows for country %r.", args.country)

    matcher = CompiledMatcher(_load_indicators_cfg().get("matcher", {}))
    _print_report(df, matcher, dump_events=args.dump_events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
