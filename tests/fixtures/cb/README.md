# Central Banks market fixtures

Small cuts of the REAL files each adapter parses, captured 2026-09-18/19. Values are verbatim; only rows/columns
were dropped (and, for the workbooks, re-saved with openpyxl). Used by `tests/test_cb_market_sources.py` and
`tests/test_cb_collect.py`.

| file | source | what was kept |
|---|---|---|
| `mpt_data_cut.xlsx` | Atlanta Fed `mpt_histdata.xlsx`, sheet `DATA` | as-of 2026-08-28/09-16/09-17, reference starts 2026-12-16 / 2027-03-17 / 2029-09-19 / 2029-12-19, fields `Rate: mean` / `Rate: mode` / `Prob: cut`. Dates and values are strings as published (`' 431.00'`). The sheet's `<dimension>` is rewritten to the publisher's stale `A1` so the read-only reader is exercised on it |
| `boe_latest_cut.zip`, `boe_hist_cut.zip` | BoE `latest-yield-curve-data.zip`, `oisddata.zip` | sheet `1. fwds, short end`: header rows, first 40 monthly columns, 2 days (current month) / 1 day (history), plus a decoy workbook in each zip. Current-month months are `1.0000000400000015`-style floats, history months are exact ints |
| `jpx_rb_e20260918_cut.csv` (cp932), `jpx_page_cut.html` | JPX settlement-price CSV and the index page anchor | the 14 `FUT_TOA3M_*` rows, 3 Nikkei 225 rows and the header lines |
| `mx_expectations_cut.html` | m-x.ca Canadian Interest Rate Expectations | 5 CRA and 3 COA `<tr>` blocks verbatim, plus the repeated CRAU26 row the page carries in a second table |
| `asx_ib_cut.json`, `asx_bb_cut.json` | ASX markit JSON API | first 5 futures items |
| `ust_par_curve_cut.csv` | Treasury daily par yield curve 2026 | header + 3 latest days |
| `boc_tbills_cut.json` | BoC Valet `TB.CDN.{30,60,90,180}D.MID` | 3 observations, `seriesDetail` kept |
| `ecb_if_cut.csv` | ECB `YC` dataflow, `IF_3M+IF_1Y+IF_2Y3M+IF_3Y+IF_4Y+SR_3M` | 2 days; `IF_4Y` (beyond 36M) and `SR_3M` (not an IF series) exercise the filters |
| `rba_f1_cut.csv` | RBA table F1 | metadata rows + the last 4 data rows; the real `18-Sep-2026` row is short (only the total-return index) |

## Phase 1B-1 (official series, decisions, SEP, calendars)

| file | source | what was kept |
|---|---|---|
| `fred_dfedtaru_cut.csv`, `fred_sofr_cut.csv` | FRED `fredgraph.csv` | 14-19 Sep 2026 (target upper limit); 3-10 Sep 2026 SOFR with the empty Labor Day cell |
| `ecb_dfr_cut.csv`, `ecb_dfr_2024_cut.csv`, `ecb_mro_2024_cut.csv` | ECB FM `DFR` / `MRR_FR` | 14-18 Sep 2026; 10-20 Sep 2024 (the spread MRO - DFR moves from 0.50 to 0.15 on 18 Sep 2024) |
| `boe_iadb_cut.csv` | BoE IADB `IUDBEDR,IUDSOIA` | 10-17 Sep 2026 |
| `boj_call_cut.json` | BoJ API `FM01 STRDCLUCON` | last 6 survey dates (weekend nulls) |
| `valet_policy_cut.json` | BoC Valet `V39079,AVG.INTWO` | 14-17 Sep 2026 |
| `bis_cbpol_cut.csv` | BIS `WS_CBPOL` JP + NZ | JP around the 17 Jun 2026 hike, NZ around 2 Sep (BIS books the OCR change on the 3rd) |
| `snb_cube_cut.csv` | SNB cube `snbgwdzid` | `LZ` and `SARON`, 17-19 Jun and 10-11 Sep 2026 |
| `fed_sep_20260916_cut.htm`, `..._20251210_...`, `..._20260318_...` | FOMC `fomcprojtabl*.htm` | Table 1 and the dot-plot table, HTML verbatim; the three pages have different horizons |
| `fomccalendars_sep_links_cut.htm` | FOMC calendar page | the SEP anchors |
| `cal_<bank>_cut.htm` | official meeting-calendar pages (Fed, ECB, BoE, BoJ, BoC, RBA, SNB schedule + archive) | the 2026-2027 blocks; text pages are the page text of the real block wrapped in `<p>`, BoJ is the two MPM tables verbatim |
| `decisions_official_cut.parquet` | data/cb/official_series | the policy / BIS series within +-4 days of the effective date of each of the 32 oracle decisions |
| `decisions_ff_cut.parquet` | data/economic_calendar_ff.parquet | the FF decision rows (matched on currency + name) around those meetings, the BoJ placeholders included, plus the real ECB row of 12 Sep 2024 |
