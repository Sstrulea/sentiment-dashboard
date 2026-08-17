"""FAZA A diagnostic (read-only): TREND on/off before/after diff.

Runs `src.economic_render.build_economic_payload()` twice in-process:

  before -> exactly as today (TREND folded in normally)
  after  -> TREND fully absent, i.e. what build_payload/compute_crossasset_scores
            already do when handed `trend_cells={}` and `trend_by_symbol={}`
            (both keyed lookups against an empty dict return None for every
            symbol, which is the documented "trend excluded" path in
            economic_compute.compute_instrument / crossasset_compute.compute_instrument_score)

The "after" run is produced by monkeypatching `trend_score_all` (the only entry
point `economic_render._trend_full()` calls) to return `{}` for the duration of
that one call, then restoring it. No file is modified — this script does not
touch economic_render.py, economic_compute.py, trend_score.py or
crossasset_compute.py; it only calls the existing public pipeline twice from a
fresh process-local monkeypatch.

Writes:
  docs/trend-off-before-after.md   (report: counts + full flip list)
  docs/trend-off-before-after.csv  (per-instrument before/after table)

Covers every instrument in both payload sections: FX (`payload["instruments"]`)
and cross-asset (`payload["crossasset"]["instruments"]`).
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.economic_render as er  # noqa: E402

DOCS_DIR = ROOT / "docs"
MD_OUT = DOCS_DIR / "trend-off-before-after.md"
CSV_OUT = DOCS_DIR / "trend-off-before-after.csv"

CSV_FIELDS = [
    "asset_class", "symbol", "trend_cell_current",
    "score_precise_before", "score_precise_after",
    "score_rounded_before", "score_rounded_after",
    "bias_before", "bias_after",
    "score_flip", "bias_flip",
]


def _fx_rows(payload: dict) -> dict[str, dict]:
    rows = {}
    for inst in payload.get("instruments", []) or []:
        precise = float(inst["score"])
        rows[inst["symbol"]] = {
            "asset_class": "FX",
            "symbol": inst["symbol"],
            "trend_cell": inst.get("trend"),
            "score_precise": precise,
            "score_rounded": int(round(precise)),
            "bias": inst["bias"],
        }
    return rows


def _crossasset_rows(payload: dict) -> dict[str, dict]:
    rows = {}
    for inst in (payload.get("crossasset", {}) or {}).get("instruments", []) or []:
        rows[inst["symbol"]] = {
            "asset_class": "CROSS-ASSET",
            "symbol": inst["symbol"],
            "trend_cell": inst.get("trend"),
            "score_precise": float(inst["score_precise"]),
            "score_rounded": int(inst["score"]),
            "bias": inst["bias_label"],
        }
    return rows


def _collect(payload: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    rows.update(_fx_rows(payload))
    rows.update(_crossasset_rows(payload))
    return rows


def run() -> tuple[list[dict], dict]:
    """Returns (diff_rows, summary). Side effect: writes the .md and .csv."""
    print("Run 1/2: build_economic_payload() -- TREND ON (baseline, as today)")
    before_payload = er.build_economic_payload()
    before = _collect(before_payload)

    print("Run 2/2: build_economic_payload() -- TREND OFF "
          "(trend_cells={}, trend_by_symbol={})")
    original_trend_score_all = er.trend_score_all
    er.trend_score_all = lambda *a, **kw: {}
    try:
        after_payload = er.build_economic_payload()
    finally:
        er.trend_score_all = original_trend_score_all
    after = _collect(after_payload)

    symbols = sorted(set(before) | set(after))
    missing = [s for s in symbols if s not in before or s not in after]
    if missing:
        print(f"WARNING: {len(missing)} symbol(s) present in only one run, "
              f"skipped from the diff: {missing}")

    diff_rows = []
    for sym in symbols:
        b, a = before.get(sym), after.get(sym)
        if b is None or a is None:
            continue
        score_flip = b["score_rounded"] != a["score_rounded"]
        bias_flip = b["bias"] != a["bias"]
        diff_rows.append({
            "asset_class": b["asset_class"],
            "symbol": sym,
            "trend_cell_current": b["trend_cell"],
            "score_precise_before": b["score_precise"],
            "score_precise_after": a["score_precise"],
            "score_rounded_before": b["score_rounded"],
            "score_rounded_after": a["score_rounded"],
            "bias_before": b["bias"],
            "bias_after": a["bias"],
            "score_flip": score_flip,
            "bias_flip": bias_flip,
        })

    total = len(diff_rows)
    score_flips = [r for r in diff_rows if r["score_flip"]]
    bias_flips = [r for r in diff_rows if r["bias_flip"]]
    summary = {
        "total": total,
        "score_flip_count": len(score_flips),
        "bias_flip_count": len(bias_flips),
        "score_flips": score_flips,
        "bias_flips": bias_flips,
    }

    _write_csv(diff_rows)
    _write_md(diff_rows, summary)
    return diff_rows, summary


def _write_csv(diff_rows: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in diff_rows:
            w.writerow(r)
    print(f"Wrote {CSV_OUT} ({len(diff_rows)} rows)")


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:+.4f}"
    return "—" if v is None else str(v)


def _write_md(diff_rows: list[dict], summary: dict) -> None:
    lines = []
    lines.append("# TREND on/off — before/after diff (FAZA A, read-only)")
    lines.append("")
    lines.append("Generated by `scripts/diag/trend_off_diff.py`. Compares "
                  "`build_economic_payload()` as-is today against the same "
                  "payload with TREND fully absent (`trend_cells={}`, "
                  "`trend_by_symbol={}` — the existing \"trend excluded\" path, "
                  "no scoring code changed). Covers every FX instrument "
                  "(`payload.instruments`) and every cross-asset instrument "
                  "(`payload.crossasset.instruments`).")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Instruments compared: **{summary['total']}**")
    lines.append(f"- Instruments whose **rounded score** changes: "
                  f"**{summary['score_flip_count']}**")
    lines.append(f"- Instruments whose **bias label** changes: "
                  f"**{summary['bias_flip_count']}**")
    lines.append("")

    lines.append("## Bias flips (full list)")
    lines.append("")
    if summary["bias_flips"]:
        lines.append("| Asset class | Symbol | Trend cell (current) | Bias before | Bias after | Score before | Score after |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in summary["bias_flips"]:
            lines.append(
                f"| {r['asset_class']} | {r['symbol']} | {_fmt(r['trend_cell_current'])} "
                f"| {r['bias_before']} | {r['bias_after']} "
                f"| {_fmt(r['score_precise_before'])} | {_fmt(r['score_precise_after'])} |"
            )
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Rounded-score flips (full list)")
    lines.append("")
    if summary["score_flips"]:
        lines.append("| Asset class | Symbol | Trend cell (current) | Score (rounded) before | Score (rounded) after | Score (precise) before | Score (precise) after |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in summary["score_flips"]:
            lines.append(
                f"| {r['asset_class']} | {r['symbol']} | {_fmt(r['trend_cell_current'])} "
                f"| {r['score_rounded_before']} | {r['score_rounded_after']} "
                f"| {_fmt(r['score_precise_before'])} | {_fmt(r['score_precise_after'])} |"
            )
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Full per-instrument table")
    lines.append("")
    lines.append("See `docs/trend-off-before-after.csv` for the complete "
                  f"{len(diff_rows)}-row table (all instruments, not just flips).")
    lines.append("")

    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {MD_OUT}")


if __name__ == "__main__":
    diff_rows, summary = run()
    print("")
    print(f"Instruments compared: {summary['total']}")
    print(f"Rounded-score flips:  {summary['score_flip_count']}")
    print(f"Bias flips:           {summary['bias_flip_count']}")
    if summary["bias_flips"]:
        print("Bias flip symbols: " + ", ".join(r["symbol"] for r in summary["bias_flips"]))
    if summary["score_flips"]:
        print("Score flip symbols: " + ", ".join(r["symbol"] for r in summary["score_flips"]))
