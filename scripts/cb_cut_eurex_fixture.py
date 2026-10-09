"""Cut the Eurex settlement-file fixture of the parser tests from a real file.

    python scripts/cb_cut_eurex_fixture.py ~/Downloads/settlement-prices_20261008.csv [--out tests/fixtures/cb/eurex_settlement_20261008_cut.csv]

Keeps, in the file's own order: every `S` row of the six curves at the pillars, a few `S` rows of those curves OFF the
pillars, a few `Z` rows, and some rows of two decoy curves (EUR.EURIBOR.3M, DKK.DESTR.1D) - so the test sees what the
parser must drop.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cb_sources.market import EurexOis              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CURVES = {"USD.FEDFUNDS.1D", "USD.SOFR.1D", "EUR.ESTR.1D", "GBP.SONIA.1D", "JPY.TONAR.1D", "CHF.SARON.1D"}
DECOYS = {"EUR.EURIBOR.3M", "DKK.DESTR.1D"}
OFF_PILLAR = {1, 2, 8, 15, 400}
Z_KEEP = {7, 31}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "cb" / "eurex_settlement_20261008_cut.csv")
    a = ap.parse_args(argv)
    src = EurexOis({"currency": "USD", "curve_id": "USD.FEDFUNDS.1D", "horizon_months": 36}, sid="eurex_ois_usd")
    kept = 0
    with open(a.csv, encoding="utf-8", newline="") as fh, open(a.out, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out, lineterminator="\n")
        rd = csv.reader(fh)
        w.writerow(next(rd))
        piv = None
        for r in rd:
            if piv is None:
                piv = src.pillars(date.fromisoformat(r[0][:10]))
            off = int(r[2])
            keep = (r[1] in CURVES and ((r[4] == "S" and (off in piv or off in OFF_PILLAR)) or (r[4] == "Z" and off in Z_KEEP))
                    or (r[1] in DECOYS and r[4] == "S" and off in (7, 31, 92)))
            if keep:
                w.writerow(r)
                kept += 1
    print(f"wrote {a.out} ({kept} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
