# §O — NZD / CHF rate-expectations sourcing: CLOSED (2026-07-28)

Status: **closed, documentation-only. No code changed, no flag added, nothing
ingested.** This note formalizes a closure that was already de-facto reached
during the §M investigation (`docs/prereg-M-currency-comparability-2026-07-28.md`
§11, §12.2, §12.3, §16, on `diag/scoring-audit`) and records it against
`src/rate_compute.py`, the module whose `RateScore.stale` field and
`MAX_AGE_BD` threshold are what a staleness-based flag would have extended.

## What §O was

A three-state visual flag for the `monetary` (rate-expectations) category,
distinguishing:

1. **live** — current data, no flag.
2. **stale, recoverable** — the source responds, but hasn't published recently
   (example: AUD/RBA, 13 days lag). Worth retrying.
3. **permanently unavailable** — the source is structurally blocked or dead.
   Not worth retrying automatically.

The point of state (3) vs (2) was operational: know which sources are worth
re-attempting and which aren't, without re-running the same dead investigation
every time NZD/CHF come up.

## What was tried, and what failed

### NZD — RBNZ

- **Original source** (`src/rate_sources/__init__.py`, `RbnzSource`): B2 daily
  XLSX, two hardcoded URLs under `rbnz.govt.nz/-/media/...`. `HTTP 403` with a
  full browser UA + `Referer` header already set.
- **Re-verification, attempt 1** (prereg §12.2): retried with the exact
  UA/IPv4-pinned config that had previously fixed FRED access. Same
  `HTTP 403`, "Website unavailable" — ruling out a UA/config-specific block.
- **Re-verification, attempt 2** (prereg §16.1), three independent new paths:
  - Current RBNZ statistics page
    (`rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates`)
    — `HTTP 403`, same as the general `/statistics` page. Domain/edge-level
    block, not a stale URL.
  - `data.govt.nz` CKAN mirror (HTML page, resource page, and the
    `/api/3/action/resource_show` / `package_show` JSON endpoints) — all
    return `HTTP 200`, but the body is an **Imperva "Pardon Our Interruption"**
    JS challenge page, not data, regardless of `Accept` header or path.
  - `nzfbf.co.nz` (New Zealand Financial Benchmark Facility, the new
    administrator) — reachable, has `/benchmarks/closing-rates/nzgs`, but only
    a methodology PDF; no visible data table or download link on the pages
    checked.
  - **New information from this pass**: the old B2 daily XLSX file was
    **discontinued 2025-08-25** — RBNZ moved to NZFMA end-of-day closing rates,
    published with a 1-day lag. A 2y government-bond benchmark exists
    conceptually under the new methodology, but no working programmatic
    access to it was found.
- **Fallback**: Stooq, bot-walled (prereg §11).

**Verdict**: genuinely blocked — 3 domains, 5 distinct URLs, 2 tools, the
block reconfirmed twice with different configurations. Also methodologically
moot even if the edge block lifted: the file the code points at no longer
gets updated.

### CHF — SNB

- **Source** (`SnbSource`): SNB data portal, cube `rendoblid`, dimension
  `D0=2J` (CHF Confederation bonds, 2-year), `data.snb.ch/api/cube/rendoblid/data/csv/en`.
- Original read (prereg §11/§12.3): 362 days stale, but the endpoint
  *responded* — read as "stale, recoverable" (state 2), the opposite of NZD.
- **Re-verification** (prereg §16.2): re-fetched the **entire** cube file
  (5.46 MB, 146,828 rows; not just the extracted "2J" series) and inspected
  its own header metadata:
  ```
  "CubeId";"rendoblid"
  "PublishingDate";"2025-09-01 14:29"
  ```
  **All 22 dimensions in the cube** (1J…30J, 10J1, E, K, P, GK, IKH, AAA, AA,
  A — not just 2J) **stop at exactly the same date: 2025-07-31.** Confirmed
  via `/api/cube/rendoblid/dimensions/en` that `D0=2J` is genuinely "2 years"
  under the correct (government) category — not a maturity/cube
  mis-selection on our side. No successor cube found on the portal in the
  time available (`rendoblim`, the monthly variant, exists but is
  lower-frequency, not a fresher replacement).

**Verdict**: this is not a query mistake and not an ordinary lag — the
source's own metadata confirms the entire cube was frozen at publication, not
just the series we happened to read. The "stale, recoverable" characterization
from the original read no longer holds; CHF is now the same operational
category as NZD.

## Why no more automatic retries

Both sources are structurally dead, not transiently unavailable:

- **NZD**: network-level block reconfirmed twice under different
  configurations, across three separate domains, plus a confirmed upstream
  methodology change that makes the currently-coded file obsolete regardless
  of the block.
- **CHF**: the source's own file metadata states the cube was last published
  2025-09-01 with data through 2025-07-31 — this is SNB's own record of
  having stopped, not an artifact of how or when we ask.

Retrying either on a schedule would burn requests against a dead endpoint for
no operational benefit — the failure mode has already been characterized.

## Why no new flag gets built

The three-state flag's job was to make "worth retrying" vs "not worth
retrying" visible per-category. Since D1=D was adopted in §M (not the
originally-planned D1=E — see `prereg-M...` §17/§18), the missing-category
problem is now handled structurally: a category absent from one leg is
excluded from **both legs'** comparison via set intersection, and the
resulting per-pair exclusion is already shown in the UI ("excluded here",
shipped in PR #4) — as a property of the specific comparison, not as a
retry-priority signal. Building a separate three-state flag on top of that
would duplicate information already on screen, for a decision (retry
priority) that no longer applies to either currency.

## Reopen condition

§O reopens only if a **new source** appears that publishes NZD 2y and/or CHF
2y yields in a programmatically accessible way (a working NZFMA/NZFBF data
endpoint, a resumed/successor SNB cube, or an equivalent). Restoring the old
RBNZ/SNB endpoints to their previous behavior would also qualify, but neither
is expected to occur — the RBNZ file's discontinuation and the SNB cube's
frozen-metadata cutover both look like deliberate changes on the source side,
not incidents.

## References

- `docs/prereg-M-currency-comparability-2026-07-28.md` (branch
  `diag/scoring-audit`) §11 (original NZD/CHF read), §12.2 (RBNZ retry),
  §12.3 (SNB CHF read), §16 (this closure's re-verification), §15.2 (the
  drafted, never-implemented three-state flag spec).
- `src/rate_sources/__init__.py` — `RbnzSource`, `SnbSource` (fetch code,
  unmodified by this closure).
- PR #4 (`feature/m-d1-d-d2c`) — D1=D + the "excluded here" per-pair marker
  that makes a separate staleness flag redundant for this purpose.
