"""Fetch Fed NET LIQUIDITY → data/net_liquidity.parquet (cross-asset data layer).

Net liquidity = WALCL − TGA − RRP, the global liquidity backdrop (same role as
the real-yield series). All three legs are keyless FRED series (existing
FredSeriesSource):
    WALCL      Fed total assets               (weekly)
    WTREGEN    Treasury General Account (TGA)  (~daily)
    RRPONTSYD  Overnight reverse repo (RRP)    (daily)

Build on a business-daily index: forward-fill WALCL (weekly→daily) and TGA/RRP
over weekends/holidays; RRP before the facility existed → 0. Deterministic,
history-preserving merge. Writes data/net_liquidity.parquet
(cols: date, net_liquidity, walcl, tga, rrp, source).

    python -m src.liquidity_fetch [--scores]

NOTE: FRED is blocked in the CC sandbox; run from the residential terminal.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .rate_sources import FredSeriesSource
from .liquidity_compute import compute_liquidity_score

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
NET_LIQUIDITY_FILE = ROOT / "data" / "net_liquidity.parquet"
COLUMNS = ["date", "net_liquidity", "walcl", "tga", "rrp", "source"]

FRED_IDS = {"walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD"}


def _fetch_leg(fred_id: str) -> Optional[pd.DataFrame]:
    src = FredSeriesSource(fred_id)
    raw = src.fetch_series()
    if raw is None:
        log.warning("%s UNRESOLVED — %s (%s)", fred_id, src.last_status, src.last_note[:60])
        return None
    out = raw[["date", "value"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    return out.rename(columns={"value": fred_id})


def assemble_net_liquidity(walcl: Optional[pd.DataFrame],
                           tga: Optional[pd.DataFrame],
                           rrp: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """PURE assembly (no network): given the three legs as DataFrames with columns
    (date, WALCL) / (date, WTREGEN) / (date, RRPONTSYD), build net liquidity on a
    business-daily index. WALCL required (defines the span) and forward-filled
    weekly→daily; TGA/RRP forward-filled over weekends/holidays; RRP missing
    (pre-facility) → 0. Returns the frame (COLUMNS) or None if WALCL is missing."""
    if walcl is None or walcl.empty:
        return None
    legs = [d for d in (walcl, tga, rrp) if d is not None and not d.empty]
    start = walcl["date"].min()
    end = max(d["date"].max() for d in legs)
    idx = pd.bdate_range(start, end)

    def _series(df, col):
        if df is None or df.empty or col not in df.columns:
            return pd.Series(np.nan, index=idx)
        s = df.set_index("date")[col].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(idx).ffill()

    walcl_ff = _series(walcl, "WALCL").rename("walcl")
    tga_ff = _series(tga, "WTREGEN").rename("tga")
    rrp_ff = _series(rrp, "RRPONTSYD").rename("rrp").fillna(0.0)   # pre-facility → 0

    out = pd.DataFrame({"walcl": walcl_ff, "tga": tga_ff, "rrp": rrp_ff})
    out = out.dropna(subset=["walcl"])                            # need WALCL present
    out["tga"] = out["tga"].fillna(0.0)
    out["net_liquidity"] = out["walcl"] - out["tga"] - out["rrp"]
    out = out.reset_index().rename(columns={"index": "date"})
    out["source"] = "fred"
    return out[COLUMNS]


def build_net_liquidity() -> Optional[pd.DataFrame]:
    """Fetch the three FRED legs and assemble net liquidity (None if WALCL missing)."""
    walcl = _fetch_leg("WALCL")
    if walcl is None or walcl.empty:
        log.warning("WALCL missing — cannot build net liquidity.")
        return None
    out = assemble_net_liquidity(walcl, _fetch_leg("WTREGEN"), _fetch_leg("RRPONTSYD"))
    if out is not None and len(out):
        log.info("net_liquidity built: n=%d %s → %s latest=%.1f",
                 len(out), out["date"].min().date(), out["date"].max().date(),
                 float(out["net_liquidity"].iloc[-1]))
    return out


def _merge(new: pd.DataFrame) -> pd.DataFrame:
    if NET_LIQUIDITY_FILE.exists():
        existing = pd.read_parquet(NET_LIQUIDITY_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new
    combined = (
        combined.dropna(subset=["date", "net_liquidity"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    NET_LIQUIDITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(NET_LIQUIDITY_FILE, index=False)
    return combined


def update_net_liquidity() -> pd.DataFrame:
    """Build net liquidity and merge into the parquet (dedup last-write-wins on
    date, history-preserving). Any leg failing (FRED down) → logged + no write."""
    new = build_net_liquidity()
    if new is None or new.empty:
        log.warning("Net liquidity not resolved this run; parquet unchanged.")
        return pd.read_parquet(NET_LIQUIDITY_FILE) if NET_LIQUIDITY_FILE.exists() else pd.DataFrame(columns=COLUMNS)
    return _merge(new)


def _print_report(df: pd.DataFrame) -> None:
    print(f"\nnet_liquidity.parquet: {len(df)} rows → {NET_LIQUIDITY_FILE}")
    if df.empty:
        print("  (empty — WALCL/TGA/RRP unresolved; run on a network with FRED access)")
        return
    sub = df.sort_values("date")
    first = pd.Timestamp(sub["date"].iloc[0]).date()
    last = pd.Timestamp(sub["date"].iloc[-1]).date()
    today = date.today()
    lag = 0 if last >= today else int(np.busday_count(last, today))
    print(f"  history {first} → {last} (lag {lag}bd)")
    print(f"  latest net_liquidity={sub['net_liquidity'].iloc[-1]:.1f}  "
          f"(WALCL {sub['walcl'].iloc[-1]:.1f} − TGA {sub['tga'].iloc[-1]:.1f} − RRP {sub['rrp'].iloc[-1]:.1f})")


def _print_scores(df: pd.DataFrame) -> None:
    print("\nNET-LIQUIDITY MOMENTUM (band-based; raw: NL falling = + tightening)")
    s = compute_liquidity_score(df)
    if s is None:
        print("  CHOSEN: none — no usable data.")
        return
    roc = "—" if s.roc is None else f"{s.roc*100:+.2f}%/mo"
    print(f"  score={s.score:+d}  roc(21td)={roc}  latest={s.latest:.1f}  "
          f"method={s.method}  stale={s.stale}  as_of={s.as_of}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch Fed net liquidity → data/net_liquidity.parquet")
    ap.add_argument("--scores", action="store_true", help="also print the −2..+2 momentum score")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    df = update_net_liquidity()
    _print_report(df)
    if args.scores:
        _print_scores(df)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
