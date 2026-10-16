"""COT for the Nikkei 225 — the only tournament index without a contract in
data/contracts.yaml (SP500 13874A, NASDAQ 209742, DJIA 124603 are already there;
DAX and FTSE100 have no CFTC contract → n/a). Evaluation only: the rows come from
the dataset the repo uses (publicreporting.cftc.gov jun7-fc8e, src/fetch.py) with
the same columns and the same first report date as data/history.parquet, and are
cached under ~/.cache/macro-tournament/cftc, never in the repo.

Contract: 240743 NIKKEI STOCK AVERAGE YEN DENOM (CME). The USD contract 240741
stops reporting on 2026-03-03, so it cannot cover the window.

    python scripts/measure/tournament/cot_extra.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CFTC_DIR, ROOT  # noqa: E402

from src.fetch import ENDPOINT, NUMERIC_COLS, SELECT_COLS  # noqa: E402

NIKKEI_CODE = "240743"
PATH = CFTC_DIR / f"cot_{NIKKEI_CODE}.parquet"


def fetch(force: bool = False) -> pd.DataFrame:
    if PATH.exists() and not force:
        return pd.read_parquet(PATH)
    import requests
    since = pd.read_parquet(ROOT / "data" / "history.parquet")["report_date_as_yyyy_mm_dd"].min()
    params = {"$select": ",".join(SELECT_COLS),
              "$where": f"cftc_contract_market_code = '{NIKKEI_CODE}' and "
                        f"report_date_as_yyyy_mm_dd >= '{pd.Timestamp(since).date().isoformat()}'",
              "$order": "report_date_as_yyyy_mm_dd ASC", "$limit": 50000}
    r = requests.get(ENDPOINT, params=params, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError(f"CFTC returned no rows for {NIKKEI_CODE}")
    df["report_date_as_yyyy_mm_dd"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    for c in NUMERIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    df = df[SELECT_COLS].drop_duplicates(["report_date_as_yyyy_mm_dd", "cftc_contract_market_code"])
    CFTC_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(PATH, index=False)
    return df


if __name__ == "__main__":
    d = fetch(force="--force" in sys.argv)
    gaps = d["report_date_as_yyyy_mm_dd"].diff().dt.days
    print(len(d), d["report_date_as_yyyy_mm_dd"].min().date(), d["report_date_as_yyyy_mm_dd"].max().date(),
          "gaps>8d:", [(str(d["report_date_as_yyyy_mm_dd"].iloc[i - 1].date()), int(g))
                       for i, g in enumerate(gaps) if g > 8])
