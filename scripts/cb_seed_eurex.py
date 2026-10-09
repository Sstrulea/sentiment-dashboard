"""Seed data/cb/market_quotes/ with the pillars of one Eurex settlement file already on disk (the first Eurex as-of).

    python scripts/cb_seed_eurex.py ~/Downloads/settlement-prices_20261008.csv [--fetched-at 2026-10-09T12:20:00Z] [--data-dir DIR]

The page keeps only the latest file (no archive), so the first as-of comes from a file downloaded by hand from
https://www.eurex.com/ec-en/clear/eurex-otc-clear/settlement-prices. Same parser and rows as the collector (every
`eurex_ois_*` entry of config/cb_sources.yaml); only the partitions that change are rewritten, and re-running on the
same store changes nothing (a fixed `fetched_at` keeps the bytes stable when the seed is rebuilt on a newer main).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import cb_collect as cc                          # noqa: E402
from src.cb_sources.base import load_sources              # noqa: E402
from src.cb_sources.market import EurexOis                # noqa: E402


def seed(csv_path: Path, fetched_at: datetime, data_dir: Path | None = None) -> dict:
    cfg = load_sources()["sources"]
    ids = [sid for sid, c in cfg.items() if c["adapter"] == EurexOis.__name__]
    srcs = [EurexOis(cfg[sid], now=lambda: fetched_at, sid=sid) for sid in ids]
    with open(csv_path, encoding="utf-8", newline="") as fh:
        f = srcs[0].parse(fh, {s.curve_id for s in srcs})
    quotes = [q for s in srcs for q in s.quotes(f)]
    paths = cc.Paths(data_dir)
    store = cc.load_store(paths)
    m = cc.merge_quotes(store, quotes)
    written = cc.write_store(paths, store, m.months)
    return {"asof": f.asof.isoformat(), "rows": len(quotes), "per_source": {s.id: len(s.quotes(f)) for s in srcs},
            "new": m.new, "updated": m.updated, "unchanged": m.unchanged, "written": written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--fetched-at", default="2026-10-09T12:20:00Z", help="UTC time the file was downloaded (stored as fetched_at)")
    ap.add_argument("--data-dir", type=Path)
    a = ap.parse_args(argv)
    at = datetime.fromisoformat(a.fetched_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    print(seed(a.csv, at, a.data_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
