# PMI decontamination — before/after (2026-07-30)

Status: **APPROVED**. Purge applied to `data/economic_calendar_ff.parquet`
(`data/economic_calendar_ff.parquet.pre-purge` = full backup, committed
alongside). Scope: 3 series only — GBP Manufacturing PMI, GBP Services PMI,
CAD Manufacturing PMI. Nothing else touched. Provenance confirmed against the
raw archive (`data/archive/`, commit `d9e656d`) before approval — see
Faza 6 findings: `Currency=GBP`/`CAD` for the foreign-country rows is what
JBlanked's own archive already contains; our ingest never rewrote anything.

## Purge rule applied

For each of the 3 series: converted `datetime_utc` to the currency's own
timezone (`Europe/London` GBP, `America/Toronto` CAD), took the mode of the
resulting local `HH:MM`, kept only rows matching that value **exactly**
(no jitter needed — the true-country cluster's local time was exact-match
clean; see Faza 3a). Months with zero rows at the dominant local time are
left empty rather than backfilled — a hole is better than a foreign value.

| currency | indicator | dominant local time | n before | n after | dropped |
|---|---|---|---|---|---|
| GBP | S&P Global/CIPS Manufacturing PMI | 09:30 | 150 | 43 | 107 |
| GBP | S&P Global/CIPS Services PMI | 09:30 | 117 | 44 | 73 |
| CAD | S&P Global Manufacturing PMI | 09:30 (local Toronto) | 82 | 43 | 39 |

## A worse finding than "extra noise": dedup was silently picking the WRONG country

Before measuring surprise stats, traced why the *current production* z-score
history for these series only ever used ~1 row/month (not 150) despite no
one having deduped anything. Cause: `to_scoring_frame`'s output schema
(`SCORING_COLUMNS` in `ff_scoring.py`) carries **no `period` column** — the
FF pipeline never populates it. `economic_compute._dedup_flash_final`'s exact
`period`-match path is therefore dead for every FF-sourced row; it *always*
falls into the release-date-proximity fallback, clustering same-day rows by
`dedup_gap_days` (18d for monthly) and keeping **whichever row in the cluster
has the LATEST `release_dt`** (`_keep_latest_published`).

Since the foreign impostor countries release LATER in the UTC/local day than
the UK (US final PMI ~13:45/14:45 UTC vs UK's 08:30/09:30), the dedup was, for
most months 2023–2025, **discarding the real UK print and keeping the US
one** (occasionally Japan's, at 00:30) — silently, every month, with no
error. Confirmed directly on the pre-purge deduped series:

```
GBP manufacturing_pmi, last 8 months before the contamination stopped:
2025-04-01 13:45  actual=50.2   <- US-timed row kept
2025-05-02 08:00  actual=49.0   <- EU-timed row kept
2025-06-02 13:45  actual=52.0   <- US-timed row kept
2025-07-01 13:45  actual=52.9   <- US-timed row kept
2025-08-01 13:45  actual=49.8   <- US-timed row kept
2025-09-02 13:45  actual=53.0   <- US-timed row kept
2025-11-04 00:30  actual=48.2   <- JP-timed row kept
2025-12-01 14:45  actual=52.2   <- US-timed row kept
2026-01-02 09:30  actual=50.6   <- first UK-timed row (contamination ended)
```
Same pattern, same magnitude, in GBP services_pmi. **This means the live
dashboard's GBP Manufacturing/Services PMI score has, for most of the last
three years, actually been scoring the US's (sometimes Japan's) PMI print
labeled as UK data — not "UK data with a noisy baseline."** The purge fixes
this going forward for the trailing-K window; historical months where the UK
row is simply absent (never released under this contamination — none found
so far) stay empty rather than backfilled.

CAD Manufacturing PMI shows the same per-month substitution pattern (Italy/
Spain-style Eurozone-periphery final PMI, released 07:30 UTC, beating the
true CAD 13:30/14:30 release into most `_keep_latest_published` picks is
NOT the case here — CAD's foreign impostor is EARLIER than the true print,
so the true CAD row was usually the one kept; verified below the true print
already dominates recent dedup output). The bigger CAD issue is separate:
`consensus` (FF forecast) is missing for nearly every CAD Manufacturing PMI
release from 2025-09 onward, before *and after* the purge — an unrelated,
pre-existing data-completeness gap, not caused by or fixed by this purge.

## Per-indicator before/after (trailing-12 window, `as_of=2026-07-30`)

| currency/indicator | n_total (dedup'd) | n pairs in window | mean surprise | sigma | today's cell score |
|---|---|---|---|---|---|
| GBP manufacturing_pmi | 43 → 42 | 12 → 12 | −0.017 → **−0.125** | 0.449 → **0.325** | **−1 → −2** |
| GBP services_pmi | 42 → 42 | 12 → 12 | −0.050 → **0.233** | 0.701 → **0.789** | 0 → 0 (unchanged) |
| CAD manufacturing_pmi | 42 → 42 | 11 → **2** | −1.264 → **0.750** | 2.081 → **0.636** | 0 → 0 (`no_consensus`, unchanged — today's print itself was already the true-CAD one) |

GBP manufacturing_pmi's cell score moves from −1 to −2 (Very Bearish PMI
surprise reading) once the trailing window is built from genuine UK prints —
the sigma shrinks (0.449→0.325, a cleaner/tighter distribution) enough that
the same-size surprise now buckets one notch more extreme.

CAD manufacturing_pmi's window collapses from 11 pairs to 2 **only because**
most of the historically-kept "pairs" were the foreign impostor rows that
happened to carry a `Forecast` value where the real CAD row didn't (a
separate, pre-existing data-completeness gap, see above) — removing the
impostors removes those forecast values along with them. Today's score is
unaffected (`no_consensus`, both before/after) because the single most
recent release used for display already was the genuine CAD print.

## Category-level before/after

| currency | category | coverage before → after | score_precise before → after |
|---|---|---|---|
| GBP | growth | 4 → 4 | **0.25 → 0.0** |
| CAD | growth | 3 → 3 | 0.667 → 0.667 (unchanged) |

## Pairs that change bias

Checked every FX pair with a GBP or CAD leg (13 pairs) before/after, same
`as_of`, calendar-only (rates/trend/sentiment factors held out of this
comparison since they don't touch the calendar and are identical in both
runs):

| pair | score before → after | bias before → after |
|---|---|---|
| GBPUSD | 0.208 → 0.0 | Neutral → Neutral |
| EURGBP | 0.069 → 0.278 | Neutral → Neutral |
| GBPJPY | −1.181 → −1.389 | Bearish → Bearish |
| GBPCHF | −0.625 → −0.833 | Neutral → Neutral |
| GBPAUD | 0.764 → 0.556 | Neutral → Neutral |
| **GBPNZD** | **−1.042 → −1.25** | **Neutral → Bearish** |
| GBPCAD | −0.069 → −0.278 | Neutral → Neutral |
| USDCAD | −0.278 → −0.278 | Neutral → Neutral |
| EURCAD | 0.0 → 0.0 | Neutral → Neutral |
| AUDCAD | −0.833 → −0.833 | Neutral → Neutral |
| NZDCAD | 0.972 → 0.972 | Neutral → Neutral |
| CADJPY | −1.111 → −1.111 | Neutral → Neutral |
| CADCHF | −0.556 → −0.556 | Neutral → Neutral |

**One bias flip: GBPNZD, Neutral → Bearish.** Every CAD-leg pair is unchanged
today (CAD's current print was already clean, per above) — the purge's
present-day effect is entirely on the GBP leg.

## The 219 purged rows are not waste — they're the missing history of thin series

The correctly-tagged EUR/JPY/USD/CHF copies of these same PMI titles all begin
in January 2026 (n=7 each) — and those exact series are the ones sitting in
the Faza 0c thin-baseline inventory (n=7-8, below their own cadence
threshold). Their 2023–2025 history isn't gone: it's sitting in the archive
(`data/archive/`, commit `d9e656d`), tagged `GBP`/`CAD` by JBlanked's mistake,
kept intact.

Re-attributing those rows by local time (the same rule used for this purge)
would turn 4 below-threshold series into ~35-38-month histories (counts
verified directly against the archive, matching the currency's own dominant
local time, not estimated):

| currency | indicator | today (n) | with archive re-attribution (n) |
|---|---|---|---|
| EUR | S&P Global Manufacturing PMI | 8 | 35 |
| EUR | S&P Global Services PMI | 8 | 36 |
| JPY | au Jibun Bank Manufacturing PMI | 8 | 34 |
| USD | S&P Global Manufacturing PMI (distinct from ISM) | — | 36 |
| USD | S&P Global Services PMI (distinct from ISM) | — | 37 |
| CHF | procure.ch Manufacturing PMI | 7 | 38 |

**Validation checked, not just assumed** — corrected after first drafting
this wrong: GBP/CAD's mistagging stopped CLEANLY at end-2025, not
gradually, so there is no window where a mistagged row and a correctly-tagged
row for the same month coexist. The real check is the CUTOVER, month to
month: the last mistagged row (Dec 2025, under GBP/CAD) vs. the first
correctly-tagged row (Jan 2026, under the true currency) —

| series | last mistagged (local time) | first correct (local time) | match |
|---|---|---|---|
| EUR Manufacturing PMI | 2025-12-01, 10:00 Berlin | 2026-01-02, 10:00 Berlin | ✓ |
| JPY Manufacturing PMI | 2025-12-01, 09:30 Tokyo | 2026-01-05, 09:30 Tokyo | ✓ |
| CHF Manufacturing PMI | 2025-12-04, 09:30 Zurich | 2026-01-05, 09:30 Zurich | ✓ |

Same local time, one release-cycle apart, in every case checked — a clean
handoff, not a coincidence. This confirms the attribution rule identifies
the right underlying series across the cutover, on real data, not just by
the timezone logic from Faza 3a. Not acted on here — recovering EUR/JPY/USD/
CHF history from the archive is a new, separately-scoped decontamination
pass, not an extension of the GBP/CAD purge in this doc.

`data/archive/` is not deleted — it is the only remaining source for this
recovery and for re-deriving anything else this audit didn't anticipate.

## Adopted

Purge approved and merged. Backup at
`data/economic_calendar_ff.parquet.pre-purge`, committed alongside for
reproducibility. `docs/proposal-pmi-ingest-guard.md` remains a proposal
(not implemented) — its case is now stronger given Faza 6 confirmed the
underlying source defect is real, not hypothetical.
