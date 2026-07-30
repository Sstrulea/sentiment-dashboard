# Proposal — country/local-time ingest guard (text only, no code)

Status: proposal, written after `docs/pmi-decontamination-before-after.md`
(the required before/after measurement). **Not implemented.** Adoption is a
separate decision.

## Problem this closes

GBP contamination stopped on its own after 2026-01 (JBlanked's live feed
apparently corrected itself); CAD's mechanism is unconfirmed — the source
file that would prove/disprove ongoing CAD contamination
(`data/raw/ff_calendar_range.json`) no longer exists, and current daily
payloads haven't shown the bare `"Manufacturing PMI"` title in the retained
14-day window. There is currently **no mechanism that would catch a
recurrence** — the contamination was only found because someone looked at
`n=150` and asked why. A guard should exist so the NEXT occurrence (GBP
relapsing, a different currency/indicator developing the same defect, or a
brand-new generic-title series JBlanked adds later) gets caught at ingest,
not three years later in an audit.

## Proposed rule

At the same point `_canonicalize()` (`src/econ_calendar_ff.py`) already
computes `canonical_id` for a row — after the alias match, before the row is
appended to `out` — add one more check:

1. Convert the row's `datetime_utc` to the row's OWN currency's timezone
   (the same 8-currency map already used in Faza 3a: GBP→Europe/London,
   EUR→Europe/Berlin, USD→America/New_York, CAD→America/Toronto,
   JPY→Asia/Tokyo, AUD→Australia/Sydney, NZD→Pacific/Auckland,
   CHF→Europe/Zurich).
2. Compare the resulting local `HH:MM` against that series'
   (`currency`, `canonical_id`) **trailing historical dominant local time**
   (mode of local `HH:MM` over the last ~24 released prints — a trailing
   window, not all-history, so a genuine one-time schedule change
   eventually becomes the new normal instead of being flagged forever).
3. If the deviation exceeds **N=2 hours** from the dominant time, do not drop
   the row silently and do not add it to the scored frame as-is — write it to
   a quarantine record (see below) and log a WARNING, mirroring the existing
   `ff_fred_crosscheck.py` pattern exactly (same log style, same
   fail-open-if-uncertain philosophy).

**N=2 hours**, not tighter, because the 3 confirmed-legitimate cases in Faza
3a (JPY BoJ, CHF Unemployment schedule move, CAD BoC schedule move) are
schedule-drift or inherently-variable-timing cases, not contamination — a
tight threshold (e.g. 30 min) would flag every central-bank decision. The 3
CONFIRMED contamination cases (GBP/CAD PMI) all show deviations of 4-14+
hours from the true local time — comfortably outside a 2h band, so N=2h
would have caught every one of them without touching the 3 exonerated cases.

## Why trailing-window, not all-history

An all-history dominant time would itself get corrupted by the same bug it's
supposed to catch (as seen: GBP's 150-row history is majority-foreign for
years). A trailing window recomputed at each ingest, combined with the
existing purge (Faza 3b) resetting the historical baseline to clean data
first, means the guard bootstraps from a clean state and only needs to catch
NEW deviations from here, not re-detect the old ones.

## Where the quarantine record lives

Reuse the existing pattern, not a new one: `data/ff_quarantine.parquet`
already holds FRED-cross-check quarantines (`src/ff_fred_crosscheck.py`) with
schema `(currency, indicator_key, datetime_utc, ff_actual, fred_value, diff,
tol)`, read back and excluded from the calendar frame at render time
(`economic_render.py:756-767`). Proposal: add a `reason` column (e.g.
`"country_mismatch"` vs. the existing `"fred_mismatch"`) so both guards share
one file and one exclusion path, with a schema addition:
`(currency, canonical_id, datetime_utc, local_hm, dominant_local_hm,
deviation_hours, reason)`.

## What does NOT change

- No row is ever silently dropped without a record — every quarantined row
  stays inspectable (unlike the current PMI contamination, which had zero
  trace pointing at it).
- A single stray row (e.g. a genuine one-off reschedule, a DST edge case in
  the trailing window itself) does not retroactively flag the WHOLE series —
  only that one row is quarantined; the trailing window naturally absorbs a
  real, sustained schedule change within ~2 releases.
- No scoring threshold, z-bucket, or weight changes. This is a data-quality
  gate, same category as the existing zero-placeholder quarantine and FRED
  cross-check — not a recalibration.

## Risks (asked for explicitly)

- **Legitimate schedule changes** (CHF Unemployment, CAD BoC, confirmed in
  Faza 3a): the FIRST print after a real schedule move will deviate from the
  trailing window's old dominant time and get flagged once. With a 24-print
  trailing window, it takes ~half the window to "flip" the dominant time for
  a monthly series — meaning a genuine change could generate a handful of
  false-positive quarantines (one per release) before the window catches up.
  Mitigation already implicit in the log-and-quarantine (not silently-drop)
  design: a human reviewing the quarantine log sees "same deviation, N times
  in a row, always the same new time" and can recognize a schedule change
  vs. random contamination (which won't repeat at a consistent new time).
- **Central bank decisions** (BoJ, and by extension any
  `interest_rate_decision` indicator): inherently variable announcement
  time, no fixed "dominant" time to compare against. Proposal: exclude
  `interest_rate_decision` from this guard entirely (it's already
  `weight=0`/display-only and not part of any `categories_cfg` aggregation
  per the Faza 2 finding — false positives here would be pure noise with no
  scoring benefit from catching them).
- **Series with genuinely irregular cadence** (`unknown` frequency, or n too
  small to have a meaningful trailing mode — anything from the Faza 0.0c
  thin-baseline inventory, n<8): a trailing window can't establish a
  reliable dominant time from 2-7 prints. Proposal: skip the guard for any
  series with fewer than ~8 released prints (same floor as the quarterly
  `CADENCE_THRESHOLD` — noting per §4 of the staleness doc that this constant
  is currently dead code; reusing its VALUE here doesn't reactivate it, it's
  just a reasonable floor to borrow).
- **A currency's OWN timezone is wrong for some indicator** (e.g. an EU
  aggregate release scheduled by Frankfurt time when the "true" release
  might be timed to Brussels or a pan-EU convention slightly offset from
  Berlin): low risk given Faza 3a validated this exact map against all 8
  currencies' real series without a false positive, but noted as an
  assumption baked into the tz map, not re-derived per indicator.
- **New genuinely-generic titles JBlanked adds later**: this guard only
  catches the SAME failure mode (country mislabeled, time-of-day reveals it).
  A future defect that mislabels currency without a time-of-day tell (e.g. a
  country whose true release time coincidentally matches another country's)
  would not be caught. Out of scope for this proposal; not aware of such a
  case existing today.

## Not implemented

Text only, per instructions. If adopted, the natural home is inside
`_canonicalize()` (single insertion point already shared by both JBlanked-
range and FF-weekly parsers) plus a small addition to
`ff_fred_crosscheck`-adjacent quarantine-write code, reusing
`data/ff_quarantine.parquet`. Estimated blast radius: one new function in
`econ_calendar_ff.py`, one schema addition to the quarantine parquet, zero
changes to scoring.
