# Spike report — JBlanked News API (forex-factory source) as calendar replacement

**Branch:** `spike/jblanked-source` (dev checkout; nothing on main, no pipeline integration).
**Dates:** 2026-07-03 (Task 1) + 2026-07-05 (single range request + full analysis).
**Goal:** can JBlanked's forex-factory feed replace MT5/MQL5 as the source of surprise data
(actual/consensus/previous) for USD/EUR/GBP/JPY/AUD/NZD/CAD/CHF — the migration motivated by
MT5 corruption (EUR CPI reported 3.2% m/m vs ~0% real; bad NFP)?
**Scripts:** `scripts/spike_jblanked/` (`./.venv/bin/python`; key in gitignored `.env`).
**Cached evidence:** `scripts/spike_jblanked/cache/` — one 4.4 MB range payload, **16,204 events, 2023-01 → 2026-07-05**.

> ⚠️ Checkout note: run in the **dev** checkout, NOT `~/projects/macro-data-analysis`. The hourly
> `econ-refresh` cron lives in prod and does `git pull --rebase`/`commit`/`push` on the checked-out
> branch — switching prod to a spike branch would divert production refresh commits off `main`.

---

## Verdict per kill criterion

| # | Criterion | Result |
|---|---|---|
| **1** | Rate limit / cost | **CONSTRAINED.** Free ≈ **1 request/day** (unreliable reset), then 401-credits. But **one call returns all 8 currencies × 3.5y** — so a *daily* pull is free-viable; intraday needs credits/VIP. |
| **2** | Indicator coverage | **PASS — 80/81** cells. All 3 MT5 gaps (CHF GDP, US ISM Mfg, US ISM Services) **filled**. Only JPY services PMI absent (also absent in MT5). |
| **3** | Correctness (reason to migrate) | **PASS.** FF cleanly separates EUR CPI **m/m ≈ 0** (real) from **y/y ≈ 3.2** → the MT5 "3.2% as m/m" corruption is **refuted**. Internally coherent; no gross corruption. |
| **4** | History depth | **PASS.** 34–44 monthly / 13–14 quarterly prints with full triple, back to 2023-01. |

**Overall: GO on data quality (the migration solves the corruption). CONDITIONAL on cost for
ongoing refresh — see Production viability.**

---

## KILL #1 — rate limit / cost (measured over two days)

- **2026-07-03:** `calendar/week/` req#1 → **200** (30 KB); req#2/#3 + `/list/` → **401** *"requires credits,
  you currently do not have any"*. → 1 free use/day, then billing wall. No `X-RateLimit-*` headers.
- **2026-07-05:** `calendar/range/?from=2023-01-01&to=2026-07-05` → **200**, **4.4 MB, 16,204 events, 8.8 s**
  (persisted raw *before* parse → `cache/range_raw_*.bin`; seeded to week/range/range_hist caches).
  So a **second free call succeeded ~2 days later** — the allowance reset despite the dashboard showing
  "Free Uses Left Today: 0 / Next Use: None". Empirically: **~1 successful free call per day, reset timing
  unreliable.** CDN = Cloudflare; `Transfer-Encoding: chunked`, `gzip`.

**Cost of paid path:** 1 credit per calendar call (5 per GPT POST); **VIP membership = unlimited**. Exact $
price is **behind login** (`/api/billing/`) — user must read it. Community/docs corroborate the model, not a
dollar figure.

**Key economic fact:** the range/week endpoints return **every currency's events in one payload**. Throughput
is never the constraint — quota is. So:
- **One-time backfill:** a single range call (already cached) delivers 3.5 y of history for all 8 currencies.
- **Daily refresh:** 1 call/day covers the whole day's releases → fits the free tier (if reset is reliable) or 1 credit/day.
- **Intraday refresh:** ~24 calls/day → credits or VIP.

---

## KILL #2 — coverage (`task2_coverage.py`, cache-only) — PASS 80/81

Coverage grid (prints per indicator×currency over 2023→2026-07; `·` = N/A in our map):

```
indicator           USD  EUR  GBP  JPY  AUD  NZD  CAD  CHF
cpi_yoy             41   86   43   44   45   15   43   45
core_cpi            38   44   42   ·    ·    ·    53   ·
ppi_yoy             40   41   38   41   15   15   42   44
core_pce            43   ·    ·    ·    ·    ·    ·    ·
gdp_qoq             43   39   42   4    13   13   41   16
manufacturing_pmi   43   14   150  7    7    42   82   7
services_pmi        41   14   117  MISS 7    42   ·    ·
retail_sales        42   42   44   41   33   13   42   49
employment_change   45   14   44   ·    43   14   42   ·
unemployment_rate   43   44   43   43   42   14   42   44
wage_growth         45   ·    44   43   15   14   ·    ·
adp/jolts/claims    45/45/183 (USD-only) ...
interest_rate       29   95   27   28   30   25   29   9
```

- **All 3 known MT5 gaps FILLED:** CHF `GDP q/q` (16), US `ISM Manufacturing PMI` (43), US `ISM Services PMI` (41).
- **Only miss:** JPY services PMI — genuinely absent in the FF feed (no au Jibun Services / JPY Services PMI).
  This was *also* an MT5 gap, so it's a shared feed limitation, not a regression.
- **⚠️ Event names DIFFER from MT5** — migration requires a **new matcher** (e.g. MT5 `CPI y/y` → FF
  `CPI Flash Estimate y/y` / `Final CPI y/y`; MT5 `Nonfarm Payrolls` → FF `Non-Farm Employment Change`).
- **⚠️ EUR triple-count risk CONFIRMED (structural):** 61 member-state events (German/French/Italian/Spanish…)
  carry `Currency=EUR`. The matcher must accept **aggregate** names only (`Flash/Final … y/y`, no country prefix)
  and reject member-state prints. Our Task-2 map already does this.
- **⚠️ FF carries many revision variants** per series (Flash→Prelim→Final→Revised, Advance→Prelim→Final) — the
  existing `_dedup_flash_final` must be re-tuned to FF's naming.

---

## KILL #3 — correctness (`task3_correctness.py`, cache-only) — PASS

**(A) EUR CPI corruption refutation — the decisive test.** Jun-2026:

```
m/m  German Prelim CPI  = -0.3     m/m  French Prelim CPI = -0.2     m/m  Italian Prelim CPI = 0.0
y/y  CPI Flash Estimate = 2.8 (new month);  Final CPI y/y (prior month) = 3.2
```

FF reports **m/m ≈ 0 / slightly negative** (the real figure) and **y/y ≈ 3.2** as a *separate* series. The MT5
feed reported **3.2% as m/m** — a **y/y-mislabelled-as-m/m corruption**. FF does not have this defect. ✅ The
central hypothesis holds: **forex-factory data is correct where MQL5 was corrupt.** (Official anchor: Eurostat
HICP flash, euro-area m/m ≈ 0 mid-2026, y/y ≈ 3.2 — https://ec.europa.eu/eurostat.)

**(B) Internal consistency `previous(t)==actual(t-1)`** — on clean, non-revised series: **90–95%**
(USD CPI y/y 38/40, ISM Mfg 40/42, ISM Svc 37/40, GBP CPI 39/42, unemployment rates ~90-95%, CHF CPI 40/44).
Revision-prone series (NFP 2/44, Retail 8/41, Claims 38/182) are low **by design**: FF's `Previous` carries the
**revised** prior value (standard Forex Factory behaviour) — median NFP revision **28 K**, i.e. real revisions,
not corruption. **Not a data-quality problem** (and irrelevant to us: our surprise uses `Forecast`, not `Previous`).

**(C) Plausibility bounds** — **zero** gross-corruption values in released data. The only out-of-range entries
(23) are `actual=0.0` on **unreleased Flash-PMI** rows (forecast/previous sane).
**⚠️ Critical ingest finding:** FF encodes *unreleased* as **`actual=0.0`**, which collides with a genuine
`0.0` reading (e.g. Italian CPI m/m = 0.0). An ingest **must decide released/unreleased by the event `Date`
(≤ now), never by the value.**

---

## KILL #4 — history depth (`task4_history.py`, cache-only) — PASS

```
indicator                       freq       released  oldest      full-triple  verdict
USD Non-Farm Employment Change  monthly    44        2023-01-05  44           PASS (>=24)
EUR CPI Flash Estimate y/y      monthly    43        2023-01-05  43           PASS
GBP GDP m/m                     monthly    37        2023-01-12  37           PASS
AUD Employment Change           monthly    41        2023-01-18  41           PASS
CHF CPI m/m                     monthly    34        2023-01-04  34           PASS
USD Advance GDP q/q             quarterly  13        2023-01-25  13           PASS (>=8)
CHF GDP q/q                     quarterly  14        2023-05-30  14           PASS
```

One range call yields ≥3 y of full-triple history for every series — enough to rebuild z-score baselines.
**Method note:** the spec's `/{ID}/history/` endpoint does **not** exist and there is no stable `Event_ID`
(often 0); history comes from the **range** endpoint, which returned the entire 2023→now window uncapped in one call.

---

## Observed JSON schema

Array of event objects (12 fields):

```json
{ "Name":"…", "Currency":"USD", "Event_ID":0,      // Event_ID often 0 — NOT a reliable join key
  "Category":"…", "Impact":"None|Low|Medium|High",
  "Date":"2026.07.02 15:30:00",                     // 'YYYY.MM.DD HH:MM:SS', tz below
  "Actual":57.0, "Forecast":50.0, "Previous":129.0, // the surprise triple (0.0 == unreleased!)
  "Outcome":"…", "Strength":"…", "Quality":"…" }    // FF-derived extras (unused)
```

Mapping: `Actual`→actual, **`Forecast`→consensus**, `Previous`→previous, `Name`+`Currency`→matcher, `Impact`→importance.

## Timezone convention (measured)

- No tz field. `Date` is a naive local timestamp. Fixed-ET releases show a **constant wall clock year-round**:
  NFP (08:30 ET) → **15:30**, ISM (10:00 ET) → 17:00, US CPI (08:30 ET) → 15:30 — in **both winter and summer**.
- ⇒ the feed clock = **US-Eastern + 7 h, DST-following** → **UTC+2 (EST winter) / UTC+3 (EDT summer)**.
- **Normalize at ingest:** `et_wall = ff_naive − 7h; utc = America/New_York.localize(et_wall).astimezone(UTC)`
  (do NOT assume a fixed offset). Residual to confirm: which DST *dates* it follows (US vs EU) — matters only in
  the 2–3 week March/Nov gaps; verify against a release in those windows.

---

## Production viability — recommended hybrid architecture

The corruption problem is **solved** by forex-factory data. Proposed split (no code written — spike only):

1. **Economic calendar → JBlanked forex-factory** (replaces MT5 calendar):
   - **One-time backfill:** the cached range payload (2023→now, all 8 ccy) → rebuild z-score baselines.
   - **Ongoing refresh:** one `calendar/week/` (or `range` last-N-days) call **per day** — covers all currencies.
     Free tier (~1/day) *may* suffice for a daily calendar; for same-day intraday surprises or reliability, use
     **credits (1/call) or VIP-unlimited** (confirm price at `/api/billing/`).
2. **OHLC / trend → MT5** (unchanged — MT5 was never wrong on price).
3. **Rates / liquidity → FRED** (unchanged).
4. **FRED as independent US cross-check** (NFP/CPI/GDP) — a cheap correctness guard on the new source, mirroring
   the existing guarded-fallback pattern.

**Integration cost flags (for the follow-up, not this spike):** new FF matcher (names differ), FF flash/final
revision dedup, `actual=0.0`=unreleased handling keyed on `Date`, EUR aggregate-only filtering, and the ET+7
→ UTC normalization.

---

## GO / NO-GO

**GO — migrate the economic calendar to JBlanked forex-factory**, conditional on funding ongoing refresh:
- ✅ **Correctness (the whole reason): validated** — FF refutes the MT5 EUR-CPI corruption and shows no gross errors.
- ✅ **Coverage 80/81**, all MT5 gaps filled; ✅ **history deep enough** to rebuild z-scores; ✅ backfill already cached.
- ⚠️ **Cost:** free = ~1 call/day (viable for a daily calendar pull; unreliable reset). For intraday/robust,
  buy credits or VIP — **check `/api/billing/` for the price before committing.**
- **NO-GO only if** the VIP/credit price is unacceptable *and* a once-daily free pull proves too coarse for
  same-day surprise capture.

**Constraints honoured:** forex-factory source only (mql5 never used; fxstreet reserved but not needed — a live
official cross-check via fxstreet/BLS/ONS was **deferred** to preserve request budget). Nothing on main; no
pipeline code; ≤2 requests total (1 valuable range call; retry not needed). Not committed — awaiting review.
