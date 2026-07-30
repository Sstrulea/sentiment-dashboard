# Adoption notes — 2026-07-30

Branch `fix/no-consensus-and-promotions`. Records the exact expiry dates for
the two backfilled slots (AUD `retail_sales`/Household Spending continuation,
CAD `core_cpi`/Median CPI y/y promotion), how to check whether a fresh print
has landed, and the options if it hasn't — no decision made here.

## AUD `retail_sales` (Household Spending m/m continuation)

- Last print in the backfilled history: **2026-06-25**.
- Frequency: `monthly` (default for `retail_sales`, no AUD override).
- Recency window: `max_age_by_frequency.monthly = 45d`.
- **Expires 2026-08-09** (2026-06-25 + 45d). On that date, absent a new
  print, this indicator drops out of AUD growth's coverage.
- **What happens then**: AUD growth today (post-backfill) is **N=2**
  (`retail_sales` + `gdp_qoq`). Losing `retail_sales` drops it to **N=1**
  (`gdp_qoq` alone, released 2026-06-03, quarterly 110d window, itself not
  expiring until **2026-09-21**) — not N=0. This is the exact volatility
  trade-off measured in `docs/measurement-no-consensus-slots.md` §5/§8: the
  continuation buys until 2026-08-09, not indefinitely.

### Verification command — has a new AUD Household Spending print landed?

```bash
.venv/bin/python3 -c "
import pandas as pd
df = pd.read_parquet('data/economic_calendar_ff.parquet')
sub = df[(df.currency=='AUD') & (df.name_canonical=='Household Spending m/m')].sort_values('datetime_utc')
print(sub[['datetime_utc','actual','forecast']].tail(3))
"
```
A new row with `datetime_utc` after 2026-06-25 means AUD growth's coverage
window has been refreshed — re-run the FAZA 5 verification script to confirm
N stays at 2 and no bias changes unexpectedly.

**Also check the raw daily cache** (`data/jb_raw/jb_range_*.json`, ~7-day
rolling window) for an event that hasn't reached the parquet yet:
```bash
for f in data/jb_raw/jb_range_*.json; do
  .venv/bin/python3 -c "
import json
data = json.load(open('$f'))
if isinstance(data, dict): data = data.get('data') or list(data.values())[0]
hits = [e for e in data if str(e.get('Currency','')).strip()=='AUD' and 'household' in str(e.get('Name','')).lower()]
if hits: print('$f', hits)
"
done
```
As of 2026-07-30, this returns nothing — no AUD Household Spending event in
any of the last several days' raw payloads, confirming §the prior
measurement's finding still holds: the projected next print (~2026-07-24 to
07-26, from a 29.5-day median cadence) has not shown up. Unknown whether ABS
delayed it or JBlanked's feed doesn't carry it every cycle.

### Options if no new print arrives before 2026-08-09 (not a decision — pick one when the date arrives)

1. **Do nothing.** AUD growth falls to N=1 (GDP alone) on 2026-08-09,
   exactly the same state Variant B alone would have produced without the
   continuation — not worse than today's pre-continuation baseline, just a
   delayed arrival at it.
2. **Investigate the source gap** before 08-09 — confirm with JBlanked/ABS
   whether Household Spending m/m is still a live, regularly-scheduled
   release; if it has been discontinued or renamed, the continuation slot
   needs a different next step (a second alias, or accepting AUD growth's
   permanent N=1 as the new floor).
3. **Extend the recency window for this one indicator** via `max_age_days`
   override on `retail_sales` for AUD specifically — a real option
   mechanically, but a recalibration decision, out of scope here (see "orice
   altă recalibrare de scoring" in the task's exclusions) and not something
   to do reflexively just to dodge a Aug-9 cliff without knowing why the
   print is late.

## CAD `core_cpi` (Median CPI y/y promotion)

- Last print in the backfilled history (from `data/archive/`): **2026-06-22**.
- Frequency: `monthly` (CAD is not in `core_cpi`'s AUD-only quarterly
  override).
- Recency window: 45d.
- **Expires 2026-08-06** (2026-06-22 + 45d) on the backfilled data alone.

### A fresher real print already exists but is NOT yet in the parquet

Checked the raw daily JBlanked cache (`data/jb_raw/jb_range_2026-07-{26,27}*.json`,
the only two of the last five daily pulls whose ~7-day rolling window still
covered that date): both contain a **CAD Median CPI y/y print dated
2026-07-20** (actual 1.9, forecast 2.1) — 10 days newer than the archive's
last row, and consistent with the series' ~28-day median cadence
(2026-06-22 + 28d ≈ 2026-07-20, bang on schedule). It is **absent** from the
2026-07-28/29/30 payloads simply because their rolling window had already
advanced past 07-20 by then (07-28's window starts 07-21) — not withdrawn or
revised, just aged out of a short lookback.

**This print has never been merged into `data/economic_calendar_ff.parquet`**
— confirmed on both the pre-backfill and post-backfill parquet (0 rows for
CAD "Median CPI y/y" before this migration; 43 after, all from the archive,
none from 07-20). The daily actuals pull (`src/jb_actuals.py`, out of scope
to run or modify here) is the production mechanism that should pick this up
on a subsequent scheduled run, now that the matcher routes "Median CPI y/y"
to a scored slot (`core_cpi`) instead of the old display-only
`median_cpi_yoy` — **that repoint didn't exist yet on 2026-07-26/27 when
this print was captured**, so whether the daily pull's own matching logic
retroactively picks up an already-seen-but-then-window-expired event, or
only catches events still inside its rolling window at run time, is not
verified here (reading `src/jb_actuals.py`'s exact backfill-vs-window
behavior is needed before assuming either way — flagged, not resolved).

**If/when that 07-20 print (or a later one) lands in the parquet through the
normal pipeline, the expiry pushes out to 2026-07-20 + 45d = 2026-09-03** —
well past the 2026-08-06 date the archive-only backfill implies today.

### Verification command — has the 2026-07-20 (or newer) print landed in the parquet yet?

```bash
.venv/bin/python3 -c "
import pandas as pd
df = pd.read_parquet('data/economic_calendar_ff.parquet')
sub = df[(df.currency=='CAD') & (df.name_canonical=='Median CPI y/y')].sort_values('datetime_utc')
print(sub[['datetime_utc','actual','forecast']].tail(3))
"
```
Expect to still see 2026-06-22 as the last row until a scheduled
`ff_refresh`/`jb_actuals` run merges the newer one in.

### Options if the parquet still shows only 2026-06-22 by 2026-08-06

1. **Do nothing.** CAD inflation drops from N=3 to N=2 (`cpi_yoy` + `ppi_yoy`
   survive — see `docs/measurement-no-consensus-slots.md` §8 N=0-exposure
   table) — not N=1, not N=0, since CAD inflation has 3 members today and
   only one (`core_cpi`) is at risk.
2. **Manually verify + merge the 07-20 print** (or whatever is latest by
   then) through the SAME narrow, guarded mechanism this migration used
   (`migrations/2026-07-30_backfill_median_household.py`'s pattern: filtered
   strictly to `(CAD, "Median CPI y/y")`, PMI guard re-checked) — reasonable
   given it's a single already-observed real print sitting in a raw payload
   that just hasn't reached the parquet, not a speculative fill.
3. **Investigate why the automated pipeline hasn't carried it forward** —
   whether `src/jb_actuals.py`'s own dedup/window logic needs the matcher
   repoint to have existed AT CAPTURE TIME to route it correctly (open
   question above).

## Summary table

| slot | last print (backfilled) | expires | falls to | newer print observed but not ingested? |
|---|---|---|---|---|
| AUD `retail_sales` (Household Spending) | 2026-06-25 | 2026-08-09 | N=2→N=1 (GDP alone, safe until 2026-09-21) | No — checked, absent from all 5 recent daily payloads |
| CAD `core_cpi` (Median CPI y/y) | 2026-06-22 | 2026-08-06 (2026-09-03 if the 07-20 print lands first) | N=3→N=2 (`cpi_yoy` + `ppi_yoy` survive) | **Yes — 2026-07-20, actual 1.9/forecast 2.1, seen in `data/jb_raw/jb_range_2026-07-{26,27}*.json`, not yet in the parquet** |
