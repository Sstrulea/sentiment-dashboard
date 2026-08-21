"""3-level health check for the Economic Dashboard data chain.

Verifies that what the dashboard shows is the latest, correct data from MT5:

    MT5 economic_calendar.csv  →  economic_calendar.parquet  →  economic.json  →  page

    python -m scripts.verify_data            # sample of key indicators
    python -m scripts.verify_data --all      # every indicator in the payload

Checks:
  1. FRESHNESS   — is economic.json newer than the MT5 CSV (dashboard not behind)?
  2. VALUES      — last published `actual` identical across CSV ↔ parquet ↔ JSON?
  3. STALE FLAGS — which indicators are greyed/excluded (silent staleness made visible);
                   also reads /history's own health report (payload["health"], stamped by
                   history_compute.build_payload — FAZA 1G 3.1/3.3): a catalog entry with
                   ZERO real prints (no_data — typo'd indicator_key, matcher regression,
                   a series that should've been removed from econ_catalog.yml) is a
                   FAILURE; a catalog entry whose latest real print fell outside its
                   recency window (stale) is informational, same as /economic's own
                   stale flags above.

Exit code 0 if all PASS (stale flags are informational; history no_data entries are NOT
— see check 3), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.economic_fetch import (
    CompiledMatcher,
    _csv_path,
    _load_indicators_cfg,
    PARQUET,
    load_raw,
    normalize,
)

ROOT = Path(__file__).resolve().parents[1]
ECON_JSON = ROOT / "public" / "data" / "economic.json"
HISTORY_HTML = ROOT / "public" / "history.html"

# (currency, indicator_key) sample for the default value check.
SAMPLE = [
    ("USD", "employment_change"), ("USD", "cpi_yoy"), ("USD", "jobless_claims"),
    ("EUR", "cpi_yoy"), ("EUR", "core_cpi"), ("GBP", "gdp_qoq"),
    ("GBP", "services_pmi"), ("JPY", "cpi_yoy"), ("CAD", "unemployment_rate"),
]


def _latest_published(df: pd.DataFrame):
    """(release_dt.date, actual) of the most recent row with a non-null actual."""
    pub = df[df["actual"].notna()]
    if pub.empty:
        return None
    row = pub.sort_values("release_dt").iloc[-1]
    return pd.Timestamp(row["release_dt"]).date(), float(row["actual"])


_HISTORY_PAYLOAD_RE = re.compile(r"window\.HISTORY_PAYLOAD\s*=\s*(.*?);\s*</script>", re.S)


def _load_history_payload() -> dict | None:
    """Extract window.HISTORY_PAYLOAD from public/history.html — same technique
    as reading any other embedded payload; there is no separate history.json
    (the page embeds its data, per FAZA 1C's "no runtime fetch" design).
    Returns None (not a failure by itself — see check 3) if the page doesn't
    exist yet or can't be parsed; a missing/broken page is caught by check 1's
    freshness logic once history.html is wired into it, not duplicated here."""
    if not HISTORY_HTML.exists():
        return None
    m = _HISTORY_PAYLOAD_RE.search(HISTORY_HTML.read_text())
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Economic Dashboard data health check.")
    ap.add_argument("--all", action="store_true", help="check every indicator, not just a sample")
    args = ap.parse_args(argv)

    failures = 0
    print("=" * 78)
    print("ECONOMIC DASHBOARD — DATA HEALTH CHECK")
    print("=" * 78)

    # ---- load the three layers ------------------------------------------
    csv_path = _csv_path()
    csv_mtime = datetime.fromtimestamp(os.path.getmtime(csv_path), tz=timezone.utc)
    pq = pd.read_parquet(PARQUET)
    pq["release_dt"] = pd.to_datetime(pq["release_dt"])
    payload = json.loads(ECON_JSON.read_text())
    gen = payload.get("generated_at")
    gen_dt = pd.Timestamp(gen).to_pydatetime() if gen else None
    if gen_dt and gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=timezone.utc)

    # CSV → normalized (what fetch WOULD produce right now)
    matcher = CompiledMatcher(_load_indicators_cfg().get("matcher", {}))
    csv_norm = normalize(load_raw(), matcher)

    # ---- Check 1: freshness ---------------------------------------------
    print("\n[1] FRESHNESS")
    print(f"  MT5 csv written     : {csv_mtime:%Y-%m-%d %H:%M UTC}")
    print(f"  dashboard generated : {gen_dt:%Y-%m-%d %H:%M UTC}" if gen_dt else "  dashboard generated : —")
    if gen_dt and gen_dt >= csv_mtime:
        print("  PASS — dashboard rendered after the latest MT5 write.")
    else:
        print("  FAIL — dashboard is OLDER than the MT5 CSV → run `python -m src.main --mode economic`.")
        failures += 1

    # ---- Check 2: value consistency CSV ↔ parquet ↔ JSON ----------------
    print("\n[2] VALUES — last published actual across the three layers")
    pairs = []
    if args.all:
        for ccy, card in payload.get("currencies", {}).items():
            for key in (card.get("breakdown") or {}):
                if key != "rate_expectations":
                    pairs.append((ccy, key))
    else:
        pairs = SAMPLE
    print(f"  {'ccy/indicator':26}{'CSV':>16}{'parquet':>16}{'dashboard':>16}  ok")
    mism = 0
    for ccy, key in pairs:
        c = _latest_published(csv_norm[(csv_norm.currency == ccy) & (csv_norm.indicator_key == key)]) if not csv_norm.empty else None
        p = _latest_published(pq[(pq.currency == ccy) & (pq.indicator_key == key)])
        e = (payload.get("currencies", {}).get(ccy, {}).get("breakdown", {}) or {}).get(key)
        j = (pd.Timestamp(e["release_dt"]).date(), float(e["actual"])) if e and e.get("actual") is not None else None
        def fmt(t):
            return f"{t[1]:g}@{t[0]}" if t else "—"
        # values must agree; the date may differ by ≤1 day (tz: CSV server vs UTC).
        vals = [x[1] for x in (c, p, j) if x]
        val_ok = len(vals) >= 2 and max(vals) - min(vals) < 1e-9
        ok = "✅" if val_ok else "⚠️"
        if not val_ok:
            mism += 1
        print(f"  {ccy} {key:21}{fmt(c):>16}{fmt(p):>16}{fmt(j):>16}  {ok}")
    if mism == 0:
        print("  PASS — values match across CSV, parquet and dashboard.")
    else:
        print(f"  FAIL — {mism} indicator(s) differ (fetch/render behind, or a mapping/dedup issue).")
        failures += 1

    # ---- Check 3: stale flags -------------------------------------------
    print("\n[3] STALE / GREYED (excluded from score — informational)")
    stale = []
    for ccy, card in payload.get("currencies", {}).items():
        for key, e in (card.get("breakdown") or {}).items():
            if e.get("stale"):
                stale.append((ccy, key, e.get("age_days")))
    if stale:
        for ccy, key, age in sorted(stale):
            age_str = f"{age}d" if age is not None else "n/a"
            print(f"  {ccy} {key:22} age={age_str}")
    else:
        print("  none — every scored indicator is within its recency window.")

    # ---- Check 3b: /history catalog health (FAZA 1G 3.3) -----------------
    print("\n[3b] HISTORY CATALOG HEALTH (public/history.html — payload['health'])")
    hist_payload = _load_history_payload()
    if hist_payload is None:
        print("  n/a — public/history.html not found or unparseable (skipped, not a failure;")
        print("        this repo state may simply predate the /history page).")
    else:
        health = hist_payload.get("health", {})
        no_data = health.get("no_data", [])
        hist_stale = health.get("stale", [])
        if no_data:
            print(f"  NO-DATA — {len(no_data)} catalog entr(y/ies) with ZERO real prints "
                 "(typo'd indicator_key, matcher regression, or a slot that should have")
            print("            been removed from data/econ_catalog.yml):")
            for e in sorted(no_data, key=lambda e: (e["currency"], e["indicator_key"])):
                print(f"    {e['currency']} {e['indicator_key']:22} ({e['category']}) — {e['display_label']}")
            print("  FAIL — a broken catalog entry is not a data-availability question, it's a config bug.")
            failures += 1
        else:
            print("  NO-DATA — none. Every catalog entry resolves to at least one real print.")
        if hist_stale:
            print(f"  STALE   — {len(hist_stale)} series informational (real data, but the latest print")
            print("            fell outside its recency window):")
            for e in sorted(hist_stale, key=lambda e: (e["currency"], e["indicator_key"])):
                print(f"    {e['currency']} {e['indicator_key']:22} ({e['category']}) — "
                     f"last print {e['last_print_release_dt'][:10]}, {e['age_days']}d old "
                     f"(max_age={e['max_age_days']}d)")
        else:
            print("  STALE   — none. Every catalog entry's latest real print is within its recency window.")

    print("\n" + "=" * 78)
    print("RESULT:", "✅ ALL PASS" if failures == 0 else f"❌ {failures} CHECK(S) FAILED")
    print("=" * 78)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
