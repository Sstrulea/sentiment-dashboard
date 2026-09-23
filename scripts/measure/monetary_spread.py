"""measure — monetary on the 2y SPREAD instead of the per-currency difference (audit 4A).

MEASUREMENT ONLY: nothing here feeds a score.

Today, in a pair, the monetary category contributes (monetary_base − monetary_quote),
each leg being its own 2y repricing score (rate_compute: 21-row change, z over the
last 252 changes, bucket 1.0/0.5). The candidate replaces that difference with

    m(b, q) = sign(z) · bucket(|z|; 1.0 / 0.5)
    z       = Δ / std(last 252 Δ, ddof=1)       (≥ 60 Δ, else the current difference)
    Δ       = 21-business-day change of (2y_base − 2y_quote), both series aligned on
              business days with ffill ≤ 5

for a pair where BOTH currencies have a fresh 2y (i.e. monetary is in the pair's D1=D
intersection). n (the intersection wsum), the 5/2 factor and D1=D stay. The currency's
own monetary score stays on its card (informative).

Measured over the trailing 3 years (business days), the 15 pairs of the 6 currencies
with a 2y (USD EUR GBP JPY CAD AUD), board orientation:
  (i)   std(m) / std(current diff)                     → K=22 and thresholds keep if in [0.9, 1.1]
  (ii)  direction agreement where both ≠ 0
  (iii) share of spread moves with |z| ≥ 1 that give 0 today
  (iv)  on price_history: corr with the pair's log return over the same 21 rows and
        over the next 5 and 20 rows (m vs current diff, same sample)
Then today's snapshot (pinned as_of): pair fund_score and Strength before/after,
invariants, and a payload diff.

    .venv/bin/python scripts/measure/monetary_spread.py --as-of 2026-09-23T07:06:11.724030 \
        [--years 3] [--out-json path] [--rates path]
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.economic_render as er  # noqa: E402
from src.rate_compute import (BASELINE_N, FB_HI, FB_LO, MIN_FOR_Z, W_DEFAULT, Z_HI, Z_LO,  # noqa: E402
                              compute_rate_scores, max_age_for)

W = W_DEFAULT            # 21
FFILL_LIMIT = 5
WITH_2Y = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]


def _bucket(a: np.ndarray, hi: float, lo: float) -> np.ndarray:
    a = np.abs(a)
    return np.where(a >= hi, 2, np.where(a >= lo, 1, 0))


# --- current per-currency score, vectorised (checked against compute_rate_scores) ---

def currency_scores(rates: pd.DataFrame, days: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """{ccy: DataFrame(index=days, score, stale)} — the rate_compute score of the
    series truncated at each day (rows ≤ day), stale per source threshold."""
    out = {}
    for ccy, g in rates.groupby("currency"):
        g = g.copy()
        g["date"] = pd.to_datetime(g["date"], errors="coerce")
        g["yield_pct"] = pd.to_numeric(g["yield_pct"], errors="coerce")
        g = g.dropna(subset=["date", "yield_pct"]).drop_duplicates("date", keep="last").sort_values("date")
        y = g["yield_pct"].to_numpy()
        n = np.arange(1, len(y) + 1)
        delta = pd.Series(y).diff(W).to_numpy()
        std = pd.Series(delta).rolling(BASELINE_N, min_periods=MIN_FOR_Z).std(ddof=1).to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            z = delta / std
        use_z = np.isfinite(std) & (std > 0)
        sc = np.where(use_z, _bucket(z, Z_HI, Z_LO), _bucket(delta, FB_HI, FB_LO)) * np.sign(delta)
        sc = np.where(n < W + 1, 0, sc)
        sc = np.nan_to_num(sc).astype(int)
        pos = np.searchsorted(g["date"].to_numpy(), days.to_numpy(), side="right") - 1
        ok = pos >= 0
        last = np.where(ok, g["date"].to_numpy()[np.clip(pos, 0, None)], np.datetime64("NaT"))
        lag = np.where(ok, np.busday_count(last.astype("datetime64[D]"),
                                           days.to_numpy().astype("datetime64[D]")), 10**6)
        lag = np.maximum(lag, 0)
        thr = np.array([max_age_for(s) for s in g["source"].to_numpy()])[np.clip(pos, 0, None)]
        out[ccy] = pd.DataFrame({"score": np.where(ok, sc[np.clip(pos, 0, None)], 0),
                                 "stale": ~ok | (lag > thr)}, index=days)
    return out


def check_vectorised(rates, cs, days, k=40, seed=7) -> int:
    rng = np.random.default_rng(seed)
    bad = 0
    for d in rng.choice(days, size=min(k, len(days)), replace=False):
        ref = compute_rate_scores(rates, as_of=pd.Timestamp(d).date())
        for ccy, rs in ref.items():
            row = cs[ccy].loc[pd.Timestamp(d)]
            if (int(row.score), bool(row.stale)) != (rs.rate_score, rs.stale):
                bad += 1
    return bad


# --- the spread signal -----------------------------------------------------------------

def aligned_yields(rates: pd.DataFrame, bdays: pd.DatetimeIndex) -> pd.DataFrame:
    r = rates.copy()
    r["date"] = pd.to_datetime(r["date"], errors="coerce")
    wide = (r.dropna(subset=["date"]).drop_duplicates(["currency", "date"], keep="last")
             .pivot(index="date", columns="currency", values="yield_pct"))
    return wide.reindex(bdays).ffill(limit=FFILL_LIMIT)


def spread_signal(Y: pd.DataFrame, base: str, quote: str) -> pd.DataFrame:
    """z and m for (base − quote) on the business-day grid. Point in time: every
    value at day d uses only yields dated ≤ d (ffill and rolling look back only)."""
    s = Y[base] - Y[quote]
    delta = s - s.shift(W)
    std = delta.rolling(BASELINE_N, min_periods=MIN_FOR_Z).std(ddof=1)
    z = delta / std
    ok = np.isfinite(z) & (std > 0)
    m = pd.Series(np.where(ok, np.sign(z) * _bucket(z.to_numpy(), Z_HI, Z_LO), np.nan), index=Y.index)
    return pd.DataFrame({"delta": delta, "z": z.where(ok), "m": m})


# --- history metrics -----------------------------------------------------------------

def board_pairs(instruments: dict) -> list[tuple[str, str, str]]:
    return [(sym, v["base"], v["quote"]) for sym, v in instruments["instruments"].items()
            if v.get("type") == "fx" and v["base"] in WITH_2Y and v["quote"] in WITH_2Y]


def history(rates, pairs, end: pd.Timestamp, years: int) -> pd.DataFrame:
    start = end - pd.DateOffset(years=years)
    first = pd.to_datetime(rates["date"]).min()
    grid = pd.bdate_range(first, end.normalize())
    days = grid[grid >= start]
    cs = currency_scores(rates, days)
    Y = aligned_yields(rates, grid)
    rows = []
    for sym, b, q in pairs:
        sig = spread_signal(Y, b, q).loc[days]
        both = ~cs[b]["stale"] & ~cs[q]["stale"]
        cur = (cs[b]["score"] - cs[q]["score"]).astype(float)
        fb = sig["m"].isna()
        m = sig["m"].where(~fb, cur)
        rows.append(pd.DataFrame({"symbol": sym, "date": days, "cur": cur.values, "m": m.values,
                                  "z": sig["z"].values, "fallback": fb.values, "both_fresh": both.values}))
    h = pd.concat(rows, ignore_index=True)
    return h, cs, days


def price_returns(prices: pd.DataFrame) -> pd.DataFrame:
    out = []
    for sym, g in prices.groupby("symbol"):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        lp = np.log(g["close"].astype(float).to_numpy())
        s = pd.Series(lp, index=pd.to_datetime(g["date"]))
        out.append(pd.DataFrame({"symbol": sym, "date": s.index,
                                 "r_same": (s - s.shift(W)).values,
                                 "r_f5": (s.shift(-5) - s).values,
                                 "r_f20": (s.shift(-20) - s).values}))
    return pd.concat(out, ignore_index=True)


def metrics(h: pd.DataFrame, prices: pd.DataFrame | None) -> dict:
    x = h[h["both_fresh"]]
    res = {"observations": int(len(x)), "pairs": int(x["symbol"].nunique()),
           "days": int(x["date"].nunique()),
           "fallback_share": float(x["fallback"].mean()),
           "i_std_ratio": float(x["m"].std(ddof=1) / x["cur"].std(ddof=1))}
    nz = x[(x["m"] != 0) & (x["cur"] != 0)]
    res["ii_agreement"] = float((np.sign(nz["m"]) == np.sign(nz["cur"])).mean())
    res["ii_n"] = int(len(nz))
    big = x[x["z"].abs() >= 1]
    res["iii_big_move_zero_today"] = float((big["cur"] == 0).mean())
    res["iii_n"] = int(len(big))
    res["m_dist"] = {int(k): int(v) for k, v in x["m"].value_counts().sort_index().items()}
    res["cur_dist"] = {int(k): int(v) for k, v in x["cur"].value_counts().sort_index().items()}
    if prices is not None:
        j = x.merge(price_returns(prices), on=["symbol", "date"], how="inner")
        corr = {}
        for col in ("r_same", "r_f5", "r_f20"):
            s = j.dropna(subset=[col])
            corr[col] = {"m": float(np.corrcoef(s["m"], s[col])[0, 1]),
                         "cur": float(np.corrcoef(s["cur"], s[col])[0, 1]), "n": int(len(s))}
        res["iv_corr"] = corr
        res["iv_window"] = [str(j["date"].min().date()), str(j["date"].max().date())]
    return res


# --- today's snapshot -----------------------------------------------------------------

def apply_to_payload(payload: dict, m_today: dict[str, float]) -> dict:
    """Pair fund_score with m in place of (monetary_base − monetary_quote); every
    other contribution row untouched. Only pairs whose monetary is in the D1=D
    intersection (both legs fresh) change."""
    new = copy.deepcopy(payload)
    scale = 5.0
    pair_divisor = 2.0
    for inst in new["instruments"]:
        sym = inst["symbol"]
        if inst["type"] != "fx" or sym not in m_today:
            continue
        mon = [c for c in inst["contributions"] if c["key"] == "rate_expectations"]
        if not mon or mon[0].get("excluded_from_pair") or "monetary" in (inst.get("categories_excluded") or []):
            continue
        wsum = float(inst["categories_used"])                  # all category weights are 1.0
        old_c = mon[0]["contribution"]
        new_c = float(m_today[sym]) * 1.0 * scale / (wsum * pair_divisor)
        mon[0]["contribution"] = new_c
        mon[0]["raw"] = int(m_today[sym])
        mon[0]["spread_m"] = int(m_today[sym])
        inst["fund_score"] = float(inst["fund_score"] - old_c + new_c)
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--rates", default=None,
                    help="2y parquet for m (default data/rates.parquet); the current per-currency "
                         "scores in the payload always come from data/rates.parquet")
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    as_of = pd.Timestamp(a.as_of)

    rates = pd.read_parquet(a.rates or er.RATES_PARQUET)
    rates = rates[pd.to_datetime(rates["date"]) <= as_of]
    inst = er._load_yaml(er.INSTRUMENTS_YAML)
    pairs = board_pairs(inst)
    assert len(pairs) == 15, pairs

    h, cs, days = history(rates, pairs, as_of, a.years)
    mism = check_vectorised(rates, cs, days)
    prices = pd.read_parquet(ROOT / "data" / "price_history.parquet")
    prices = prices[pd.to_datetime(prices["date"]) <= as_of]
    res = {"as_of": str(as_of), "window": [str(days.min().date()), str(days.max().date())],
           "vectorised_vs_rate_compute_mismatches": mism, **metrics(h, prices)}

    # antisymmetry, computed independently in both orientations
    grid = pd.bdate_range(pd.to_datetime(rates["date"]).min(), as_of.normalize())
    Y = aligned_yields(rates, grid)
    asym = 0
    for _s, b, q in pairs:
        m1, m2 = spread_signal(Y, b, q)["m"], spread_signal(Y, q, b)["m"]
        both = m1.notna() | m2.notna()
        asym += int((m1[both] != -m2[both]).sum())
    res["antisymmetry_violations"] = asym

    # today
    d0 = as_of.normalize()
    today = h[h["date"] == d0].set_index("symbol")
    m_today = {s: float(r["m"]) for s, r in today.iterrows() if r["both_fresh"]}
    res["today"] = {s: {"cur": int(r["cur"]), "m": int(r["m"]),
                        "z": None if pd.isna(r["z"]) else round(float(r["z"]), 3),
                        "fallback": bool(r["fallback"])} for s, r in today.iterrows()}

    payload = er.build_economic_payload(as_of=as_of)
    payload.pop("_integrity_report", None)
    after = apply_to_payload(payload, m_today)
    s0 = er.strength_from_pairs(payload)["scores"]
    s1 = er.strength_from_pairs(after)["scores"]
    f0 = {i["symbol"]: i["fund_score"] for i in payload["instruments"] if i["type"] == "fx"}
    f1 = {i["symbol"]: i["fund_score"] for i in after["instruments"] if i["type"] == "fx"}
    res["pairs_before_after"] = {s: [round(f0[s], 4), round(f1[s], 4)] for s in f0 if f0[s] != f1[s]}
    res["strength_before_after"] = {c: [round(s0[c], 4), round(s1[c], 4)] for c in s0}
    res["strength_sum_after"] = float(sum(s1.values()))
    untouched = [i for i in payload["instruments"] if i["type"] == "fx"
                 and ({i["breakdown"]["base"]["currency"], i["breakdown"]["quote"]["currency"]} & {"NZD", "CHF"})]
    by = {i["symbol"]: i for i in after["instruments"]}
    res["nzd_chf_pairs_bit_identical"] = all(json.dumps(i, sort_keys=True) == json.dumps(by[i["symbol"]], sort_keys=True)
                                             for i in untouched)
    non_mon = all([c for c in i["contributions"] if c["key"] != "rate_expectations"]
                  == [c for c in by[i["symbol"]]["contributions"] if c["key"] != "rate_expectations"]
                  for i in payload["instruments"])
    res["non_monetary_contributions_bit_identical"] = bool(non_mon)

    print(json.dumps(res, indent=1))
    if a.out_json:
        Path(a.out_json).write_text(json.dumps({"before": payload, "after": after}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
