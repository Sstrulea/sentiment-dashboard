# Pre-registration: Liquidity pillar — `net_liquidity` → bank reserves

**Date:** 2026-08-15
**Status:** PRE-REGISTERED. Diagnostic not yet run. No code touched.
**Scope:** Phase 1 only — swap the input series of the existing band scorer. SOFR−IORB regime flag is a separate specification and is explicitly OUT of scope.

---

## 1. Claim under test

The liquidity pillar currently scores the 21-bd ROC of `net_liquidity = WALCL − TGA − RRP`.

By Fed balance-sheet identity:

WALCL − TGA − RRP == Reserves + Currency in circulation + Other liabilities & capital


So the current input is bank reserves plus roughly $2.87T of non-reserve balast (5,813,370 − 2,944,059 as of 2026-08-12).

**Claim:** scoring reserves directly is strictly better than scoring reserves-plus-balast, because:

- **C1 (dilution).** The same dollar drain is divided by a ~2× larger base. Observed 2026-07-15 → 2026-08-12: −$199B reads as −6.3% on reserves, −2.64% on NL.
- **C2 (drift bias).** Currency in circulation grows monotonically. It imparts a permanent easing tilt to the NL ROC that has no liquidity content.

C1 is an arithmetic identity and is not in dispute. **C2 is an assertion and is tested in D4 below.**

### Explicitly retracted

The "2023–24 episode proves reserves win" argument is withdrawn. It is a single post-hoc-selected episode and does not meet the standard for evidence. It survives only as illustration of the mechanism, not as validation.

---

## 2. Acceptance criteria — declared before any numbers

| Outcome | Condition | Action |
|---|---|---|
| **ABANDON** | Sign agreement between the two ROCs ≥ 95% of business days (D1) | Keep `net_liquidity`. Change is cosmetic; recalibration risk not justified. Close the workstream. |
| **PROCEED** | Sign disagreement ≥ 8% of days **AND** ≥ 3 distinct divergence episodes lasting ≥ 10 bd (D1, D3) | Write V-spec, then implement. |
| **GREY** | Disagreement 5–8% | Proceed **only** if D3 attribution shows divergences cluster at regime turning points rather than in flat regimes. Otherwise abandon. |

Band derivation is fixed in advance to the existing methodology — `band_hi = p68(|roc|)`, `band_lo = p40(|roc|)` on the full reserve history. **No band value may be chosen by inspecting its effect on any current score.** If the p68/p40 rule produces an unacceptable bucket mix, the correct response is to abandon, not to hand-pick bands.

---

## 3. Adverse pre-commitments

1. **Gold's current −2 may weaken.** Reserve dispersion is wider, so recalibrated bands sit higher. A −6.3% reserve ROC against a wider band may land at ±1, not ±2. I will not retro-fit bands to preserve the −2.
2. **|Scores| move board-wide.** Every cross-asset row changes, including rows currently sitting at a comfortable `0`. Rows that agree with the current directional bias will move against it in some cases.
3. **If D4 shows currency drift contributes < 0.15%/mo to the NL ROC, C2 is retracted** and the case rests on C1 alone — which is a weaker case, and moves the decision toward GREY.
4. **If D1 returns ≥ 95% agreement, the entire recommendation is withdrawn** and this document is closed as a negative result. No partial implementation, no "do it anyway because the theory is cleaner."

---

## 4. Open specification decisions (answered by the diagnostic, not by preference)

| # | Decision | Candidates | Resolved by |
|---|---|---|---|
| S1 | Which reserve series | `WRESBAL` (week average) vs `WRBWFRBL` (Wednesday level) | D5 — pick the one whose timing semantics match the existing Wednesday-level WALCL pipeline, unless its ROC stdev is materially worse |
| S2 | `smooth` window | keep 5, or reduce (weekly series has ~4 independent obs per 21 bd) | D5 |
| S3 | `max_age_bd` | keep 10 | D5 — both series are H.4.1, publication lag should be identical |
| S4 | Bands | p68 / p40 per §2 | D2 |

---

## 5. Diagnostic prompt (READ-ONLY)

> Run against `~/projects/macro-data-analysis`.
> **FRED is blocked in the Claude Code sandbox — run this from the residential terminal.**

READ-ONLY DIAGNOSTIC. Do not modify src/, data/, or any existing module.
Do not open a PR. Write a single throwaway script under scripts/diag/ and
report results to stdout. Do not edit src/liquidity_compute.py or
src/liquidity_fetch.py.

CONTEXT
The liquidity pillar scores the 21-bd ROC of net_liquidity = WALCL - TGA - RRP,
using compute_liquidity_score() in src/liquidity_compute.py with
smooth=5, band_hi=0.0201, band_lo=0.0098, W=21.
We are testing whether bank reserves are a better input series.

DATA TO FETCH (FRED, keyless, via the existing FredSeriesSource in
src/rate_sources.py):
WRESBAL reserve balances, week average, ending Wednesday
WRBWFRBL reserve balances, Wednesday level
WCURCIR currency in circulation
Existing data/net_liquidity.parquet already carries date, net_liquidity,
walcl, tga, rrp. Do not overwrite it.

Build reserves on the same business-day forward-filled index the NL pipeline
uses (see assemble_net_liquidity). Window for all tests: 2013-09-23 to latest.

D1 - SIGN AGREEMENT (kill criterion)
Compute the 21-bd ROC (smooth=5) for net_liquidity and for reserves.
Report:
a) % of business days where sign(roc_NL) == sign(roc_RES), treating |roc|
1e-6 as zero
b) % of days where the BUCKETED score (via the existing bucket() helper,
current bands for NL, p68/p40-derived bands for reserves) differs by >= 1
c) % where it differs by >= 2
Do this for both WRESBAL and WRBWFRBL.

D2 - BAND DERIVATION
For reserves, report p40 and p68 of |roc| over the full window. Then report
the realized bucket mix {-2,-1,0,+1,+2} under those bands. Compare against the
NL reference mix {-2:11.5, -1:11.2, 0:40.0, +1:16.8, +2:20.5}.
Do NOT tune. Report p68/p40 as computed.

D3 - DIVERGENCE ATTRIBUTION
List every contiguous run of >= 5 business days where sign(roc_NL) !=
sign(roc_RES). For each run report: start date, end date, length in bd,
mean roc_NL, mean roc_RES, and the total delta over the run in WALCL, TGA,
RRP, and WCURCIR. This tells us WHICH leg causes each divergence.

D4 - CURRENCY DRIFT (tests claim C2)
Report the mean and stdev of the 21-bd delta in WCURCIR, and express the mean
as a percentage of the mean net_liquidity level. This is the size of the
permanent easing bias currency imparts to the NL ROC. Report it as %/mo.

D5 - CADENCE AND SERIES CHOICE
a) Publication lag in business days for WRESBAL and WRBWFRBL vs WALCL over
the last 52 weeks (all three are H.4.1 - confirm they are identical)
b) stdev of the 21-bd ROC for WRESBAL vs WRBWFRBL
c) correlation between the two ROC series
d) count of days in the last 52 weeks where either would breach
max_age_bd = 10

D6 - SATURATION CHECK
Apply the CURRENT NL bands (band_hi=0.0201, band_lo=0.0098) to the reserve ROC
and report the resulting bucket mix. This tests the claim that reusing current
bands would saturate the pillar at +/-2.

OUTPUT
One markdown table per diagnostic, plus the D3 episode list. No prose
interpretation, no recommendation. Numbers only.


---

## 6. What happens after

Diagnostic results return → evaluate against §2 → **STOP GATE**. Explicit sign-off required before any V-spec is written. No code changes until the spec document is committed.

If PROCEED: feature branch + PR. Test written and demonstrated failing on `main` first. Byte-level score diff produced and reviewed against the adverse pre-commitments in §3.
