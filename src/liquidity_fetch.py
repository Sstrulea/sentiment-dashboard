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
COLUMNS_WITH_RESERVES = ["date", "net_liquidity", "walcl", "tga", "rrp", "reserves", "source"]

# S1 choice (PHASE 1, scripts/diag/reserves_gate.py, 2026-08-15): WRBWFRBL over
# WRESBAL — stdev(roc_WRBWFRBL)=0.063356 not > 1.25x stdev(roc_WRESBAL)=0.062990,
# so the default (timing-consistent with Wednesday-level WALCL) stands.
RESERVES_FRED_ID = "WRBWFRBL"

FRED_IDS = {"walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD", "reserves": RESERVES_FRED_ID}

# RRPONTSYD (overnight reverse repo) facility began 2013-09-23. A missing RRP leg
# whose span reaches this date or later is a failed fetch, not pre-facility absence.
RRP_ERA_START = pd.Timestamp("2013-09-23")

# RRPONTSYD is in $ billions on FRED; WALCL/WTREGEN are in $ millions.
RRP_BILLIONS_TO_MILLIONS = 1000.0


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
                           rrp: Optional[pd.DataFrame],
                           reserves: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
    """PURE assembly (no network): given the legs as DataFrames with columns
    (date, WALCL) / (date, WTREGEN) / (date, RRPONTSYD) / (date, WRBWFRBL), build
    net liquidity + reserves on a business-daily index. WALCL required (defines
    the span) and forward-filled weekly→daily; TGA/RRP forward-filled over
    weekends/holidays; RRP missing (pre-facility) → 0. `reserves`, when given, is
    carried in its own `reserves` column and does NOT depend on RRP. Returns the
    frame (COLUMNS, or COLUMNS_WITH_RESERVES when `reserves` is supplied) or None
    if WALCL is missing.

    DEGRADED-BUILD GUARD: a *failed* RRP fetch arrives here as None/empty, which
    is indistinguishable from "pre-facility → 0" only by date. If RRP is missing
    AND the WALCL span reaches into the RRP era (≥ 2013-09, when RRPONTSYD began),
    treating RRP as 0 would silently inflate net liquidity by up to ~2.5T (RRP's
    2022 peak) and clobber good history. In that case net_liquidity (and rrp) are
    blocked (NaN) rather than computed. Reserves has no RRP dependency, so a
    blocked RRP must not also blank it out: if `reserves` was supplied, we still
    return a frame (with reserves populated, net_liquidity NaN) instead of None.
    Without `reserves`, the old all-or-nothing behaviour is unchanged — callers
    keep the existing parquet. A genuine recent ~0 RRP still comes back as a
    non-empty frame from FRED, so it passes the guard."""
    if walcl is None or walcl.empty:
        return None
    reserves_provided = reserves is not None and not reserves.empty
    legs = [d for d in (walcl, tga, rrp, reserves) if d is not None and not d.empty]
    start = walcl["date"].min()
    end = max(d["date"].max() for d in legs)

    rrp_missing = rrp is None or rrp.empty
    nl_blocked = rrp_missing and end >= RRP_ERA_START
    if nl_blocked and not reserves_provided:
        log.warning("RRP fetch returned no data but WALCL span reaches %s (≥ RRP era %s) "
                    "— refusing to assemble a degraded net liquidity (RRP=0 over the RRP era). "
                    "Keeping existing parquet.", end.date(), RRP_ERA_START.date())
        return None

    idx = pd.bdate_range(start, end)

    def _series(df, col):
        if df is None or df.empty or col not in df.columns:
            return pd.Series(np.nan, index=idx)
        s = df.set_index("date")[col].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(idx).ffill()

    walcl_ff = _series(walcl, "WALCL").rename("walcl")             # $ millions
    tga_ff = _series(tga, "WTREGEN").rename("tga")                 # $ millions
    if nl_blocked:
        # RRP genuinely unresolved (not pre-facility) — leave it NaN, not 0.
        rrp_ff = pd.Series(np.nan, index=idx).rename("rrp")
    else:
        # RRPONTSYD is reported in $ BILLIONS on FRED, while WALCL/WTREGEN are in
        # $ MILLIONS. Convert RRP billions → millions (×1000) before subtracting,
        # else a $2.2T RRP subtracts as $2.2M (noise) and net liquidity is inflated.
        rrp_ff = (_series(rrp, "RRPONTSYD").rename("rrp").fillna(0.0)  # pre-facility → 0
                  * RRP_BILLIONS_TO_MILLIONS)

    data = {"walcl": walcl_ff, "tga": tga_ff, "rrp": rrp_ff}
    out_cols = COLUMNS
    if reserves_provided:
        data["reserves"] = _series(reserves, RESERVES_FRED_ID).rename("reserves")
        out_cols = COLUMNS_WITH_RESERVES

    out = pd.DataFrame(data)
    out = out.dropna(subset=["walcl"])                            # need WALCL present
    out["tga"] = out["tga"].fillna(0.0)
    out["net_liquidity"] = np.nan if nl_blocked else (out["walcl"] - out["tga"] - out["rrp"])
    out = out.reset_index().rename(columns={"index": "date"})
    out["source"] = "fred"
    return out[out_cols]


def build_net_liquidity() -> Optional[pd.DataFrame]:
    """Fetch the FRED legs (incl. reserves) and assemble net liquidity + reserves
    (None if WALCL missing)."""
    walcl = _fetch_leg(FRED_IDS["walcl"])
    if walcl is None or walcl.empty:
        log.warning("WALCL missing — cannot build net liquidity.")
        return None
    out = assemble_net_liquidity(walcl, _fetch_leg(FRED_IDS["tga"]), _fetch_leg(FRED_IDS["rrp"]),
                                  reserves=_fetch_leg(FRED_IDS["reserves"]))
    if out is not None and len(out):
        nl_latest = out["net_liquidity"].iloc[-1]
        nl_str = f"{nl_latest:.1f}" if pd.notna(nl_latest) else "BLOCKED"
        res_str = (f" reserves={out['reserves'].iloc[-1]:.1f}"
                   if "reserves" in out.columns and pd.notna(out["reserves"].iloc[-1]) else "")
        log.info("net_liquidity built: n=%d %s → %s latest=%s%s",
                 len(out), out["date"].min().date(), out["date"].max().date(), nl_str, res_str)
    return out


def _merge(new: pd.DataFrame) -> pd.DataFrame:
    """Merge `new` into the parquet, per-column: a column value in `new` wins
    when present, else the existing value is kept. This is what lets a run with
    a blocked net_liquidity (NaN, RRP failed) still persist a resolved reserves
    column, without a bad run erasing good historical net_liquidity — plain
    last-write-wins row dedup would do either of those wrong."""
    new = new.copy()
    new["date"] = pd.to_datetime(new["date"])
    if NET_LIQUIDITY_FILE.exists():
        existing = pd.read_parquet(NET_LIQUIDITY_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        all_cols = list(dict.fromkeys(list(existing.columns) + list(new.columns)))
        combined = (
            new.reindex(columns=all_cols).set_index("date")
            .combine_first(existing.reindex(columns=all_cols).set_index("date"))
            .reset_index()
        )
    else:
        combined = new
    combined = (
        combined.dropna(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    NET_LIQUIDITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(NET_LIQUIDITY_FILE, index=False)
    return combined


def update_net_liquidity() -> pd.DataFrame:
    """Build net liquidity + reserves and merge into the parquet (history-
    preserving; see _merge). Any leg failing (FRED down) → logged + no write."""
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
    # The committed (manually-built) parquet has only date+net_liquidity; the leg
    # breakdown is present only on a fresh 6-col build. Guard so the report (hence
    # the scheduler's liquidity step) never crashes when walcl/tga/rrp are absent.
    if {"walcl", "tga", "rrp"}.issubset(sub.columns):
        nl_latest = sub["net_liquidity"].iloc[-1]
        nl_str = f"{nl_latest:.1f}" if pd.notna(nl_latest) else "BLOCKED (RRP unresolved)"
        print(f"  latest net_liquidity={nl_str}  "
              f"(WALCL {sub['walcl'].iloc[-1]:.1f} − TGA {sub['tga'].iloc[-1]:.1f} − RRP {sub['rrp'].iloc[-1]:.1f})")
    else:
        print(f"  latest net_liquidity={sub['net_liquidity'].iloc[-1]:.1f}  "
              f"(leg breakdown unavailable — net_liquidity-only parquet)")
    if "reserves" in sub.columns and pd.notna(sub["reserves"].iloc[-1]):
        print(f"  latest reserves={sub['reserves'].iloc[-1]:.1f}  (source: {RESERVES_FRED_ID}, scored series)")


def _print_scores(df: pd.DataFrame) -> None:
    print("\nLIQUIDITY-PILLAR MOMENTUM (band-based; raw: level falling = + tightening)")
    s = compute_liquidity_score(df)
    if s is None:
        print("  CHOSEN: none — no usable data.")
        return
    roc = "—" if s.roc is None else f"{s.roc*100:+.2f}%/mo"
    print(f"  series={s.series}  score={s.score:+d}  roc(21td)={roc}  latest={s.latest:.1f}  "
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
