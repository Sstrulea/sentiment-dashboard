"""TREND v2 validation — before/after (v1 MA×ADX vs v2 regime+momentum) on every
instrument with a price series, composite bias impact, sanity anchors, sign changes.
Writes docs/trend-v2-before-after.md + docs/trend-v2-before-after.csv. Read-only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import src.economic_render as ER
from src.economic_render import build_economic_payload
from src.trend_score import (atr, compute_adx, load_history, score_all, _series_for)

ROOT = ER.ROOT
DOCS = ROOT / "docs"


def _round_half_away(x):
    return 0 if not np.isfinite(x) else int(np.sign(x) * np.floor(np.abs(x) + 0.5))


def v1_cell(g: pd.DataFrame):
    """The OLD MA×ADX cell: raw = short(SMA10v20)+long(SMA20v50)+slope(SMA20/10b),
    × ADX factor (>=22→1, >=15→0.5, else 0.25), clamp(round-half-away)."""
    c = pd.to_numeric(g["close"], errors="coerce").dropna().reset_index(drop=True)
    if len(c) < 50:
        return None
    s, m, l = c.rolling(10).mean(), c.rolling(20).mean(), c.rolling(50).mean()
    short = 1 if s.iloc[-1] > m.iloc[-1] else -1
    long = 1 if m.iloc[-1] > l.iloc[-1] else -1
    slope = 0 if m.iloc[-1] == m.iloc[-11] else (1 if m.iloc[-1] > m.iloc[-11] else -1)
    raw = short + long + slope
    adx = float(compute_adx(g).iloc[-1])
    fac = 1.0 if adx >= 22 else (0.5 if adx >= 15 else 0.25)
    return int(max(-3, min(3, _round_half_away(raw * fac))))


def main() -> int:
    df = load_history()
    v2 = score_all()
    keys = [k for k, v in v2.items() if v["trend_cell"] is not None]

    rows = []
    for k in sorted(keys):
        g = _series_for(df, k)
        old = v1_cell(g)
        e = v2[k]
        rows.append({
            "instrument": k, "score_v1": old, "regime": e["regime"],
            "momentum": e["momentum"], "score_v2": e["trend_cell"],
            "delta": (None if old is None else e["trend_cell"] - old),
            "slope_atr": e["slope_atr"], "adx": e["adx"],
        })
    tbl = pd.DataFrame(rows)
    DOCS.mkdir(exist_ok=True)
    tbl.to_csv(DOCS / "trend-v2-before-after.csv", index=False)

    # sanity anchors
    def cell(k):
        return v2[k]["trend_cell"]
    anchors = {
        "NASDAQ >= +1": cell("NASDAQ") >= 1, "SP500 >= +1": cell("SP500") >= 1,
        "DAX == +3": cell("DAX") == 3,
    }
    # FX sign changes (exclude indices/metals — FX = 6-char pairs)
    FXSET = {r["instrument"] for _, r in tbl.iterrows()
             if len(r["instrument"]) == 6 and r["instrument"].isalpha()}
    sign_changes = [r for _, r in tbl.iterrows()
                    if r["instrument"] in FXSET and r["score_v1"] is not None
                    and np.sign(r["score_v1"]) != np.sign(r["score_v2"])
                    and (r["score_v1"] != 0 and r["score_v2"] != 0)]

    # composite bias impact: v2 payload vs v1 payload (patch trend to v1 cells)
    p2 = build_economic_payload()
    v1_entries = {k: {"trend_cell": v1_cell(_series_for(df, k))} for k in v2}
    orig = ER.trend_score_all
    ER.trend_score_all = lambda *a, **k: v1_entries
    p1 = build_economic_payload()
    ER.trend_score_all = orig

    def inst_map(p):
        out = {}
        for i in p.get("instruments", []):                       # FX pairs
            out[i["symbol"]] = (round(i.get("score", 0), 3), i.get("bias"))
        for i in p.get("crossasset", {}).get("instruments", []):  # cross-asset
            out[i["symbol"]] = (round(i.get("score_precise", 0), 3), i.get("bias_label"))
        return out
    m1, m2 = inst_map(p1), inst_map(p2)
    bias_changes = []
    for s in sorted(set(m1) & set(m2)):
        if m1[s][1] != m2[s][1] or abs(m1[s][0] - m2[s][0]) >= 0.01:
            bias_changes.append((s, m1[s][0], m1[s][1], m2[s][0], m2[s][1]))

    # ---- write report ----
    lines = ["# TREND v2 — before/after (v1 MA×ADX → v2 Regime+Momentum)\n",
             "Same ±3 range ⇒ composite weight (0.5) unchanged. ADX is display-only in v2.\n",
             "## TASK 0 — empirical diagnostic verdict (both hypotheses CONFIRMED)\n",
             "- **(a) NAS/SPX −1 in an uptrend pullback:** v1 `raw = short(SMA10v20) + "
             "long(SMA20v50) + slope(SMA20)`; the two SHORT terms are correlated (both read the "
             "micro-pullback) → −2, dominating the bullish SMA50/200 regime (bull_points=3 ⇒ +2).\n"
             "- **(b) DAX +1 at an ATH breakout:** v1 `raw=+3` but `ADX=12.6` (lagging) → factor "
             "0.25 → cell +1 (damped). Absolute slope threshold (eps=0) also can't scale across "
             "instruments (DAX slope/ATR=0.065 vs FTSE 0.380) — v2's ATR-normalized slope does.\n"
             "\nv2 fixes both: SMA50/200 regime is dominant and ADX is removed from the score.\n",
             "## Momentum HYSTERESIS (addendum)\n",
             "Momentum uses a slope_atr **hysteresis band** (config/trend.yaml): ±1 activates at "
             "`|slope_atr| > slope_enter` (0.035) and persists until `|slope_atr| < slope_exit` "
             "(0.025); in the band it keeps the prior state (stateless — derived by walking the "
             "historical slope_atr series from price_history.parquet, no persisted state file).\n",
             "## Sanity anchors"]
    for a, ok in anchors.items():
        lines.append(f"- {'✅' if ok else '❌ FAIL'} {a}  (got {a.split()[0]}={cell(a.split()[0])})")
    lines.append("\n## Full before/after (trend cell)\n")
    lines.append("| instrument | v1 | regime | momentum | v2 | Δ | slope/atr | ADX* |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in tbl.iterrows():
        dtxt = "" if r["delta"] is None else f"{int(r['delta']):+d}"
        v1txt = "None" if r["score_v1"] is None else f"{int(r['score_v1']):+d}"
        lines.append(f"| {r['instrument']} | {v1txt} | {r['regime']:+d} | {r['momentum']:+d} "
                     f"| {r['score_v2']:+d} | {dtxt} | {r['slope_atr']} | {r['adx']} |")
    lines.append(f"\n_*ADX display-only. {len(tbl)} instruments; "
                 f"{int((tbl['delta'].fillna(0) != 0).sum())} changed cell._\n")

    lines.append("## FX sign changes (v1 → v2)\n")
    if sign_changes:
        lines.append("| pair | v1 | v2 | regime | momentum | explanation |")
        lines.append("|---|---|---|---|---|---|")
        for r in sign_changes:
            expl = (f"v1 saw SMA10/20/50 momentum; v2 regime={r['regime']:+d} "
                    f"(SMA50/200 structure) dominates, momentum={r['momentum']:+d}")
            lines.append(f"| {r['instrument']} | {r['score_v1']:+d} | {r['score_v2']:+d} "
                         f"| {r['regime']:+d} | {r['momentum']:+d} | {expl} |")
    else:
        lines.append("_None._")

    lines.append("\n## Composite bias/score impact (v1 trend → v2 trend)\n")
    if bias_changes:
        lines.append("| instrument | score v1 | bias v1 | score v2 | bias v2 |")
        lines.append("|---|---|---|---|---|")
        for s, s1, b1, s2, b2 in bias_changes:
            flip = " **← bias flip**" if b1 != b2 else ""
            lines.append(f"| {s} | {s1:+.2f} | {b1} | {s2:+.2f} | {b2}{flip} |")
    else:
        lines.append("_No composite score/bias changes._")

    (DOCS / "trend-v2-before-after.md").write_text("\n".join(lines))
    print("anchors:", anchors)
    print(f"instruments: {len(tbl)} | cell changes: {int((tbl['delta'].fillna(0)!=0).sum())} | "
          f"FX sign changes: {len(sign_changes)} | composite bias/score changes: {len(bias_changes)}")
    print("wrote docs/trend-v2-before-after.md + .csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
