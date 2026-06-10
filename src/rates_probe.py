"""Read-only data spike for the Rate-Expectations pillar.

Diagnostic only — probes the keyless 2y-yield adapters (now living in
`src.rate_sources`) and prints a coverage matrix + recommendation. Writes
nothing. The fetch/compute engine (`src.rate_fetch`, `src.rate_compute`) reuses
the same adapters.

    python -m src.rates_probe
    python -m src.rates_probe --currency USD --currency EUR
    python -m src.rates_probe --source stooq
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .rate_sources import (
    ALL_SOURCES,
    CURRENCIES,
    SOURCE_PREFERENCE,
    Assessment,
    YieldSeries,
    assess,
)

log = logging.getLogger(__name__)


@dataclass
class Probe:
    currency: str
    source: str
    status: str
    series: Optional[YieldSeries]
    assessment: Optional[Assessment]
    note: str = ""


def probe_currency(currency: str, sources: list[str], today: date) -> list[Probe]:
    out: list[Probe] = []
    for name in sources:
        src = ALL_SOURCES.get(name)
        if src is None or not src.supports(currency):
            continue
        log.info("probing %s via %s ...", currency, name)
        series = src.fetch(currency)
        if series is None:
            out.append(Probe(currency, name, src.last_status or "UNREACHABLE",
                             None, None, src.last_note))
            continue
        a = assess(series, today)
        out.append(Probe(currency, name, a.status, series, a, series.note))
    return out


def choose(probes: list[Probe], pref: list[str]) -> Optional[Probe]:
    order = {n: i for i, n in enumerate(pref)}
    qualified = [p for p in probes if p.assessment and p.assessment.qualifies]
    if not qualified:
        return None
    qualified.sort(key=lambda p: order.get(p.source, 99))
    return qualified[0]


def probe_all(currencies: list[str], sources: list[str]) -> dict[str, list[Probe]]:
    today = date.today()
    return {c: probe_currency(c, [s for s in SOURCE_PREFERENCE[c] if s in sources], today)
            for c in currencies}


def _tail(series: Optional[YieldSeries], n=3) -> str:
    if not series or not series.points:
        return "—"
    return ", ".join(f"{d.isoformat()}={v:g}" for d, v in series.points[-n:])


def report(results: dict[str, list[Probe]]) -> None:
    today = date.today()
    print("\n" + "=" * 92)
    print(f"RATE-EXPECTATIONS DATA SPIKE — ~2y sovereign yields, keyless · {today.isoformat()}")
    print("=" * 92)

    print("\n[A] PER-SOURCE LOG")
    for ccy, probes in results.items():
        print(f"\n  {ccy}")
        for p in probes:
            s = p.series
            if s is None:
                print(f"    {p.source:13} {p.status:11} — {p.note}")
            else:
                lag = s.business_days_lag(today)
                print(f"    {p.source:13} {p.status:11} {s.tenor:>4} {s.frequency:<8} "
                      f"since {s.history_start} latest {s.latest_date} lag={lag}bd n={s.n_points}")
                print(f"      last: {_tail(s)}"
                      + (f"   flags: {','.join(p.assessment.flags)}" if p.assessment and p.assessment.flags else ""))

    print("\n[B] COVERAGE MATRIX (chosen per currency)")
    print(f"  {'CCY':<4}{'source':<14}{'tenor':<6}{'freq':<9}{'latest':<12}{'lag':<7}{'status'}")
    chosen: dict[str, Optional[Probe]] = {}
    for ccy, probes in results.items():
        pick = choose(probes, SOURCE_PREFERENCE[ccy])
        chosen[ccy] = pick
        if pick is None:
            avail = [p for p in probes if p.series]
            ctx = avail[0] if avail else (probes[0] if probes else None)
            if ctx and ctx.series:
                lag = ctx.series.business_days_lag(today)
                print(f"  {ccy:<4}{'(none qualifies)':<14}{ctx.series.tenor:<6}{ctx.series.frequency:<9}"
                      f"{str(ctx.series.latest_date):<12}{str(lag)+'bd':<7}{ctx.status}")
            else:
                print(f"  {ccy:<4}{'(unresolved)':<14}{'—':<6}{'—':<9}{'—':<12}{'—':<7}"
                      f"{ctx.status if ctx else '—'}")
            continue
        s = pick.series
        lag = s.business_days_lag(today)
        print(f"  {ccy:<4}{pick.source:<14}{s.tenor:<6}{s.frequency:<9}"
              f"{str(s.latest_date):<12}{str(lag)+'bd':<7}{pick.status}")

    print("\n[C] VERDICT")
    twoy = [c for c, p in chosen.items() if p and p.series.tenor == "2y"]
    fallback = [c for c, p in chosen.items() if p and p.series.tenor != "2y"]
    bad = [c for c, p in chosen.items() if not p]
    print(f"  qualifies 2y-daily : {', '.join(twoy) or '—'}")
    print(f"  qualifies non-2y   : {', '.join(fallback) or '—'}")
    print(f"  UNRESOLVED         : {', '.join(bad) or '—'}")

    print("\n[D] CHOSEN ADAPTER PER CURRENCY")
    for ccy in results:
        pick = chosen[ccy]
        if pick:
            print(f"  {ccy:<4}-> {pick.source} ({pick.series.tenor} {pick.series.frequency}) [{pick.status}]")
        else:
            print(f"  {ccy:<4}-> UNRESOLVED — validate locally / dedicated parser")
    print("\n[STOP] Spike only — no parquet, no pipeline changes.\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only ~2y-yield source spike.")
    ap.add_argument("--currency", action="append", choices=CURRENCIES)
    ap.add_argument("--source", action="append", choices=list(ALL_SOURCES))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    ccys = args.currency or CURRENCIES
    srcs = args.source or list(ALL_SOURCES)
    report(probe_all(ccys, srcs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
