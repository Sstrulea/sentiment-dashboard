"""Read-only inventory probe for the Cross-Asset model (step 6.1).

Confirms the three inputs the cross-asset layer will need WITHOUT building or
modifying anything. Nothing is written, no scoring/compute/YAML/render is
touched, no commit. It reuses the existing modules and prints three findings:

  (a) US 10y REAL yield (FRED DFII10) — is it reachable via the same keyless
      FRED-CSV mechanism already used for USD DGS2 in src/rate_sources?
  (b) VIX/VIX3M risk signal — where it's computed and what shape/range it has.
  (c) Per-currency category scores (Growth/Inflation/Labour/Monetary) — already
      produced by build_payload; confirm how to read them programmatically.

    python -m src.crossasset_probe
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# (a) FRED DFII10 — US 10y real yield
# ---------------------------------------------------------------------------

def probe_dfii10() -> None:
    print("\n" + "=" * 78)
    print("[a] US 10y REAL YIELD — FRED DFII10")
    print("=" * 78)

    # The existing FRED adapter is CURRENCY-keyed (returns a per-ccy YieldSeries):
    #   src/rate_sources FredSource.SERIES = {"USD": ("DGS2", "2y")}
    # DFII10 is NOT a currency 2y — it's a single US real-yield series — so the
    # cross-asset layer should fetch it via the SAME keyless FRED-CSV mechanism
    # (same URL + parse), as a small generic FRED-series reader rather than the
    # currency adapter. Confirm the contract is already present:
    try:
        from src.rate_sources import FredSource, BaseSource, _mk  # noqa: F401
        fs = FredSource()
        print("  FRED adapter present: src/rate_sources.FredSource")
        print(f"    currency-keyed SERIES = {fs.SERIES}  (returns YieldSeries per ccy)")
        print("    mechanism = keyless GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>")
        print("    -> DFII10 reuses this exact mechanism; add as a generic FRED series")
        print("       reader (NOT a currency adapter, since it isn't a 2y per ccy).")
    except Exception as e:
        print(f"  ! could not import FRED adapter: {e}")

    # Live reachability check (read-only GET). FRED is often blocked in the CC
    # sandbox — that is expected; validate from the residential terminal.
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
    print(f"\n  live fetch: GET {url}")
    try:
        import requests
        ua = {"User-Agent": "Mozilla/5.0 (probe; read-only)"}
        r = requests.get(url, headers=ua, timeout=20)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code} — likely sandbox block; validate on residential terminal.")
        else:
            df = pd.read_csv(io.StringIO(r.text))
            dcol = df.columns[0]
            vcol = [c for c in df.columns if c != dcol][0]
            df[dcol] = pd.to_datetime(df[dcol], errors="coerce")
            df[vcol] = pd.to_numeric(df[vcol].replace(".", None), errors="coerce")
            ok = df.dropna(subset=[vcol])
            print(f"    OK rows={len(df)} non-null={len(ok)} "
                  f"history {ok[dcol].min().date()} → {ok[dcol].max().date()}")
            print("    last 3:")
            for _, row in ok.tail(3).iterrows():
                print(f"      {row[dcol].date()} = {row[vcol]:.2f}%")
    except Exception as e:
        print(f"    fetch failed ({type(e).__name__}: {e}) — expected if FRED is blocked here.")
        print("    ACTION: confirm on residential terminal that DFII10 returns a recent value + history.")


# ---------------------------------------------------------------------------
# (b) VIX/VIX3M risk signal
# ---------------------------------------------------------------------------

def probe_vix() -> None:
    print("\n" + "=" * 78)
    print("[b] VIX/VIX3M RISK SIGNAL")
    print("=" * 78)
    print("  location : src/sentiment_compute.py")
    print("  producers:")
    print("    compute_vix_ratio_metrics(df) -> DataFrame  (adds regime/crossovers)")
    print("    build_vix_chart_payload(vix_df, window_days) -> dict")
    print("    _classify_regime(ratio) -> str")
    print("  source parquet: data/sentiment_history.parquet "
          "(cols: date, vix_close, vix3m_close, ratio)")
    print("  rendered JSON : public/data/vix-ratio.json (current_value, current_regime)")

    print("\n  SHAPE/RANGE of the signal:")
    print("    ratio = VIX/VIX3M, a positive float (~0.80–1.20 typical).")
    print("    regime (categorical, from VIX_RATIO_THRESHOLDS):")
    print("      ratio > 1.10           -> ACUTE_PANIC   (risk-OFF, deep backwardation)")
    print("      1.00 < ratio <= 1.10   -> BACKWARDATION (risk-OFF)")
    print("      0.90 <= ratio <= 1.00  -> NORMAL")
    print("      ratio < 0.90           -> COMPLACENCY   (risk-ON)")
    print("    NOTE: this is a ratio + 4-state regime, NOT a -2..+2 score. The")
    print("    cross-asset layer must MAP it to a risk-on/off scalar itself (read-only here).")

    try:
        from src.sentiment_compute import build_vix_chart_payload
        vix_pq = ROOT / "data" / "sentiment_history.parquet"
        if vix_pq.exists():
            vdf = pd.read_parquet(vix_pq)
            payload = build_vix_chart_payload(vdf, window_days=30)
            print("\n  CURRENT (read from existing parquet):")
            print(f"    current_value (ratio) = {payload['current_value']}")
            print(f"    current_regime        = {payload['current_regime']}")
            print(f"    days_in_regime        = {payload['days_in_regime']}")
            print(f"    rows in history       = {len(vdf)}")
        else:
            print(f"\n  ! {vix_pq} not found (run --mode daily to build it).")
    except Exception as e:
        print(f"\n  ! could not compute VIX payload: {e}")


# ---------------------------------------------------------------------------
# (c) Per-currency 4-category scores
# ---------------------------------------------------------------------------

def probe_category_scores() -> None:
    print("\n" + "=" * 78)
    print("[c] PER-CURRENCY CATEGORY SCORES (Growth / Inflation / Labour / Monetary)")
    print("=" * 78)
    print("  path: src.economic_compute.build_payload(...) -> payload['currencies'][CCY]")
    print("        ['categories'][cat] = {score_cell (int -2..+2), score_precise (float), coverage}")
    print("  ACCESSIBLE WITHOUT modifying scoring: YES — just call build_payload and read.\n")

    try:
        import yaml
        from src.economic_compute import build_payload
        from src.rate_compute import compute_rate_scores

        cal = pd.read_parquet(ROOT / "data" / "economic_calendar.parquet")
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        ind = yaml.safe_load(open(ROOT / "data" / "economic_indicators.yaml"))
        inst = yaml.safe_load(open(ROOT / "data" / "economic_instruments.yaml"))
        as_of = pd.Timestamp.utcnow().tz_localize(None)

        rates_pq = ROOT / "data" / "rates.parquet"
        rs = {}
        if rates_pq.exists():
            rdf = pd.read_parquet(rates_pq)
            rs = compute_rate_scores(rdf, as_of=as_of.date())

        payload = build_payload(cal, ind, inst, as_of=as_of, rate_scores=rs or None)
        cats = ["growth", "inflation", "labour", "monetary"]
        print(f"  {'CCY':5}" + "".join(f"{c:>12}" for c in cats) + f"{'index':>9}")
        for ccy in sorted(payload["currencies"]):
            card = payload["currencies"][ccy]
            cells = card.get("categories", {})
            row = f"  {ccy:5}"
            for c in cats:
                cell = cells.get(c)
                row += f"{(str(cell['score_cell']) if cell else '—'):>12}"
            row += f"{card.get('index', 0):>9.2f}"
            print(row)
        print("\n  (score_cell shown; score_precise + coverage also available per cell.)")
        print("  monetary present only for currencies with a resolved 2y rate score.")
    except Exception as e:
        print(f"  ! could not build payload: {type(e).__name__}: {e}")


def main() -> int:
    print("CROSS-ASSET INPUT INVENTORY (6.1) — READ-ONLY, no writes, no commit")
    probe_dfii10()
    probe_vix()
    probe_category_scores()
    print("\n[STOP] Inventory only — nothing built/modified. Awaiting go-ahead for 6.2.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
