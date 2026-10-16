# Turneu de scoring — auditul datelor (Pasul 1)

Instantaneu: 2026-10-16 15:02:20 UTC, branch `eval/scoring-tournament`. Generat de `scripts/measure/tournament/audit.py`. Fără scoruri și fără metrici pe randamente.

Gol = interval fără observații mai lung decât: 7 zile calendaristice pentru seriile zilnice, 8 zile pentru COT, 2× cadența din config pentru calendarul FF și 100 de zile pentru deciziile de politică.

## 1. Calendarul ForexFactory (cadrul de scoring)

Cadrul de scoring are 3639 rânduri. Este construit ca în producție: `_load_calendar_frame`, `_with_policy_rate`, `scoring_view`. Are 0 rânduri fără indicator_key. Tabelul numără doar publicările cu actual.

| Valută | Indicator | Categ. | Frecv. | Prima | Ultima | N | N cu consens | Goluri |
|---|---|---|---|---|---|---:|---:|---|
| AUD | capital_expenditure | growth | quarterly | 2023-02-23 | 2026-08-27 | 15 | 15 | — |
| AUD | company_operating_profits_qoq | growth | quarterly | 2023-02-27 | 2026-08-31 | 15 | 15 | — |
| AUD | gdp_qoq | growth | quarterly | 2023-03-01 | 2026-09-02 | 15 | 15 | — |
| AUD | manufacturing_pmi | growth | monthly | 2026-01-22 | 2026-09-22 | 9 | 0 | — |
| AUD | retail_sales | growth | monthly | 2023-01-11 | 2026-09-29 | 46 | 46 | — |
| AUD | services_pmi | growth | monthly | 2026-01-22 | 2026-09-22 | 9 | 0 | — |
| AUD | core_cpi | inflation | monthly | 2023-01-25 | 2025-10-29 | 12 | 12 | 2023-01-25→2023-04-26 (91z); 2023-04-26→2023-07-26 (91z); 2023-07-26→2023-10-25 (91z); +8 |
| AUD | cpi_monthly | inflation | monthly | 2026-01-07 | 2026-09-30 | 10 | 10 | — |
| AUD | cpi_yoy | inflation | monthly | 2023-01-11 | 2026-09-30 | 46 | 46 | — |
| AUD | import_prices | inflation | quarterly | 2023-01-27 | 2026-07-30 | 15 | 15 | — |
| AUD | ppi_yoy | inflation | quarterly | 2023-01-27 | 2026-07-31 | 15 | 15 | — |
| AUD | trimmed_mean_cpi_monthly | inflation | monthly | 2026-01-07 | 2026-09-30 | 10 | 10 | — |
| AUD | employment_change | labour | monthly | 2023-01-19 | 2026-10-15 | 46 | 46 | — |
| AUD | unemployment_rate | labour | monthly | 2023-01-19 | 2026-10-15 | 46 | 46 | — |
| AUD | wage_growth | labour | quarterly | 2023-02-22 | 2026-08-19 | 16 | 16 | — |
| AUD | interest_rate_decision | rates | monthly | 2026-03-17 | 2026-09-29 | 5 | 5 | — |
| CAD | gdp_qoq | growth | monthly | 2023-01-31 | 2026-09-29 | 45 | 45 | — |
| CAD | manufacturing_pmi | growth | monthly | 2023-01-03 | 2026-10-01 | 46 | 2 | — |
| CAD | retail_sales | growth | monthly | 2023-01-20 | 2026-09-24 | 45 | 45 | — |
| CAD | common_cpi_yoy | inflation | monthly | 2023-01-17 | 2026-09-14 | 45 | 45 | — |
| CAD | core_cpi | inflation | monthly | 2023-01-17 | 2026-09-14 | 46 | 46 | — |
| CAD | cpi_yoy | inflation | monthly | 2023-01-17 | 2026-09-14 | 45 | 45 | — |
| CAD | ppi_yoy | inflation | monthly | 2023-01-18 | 2026-09-17 | 45 | 45 | — |
| CAD | trimmed_cpi_yoy | inflation | monthly | 2023-01-17 | 2026-09-14 | 45 | 45 | — |
| CAD | employment_change | labour | monthly | 2023-01-06 | 2026-10-09 | 46 | 46 | — |
| CAD | unemployment_rate | labour | monthly | 2023-01-06 | 2026-10-09 | 46 | 46 | — |
| CAD | interest_rate_decision | rates | monthly | 2026-04-29 | 2026-09-02 | 4 | 4 | — |
| CHF | gdp_qoq | growth | quarterly | 2023-02-28 | 2026-09-03 | 17 | 17 | — |
| CHF | manufacturing_pmi | growth | monthly | 2023-02-01 | 2026-10-01 | 45 | 45 | — |
| CHF | retail_sales | growth | monthly | 2023-01-06 | 2026-10-01 | 46 | 46 | — |
| CHF | cpi_yoy | inflation | monthly | 2023-01-04 | 2026-10-01 | 46 | 46 | — |
| CHF | ppi_yoy | inflation | monthly | 2023-01-19 | 2026-10-15 | 46 | 46 | — |
| CHF | unemployment_rate | labour | monthly | 2023-01-09 | 2026-10-06 | 46 | 46 | — |
| CHF | interest_rate_decision | rates | monthly | 2025-09-25 | 2026-09-24 | 5 | 0 | 2025-09-25→2025-12-11 (77z); 2025-12-11→2026-03-19 (98z); 2026-03-19→2026-06-18 (91z); +1 |
| EUR | gdp_qoq | growth | quarterly | 2023-01-31 | 2026-07-30 | 15 | 15 | — |
| EUR | manufacturing_pmi | growth | monthly | 2026-01-23 | 2026-09-23 | 9 | 9 | — |
| EUR | retail_sales | growth | monthly | 2023-01-06 | 2026-10-06 | 46 | 46 | — |
| EUR | services_pmi | growth | monthly | 2026-01-23 | 2026-09-23 | 9 | 9 | — |
| EUR | core_cpi | inflation | monthly | 2023-01-06 | 2026-10-02 | 46 | 46 | — |
| EUR | cpi_yoy | inflation | monthly | 2023-01-06 | 2026-10-02 | 46 | 46 | — |
| EUR | ppi_yoy | inflation | monthly | 2023-01-05 | 2026-10-05 | 46 | 46 | — |
| EUR | employment_change | labour | quarterly | 2023-02-14 | 2026-08-14 | 15 | 15 | — |
| EUR | unemployment_rate | labour | monthly | 2023-01-09 | 2026-10-01 | 47 | 47 | — |
| EUR | interest_rate_decision | rates | monthly | 2026-04-30 | 2026-09-10 | 4 | 4 | — |
| GBP | gdp_qoq | growth | monthly | 2023-01-13 | 2026-10-15 | 46 | 46 | — |
| GBP | industrial_production_mm | growth | monthly | 2023-01-13 | 2026-10-15 | 47 | 47 | — |
| GBP | manufacturing_pmi | growth | monthly | 2023-01-03 | 2026-10-01 | 46 | 46 | — |
| GBP | retail_sales | growth | monthly | 2023-01-20 | 2026-09-18 | 46 | 46 | — |
| GBP | services_pmi | growth | monthly | 2023-01-05 | 2026-10-05 | 46 | 46 | — |
| GBP | core_cpi | inflation | monthly | 2023-01-18 | 2026-09-16 | 45 | 45 | — |
| GBP | cpi_yoy | inflation | monthly | 2023-01-18 | 2026-09-16 | 45 | 45 | — |
| GBP | ppi_yoy | inflation | monthly | 2023-01-24 | 2026-09-16 | 40 | 38 | 2025-02-19→2025-10-21 (244z) |
| GBP | employment_change | labour | monthly | 2023-01-17 | 2026-09-15 | 46 | 46 | — |
| GBP | unemployment_rate | labour | monthly | 2023-01-17 | 2026-09-15 | 46 | 46 | — |
| GBP | wage_growth | labour | monthly | 2023-01-17 | 2026-09-15 | 46 | 46 | — |
| GBP | interest_rate_decision | rates | monthly | 2026-04-30 | 2026-09-17 | 4 | 4 | — |
| JPY | capital_expenditure | growth | quarterly | 2023-03-01 | 2026-08-31 | 15 | 15 | — |
| JPY | core_machinery_orders_mm | growth | monthly | 2023-01-17 | 2026-10-14 | 46 | 46 | — |
| JPY | gdp_qoq | growth | quarterly | 2023-02-13 | 2026-08-16 | 15 | 15 | — |
| JPY | industrial_production_mm | growth | monthly | 2023-01-30 | 2026-09-29 | 44 | 44 | — |
| JPY | manufacturing_pmi | growth | monthly | 2025-12-16 | 2026-09-24 | 10 | 10 | — |
| JPY | retail_sales | growth | monthly | 2023-01-30 | 2026-09-29 | 45 | 45 | — |
| JPY | core_cpi | inflation | monthly | 2023-01-24 | 2026-09-25 | 44 | 44 | 2024-11-26→2025-01-28 (63z) |
| JPY | cpi_yoy | inflation | monthly | 2023-01-19 | 2026-09-17 | 45 | 45 | — |
| JPY | ppi_yoy | inflation | monthly | 2023-01-15 | 2026-10-12 | 46 | 46 | — |
| JPY | sppi_yoy | inflation | monthly | 2023-01-25 | 2026-09-27 | 46 | 46 | — |
| JPY | tokyo_core_cpi_yoy | inflation | monthly | 2023-01-09 | 2026-10-01 | 46 | 46 | — |
| JPY | gdp_price_index | inflation_display | quarterly | 2023-02-13 | 2026-08-16 | 15 | 15 | — |
| JPY | unemployment_rate | labour | monthly | 2023-01-30 | 2026-10-01 | 45 | 45 | — |
| JPY | wage_growth | labour | monthly | 2023-01-05 | 2026-10-06 | 46 | 46 | — |
| JPY | interest_rate_decision | rates | monthly | 2026-04-28 | 2026-09-18 | 4 | 4 | — |
| NZD | gdp_qoq | growth | quarterly | 2023-03-15 | 2026-09-16 | 15 | 15 | — |
| NZD | manufacturing_pmi | growth | monthly | 2023-01-19 | 2026-09-10 | 44 | 0 | 2023-11-09→2024-01-18 (70z) |
| NZD | retail_sales | growth | quarterly | 2023-02-26 | 2026-08-23 | 15 | 15 | — |
| NZD | services_pmi | growth | monthly | 2023-01-23 | 2026-09-13 | 45 | 0 | — |
| NZD | cpi_yoy | inflation | quarterly | 2023-01-24 | 2026-07-20 | 15 | 15 | — |
| NZD | ppi_yoy | inflation | quarterly | 2023-02-20 | 2026-08-18 | 16 | 16 | — |
| NZD | employment_change | labour | quarterly | 2023-01-31 | 2026-08-04 | 15 | 15 | — |
| NZD | unemployment_rate | labour | quarterly | 2023-01-31 | 2026-08-04 | 15 | 15 | — |
| NZD | wage_growth | labour | quarterly | 2023-01-31 | 2026-08-04 | 15 | 15 | — |
| NZD | interest_rate_decision | rates | monthly | 2026-04-08 | 2026-09-02 | 4 | 4 | — |
| USD | durable_goods_orders_mm | growth | monthly | 2023-01-26 | 2026-09-25 | 46 | 46 | — |
| USD | gdp_qoq | growth | quarterly | 2023-01-26 | 2026-07-30 | 15 | 15 | — |
| USD | industrial_production_mm | growth | monthly | 2023-01-18 | 2026-09-18 | 46 | 45 | 2025-09-16→2025-12-03 (78z) |
| USD | manufacturing_pmi | growth | monthly | 2023-01-04 | 2026-10-01 | 46 | 46 | — |
| USD | personal_income_mm | growth | monthly | 2023-01-27 | 2026-09-30 | 45 | 45 | 2025-09-26→2025-12-05 (70z) |
| USD | personal_spending_mm | growth | monthly | 2023-01-27 | 2026-09-30 | 45 | 45 | 2025-09-26→2025-12-05 (70z) |
| USD | retail_sales | growth | monthly | 2023-01-18 | 2026-10-15 | 46 | 46 | 2025-09-16→2025-11-25 (70z) |
| USD | services_pmi | growth | monthly | 2023-01-06 | 2026-10-05 | 46 | 46 | — |
| USD | core_cpi | inflation | monthly | 2023-01-12 | 2026-10-14 | 44 | 44 | 2025-10-24→2026-01-13 (81z) |
| USD | core_pce | inflation | monthly | 2023-01-27 | 2026-09-30 | 44 | 44 | 2025-09-26→2025-12-05 (70z) |
| USD | cpi_yoy | inflation | monthly | 2023-01-12 | 2026-10-14 | 45 | 45 | — |
| USD | import_prices | inflation | monthly | 2023-01-13 | 2026-09-16 | 44 | 44 | 2025-09-16→2025-12-03 (78z) |
| USD | ppi_yoy | inflation | monthly | 2023-01-18 | 2026-10-15 | 47 | 47 | 2025-09-10→2025-11-25 (76z) |
| USD | gdp_price_index | inflation_display | quarterly | 2023-01-26 | 2026-07-30 | 15 | 15 | 2025-07-30→2026-02-20 (205z) |
| USD | adp | labour | monthly | 2023-01-05 | 2026-09-30 | 46 | 46 | — |
| USD | employment_change | labour | monthly | 2023-01-06 | 2026-10-02 | 47 | 46 | 2025-09-05→2025-11-20 (76z) |
| USD | jobless_claims | labour | weekly | 2023-01-05 | 2026-10-15 | 194 | 192 | 2025-09-25→2025-11-18 (54z) |
| USD | jolts | labour | monthly | 2023-01-04 | 2026-09-29 | 46 | 45 | 2025-09-30→2025-12-08 (69z) |
| USD | unemployment_rate | labour | monthly | 2023-01-06 | 2026-10-02 | 46 | 46 | 2025-09-05→2025-11-20 (76z) |
| USD | unit_labor_costs_qoq | labour | quarterly | 2023-02-02 | 2026-08-06 | 15 | 15 | — |
| USD | wage_growth | labour | monthly | 2023-01-06 | 2026-10-02 | 47 | 46 | 2025-09-05→2025-11-20 (76z) |
| USD | interest_rate_decision | rates | monthly | 2026-04-29 | 2026-09-16 | 4 | 4 | — |

## 2. Randamente 2Y și reale

| Serie | Prima | Ultima | N | Sursă | Goluri (din 2014) |
|---|---|---|---:|---|---|
| AUD 2y | 2013-09-02 | 2026-10-14 | 3297 | rba | — |
| CAD 2y | 2020-05-29 | 2026-10-15 | 1589 | boc_valet | — |
| CHF 2y | — | — | 0 | — | no 2Y series in data/rates.parquet |
| EUR 2y | 2021-09-24 | 2026-10-15 | 1294 | ecb | — |
| GBP 2y | 2016-01-04 | 2026-10-15 | 2726 | boe_glc | — |
| JPY 2y | 1974-09-24 | 2026-10-15 | 12958 | mof_jgb | 2019-04-26→2019-05-07 (11z) |
| NZD 2y | — | — | 0 | — | no 2Y series in data/rates.parquet |
| USD 2y | 1961-06-14 | 2026-10-14 | 16318 | dbnomics_fed, fred | — |
| DFII10 | 2003-01-02 | 2026-10-14 | 5950 | fred | — |
| REAL_10Y_TSY | 2024-01-02 | 2026-10-15 | 697 | treasury | — |

## 3. Ratele de politică (`src.policy_rate.policy_rate_history`)

| Valută | Prima decizie | Ultima | N | Surse | Goluri > 100 z | Rata curentă |
|---|---|---|---:|---|---|---:|
| USD | 2023-02-01 | 2026-09-16 | 31 | ff 27, cb_decisions 4 | — | 3.875 |
| EUR | 2023-02-02 | 2026-09-10 | 30 | ff 26, cb_decisions 4 | — | 2.500 |
| GBP | 2023-02-02 | 2026-09-17 | 30 | ff 26, cb_decisions 4 | — | 3.750 |
| JPY | 2023-01-18 | 2026-09-18 | 30 | ff 26, cb_decisions 4 | — | 1.250 |
| AUD | 2023-02-07 | 2026-09-29 | 33 | ff 28, cb_decisions 5 | — | 4.600 |
| NZD | 2023-02-22 | 2026-09-02 | 27 | ff 23, cb_decisions 4 | — | 2.750 |
| CAD | 2023-01-25 | 2026-09-02 | 31 | ff 27, cb_decisions 4 | — | 2.250 |
| CHF | 2023-03-23 | 2026-09-24 | 15 | ff 10, cb_decisions 5 | — | 0.000 |

## 4. COT (`data/history.parquet`)

| Simbol | Cod CFTC | Prima | Ultima | N | Goluri |
|---|---|---|---|---:|---|
| AUD | 232741 | 2023-03-28 | 2026-10-06 | 185 | — |
| CAD | 090741 | 2023-03-28 | 2026-10-06 | 185 | — |
| CHF | 092741 | 2023-03-28 | 2026-10-06 | 185 | — |
| COPPER | 085692 | 2023-03-28 | 2026-10-06 | 185 | — |
| DOW | 124603 | 2023-03-28 | 2026-10-06 | 185 | — |
| DXY | 098662 | 2023-03-28 | 2026-10-06 | 185 | — |
| EUR | 099741 | 2023-03-28 | 2026-10-06 | 185 | — |
| GBP | 096742 | 2023-03-28 | 2026-10-06 | 185 | — |
| GOLD | 088691 | 2023-03-28 | 2026-10-06 | 185 | — |
| JPY | 097741 | 2023-03-28 | 2026-10-06 | 185 | — |
| MXN | 095741 | 2023-03-28 | 2026-10-06 | 185 | — |
| NASDAQ | 209742 | 2023-03-28 | 2026-10-06 | 185 | — |
| NIKKEI* | 240743 | 2023-03-28 | 2026-10-06 | 185 | — |
| NZD | 112741 | 2023-03-28 | 2026-10-06 | 185 | — |
| PALLADIUM | 075651 | 2023-03-28 | 2026-10-06 | 185 | — |
| PLATINUM | 076651 | 2023-03-28 | 2026-10-06 | 185 | — |
| RUSSELL | 239742 | 2023-03-28 | 2026-10-06 | 185 | — |
| SILVER | 084691 | 2023-03-28 | 2026-10-06 | 185 | — |
| SP500 | 13874A | 2023-03-28 | 2026-10-06 | 185 | — |
| VIX | 1170E1 | 2023-03-28 | 2026-10-06 | 185 | — |
| ZAR | 122741 | 2023-03-28 | 2026-10-06 | 185 | — |

\* NIKKEI nu are contract în `data/contracts.yaml`. Contractul 240743 (NIKKEI STOCK AVERAGE YEN DENOM, CME) e descărcat doar pentru evaluare, din același dataset CFTC (jun7-fc8e), de la aceeași primă dată, în `~/.cache/macro-tournament/cftc`. Contractul în USD, 240741, nu mai raportează după 2026-03-03. DAX și FTSE100 nu au contract CFTC, deci sunt n/a la COT.

## 5. Put/call CBOE (`data/pc_history.parquet`)

| Coloană | Prima | Ultima | N | Goluri |
|---|---|---|---:|---|
| equity | 2019-10-07 | 2026-10-15 | 1766 | — |
| total | 2019-10-07 | 2026-10-15 | 1766 | — |

## 6. Retail (`data/retail_history.parquet`)

Furnizori: myfxbook. Interval total: 2026-04-29 → 2026-10-16.

| Simbol | Prima | Ultima | Zile | Goluri |
|---|---|---|---:|---|
| AUDJPY | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| AUDUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| BTCUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| ETHUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| EURAUD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| EURGBP | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| EURJPY | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| EURUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| GBPJPY | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| GBPUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| GER40 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| JPN225 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| NAS100 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| NZDUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| UK100 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| US30 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| US500 | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| USDCAD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| USDCHF | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| USDJPY | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| XAGUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |
| XAUUSD | 2026-04-29 | 2026-10-16 | 164 | 2026-09-06→2026-09-14 (8z) |

## 7. Prețuri

| Unitate | Sursă | Prima | Ultima | N | Goluri (din 2014) | Ultima vineri cu randament la 4 săpt. |
|---|---|---|---|---:|---|---|
| EUR | FRED DEXUSEU | 1999-01-04 | 2026-10-09 | 6965 | — | 2026-09-04 |
| GBP | FRED DEXUSUK | 1971-01-04 | 2026-10-09 | 13986 | — | 2026-09-04 |
| AUD | FRED DEXUSAL | 1971-01-04 | 2026-10-09 | 13979 | — | 2026-09-04 |
| NZD | FRED DEXUSNZ | 1971-01-04 | 2026-10-09 | 13970 | — | 2026-09-04 |
| JPY | FRED DEXJPUS | 1971-01-04 | 2026-10-09 | 13980 | — | 2026-09-04 |
| CAD | FRED DEXCAUS | 1971-01-04 | 2026-10-09 | 13992 | — | 2026-09-04 |
| CHF | FRED DEXSZUS | 1971-01-04 | 2026-10-09 | 13986 | — | 2026-09-04 |
| SP500 | Yahoo ^GSPC | 2000-01-03 | 2026-10-15 | 6737 | — | 2026-09-11 |
| NASDAQ | Yahoo ^IXIC | 2000-01-03 | 2026-10-15 | 6737 | — | 2026-09-11 |
| DJIA | Yahoo ^DJI | 2000-01-03 | 2026-10-15 | 6737 | — | 2026-09-11 |
| DAX | Yahoo ^GDAXI | 2000-01-03 | 2026-10-15 | 6803 | — | 2026-09-11 |
| NIKKEI | Yahoo ^N225 | 2000-01-04 | 2026-10-15 | 6559 | 2019-04-26→2019-05-07 (11z) | 2026-09-11 |
| FTSE100 | Yahoo ^FTSE | 2000-01-04 | 2026-10-15 | 6767 | — | 2026-09-11 |
| GOLD | Stooq xauusd_d.csv | 2000-03-01 | 2026-10-06 | 6848 | — | 2026-09-04 |
| SILVER | Stooq xagusd_d.csv | 2000-03-01 | 2026-10-06 | 6792 | — | 2026-09-04 |

**Sfârșitul ferestrei comune (regula din pre-înregistrare): 2026-09-04**. Seriile care îl determină: AUD, CAD, CHF, EUR, GBP, GOLD, JPY, NZD, SILVER.

### Maparea față de `data/crossasset_instruments.yaml`

Instrumente în config: DAX, DJIA, FTSE100, GOLD, NASDAQ, NIKKEI, SILVER, SP500. Lipsă din mapare: niciunul. În plus în mapare: niciunul.

| Instrument | Tip (config) | home_ccy (config) | Sursă | Ticker/fișier |
|---|---|---|---|---|
| SP500 | index | USD | yahoo | `^GSPC` |
| NASDAQ | index | USD | yahoo | `^IXIC` |
| DJIA | index | USD | yahoo | `^DJI` |
| DAX | index | EUR | yahoo | `^GDAXI` |
| NIKKEI | index | JPY | yahoo | `^N225` |
| FTSE100 | index | GBP | yahoo | `^FTSE` |
| GOLD | metal | USD | stooq | `xauusd_d.csv` |
| SILVER | metal | USD | stooq | `xagusd_d.csv` |

### Corelația randamentelor zilnice Yahoo vs FRED (prag > 0,99)

| Instrument | Yahoo | FRED | Perioada comună | N randamente | Corelație | Abatere max. de nivel | OK |
|---|---|---|---|---:|---:|---:|---|
| SP500 | ^GSPC | SP500 | 2016-10-17 → 2026-10-15 | 2512 | 0.999995 | 0.119% | da |
| NASDAQ | ^IXIC | NASDAQCOM | 2000-01-03 → 2026-10-15 | 6736 | 0.995495 | 1.360% | da |
| DJIA | ^DJI | DJIA | 2016-10-17 → 2026-10-15 | 2512 | 0.999999 | 0.042% | da |
| NIKKEI | ^N225 | NIKKEI225 | 2000-01-04 → 2026-10-15 | 6555 | 1.000000 | 0.000% | da |
