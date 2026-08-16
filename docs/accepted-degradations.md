# Accepted Degradations

Log of scoring components deliberately weighted to 0 (or otherwise degraded)
while their underlying series keeps fetching and their score keeps computing.
Not deletions: the pillar stays in the YAML config and stays visible in the
UI (greyed, badged) so the number is always inspectable, even at weight 0.
Each entry records why, and what would trigger re-evaluation.

---

## 2026-08-16 — Cross-asset `balance_sheet` (liquidity pillar) weight → 0

**Component:** `balance_sheet` sub-component of the `rates` factor, in
`data/crossasset_instruments.yaml`, for all 8 cross-asset instruments
(DJIA, SP500, NASDAQ, DAX, NIKKEI, FTSE100, GOLD, SILVER).

**Change:** `weight: 1.0` → `weight: 0.0` (sign unchanged, component config
key kept). The series (WRBWFRBL, falling back to net_liquidity) keeps
fetching and `compute_liquidity_score()` keeps computing a score; it now
contributes 0 to every instrument's composite. The cell remains visible in
both the cross-asset table and the rate-breakdown modal, greyed, with an
"excluded from composite" badge.

**Reason:** The 21-bd rate-of-change on reserves conflates calendar TGA
flows (debt-ceiling drawdowns, quarter-end settlement, tax-date swings) with
genuine regime change in the liquidity backdrop. Those calendar-driven moves
in WRBWFRBL can dominate a 21-bd window without reflecting an actual
tightening/easing regime shift, so the pillar's signal-to-noise ratio at the
current horizon is not high enough to carry weight in the composite.

**Re-evaluation trigger:** Revisit after the trend engine rebuild lands —
that work is expected to change how regime-vs-noise is separated for
momentum-style pillars generally, and the liquidity pillar's viability
should be reassessed under whatever windowing/smoothing approach comes out
of it, rather than by further hand-tuning the current 21-bd/5-day-smooth
band scorer in isolation.
