"""Weekly point-in-time scores of every variant → cache (never the repo).

    python scripts/measure/tournament/scores.py main    # 2023-09-22 .. last collected Friday
    python scripts/measure/tournament/scores.py long    # long window, variants without FF (from 2014)

Also prints, per variant and view, the first Friday it is defined (no returns used).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE, CCYS, fridays  # noqa: E402
import collect  # noqa: E402
import variants as VV  # noqa: E402

MAIN = CACHE / "scores_main.parquet"
CATS = CACHE / "cats_main.parquet"
LONG = CACHE / "scores_long.parquet"
LONG_START = pd.Timestamp("2014-01-03 21:00")
FX_DEFINED_MIN = 6        # currencies with a score for an FX variant to count as defined


def load_data(with_recs: bool = True) -> VV.Data:
    recs = {r["week"]: r for r in collect.load()["recs"]} if with_recs else {}
    return VV.Data(recs)


def first_defined(df: pd.DataFrame) -> pd.DataFrame:
    """Per variant: first week FX is defined (>= 6 currencies with a score), and the
    first week each cross instrument class has a score."""
    rows = []
    for v, g in df.groupby("variant"):
        c = g[(g["view"] == "ccy") & g["score"].notna()].groupby("week")["unit"].nunique()
        fx = c[c >= FX_DEFINED_MIN].index.min() if len(c) else None
        x = g[(g["view"] == "cross") & g["score"].notna()]
        rows.append({"variant": v, "fx_start": fx,
                     "index_start": x[x["unit"].isin(VV.INDICES)]["week"].min() if len(x) else None,
                     "metal_start": x[x["unit"].isin(VV.METALS)]["week"].min() if len(x) else None})
    return pd.DataFrame(rows)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "main"
    if cmd == "main":
        D = load_data()
        weeks = sorted(D.recs)
        df = VV.scores_frame(D, weeks)
        df.to_parquet(MAIN, index=False)
        VV.cats_frame(D, weeks).to_parquet(CATS, index=False)
    else:
        D = load_data(with_recs=False)
        last = max(collect.load()["weeks"])
        weeks = fridays(LONG_START, last)
        df = VV.scores_frame(D, weeks, names=[n for n in VV.NO_FF if n != "D1"])
        df.to_parquet(LONG, index=False)
    print(first_defined(df).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
