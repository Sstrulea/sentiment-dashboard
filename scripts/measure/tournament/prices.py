"""Daily prices for the tournament — never data/price_history.parquet, never MT5.

  FX       FRED H.10 (factor_scales.FRED_FX through factor_scales._fred), as
           v3_scales.fred_usd_values: USD value of one unit of each currency, USD = 1
  indices  Yahoo Finance via yfinance, Close column, cached in ~/.cache/macro-tournament/yahoo
  metals   Stooq CSVs read as they are from ~/Downloads (xauusd_d.csv, xagusd_d.csv)

Entry = first close on or after the Monday after the Friday; exit = first close on
or after entry + 7·h days (scripts/measure/v3_metals.fwd). Past return over h
weeks (coincident check) = last close on or before the Friday's date vs the last
close on or before that date − 7·h days.

    python scripts/measure/tournament/prices.py fetch   # Yahoo → cache (only when missing)
    python scripts/measure/tournament/prices.py check   # mapping + FRED correlation
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CORR_MIN, CROSS, FRED_DIR, FRED_INDEX, INDICES, METALS, ROOT, STOOQ,  # noqa: E402
                    YAHOO, YAHOO_DIR)

import factor_scales as fs  # noqa: E402


class PriceError(RuntimeError):
    """A price series is missing or incomplete — the run stops (fail-closed)."""


def fred(series: str) -> pd.Series:
    FRED_DIR.mkdir(parents=True, exist_ok=True)
    return fs._fred(series, FRED_DIR).sort_index()


def fred_usd_values() -> dict[str, pd.Series]:
    """USD value of one unit of each currency (FRED H.10), as v3_scales.fred_usd_values."""
    out = {}
    for c, (sid, inv) in fs.FRED_FX.items():
        s = fred(sid)
        out[c] = (1.0 / s) if inv else s
    return out


def yahoo_path(sym: str) -> Path:
    return YAHOO_DIR / f"{YAHOO[sym].lstrip('^')}.csv"


def fetch_yahoo(start: str = "2000-01-01") -> dict:
    """Download every index Close once into the cache; the bar of the fetch day
    (possibly not closed yet) is dropped. Raises PriceError on an empty answer."""
    import yfinance as yf
    YAHOO_DIR.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    info = {}
    for sym, tk in YAHOO.items():
        p = yahoo_path(sym)
        if p.exists():
            continue
        df = yf.download(tk, start=start, interval="1d", auto_adjust=False, progress=False, actions=False)
        if df is None or df.empty or "Close" not in df:
            raise PriceError(f"Yahoo returned nothing for {sym} ({tk})")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        s = df["Close"].dropna()
        s = s[s.index < today]
        s.rename("Close").to_frame().rename_axis("Date").to_csv(p)
        info[sym] = {"ticker": tk, "rows": len(s), "first": str(s.index.min().date()),
                     "last": str(s.index.max().date()), "fetched_utc": str(pd.Timestamp.now(tz="UTC"))}
    if info:
        meta = YAHOO_DIR / "fetch_meta.json"
        old = json.loads(meta.read_text()) if meta.exists() else {}
        meta.write_text(json.dumps({**old, **info}, indent=1))
    return info


def yahoo(sym: str) -> pd.Series:
    p = yahoo_path(sym)
    if not p.exists():
        raise PriceError(f"Yahoo cache missing for {sym}: {p} (run `prices.py fetch`)")
    d = pd.read_csv(p, parse_dates=["Date"])
    return pd.Series(d["Close"].astype(float).values, index=d["Date"]).sort_index().dropna()


def stooq(sym: str) -> pd.Series:
    p = STOOQ[sym]
    if not p.exists():
        raise PriceError(f"Stooq CSV missing for {sym}: {p}")
    d = pd.read_csv(p, parse_dates=["Date"])
    return pd.Series(d["Close"].astype(float).values, index=d["Date"]).sort_index().dropna()


def cross_price(sym: str) -> pd.Series:
    return yahoo(sym) if sym in INDICES else stooq(sym)


def all_prices() -> dict[str, pd.Series]:
    """{unit: daily close}: the 7 non-USD currencies (USD value), indices, metals."""
    out = {c: s for c, s in fred_usd_values().items()}
    for s in CROSS:
        out[s] = cross_price(s)
    return out


def pair_price(usd_values: dict, base: str, quote: str) -> pd.Series:
    """Daily pair price from the USD values (USD = 1), common FRED days only."""
    b = None if base == "USD" else usd_values[base]
    q = None if quote == "USD" else usd_values[quote]
    if b is None:
        return (1.0 / q).dropna()
    if q is None:
        return b
    return (b / q).dropna()


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

def entry_exit(px: pd.Series, t: pd.Timestamp, h: int):
    """(entry date, entry close, exit date, exit close) or None — entry = first close
    on or after the Monday after Friday t, exit = first close on or after entry + 7·h days."""
    monday = t.normalize() + pd.Timedelta(days=3)
    i0 = px.index.searchsorted(monday, side="left")
    if i0 >= len(px):
        return None
    d0 = px.index[i0]
    i1 = px.index.searchsorted(d0 + pd.Timedelta(days=7 * h), side="left")
    if i1 >= len(px):
        return None
    return d0, float(px.iloc[i0]), px.index[i1], float(px.iloc[i1])


def fwd_log_return(px: pd.Series, t: pd.Timestamp, h: int) -> float | None:
    e = entry_exit(px, t, h)
    return None if e is None else math.log(e[3] / e[1])


def past_log_return(px: pd.Series, t: pd.Timestamp, h: int) -> float | None:
    """log(last close ≤ Friday date / last close ≤ Friday date − 7·h days)."""
    d = t.normalize()
    i1 = px.index.searchsorted(d, side="right") - 1
    i0 = px.index.searchsorted(d - pd.Timedelta(days=7 * h), side="right") - 1
    if i0 < 0 or i1 < 0 or i1 <= i0:
        return None
    return math.log(float(px.iloc[i1]) / float(px.iloc[i0]))


def last_friday_with_return(px: pd.Series, h: int) -> pd.Timestamp:
    """The last Friday (21:00 UTC) whose h-week forward return exists in px."""
    t = (px.index.max() - pd.Timedelta(days=7 * h + 3)).normalize()
    t = t - pd.Timedelta(days=(t.weekday() - 4) % 7) + pd.Timedelta(hours=21)
    while fwd_log_return(px, t, h) is None:
        t -= pd.Timedelta(days=7)
    while fwd_log_return(px, t + pd.Timedelta(days=7), h) is not None:
        t += pd.Timedelta(days=7)
    return t


# ---------------------------------------------------------------------------
# Checks: mapping vs data/crossasset_instruments.yaml, FRED correlation
# ---------------------------------------------------------------------------

def mapping_check() -> dict:
    xcfg = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
    insts = xcfg["instruments"]
    src = {**{s: ("yahoo", YAHOO[s]) for s in INDICES}, **{s: ("stooq", STOOQ[s].name) for s in METALS}}
    out = {"config_symbols": sorted(insts), "mapped": sorted(src),
           "missing_in_map": sorted(set(insts) - set(src)), "extra_in_map": sorted(set(src) - set(insts)),
           "rows": {}}
    for s, (vendor, tk) in src.items():
        c = insts.get(s, {})
        out["rows"][s] = {"vendor": vendor, "ticker": tk, "type": c.get("type"), "home_ccy": c.get("home_ccy")}
    out["ok"] = not out["missing_in_map"] and not out["extra_in_map"]
    return out


def fred_correlation() -> dict:
    """Pearson correlation of daily log returns, Yahoo vs FRED, on the common dates."""
    out = {}
    for sym, sid in FRED_INDEX.items():
        y, f = yahoo(sym), fred(sid)
        j = pd.concat([y.rename("y"), f.rename("f")], axis=1, join="inner").dropna()
        r = np.log(j).diff().dropna()
        out[sym] = {"fred": sid, "yahoo": YAHOO[sym], "common_from": str(j.index.min().date()),
                    "common_to": str(j.index.max().date()), "n_returns": int(len(r)),
                    "corr": float(r["y"].corr(r["f"])),
                    "max_abs_level_rel_diff": float((j["y"] / j["f"] - 1).abs().max())}
        out[sym]["ok"] = out[sym]["corr"] > CORR_MIN
    return out


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "fetch":
        print(json.dumps(fetch_yahoo(), indent=1))
        return 0
    res = {"mapping": mapping_check(), "fred_correlation": fred_correlation()}
    print(json.dumps(res, indent=1))
    ok = res["mapping"]["ok"] and all(v["ok"] for v in res["fred_correlation"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
