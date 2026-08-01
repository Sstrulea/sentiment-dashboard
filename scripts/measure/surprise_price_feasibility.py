"""measure/surprise-price-link — FAZA 1: feasibility check.

Investigation only. For each (currency, indicator_key) x relevant FX
instrument, counts how many release dates have BOTH a valid surprise
(actual & consensus present) AND a D1 price bar on the aligned date.

Reports counts under TWO candidate day-alignments (see the timezone
finding in docs/measurement-surprise-price-link.md):
  - naive: release_dt's own UTC calendar date
  - eet: release_dt converted to Europe/Bucharest (EET/EEST, UTC+2/+3
    DST-following) before taking the date — the same DST-following
    convention already documented for this project's FF/JBlanked feed
    clock (docs/spike-report-jblanked.md), offered as the best available
    analogy for the MT5 price server's clock, NOT verified for price data.
"""
from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
PRICE_PARQUET = ROOT / "data" / "price_history.parquet"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"

EET = ZoneInfo("Europe/Bucharest")
OUR_CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


def build_scoring_frame() -> pd.DataFrame:
    ffdf = pd.read_parquet(FF_PARQUET)
    ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
    cal = to_scoring_frame(ffdf, build_matcher())
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    return cal


def fx_pairs_for_currency(ccy: str, inst_cfg: dict) -> list[tuple[str, str, int]]:
    """(symbol, other_ccy, sign) — sign=+1 if ccy is base, -1 if quote (a
    positive-direction surprise on `ccy` should move the pair sign*direction)."""
    out = []
    for sym, cfg in (inst_cfg.get("instruments") or {}).items():
        if cfg.get("type") != "fx":
            continue
        base, quote = cfg.get("base"), cfg.get("quote")
        if base == ccy:
            out.append((sym, quote, 1))
        elif quote == ccy:
            out.append((sym, base, -1))
    return out


def main():
    inst_cfg = yaml.safe_load(open(INSTRUMENTS_YAML))
    cal = build_scoring_frame()
    valid = cal[cal["actual"].notna() & cal["consensus"].notna()].copy()
    valid["naive_date"] = valid["release_dt"].dt.normalize()
    valid["eet_date"] = valid["release_dt"].apply(
        lambda ts: pd.Timestamp(pd.Timestamp(ts).tz_localize("UTC").astimezone(EET).date())
    )

    px = pd.read_parquet(PRICE_PARQUET)
    px_symbols = set(px["symbol"].unique())
    px_by_symbol = {s: set(g["date"]) for s, g in px.groupby("symbol")}

    rows = []
    for (ccy, key), g in valid.groupby(["currency", "indicator_key"]):
        pairs = fx_pairs_for_currency(ccy, inst_cfg)
        for sym, other, sign in pairs:
            if sym not in px_symbols:
                continue
            dates = px_by_symbol[sym]
            n_naive = g["naive_date"].isin(dates).sum()
            n_eet = g["eet_date"].isin(dates).sum()
            rows.append({
                "currency": ccy, "indicator_key": key, "symbol": sym,
                "n_valid_surprises": len(g), "n_overlap_naive": int(n_naive),
                "n_overlap_eet": int(n_eet),
            })

    out = pd.DataFrame(rows).sort_values("n_overlap_naive", ascending=False)
    out.to_csv(ROOT / "docs" / "surprise-price-overlap.csv", index=False)

    print(f"{len(out)} (indicator, instrument) pairs total")
    for thresh in [30]:
        n_naive = (out["n_overlap_naive"] >= thresh).sum()
        n_eet = (out["n_overlap_eet"] >= thresh).sum()
        print(f"pairs with n_overlap >= {thresh}: naive={n_naive}, eet={n_eet}")

    print()
    print("=== Top 25 by naive overlap ===")
    pd.set_option("display.width", 160)
    print(out.head(25).to_string(index=False))

    print()
    print("=== JPY/NZD pairs specifically (alignment-sensitive) ===")
    jn = out[out["currency"].isin(["JPY", "NZD"])].sort_values("n_overlap_naive", ascending=False)
    print(jn.to_string(index=False))


if __name__ == "__main__":
    main()
