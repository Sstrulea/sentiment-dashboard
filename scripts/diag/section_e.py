"""Section E — zero density per displayed table column (current + window-avg).

Mirrors src.economic_render._build_indicator_cells exactly (same per-instrument
per-column cell value: base-leg score minus quote-leg score, missing leg = 0,
both legs missing = blank/None) — this is the literal cell a user sees on the
dashboard's dense table, distinct from section A's raw per-currency values.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_render import TABLE_COLUMN_KEYS  # noqa: E402
from scripts.diag.analysis_common import DOCS_DIR, INSTRUMENTS_CFG, load_today, load_window  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
COLUMNS = list(TABLE_COLUMN_KEYS) + (["rate_expectations"] if "rate_expectations" not in TABLE_COLUMN_KEYS else [])


def _cell_and_cause(eb: dict | None, eq: dict | None) -> tuple[int | None, str]:
    """(value, cause). cause in {no_data, stale, no_consensus, dead_zone,
    cancellation, nonzero}. Missing leg counts as 0 in the value (mirrors
    _build_indicator_cells), but "both legs missing" is its own no_data cause."""
    if eb is None and eq is None:
        return None, "no_data"

    eb_score = eb["score"] if eb else 0
    eq_score = eq["score"] if eq else 0
    v = eb_score - eq_score

    if (eb and eb.get("stale")) or (eq and eq.get("stale")):
        return v, "stale"
    if (eb and eb.get("flag") == "no_consensus") or (eq and eq.get("flag") == "no_consensus"):
        return v, "no_consensus"
    if v != 0:
        return v, "nonzero"
    if eb_score == 0 and eq_score == 0:
        return 0, "dead_zone"
    return 0, "cancellation"   # eb_score == eq_score != 0 (two real signals offsetting)


def _rows_for_snapshot(snapshot: dict) -> list[dict]:
    rows = []
    for inst in snapshot["instruments"]:
        cfg = INST_CFG[inst["symbol"]]
        bdn = inst.get("breakdown", {}) or {}
        base_ccy = (bdn.get("base") or {}).get("currency")
        quote_ref = bdn.get("quote")
        quote_ccy = quote_ref.get("currency") if quote_ref else None
        currencies = snapshot["currencies"]
        bb = (currencies.get(base_ccy, {}) or {}).get("breakdown", {}) or {}
        bq = (currencies.get(quote_ccy, {}) or {}).get("breakdown", {}) or {} if quote_ccy else {}

        for key in COLUMNS:
            eb = bb.get(key)
            eq = bq.get(key) if cfg["type"] == "fx" else None
            v, cause = _cell_and_cause(eb, eq)
            rows.append({"symbol": inst["symbol"], "column": key, "value": v, "cause": cause})
    return rows


def run() -> dict:
    today = load_today()["snapshot"]
    win = load_window()

    today_rows = pd.DataFrame(_rows_for_snapshot(today))
    today_rows.to_csv(DOCS_DIR / "diag-E-zero-density-today-raw.csv", index=False)

    window_rows = pd.concat([pd.DataFrame(_rows_for_snapshot(s)) for s in win["snapshots"]], ignore_index=True)
    window_rows.to_csv(DOCS_DIR / "diag-E-zero-density-window-raw.csv", index=False)

    def _summarize(df: pd.DataFrame) -> pd.DataFrame:
        out = []
        for col in COLUMNS:
            sub = df[df["column"] == col]
            n = len(sub)
            zero_causes = ["no_data", "dead_zone", "cancellation"]  # "zero or blank" cells
            is_zero = sub["cause"].isin(zero_causes) | (sub["value"] == 0)
            pct_zero = 100.0 * is_zero.mean() if n else float("nan")
            cause_counts = sub.loc[is_zero, "cause"].value_counts()
            dominant = cause_counts.idxmax() if len(cause_counts) else "—"
            out.append({"column": col, "n": n, "pct_zero_or_blank": round(pct_zero, 1),
                       "dominant_cause": dominant, **{f"pct_{c}": round(100 * sub["cause"].eq(c).mean(), 1)
                                                       for c in ["no_data", "stale", "no_consensus", "dead_zone", "cancellation"]}})
        return pd.DataFrame(out)

    today_summary = _summarize(today_rows)
    window_summary = _summarize(window_rows)
    today_summary.to_csv(DOCS_DIR / "diag-E-zero-density-today-summary.csv", index=False)
    window_summary.to_csv(DOCS_DIR / "diag-E-zero-density-window-summary.csv", index=False)

    return {"today_summary": today_summary, "window_summary": window_summary}


if __name__ == "__main__":
    res = run()
    print("TODAY (current snapshot):")
    print(res["today_summary"].to_string(index=False))
    print("\nWINDOW-AVERAGED:")
    print(res["window_summary"].to_string(index=False))
