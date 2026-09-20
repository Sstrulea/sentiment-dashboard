"""Freeze the slice of data/cb that the 1B-2 acceptance tests read into tests/fixtures/cb_engine/.

    python scripts/cb_freeze_engine_fixture.py [--asof 2026-09-18] [--out tests/fixtures/cb_engine]

Everything dated after --asof is dropped (market snapshots by `asof`, official observations by `date`), so the acceptance
numbers do not move when the CI refresh appends new data. Deterministic: same input, same bytes.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import cb_collect as cc          # noqa: E402
from src import cb_store as cs            # noqa: E402


def freeze(asof: date, out: Path, src: Path | None = None) -> dict:
    src_paths = cc.Paths(src)
    out_paths = cc.Paths(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    quotes = {k: r for k, r in cc.load_store(src_paths).items() if r["asof"] <= asof}
    official = {k: r for k, r in cc.load_official_store(src_paths).items() if r["date"] <= asof}
    cs.write(cc.MARKET, out, quotes)
    cs.write(cc.OFFICIAL, out, official)
    for name in ("decisions.parquet", "projections.parquet", "meetings.yaml"):
        shutil.copy2(src_paths.dir / name, out / name)
    (out / "manual").mkdir()
    shutil.copy2(src_paths.manual / "rbnz.yaml", out / "manual" / "rbnz.yaml")
    return {"market_rows": len(quotes), "official_rows": len(official)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asof", type=date.fromisoformat, default=date(2026, 9, 18))
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "cb_engine")
    a = ap.parse_args(argv)
    print(freeze(a.asof, a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
