## TASK 1 — FF historical parquet

- file: `data/economic_calendar_ff.parquet`  ·  **3053 rows**, **82 series**, 2023-01-02 → 2026-07-03
- currencies: ['AUD', 'CAD', 'CHF', 'EUR', 'GBP', 'JPY', 'NZD', 'USD']

Continuity — monthly-ish series with a gap > 60 days (sanity):

| series | #gaps>60d | max gap (d) |
|---|---|---|
| gbp_ppi_output | 2 | 244 |
| usd_ppi | 1 | 132 |
| aud_rba_interest_rate_decision | 2 | 91 |
| cad_boc_interest_rate_decision | 1 | 91 |
| eur_ecb_interest_rate_decision | 1 | 91 |
| jpy_retail_sales | 1 | 91 |
| usd_fed_interest_rate_decision | 1 | 91 |
| usd_core_cpi | 3 | 81 |
| nzd_businessnz_services_index | 1 | 71 |
| aud_retail_sales | 1 | 70 |
| nzd_businessnz_manufacturing_index | 1 | 70 |
| usd_core_pce_price_index | 1 | 70 |
| usd_retail_sales | 2 | 70 |
| usd_jolts_job_openings | 2 | 69 |
| chf_cpi | 1 | 65 |
| jpy_ppi | 1 | 64 |
| usd_adp_nonfarm_employment_change | 1 | 64 |
| aud_unemployment_rate | 1 | 63 |
| cad_employment_change | 1 | 63 |
| cad_unemployment_rate | 1 | 63 |
| gbp_average_weekly_earnings_total_pay | 1 | 63 |
| gbp_claimant_count_change | 1 | 63 |
| gbp_core_cpi | 1 | 63 |
| gbp_gdp | 1 | 63 |
| gbp_unemployment_rate | 1 | 63 |
| jpy_labor_cash_earnings | 1 | 63 |
| usd_average_hourly_earnings | 2 | 63 |
| usd_ism_manufacturing_pmi | 1 | 63 |
| usd_ism_non_manufacturing_pmi | 2 | 63 |
| usd_nonfarm_payrolls | 2 | 63 |
| usd_unemployment_rate | 2 | 63 |
| cad_gdp | 1 | 62 |
| aud_employment_change | 1 | 61 |
| cad_ippi | 1 | 61 |
| cad_s_p_global_manufacturing_pmi | 1 | 61 |
| chf_ppi | 2 | 61 |
| chf_unemployment_rate | 1 | 61 |
| eur_retail_sales | 1 | 61 |
| gbp_cpi | 1 | 61 |
| gbp_retail_sales | 1 | 61 |