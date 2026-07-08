# Fundamental V4C — edge confirmation battery + V4.0 vs V4.3 head-to-head (READ-ONLY)

Branch `feat/fundamental-v4`. Two objectives: **(A)** stress-test the provisional cross-asset
long-horizon edge from `fundamental-v4-horizon.md`; **(B)** head-to-head production **V4.0** (prod
z-buckets) vs **V4.3 "SIGN"**. Both on the V4.0 **fundamental-only** score (growth+inflation+labour),
non-Neutral readings, base-rate-corrected, H ∈ {15,21,30} trading days, 829 as-ofs 2023-05→2026-07.
**Nothing adopted; production defaults unchanged (byte-identical guard passes).**

## V4.3 "SIGN" scoring (flag `defaults.scoring_variant: prod|sign`)
Per indicator, direction applied, `s = direction·(actual−consensus)`: `actual==consensus` at
reported granularity (rounded 6 dp — kills float noise) → 0; else `sign(s)·1`, gated to `sign(s)·2`
when `|z| ≥ z_buckets[0]` (=1.54). **No dead-zone — any beat/miss scores** (vs prod's |z|<0.81→0).
z is production std-σ over 12 prints, used ONLY as the ±1/±2 gate. Fallback (<6 prints): sign-only
±1. no_consensus → 0. Categories/signs/weights/pipeline identical. Under `prod` → byte-identical.

---

## (A) CONFIRMATION BATTERY — V4.0 cross-asset

### A1 — disjoint windows (as-of step = H, independent obs)
| H | n | S_full (bps) | S_disjoint | excess_full | excess_disj |
|---|---|---|---|---|---|
| 15 | 41 | 443 | **+323** | +10.3 | +11.4 |
| 30 | 16 | 421 | **n/a** | +2.8 | n/a |

At H=30 the disjoint sample is too thin (16 as-ofs; <5 bull or bear cross-asset obs) to form S →
criterion (i) "S_disjoint>0 @15 **and** 30" **cannot be met**.

### A2 — dedup profiles (only PROFILE_REP: SP500/DAX/NIKKEI/FTSE100/GOLD)
| H | n | S_all (bps) | S_rep | monotone on reps? |
|---|---|---|---|---|
| 15 | 381 | 443 | 324 | **✗** |
| 30 | 364 | 421 | 220 | **✗** |

S stays positive but the bucket **monotonicity breaks once correlated clones are removed** — the clean
VBear<Bear<Bull<VBull ordering was a clone artifact (US indices identical, metals identical).

### A3 — leave-top-out (episodes = consecutive same instrument+bias-group; drop top-3 by contribution)
| H | n episodes | S_full (bps) | **S_loo** |
|---|---|---|---|
| 15 | 29 | 443 | **−98** |
| 30 | 29 | 421 | **−215** |

**Smoking gun: removing the 3 largest episodes flips S NEGATIVE.** The entire spread comes from a
handful of episodes, not a broad effect.

### A4 — placebo ×2 (must give S≈0; |S_placebo| < 25% of S_real to pass)
| H | S_real | S_permute (time-shuffled) | S_lag60 | pass? |
|---|---|---|---|---|
| 15 | 443 | **204** (46%) | 70 | **FLAG** |
| 30 | 421 | **155** (37%) | 149 | **FLAG** |

**Red flag:** even time-permuted scores produce large positive S. The metric is a **composition
artifact** — high-drift index obs dominate the "bull" pool regardless of score↔return timing — not
genuine predictive alignment.

### A5 — FX-2024 reversal diagnostic (S per year, carry JPY/CHF leg vs non-carry)
| H | carry 2024/25/26 | non-carry 2024/25/26 |
|---|---|---|
| 15 | n/a / +99 / +36 | **−68** / +32 / +43 |
| 30 | n/a / +203 / −3 | **−83** / +38 / −48 |

The 2024 reversal sits on **non-carry** pairs (carry has too few 2024 non-Neutral obs) → **broad**, not a carry-funding artifact.

### A-VERDICT
| gate | result |
|---|---|
| S_disjoint > 0 @15 **and** 30 | ✗ (H30 n/a) |
| S_loo > 0 | ✗ (**−98 / −215**) |
| both placebo < 25% S_real | ✗ (**flagged**) |
| monotone on dedup | ✗ |

**→ PROVISIONAL edge DEMOTED to NULL.** The cross-asset long-horizon "edge" is an artifact of a few
episodes plus index-drift composition (placebo positive) — it does not survive the confirmation battery.

---

## (B) HEAD-TO-HEAD — V4.0 (prod) vs V4.3 (sign)

**Selectivity** (% non-Neutral): fx V4.0 35.2 / V4.3 37.3 (+2.1pp); cross-asset 16.6 / 21.4 (+4.7pp)
— both **≤10pp**, no equalization needed. (SIGN adds ±1s per indicator, but composite averaging +
bias thresholds absorb most, so directionality at the bias level barely moves.)

### B-FULL (all as-ofs; overlapping windows)
| class | H | exc V4.0 | exc V4.3 | S V4.0 | S V4.3 | ΔS |
|---|---|---|---|---|---|---|
| fx | 15 | 8.9 | 5.0 | 47 | 21 | **−26** |
| fx | 21 | 8.4 | 5.3 | 61 | 29 | −32 |
| fx | 30 | 7.2 | 6.4 | 68 | 35 | −32 |
| cross-asset | 15 | 10.3 | 11.1 | 443 | 775 | +332 |
| cross-asset | 30 | 2.8 | 10.5 | 421 | 978 | +556 |

### B-DISJOINT (as-of step = H, independent; the decision basis)
| class | H | exc V4.0 | exc V4.3 | S V4.0 | S V4.3 |
|---|---|---|---|---|---|
| fx | 15 | **12.6** | 7.0 | **72** | 28 |
| fx | 30 | **13.0** | 9.3 | **110** | 72 |
| cross-asset | 15 | 11.4 | 6.8 | 323 | 593 |
| cross-asset | 30 | n/a | 21.0 | n/a | 1028 |

### B-M3 — spread S sign by sub-period (2024/2025/2026)
- V4.0 fx H15 [−114,+56,+36] H30 [−75,+99,−28] · cross-asset H15 [n/a,+469,+782] H30 [n/a,+581,+332]
- V4.3 fx H15 [−12,+30,−14] H30 [−89,+61,−50] · cross-asset H15 [n/a,+1016,+409] H30 [n/a,+1480,−99]

Both variants remain **sign-inconsistent** across sub-periods (fx flips every year; cross-asset only
2 periods with data, and V4.3 cross-asset H30 flips negative in 2026).

### B-VERDICT (V4.3 wins iff @H15 AND H30 disjoint: exc≥V4.0+1.5pp OR S≥V4.0+15bps(fx)/+100bps(x-asset), sign-consistent ≥2/3, no other-class deterioration)
| class | H15 improves | H30 improves | verdict |
|---|---|---|---|
| **fx** | ✗ (worse on both) | ✗ | **V4.0 stays** |
| **cross-asset** | ✓ (S) | ✗ (V4.0 n/a) | **V4.0 stays** |

**→ V4.0 stays.** V4.3 is strictly **worse on FX** (excess and S, full and disjoint); on cross-asset it
only "improves" the S metric that battery (A) just showed is an artifact, fails the both-horizons
requirement, and is sign-inconsistent. No adoption.

---

## Conclusion
1. **The provisional cross-asset long-horizon edge is refuted** — demoted to NULL by the confirmation
   battery (S flips negative without 3 episodes; time-permuted placebo still positive; monotonicity is
   a clone artifact). The earlier `fundamental-v4-horizon.md` "EDGE (per criterion)" was correctly
   flagged provisional and does **not** survive.
2. **V4.3 SIGN does not beat production V4.0** — worse on FX, artifact-only on cross-asset.
3. **Nothing adopted; production V4.0 unchanged** (guard `test_prod_variant_byte_identical`).

Data: `data/v4c_headtohead.csv` (20,140 non-Neutral obs, both variants, r15/r21/r30). Reproduce:
`SCRATCH=<dir> python -m scripts.v4c` (after the two step=1 fundamental replays). Prod/FX-2024 stays
broad-regime, not carry-specific.
