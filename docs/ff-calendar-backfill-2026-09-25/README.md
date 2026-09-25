# Completare date lipsă + consens 0.0 — din paginile ForexFactory (2026-09-25)

Pornit de la auditul `/history` din 2026-09-24 (doc în Project: `claude/audit-history-2026-09-24.md`).
Cerința: toate datele lipsă și rândurile cu consens 0.0, verificate și completate cu valorile corecte.

**Sursă:** paginile de zi ForexFactory (`https://www.forexfactory.com/calendar?day=<mon><d>.<yyyy>`) —
aceeași sursă pe care o ingerează pipeline-ul (feedul FF săptămânal). 244 de pagini extrase sunt în
`pages/` (JSON; ora afișată = America/New_York a zilei respective, convertită DST-aware în UTC).
`2026-01-14.json` și `2026-01-22.json` conțin doar rândurile USD. Surse oficiale folosite punctual:
SNB (decizia 2026-09-24, override), ONS (pauza PPI 2025, `config/known_gaps.yaml`).

**Aplicare:** `migrations/2026-09-25_backfill_ff_calendar_pages.py` aplică `changes.csv` pe
`data/economic_calendar_ff.parquet` (backup `...parquet.pre-ff-calendar-backfill`). Fiecare rând din
`changes.csv` are `reason`, `evidence` (URL-ul paginii FF) și `old` (rândul dinainte).
Gărzi: țintele delete/update există exact o dată, inserturile nu există deja (a doua rulare refuză).

## Ce s-a schimbat: 3904 → 3848 rânduri

| operație | n | detaliu |
|---|---:|---|
| insert | 122 | 119 publicații lipsă (golul JB 1–31 dec 2023, 3–6 apr 2026, JPY Prelim GDP 2023–2025, BoJ/SNB, AUD CPI feb 2026 etc.) + 3 printuri din ian 2024 care existau doar ca rând „an greșit” |
| update | 116 | 60 consens 0.0 confirmat real pe FF (`forecast_origin=ff`); 19 consens gol pe FF (0.0 → fără consens, `ff_blank`); 21 placeholder 0.0/gol completat cu printul FF; 11 rânduri „an greșit” înlocuite cu printul real din ian 2023; 5 valori greșite corectate |
| delete | 178 | 71 copii „an greșit” (ian 2024 datat ian 2023); 36 placeholdere JB 0/0/0; 25 pre-listări/re-listări lângă printul real; 14 placeholdere BoJ; 11 PMI ale altei țări (purjate pe 30 iul, reapărute); 10 placeholdere 0.0 pe serii de nivel; 4 copii JB datate greșit ale rândurilor „Oct Data”; 4 listări cu 1h mai devreme; 3 placeholdere shutdown SUA |

### Valori greșite corectate (găsite comparând parquetul cu paginile FF)

| rând | înainte | FF (corect) | cauza |
|---|---|---|---|
| GBP GDP m/m 2026-08-13 | −0.5 / 0.0 / −0.2 | **0.3 / 0.0 / 0.0** | JB a pus valorile Manufacturing Production |
| JPY Prelim IP 2026-02-26 | 1.8 / 0.1 / −0.9 | **2.2 / 5.5 / −0.1** | JB a pus valorile Retail Sales din același minut |
| USD Advance GDP Price Index 2026-02-20 | 0.4 / 0.4 / 0.4 | **3.6 / 2.8 / 3.8** | valori ale altui eveniment |
| AUD Monthly CPI 2026-02-25 | −0.1 / 1.2 / 0.1 | 0.4 / 0.2 / 1.0 | (batch 1) |
| CHF PPI 2023-01-19, JPY Tokyo Core CPI 2025-09-25 | — | vezi `changes.csv` | (batch 1) |

GBP GDP 2026-08-13 avea și un override manual (0.4, dolce) — acela era tot greșit (0.4 e `Prelim GDP q/q`)
și rămâne inert, pentru că rândul brut are acum valoarea reală.

### Rândurile „an greșit” din ianuarie 2023

Arhiva JB a datat o parte din ianuarie 2024 ca ianuarie 2023 (rânduri identice actual/forecast/previous
cu cele din 2024). Purja din 2026-07-31 (`migrations/2026-07-31_purge_jan2023_duplicates.py`, 64 rânduri)
**nu mai era în vigoare**: 63/64 erau din nou în parquet la HEAD, iar din cele 219 PMI purjate pe
2026-07-30 reapăruseră 11 — cel mai probabil re-adăugate de re-scanarea arhivei (`src/archive_backfill.py`),
care nu avea nicio evidență a ștergerilor. Concluzia „adăugare, nu înlocuire” din
`docs/jan2023-duplicate-purge.md` nu ține pentru 11 serii: acolo rândul greșit ocupa locul printului real
(ex. CAD GDP 2023-01-31, EUR PPI 2023-01-05, CHF/EUR unemployment 2023-01-09, JPY PPI/SPPI/machinery) —
acum au printul real de pe FF. Pentru USD IP, JPY retail, JPY prelim IP printul din ian 2024 lipsea cu
totul și a fost mutat la data corectă.

**Ca să nu se mai întâmple:** fiecare rând șters e în `data/ff_tombstones.csv` (împreună cu cele din
purjele din 30–31 iulie: 387 rânduri), iar `scope_recoverable_rows` sare peste un (canonical_id, dată)
tombstoned (test nou în `tests/test_archive_backfill.py`).

## Consens 0.0 — rezultat

- 60 de consensuri JB 0.0 sunt reale pe FF („0.0%”) → acum contează ca consens (`forecast_origin=ff`).
- 22 de rânduri au consensul gol pe FF → fără consens (`ff_blank`; 19 doar consens, 3 în rânduri completate).
- Regula de scoring pentru restul (JB 0.0 neverificat → NaN) rămâne neschimbată.

## Zerouri reale confirmate pe FF, fără override (intenționat)

15 printuri 0.0 sunt reale pe FF, dar regula zero (Z2/R2) le tratează drept placeholder pentru că
printul următor le-a revizuit: USD IP 2023-02-15; CAD GDP 2023-05-31, 2023-06-30, 2024-02-29;
EUR Retail 2023-07-06; JPY Prelim IP 2023-09-28; NZD Retail 2023-11-23; USD Import Prices 2024-01-17
și 2025-06-17; USD Durable Goods 2024-01-25 și 2024-09-26; AUD Retail 2024-08-30; USD Core PCE
2025-04-30; CHF Retail 2025-07-01; GBP PPI Output 2025-10-22 06:05.
Un override ZERO_CONFIRM le-ar face corecte în scoring, dar azi fiecare override pe un rând
placeholder apare **de două ori** pe `/history` (bug-ul „override twin”, mai jos). De adăugat după fixul
de cod. Impactul pe scorul de azi e nul (toate sunt în afara ferestrelor curente).

## Ce nu are sursă (documentat în `config/known_gaps.yaml`)

- SUA shutdown oct–nov 2025 (CPI oct anulat, NFP/UR/AHE oct, GDP Q3 advance etc.) — publicații inexistente.
- GBP PPI feb–iul 2025 — ONS a suspendat publicarea (21 mar 2025), reluată 2025-10-22.
- CHF PPI aug 2025 — nu apare pe FF (săptămânile 31 aug–27 sep verificate) și nici în arhiva JB.
- Intrările `aud_cpi_2026_02`, `nzd_retail_sales_2025_q4`, `cad_trimmed_cpi_2025_11`,
  `jpy_manufacturing_pmi_2025_12_flash`, `aud_company_profits_2024_q3`, `jpy_capital_spending_2024_q3`
  au fost scoase: acele publicații sunt acum completate.
- Săptămâna curentă (AUD employment/UR 2026-09-24, JPY flash PMI 2026-09-24) — o aduce pipeline-ul la
  următorul fetch.

## Impact măsurat (render HEAD vs render cu datele noi, la același moment)

| valută | index | strength | cauza |
|---|---|---|---|
| AUD | 3.50 → 3.75 | −0.105 → 0.044 | CPI y/y: z 0.73 → 0.85, scor 0 → 1 (CPI feb 2026 intră în sigma) |
| CAD | 3.375 → 2.75 | −0.540 → −0.933 | GDP: z 0.89 → 0.74, scor 1 → 0 (consensuri 0.0 confirmate) |
| GBP | 3.00 → 3.25 | −0.772 → −0.623 | GDP: z 1.39 → 1.76, scor 1 → 2 (GDP 2026-08-13 corectat) |
| JPY | 4.167 → 4.375 | −0.675 → −0.552 | GDP: fallback −2 → 0 (12 printuri Prelim GDP); IP: z 0.88 → 0.59, scor 1 → 0 |
| CHF, EUR, NZD, USD | neschimbat | ±0.01 | doar z-uri |

Instrumente: 21 din 29 își schimbă scorul, **0 schimbări de etichetă** (bias).

## `/history` după migrare

| | HEAD | după |
|---|---:|---:|
| serii afișate | 44 | 47 (+ CHF GDP, JPY GDP, SNB) |
| puncte reale | 1600 | 1701 |
| goluri | 60 | 12 (toate legitime: shutdown SUA ×7, RBA ian, RBNZ vara ×4 — iul 2023 ascuns de detectorul de cod) |
| puncte fantomă | 31 | 11 (toate „override twin” — cod) |
| valori afișate greșit | 45 | 6 (3 revizii reale + 3 override twin) |
| printuri fără consens | 30 | 2 (catch-up oct 2025, fără consens pe FF) |

## Rămase de rezolvat — în COD, nu în date

1. **Override twin** (`src/history_compute.py`): un override pe un rând placeholder apare de două ori
   (punctul recuperat + rândul manual) → 11 fantome, 3 valori greșite.
2. **Regula zero**: un 0.0 real revizuit ulterior rămâne „placeholder” fără override (vezi lista de 15).
3. **Detectorul de ghost** pe rate ascunde RBNZ 2023-07-12.
4. **Duplicate identice la 1h** (datoria cunoscută `docs/known-debt-ff-dst-duplicate.md`, 28 perechi) —
   neatinse aici, intenționat.
5. **Integritate**: verificarea `previous` pe PMI final compară cu flash-ul (convenția FF) → WARN fals
   (GBP PMI 2025-10-01, „new” la acest render).
6. **Re-atribuirea PMI flash 2023–2025** (EUR/GBP/AUD/JPY etichetate USD în arhivă) — pas separat, schimbă scoruri.
7. Două override-uri dolce (BoJ 2026-07-29 21:00 și 2026-09-16 21:00) stăteau pe placeholdere acum șterse;
   deciziile reale (07-31 03:11 = 1.00 prin override, 09-18 02:54 = 1.25) rămân.

## Verificare

Un agent separat, care nu a văzut lucrul, a verificat versiunea dinaintea batch-ului 5: a re-aplicat
`changes.csv` independent (identic cu parquetul, 3642 rânduri neatinse bit-identice), a verificat 233 de
rânduri contra paginilor (226 exact, restul explicate) și a clasificat toate ștergerile. A găsit 7 probleme; toate au fost corectate în batch-ul 5 (PMI reapărute,
JPY IP 2026-02-26, USD GDP Price Index 2026-02-20, JPY 2026-04-29 la ora corectă, CAD Core CPI placeholdere,
claims 2025-11-18 previous, `old` complet). Apoi 1111 rânduri comparate cu toate cele 244 de pagini:
diferențele rămase sunt doar override-uri existente, ore decalate 1h (datoria cunoscută), rânduri catch-up
fără oră și săptămâna curentă.

Teste: `python3 -m pytest -q` → 2004 passed, 10 skipped.

## Reproducere

```bash
cp data/economic_calendar_ff.parquet.pre-ff-calendar-backfill data/economic_calendar_ff.parquet
rm -f data/ff_tombstones.csv
python3 migrations/2026-09-25_backfill_ff_calendar_pages.py
python3 -m src.main --mode render-all
```

## Re-aplicat pe f717a8fc (origin/main) la 2026-09-25

Sursele din bundle (6719cab + 2 commit-uri); datele regenerate pe parquetul de la f717a8fc (nu luate din bundle).

- Migrare: `3904 -> 3848 rows; {'delete': 178, 'insert': 122, 'update': 116}; 387 tombstones`; a doua rulare refuză
  (`insert: 122 row(s) already present (already migrated?)`).
- `scope_recoverable_rows(now_utc=2026-09-25 08:17 UTC)` → 0 rânduri; 0 chei (canonical_id, datetime_utc) din
  `ff_tombstones.csv` în parquet.
- `/history`: 44 → 47 serii; BOJ 23 → 30 puncte, SNB 15, CHF GDP 15, JPY GDP 15; 0 puncte duble în ian 2023 (main: 2).
- Integritate (render 2026-09-25 08:15 UTC): release_conflict 53 → 0, warn 29 → 23, new_warn 4, față de raportul de pe main:
  GBP PMI 2025-10-01 (fals pozitiv, vezi mai sus) + 3 revizii reale ale printurilor completate/corectate aici
  (AUD Company Profits 2024-09-02 −5.3 → −6.8; JPY Prelim IP 2026-02-26 2.2 → 4.3; USD Durable Goods 2026-06-25 −4.5 → −4.0).
- Teste: 2014 passed; snapshot AUD/JPY phone bars 1.65 (5 bare, Growth 0.10) → 1.67 (4 bare, Inflation 0.31).

Impact pe scor, `build_economic_payload(as_of=2026-09-25 08:30)`, main f717a8fc vs branch:

| valută | index | strength |
|---|---|---|
| AUD | 3.500 → 3.750 | −0.048 → +0.101 |
| CAD | 2.750 → 2.125 | −0.869 → −1.262 |
| GBP | 3.000 → 3.250 | −0.715 → −0.566 |
| JPY | 4.167 → 4.375 | −0.617 → −0.494 |
| CHF, EUR, NZD, USD | neschimbat | ±0.01 |

Instrumente: 21 din 29 își schimbă scorul; **2 schimbări de bias**: GBPCAD +0.944 → +1.333 (Neutral → Bullish, trece
`mild` 1.12) și CADCHF −2.074 → −2.444 (Bearish → Very Bearish, trece `very` 2.41). Cauza e aceeași mișcare CAD
ca pe 6719cab (GDP z 0.886 → 0.742, scor 1 → 0; index −0.625), dar main-ul pornește acum de la CAD 2.75 (3.375 pe 6719cab),
așa că aceeași deplasare trece pragurile.
