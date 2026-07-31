"""FAZA 1 (fix/watchdog-per-instrument) — standalone freshness gate.

NOT wired into any `.github/workflows/*.yml` — that file set is explicitly
out of scope for this change. This is a complete, tested, ready-to-add
entrypoint: a future, separate change adds one workflow step
(`run: python -m scripts.check_freshness`), mirroring exactly how
src/pmi_ingest_guard.py was left ready-but-unwired for src/ff_refresh.py.

Exit code semantics (the ONLY thing that should ever wire to a workflow's
`exit 1` -> GitHub's automatic failure email, mirroring the existing pattern
in .github/workflows/econ-refresh.yml's push-retry fallback):

  0  everything checked is fresh, or the only staleness is in `price`
  1  `calendar` or `actuals_pull` is stale — sources GitHub Actions ITSELF
     refreshes hourly; a workflow email is actionable here.

`price` is NEVER a reason to exit 1. It's refreshed exclusively by a
personal Mac via MT5/Wine (scripts/econ_refresh.sh, unconnected to any
GitHub Actions workflow) — a cloud job failing on `price` staleness would
mean an email every time the laptop is off overnight or over a weekend,
which is routine, not a defect (see the "guaranteed false positive" note in
the task). `price`'s per-instrument report is still computed and printed —
so FTSE100-style single-instrument freezes stay VISIBLE in the run log even
though they never page anyone — just not exit-gated from a cloud job that
cannot fix a Mac-side problem regardless.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.economic_render import _freshness  # noqa: E402
from src.price_fetch import PARQUET as PRICE_PARQUET, SYMBOLS_YAML, load_symbol_map  # noqa: E402
from src.price_freshness_guard import per_instrument_freshness, freshness_report  # noqa: E402

# Sources GitHub Actions itself refreshes (econ-refresh.yml): a stale badge
# here means the cloud job ran but the data didn't move — actionable.
# `price` is excluded — see module docstring.
CLOUD_REFRESHED_SOURCES = ("calendar", "actuals_pull")


def _price_report(as_of: pd.Timestamp) -> dict:
    if not PRICE_PARQUET.exists():
        return {"any_stale": False, "stale_count": 0, "stale": [], "no_data": [],
               "fresh_count": 0, "total_count": 0, "error": "price_history.parquet missing"}
    price_df = pd.read_parquet(PRICE_PARQUET)
    _, board_symbols = load_symbol_map(SYMBOLS_YAML)
    per_instrument = per_instrument_freshness(price_df, board_symbols, as_of)
    return freshness_report(per_instrument)


def main() -> int:
    as_of = pd.Timestamp.utcnow().tz_localize(None)

    try:
        general = _freshness(as_of)
    except Exception as e:  # noqa: BLE001 — fail open, never crash the gate itself
        print(f"WARNING: _freshness() unavailable ({e}); skipping cloud-source check.")
        general = {}

    price = _price_report(as_of)

    print("=== Freshness report ===")
    for src in ("calendar", "actuals_pull"):
        v = general.get(src)
        if v:
            print(f"{src}: last_update={v.get('last_update')} age_days={v.get('age_days')} "
                 f"stale={v.get('stale')}")
    print(f"price (informational — never exit-gated): "
         f"{price['stale_count']} stale / {price['total_count']} instruments, "
         f"{len(price['no_data'])} no_data ({', '.join(price['no_data']) or 'none'})")
    if price["stale"]:
        for row in price["stale"]:
            print(f"  STALE {row['symbol']}: last={row['last_date']} age={row['age_days']}d "
                 f"(threshold {row['threshold_days']}d)")

    cloud_stale = [s for s in CLOUD_REFRESHED_SOURCES if general.get(s, {}).get("stale")]
    if cloud_stale:
        print(f"FAIL: cloud-refreshed source(s) stale: {cloud_stale}")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
