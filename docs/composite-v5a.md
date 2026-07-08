# Composite V5a — weight ablation (READ-ONLY, nothing adopted)

Branch `feat/composite-v5a`. Ablates the COMPOSITE weights only — **internal pillar scoring is
untouched** (see memory `fundamental-pillar-no-edge`). Variants differ solely by which factors enter
the weighted mean (auto-renormalized, so `bias_thresholds` stay applicable). Flag
`composite_variant: full` (inert in production; the replay reads it). 829 as-ofs 2023-05→2026-07,
point-in-time. **Verdict up front: pre-registered decision-rule point 5 — no variant (incl. V5.T)
has a consistent positive M1; MAJOR FINDING, STOP for discussion, NO architecture recommendation.**

## Variants (flag `composite_variant`)
| variant | surprise cats | monetary | sentiment | trend | rates&liq (x-asset) |
|---|---|---|---|---|---|
| V5.T | — | — | — | ✓ | — |
| V5.TS | — | — | ✓ | ✓ | — |
| V5.NOF | — | ✓ | ✓ | ✓ | ✓ |
| V5.HALF | 0.5× | ✓ | ✓ | ✓ | ✓ |
| V5.FULL | 1.0× (prod) | ✓ | ✓ | ✓ | ✓ |

Implemented by scaling surprise-category weights (`variant_ind_cfg`) and adding/removing cross-asset
factors (`variant_ca_cfg`); FULL == production config (guard `test_full_variant_is_config_identical`).

## Replay (all components point-in-time)
- **trend v2**: `score_all(server_today=as_of)` — causal walk on OHLC < as-of.
- **COT sentiment**: sliced to publication = report(Tue) **+3 calendar days** (ASSUMPTION — the COT
  parquet has no release-date column; documented). Currencies + metals cells; **US-index P/C
  sentiment is EXCLUDED** (no point-in-time P/C history) — a documented replay limitation, so
  cross-asset index sentiment is metals-COT-only.
- **monetary / real-yield / net-liquidity**: FRED parquet slice ≤ as-of.
- **surprise**: FF calendar with zero-placeholder quarantine (as V4).
- r_H at H ∈ {5,15,30} trading days, position-based, tail excluded.

## ⚠️ COVERAGE — the binding limitation
| year | n as-of | trend ≥1 sym | **trend = full** | COT | real-yield | liquidity |
|---|---|---|---|---|---|---|
| 2023 | 174 | 100% | **0%** | 100% | 100% | 100% |
| 2024 | 262 | 100% | **0%** | 100% | 100% | 100% |
| 2025 | 261 | 100% | **28%** | 100% | 100% | 100% |
| 2026 | 132 | 100% | **100%** | 100% | 100% | 100% |

Price history for all symbols except FTSE100 starts ~2024-10 (≈400 bars), so trend needs 200 bars →
**trend is essentially absent 2023-2024, only 28% of symbols in 2025, full only in 2026.** The
trend-based variants (V5.T, V5.TS) therefore have meaningful data only in ~2025H2-2026, and
cross-asset forward returns exist only from 2024-10 (cross-asset sub-periods = 2025/2026, and the
H=30 disjoint sample is too thin to form a spread — n/a below).

## Selectivity (% non-Neutral) — differs >10pp, so raw ranks are read with the caveat
| variant | fx | cross-asset |
|---|---|---|
| V5.T | 23.6 | 32.9 |
| V5.TS | 78.1 | 49.7 |
| V5.NOF | 74.0 | 66.0 |
| V5.HALF | 45.8 | 43.8 |
| V5.FULL | 35.8 | 29.9 |

(V5.T is *less* selective because trend is absent pre-2026 → score 0 → Neutral. Equalizing selectivity
does not change the verdict — no ablation beats FULL even at raw selectivity.)

## M1 excess (pp) / M2 demeaned-S (bps) — DISJOINT (step=H, PRIMARY)
Returns are **demeaned per instrument** before the spread — closes the drift/composition artifact
that inflated V4C's S.

**FX**
| variant | H5 exc / S | H15 exc / S | H30 exc / S |
|---|---|---|---|
| V5.T | −3.0 / −14 | −8.0 / −33 | −11.0 / −41 |
| V5.TS | −2.4 / −7 | −5.0 / −25 | −3.0 / −5 |
| V5.NOF | −2.6 / −8 | −1.9 / −7 | +3.0 / +53 |
| V5.HALF | −0.8 / +1 | +3.2 / +23 | +4.9 / +104 |
| **V5.FULL** | **+0.5 / +6** | **+4.6 / +40** | **+11.2 / +163** |

**cross-asset** (H30 disjoint too thin → n/a)
| variant | H5 exc / S | H15 exc / S | H30 exc |
|---|---|---|---|
| V5.T | −0.9 / +8 | −2.5 / n/a | −0.6 |
| V5.TS | −2.2 / −23 | −2.8 / +170 | −3.1 |
| V5.NOF | −0.8 / −67 | −6.9 / −155 | −8.7 |
| V5.HALF | −2.7 / −114 | −9.4 / −190 | −14.4 |
| V5.FULL | −2.3 / −93 | −2.0 / +79 | −8.8 |

**V5.FULL (production) is the BEST FX variant; every ablation is worse** (removing the surprise
categories *hurts* FX in aggregate). **Cross-asset excess is NEGATIVE for every variant.** (Step-1
descriptive numbers, in `data/v5a_ablation.csv`, agree.)

## M3 sign consistency (demeaned-S by sub-period, DISJOINT)
| variant | fx H15 [24,25,26] | fx H30 [24,25,26] |
|---|---|---|
| V5.T | n/a, −14, −46 | n/a, +12, −101 |
| V5.NOF | −38, +2, −18 | n/a, +91, −75 |
| V5.HALF | −96, +40, +8 | n/a, +143, −14 |
| V5.FULL | −95, +51, +35 | n/a, +184, +90 |

**Every FX variant flips sign in 2024** (and several in 2026) → none is sign-consistent ≥2/3.
Cross-asset has data in only ~one sub-period (2025) → consistency untestable.

## Anti-artifact battery (V5.FULL cross-asset, demeaned S)
| H | S_real | S_loo (>0?) | S_placebo (<25%?) | S_dedup_rep |
|---|---|---|---|---|
| 15 | 1 | **−173 (no)** | 49 (**no**) | −26 |
| 30 | −57 | **−353 (no)** | −14 (yes) | −112 |

Even the best cross-asset variant (FULL) **fails the battery** — S≈0/negative, leave-top-out flips it
sharply negative, dedup on PROFILE_REP is negative. Consistent with V4C: cross-asset has no robust edge.

## Verdict — pre-registered decision rule
1. Placebo / leave-top-out: V5.FULL cross-asset **fails** → cross-asset has no survivor.
2–4. Among FX variants, **V5.FULL is best and no ablation beats it**; but **no variant is
   sign-consistent ≥2/3 sub-periods** (all flip in 2024), and none clears Δexcess ≥+1.5pp OR ΔS at
   BOTH H15 & H30 vs FULL. No tie-break needed.
5. **NO variant (including V5.T) has M1 > 0 consistent across sub-periods** → **this rule fires.**

**MAJOR FINDING — STOP for discussion, NO architecture recommendation.** Neither adding weight to
trend/sentiment (V5.T/V5.TS/V5.NOF) nor halving/removing fundamentals beats production, and nothing
shows a consistent positive directional excess. The binding constraint is DATA: trend has ~1 year of
real coverage (2026-full, 2025-partial), cross-asset returns start 2024-10, so the composite-weight
question **cannot be answered** on the available history. Production V5.FULL is unbeaten but is itself
regime-dependent (2024 negative) and its cross-asset edge fails the battery.

**Nothing adopted; production unchanged** (`composite_variant: full` inert). Data:
`data/v5a_ablation.csv` (77,116 non-Neutral obs × variants × H). Reproduce: `SCRATCH=<dir> python -m scripts.v5a`.
