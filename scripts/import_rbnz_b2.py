"""Import the RBNZ B2 wholesale interest rates file downloaded by hand -> NZD 2Y.

The RBNZ site answers automated requests with a Cloudflare wall (HTTP 403,
"Website unavailable"); it is never bypassed. A person downloads the official file
in a browser and drops it in data/manual/rbnz/:

  page   https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates
  file   "Wholesale Interest Rates – B2 Daily close (2018-current)"
         https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/hb2-daily-close.xlsx
         (the older "B2 Daily (1985-2017)" file was retired on 2025-08-25; if a copy is at hand it
         may be dropped in the same folder as hb2-daily.xlsx and is merged, the close file winning)

Provenance: every import writes data/manual/rbnz/PROVENANCE.json (file name, sha256, size,
modified time, rows, column header used). The 2-year column is found from the file's own
header band: a header containing "2 year" (or "2-year") and "government"/"bond".

    python scripts/import_rbnz_b2.py            # report what would be imported
    python scripts/fetch_backbone.py --only Y2_NZD_MANUAL   # write Y2_NZD into the backbone
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MANUAL_DIR = ROOT / "data" / "manual" / "rbnz"
FILES = ["hb2-daily-close.xlsx", "hb2-daily.xlsx"]       # first wins a shared date
URL = ("https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/"
       "hb2-daily-close.xlsx")


def find_2y_column(rows: list[tuple]) -> tuple[int, str]:
    """(column index, header text) of the 2-year government bond column, from the
    first 15 rows. Raises ValueError when none or several match."""
    hits = {}
    for row in rows[:15]:
        for i, cell in enumerate(row):
            c = str(cell or "").lower()
            if re.search(r"\b2[- ]year", c) and ("government" in c or "bond" in c) and "swap" not in c:
                hits[i] = str(cell)
    if len(hits) != 1:
        raise ValueError(f"expected one 2-year government bond column, found {hits}")
    return next(iter(hits.items()))


def parse(path: Path) -> tuple[pd.DataFrame, str]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["Data"] if "Data" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    col, header = find_2y_column(rows)
    out = []
    for r in rows:
        if not r or col >= len(r):
            continue
        d = pd.to_datetime(r[0], errors="coerce")
        v = pd.to_numeric(r[col], errors="coerce")
        if pd.notna(d) and pd.notna(v):
            out.append((d.normalize(), float(v)))
    if not out:
        raise ValueError(f"{path.name}: no numeric rows under {header!r}")
    return pd.DataFrame(out, columns=["date", "value"]), header


def load_manual(directory: Path = MANUAL_DIR) -> tuple[pd.DataFrame, str]:
    found = [directory / f for f in FILES if (directory / f).exists()]
    if not found:
        raise FileNotFoundError(f"no RBNZ B2 file in {directory} (expected {FILES[0]} from {URL})")
    frames, prov = [], []
    for f in found:
        df, header = parse(f)
        frames.append(df)
        b = f.read_bytes()
        prov.append({"file": f.name, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
                     "modified": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "column": header, "rows": len(df), "first": str(df["date"].min().date()),
                     "last": str(df["date"].max().date())})
    df = pd.concat(frames).drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)
    (directory / "PROVENANCE.json").write_text(json.dumps(
        {"source": "Reserve Bank of New Zealand, B2 wholesale interest rates (official)",
         "transport": "manual download in a browser", "url": URL, "files": prov}, indent=1) + "\n")
    return df, "RBNZ:B2 2-year govt bond (manual: " + ", ".join(p["file"] for p in prov) + ")"


def main() -> int:
    try:
        df, note = load_manual()
    except FileNotFoundError as e:
        print(e)
        return 1
    print(f"{note}: {len(df)} rows {df['date'].min().date()} -> {df['date'].max().date()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
