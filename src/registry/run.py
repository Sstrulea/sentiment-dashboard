"""Registry CLI (phase 5, stage G).

    python -m src.registry.run --mode refresh  [--monthly] [--force]
    python -m src.registry.run --mode snapshot [--asof YYYY-MM-DD]   # Friday after the close
    python -m src.registry.run --mode score                           # Tuesday

Writes only under data/registry/. snapshot refreshes the weekly inputs (and the monthly ones when
due) first; score refreshes the FX it needs, then fills the outcomes that are due."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.registry import core, inputs  # noqa: E402


def last_friday(now: pd.Timestamp) -> pd.Timestamp:
    now = now.normalize()
    return now - pd.Timedelta(days=(now.weekday() - 4) % 7)


def do_score(now: pd.Timestamp) -> dict:
    from scripts.registry.validate_calls import CALLS, FXCALLS, validate
    from src.tree.composite import P5Inputs
    snaps = pd.read_parquet(core.SNAP) if core.SNAP.exists() else pd.DataFrame()
    h10 = core._h10_levels()
    inp = P5Inputs(research_dir=inputs.DIR)
    real = core.realized(snaps["asof"].unique() if len(snaps) else [], h10, inp.fx, inp.rate_at)
    real.to_parquet(core.REALIZED, index=False)
    summary = core.summarise(snaps, real) if len(snaps) and len(real) else []
    rates = pd.read_parquet(ROOT / "data" / "rates.parquet")
    y2 = {c: pd.Series(g["yield_pct"].to_numpy(float), index=pd.DatetimeIndex(g["date"])).sort_index()
          for c, g in rates.groupby("currency")}
    banks = [r for r in validate(CALLS, "bank") if r["status"] == "accepted"]
    fxc = [r for r in validate(FXCALLS, "fx") if r["status"] == "accepted"]
    out = {"scored_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "inputs": inputs.staleness(now.tz_localize(None)),
           "sources": summary, "george_banks": core.score_george_banks(banks, y2, now.tz_localize(None)),
           "george_fx": core.score_george_fx(fxc, h10, snaps) if len(snaps) else {"n": 0},
           "y2_missing": sorted({"NZD", "CHF"} - set(y2))}
    core.SUMMARY.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["refresh", "snapshot", "score"], required=True)
    ap.add_argument("--asof")
    ap.add_argument("--monthly", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--payload", type=Path, default=core.PAYLOAD, help="production payload (default public/data/economic.json)")
    a = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    if a.mode == "refresh":
        print(json.dumps(inputs.refresh("monthly" if a.monthly else "weekly", force=a.force), indent=1))
        return 0
    if a.mode == "snapshot":
        if not a.no_refresh:
            print("weekly:", json.dumps(inputs.refresh("weekly"), indent=1))
            print("monthly:", json.dumps(inputs.refresh("monthly"), indent=1))
        t = pd.Timestamp(a.asof) if a.asof else last_friday(now.tz_localize(None))
        df = core.snapshot(t, payload=a.payload)
        cur = df[(df["asof"] == t) & (df["universe"] == "8")].pivot_table(index="ccy", columns="source", values="rank")
        print(f"snapshot {t.date()}: {len(df)} rows total")
        print(cur.reindex(core.CCY8).to_string())
        return 0
    if not a.no_refresh:
        print("weekly:", json.dumps(inputs.refresh("weekly", jobs={k: v for k, v in inputs.JOBS["weekly"].items()
                                                                if k in ("h10_fx.parquet", "p5_ecb_fx.parquet")}), indent=1))
    out = do_score(now)
    print(f"scored: {sum(1 for s in out['sources'] if s['snapshots'])} source x universe x horizon cells with outcomes; "
          f"George banks {out['george_banks']['n_scored']} scored / {out['george_banks']['n_pending']} pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
