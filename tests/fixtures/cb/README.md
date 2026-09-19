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
