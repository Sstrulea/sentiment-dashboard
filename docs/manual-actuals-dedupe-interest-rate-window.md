# interest_rate_decision dedup window — measurement + open thread

Context: `src/manual_actuals.py`'s duplicate-suppression rule (c) needed a
safe cross-day clustering window per indicator. This doc records the
measurement behind the `interest_rate_decision` override
(`RULE_C_GAP_DAYS_BY_INDICATOR = {"interest_rate_decision": 7}`, days) and a
separate data-quality issue surfaced while measuring it.

## Window measurement (2026-08-12, full parquet history)

Gap between consecutive **real** (non-NaN) prints per canonical_id, all 8
tracked central banks:

| canonical_id | min gap | median | sub-18d gaps |
|---|---|---|---|
| aud_rba | 28.0d | 42.0d | 0 |
| chf_snb | 77.0d | 91.0d | 0 |
| gbp_boe | 42.0d | 49.0d | 0 |
| cad_boc | 0.0d | 42.0d | 2 |
| eur_ecb | 5.0d | 42.0d | 2 |
| jpy_boj | 5.0d | 47.0d | 1 |
| nzd_rbnz | 0.0d | 49.0d | 1 |
| usd_fed | 0.0d | 42.0d | 2 |

Minimum real gap between two confirmed-distinct decisions: **28 days**
(AUD). Every sub-18d gap found traced to an artifact (see below), not a
second genuine meeting. 7 days covers every confirmed same-event
revision span in the data (BoJ 07-29→07-31, ~1.3d; CHF cases, ~1–2d) with
>20 days of margin below the 28d real minimum — chosen over the
frequency-derived 18d default specifically for this indicator, since 18d
left too little headroom for a hypothetical unscheduled/emergency decision
landing near a regularly scheduled one (not observed in this dataset, but
not excluded by it either).

## Separate finding: fabricated "ghost" rows, not duplicates — NOT fixed here

Four of the sub-18d pairs above are **not** revision duplicates of one real
meeting. In each, one row is a real decision (matches known history) and
the other has `actual == previous` (i.e. "held", internally consistent) but
does **not** correspond to any real scheduled decision at that date/level:

| canonical_id | ghost row | ghost actual/fcst/prev | real row (same cluster) |
|---|---|---|---|
| cad_boc_interest_rate_decision | 2023-01-24 14:45 | 5.0 / 5.0 / 5.00 | 2023-01-25 15:00 → 4.5 / 4.5 / 4.25 |
| eur_ecb_interest_rate_decision | 2023-01-25 13:15 | 4.5 / 4.5 / 4.5 | 2023-02-02 13:15 → 3.0 / 3.0 / 2.5 |
| eur_ecb_interest_rate_decision | 2024-01-30 13:15 | 0.0 / 0.0 / 0.0 | 2024-01-25 13:15 → 4.5 / 4.5 / 4.5 (real) |
| usd_fed_interest_rate_decision | 2023-01-31 19:00 | 5.50 / 5.50 / 5.5 | 2023-02-01 19:00 → 4.75 / 4.75 / 4.5 |

None of these are duplicates in the sense rule (c) targets — they have a
**real, non-NaN actual**, so they're excluded from `find_actionable_rows`'s
output entirely and dedup never touches them. The dedup rollout **hides
them from the panel** (they were never actionable, nothing to hide) but
does **nothing to fix them in the underlying data** — they remain in
`data/economic_calendar_ff.parquet` and will still enter the z-score
baseline / scoring for their respective currencies as if they were real
prints.

This scan only checked pairs with a sub-18-day gap to a same-indicator
neighbor; it is not a full audit of `interest_rate_decision` data quality,
and ghost rows with no close-in-time neighbor would not have surfaced this
way. Left open, not touched, per instruction — scoring/thresholds/ingest
are out of scope for feat/manual-actuals-dedupe.

## Known limitation: rule (a) can orphan a row that rule (c) would have caught

`chf_retail_sales` has two ambiguous (ZERO_CONFIRM) rows for one real
release: `2025-01-05 22:00` and `2025-01-06 06:30`, both 0.0, same
canonical_id, same forecast (1.3) — the real print landed at
`2025-01-06 07:30` (0.8). Expected: all three collapse to nothing
actionable (the 0.0s are the same event as the real print). Actual: only
`2025-01-06 06:30` gets suppressed; `2025-01-05 22:00` survives as a
standalone ZERO_CONFIRM row.

Cause: rules run strictly (a) → (b) → (c). Rule (a) suppresses
`01-06 06:30` on its first pass (same calendar day as the valid
`01-06 07:30` sibling) *before* rule (c) ever runs. By the time (c) looks
for a cross-day cluster partner for `01-05 22:00`, its only possible
partner (`01-06 06:30`) is already gone — the pair never gets a chance to
cluster, so (c) has nothing left to collapse and `01-05 22:00` falls
through both rules.

Fix considered and rejected: running (c) before (a) resolves this cleanly
(verified) — (c) would collapse `01-05 22:00` + `01-06 06:30` first, then
(a) would suppress the surviving representative once it sees the same-day
valid sibling. Rejected because it would make rule (a)'s result — today
deterministic and independent of the (c) heuristic window/content-guard —
depend on that window instead. Kept (a)→(b)→(c) as originally specified.

Impact: `2025-01-05 22:00` is ~19 months old, well outside the panel's
45-day relevance window — invisible on the live panel today, archive-only.
Accepted cost, documented, not fixed.
