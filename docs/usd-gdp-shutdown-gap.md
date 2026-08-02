# USD GDP — impactul golului din shutdown asupra baseline-ului

Status: **investigație încheiată, zero cod, zero config.** Branch
`investigate/usd-gdp-gap`, worktree `../macro-dev`.

## 1. Răspuns direct: câte observații, sub sau peste prag, ce flag azi

**13 observații valide (actual+consensus) în tot istoricul `usd_gdp_qoq`,
din care 12 intră în fereastra curentă `surprise_window_k=12`. Peste
`fallback_min_prints=6`. Flag-ul afișat azi: `null` (z-score normal, NU
`fallback`).**

Verificat direct în `public/data/economic.json` (US-DOLLAR →
`breakdown.base.indicators.gdp_qoq`), reproduce exact ce calculez separat:

```
actual=1.5  consensus=2.1  surprise=-0.6  z=-0.8432424966313627
score=-1  flag=null  release_dt=2026-07-30T12:30:00  stale=false
```

**Corecție de presupunere, consemnată per regulă:** task-ul cere comparație
cu `CADENCE_THRESHOLD` (`quarterly: 8` în `src/ff_scoring.py`). Verificat —
**această constantă nu e folosită NICĂIERI în codul de producție** (`src/`),
doar în propriul test (`tests/test_ff_scoring.py:75`). Pragul care chiar
guvernează fallback-ul e `fallback_min_prints=6` din
`data/economic_indicators.yaml` (`defaults:`), consumat direct în
`compute_indicator_score`. Am folosit acest prag real, nu `CADENCE_THRESHOLD`
(mort, nefolosit) — altfel comparația ar fi fost față de un cod inexistent.

Cu 12 perechi valide azi, `usd_gdp_qoq` e cu 6 peste pragul real (100%
marjă) — departe de fallback, indiferent care prag ai compara.

## 2. Rândul fals 2023-01-25 — confirmat scos

```
2023-01-26 13:30  actual=2.9  forecast=2.6   ← singurul rând ianuarie 2023 rămas
```

Nu există niciun rând datat 2023-01-25 pentru `Advance GDP q/q` în parquet-ul
curent. Valorile lui (act=3.3, fc=2.0 — copie a lui 2024-01-25) nu mai apar
nicăieri sub 2023. **Purja a funcționat, confirmat direct pe date, nu
presupus.**

## 3. Golul Q3/Q4 2025 — confirmat, nerecuperabil

```
2025-07-30  actual=3.0   (ultimul print înainte de gol)
2026-02-20  actual=0.0, forecast=2.8, previous=4.4   (print de recuperare — placeholder)
```

Niciun rând între 2025-07-30 și 2026-02-20 — golul acoperă exact Q3 2025
(așteptat ~10-30) și Q4 2025 (așteptat ~01-30), 205 zile fără print, față de
cadența normală de 91 zile. Absent și din `data/archive/ff_calendar_range.json`
(verificat, nu presupus). **Nerecuperabil — nu am încercat să-l reconstitui,
per instrucțiune.**

`gdp_qoq` NU e în `can_be_zero` → `to_scoring_frame` transformă
`actual=0.0` în `NaN` pentru rândul din 2026-02-20. **Confirmat: rândul mort
nu contribuie la scor.** El nici măcar nu apare ca pereche
(`actual`+`consensus` ambele valide) în calculul sigma — e complet exclus,
nu doar ignorat parțial.

## 4. Cât de sistemic e golul — mai larg decât growth+labour

Scanat cadența TUTUROR indicatorilor USD (nu doar growth/labour), comparând
gap-ul maxim din fereastra shutdown-ului (2025-10 → 2026-02) cu gap-ul median
al fiecărei serii, pe calea reală (`to_scoring_frame` → dedup):

| indicator | categorie | gap max în fereastră | gap median | raport | e gap-ul maxim din TOT istoricul? |
|---|---|---|---|---|---|
| `jobless_claims` | labour | 56 zile | 7 | **8.00×** | da |
| `ppi_yoy` | inflation | 132 zile | 29 | **4.55×** | da |
| `core_cpi` | inflation | 81 zile | 30 | **2.70×** | da |
| `import_prices` | inflation | 78 zile | 30 | **2.60×** | da |
| `core_pce` | inflation | 70 zile | 28 | **2.50×** | da |
| `personal_spending_mm` | growth | 70 zile | 28 | **2.50×** | da |
| `personal_income_mm` | growth | 70 zile | 28.5 | **2.46×** | da |
| `jolts` | labour | 70 zile | 29 | **2.41×** | da |
| `retail_sales` | growth | 70 zile | 30 | **2.33×** | da |
| `gdp_qoq` | growth | 205 zile | 91 | **2.25×** | da |
| `gdp_price_index` | inflation | 205 zile | 91 | **2.25×** | da |
| `unit_labor_costs_qoq` | labour | 154 zile | 91 | 1.69× | da |
| `employment_change`/`unemployment_rate`/`wage_growth` | labour | 48 zile | 28 | 1.71× | nu (gap mai mare există și altundeva, neconectat la shutdown) |
| `cpi_yoy` | inflation | 55 zile | 29 | 1.90× | nu |
| altele (PMI, ADP, rate) | — | ≤35% peste median | — | ≤1.25× | nu |

**Corecție de presupunere #2:** task-ul a încadrat problema ca
"growth și labour" — dar `core_cpi`, `ppi_yoy`, `import_prices`, `core_pce`
(toate INFLATION) arată exact același tipar (raport 2.5-4.55×, gap-ul maxim
din tot istoricul cade chiar în fereastra shutdown-ului). Coerent cu cauza:
BLS colectează atât CPI/PPI cât și CES/JOLTS/ocuparea — un shutdown BLS
lovește ambele familii de serii, nu doar growth/labour. Golul e mai larg
decât presupunerea inițială — 12 serii afectate clar, în 3 categorii.

**Dar — verificat, nu presupus: niciuna nu e sub prag azi.** Pentru toate
cele 12 serii afectate (plus restul indicatorilor growth/inflation/labour
USD), `n_pairs_in_window` = 12 (fereastra completă), niciuna sub
`fallback_min_prints=6`, niciun `flag=fallback` cauzat de gol. Motivul:
fiecare serie are 14-45 de printuri istorice totale — golul a costat 1-2
observații, nu suficient ca vreo serie să scadă sub prag, pentru că
`window_k=12` oricum trunchiază la ultimele 12, iar toate au ≥12 disponibile
chiar și după gol. **"Baseline subțiat simultan" (presupunerea din task) nu
se confirmă la nivelul de azi al scorului — se confirmă doar ca istoric total
mai sărac cu 1-2 trimestre/luni, invizibil în scorul afișat.**

## 5. Efectul asupra σ — măsurat, nu presupus, tablou mixt

Pentru cele 12 serii afectate: σ al surprizei pe fereastra curentă (cu
printul/printurile de după gol incluse) vs. σ pe aceeași fereastră EXCLUZÂND
acele printuri.

| indicator | print(uri) post-gol în fereastra activă | σ cu | σ fără | raport |
|---|---|---|---|---|
| `jolts` | 4 printuri, surprize până la ±0.71 | 0.417 | 0.295 | **1.414** |
| `retail_sales` | 5 printuri, surprize până la ±0.4 | 0.239 | 0.177 | **1.350** |
| `ppi_yoy` | 2 printuri | 0.475 | 0.520 | 0.913 |
| `core_pce` | 1 print | 0.060 | 0.063 | 0.953 |
| `personal_income_mm` | 1 print | 0.340 | 0.356 | 0.955 |
| `gdp_price_index` | 1 print (surpriză 0.0) | 0.792 | 0.830 | 0.954 |
| `import_prices` | 2 printuri | 0.698 | 0.805 | 0.867 |
| `core_cpi` | 4 printuri | 0.079 | 0.092 | 0.866 |
| `personal_spending_mm` | 1 print | 0.100 | 0.098 | 1.015 |
| `jobless_claims` | niciunul mai e în fereastră | — | — | 1.000 (nul) |
| `gdp_qoq` | niciunul (singurul candidat e NaN, exclus) | — | — | 1.000 (nul) |
| `unit_labor_costs_qoq` | niciunul mai e în fereastră | — | — | 1.000 (nul) |

**Ipoteza din task ("consensul e mai prost după gol → σ umflat → z comprimat")
se confirmă doar pentru 2 din 12 serii** (`jolts` +41%, `retail_sales` +35%).
Pentru celelalte 9 cu printuri încă în fereastră, σ e **neschimbat sau chiar
MAI MIC** cu printurile post-gol incluse (rapoarte 0.86-1.02) — surprizele de
după shutdown au fost, în majoritate, mai calme decât restul ferestrei, nu
mai zgomotoase. Pentru 3 serii (`jobless_claims`, `gdp_qoq`,
`unit_labor_costs_qoq`) întrebarea e deja irelevantă azi — fie golul nu a
lăsat un print valid în fereastră (GDP, catch-up-ul e NaN), fie printul
post-gol a ieșit deja din fereastra de 12.

### Consecință vizibilă, verificată: scorul `jolts` de azi e suprimat de exact acest efect

```
z curent (cu σ umflat de gol):  0.7435  → score = 0  (Neutral)
z ipotetic (fără printurile post-gol): 0.31/0.295 = 1.051  → ar fi score = +1
```

Verificat pe buckets reale (`defaults.z_buckets = [1.54, 0.81]`, nu
placeholderul `[1.0, 0.33]` din fixture-urile de test) — `0.7435 < 0.81`
(sub prag), `1.051 > 0.81` (peste). **Ăsta e efectul concret, măsurat, nu un
efect ipotetic generic**: labour USD arată azi Neutral la `jolts` parțial
pentru că fereastra sigma include zgomotul din perioada de shutdown.
`retail_sales`, deși σ-ul lui e la fel de umflat (+35%), nu suferă un
bucket-flip azi — surpriza lui curentă e exact 0.0 (z=0 indiferent de σ).

## Fire găsite pe parcurs — consemnate, fără acțiune

**`gdp_price_index` are un rând-duplicat identic ca tipar cu cel purjat din
`gdp_qoq`, dar NEpurjat din parquet — momentan inofensiv.**

```
2023-01-25 13:30  actual=1.5  forecast=2.3  previous=3.3   ← copie EXACTĂ a lui 2024-01-25
2023-01-26 13:30  actual=3.5  forecast=3.2  previous=4.4   ← printul real ianuarie 2023
2024-01-25 13:30  actual=1.5  forecast=2.3  previous=3.3   ← originalul
```

Exact același tipar ca duplicatul din `gdp_qoq` (rândul fals datat cu o zi
înainte de cel real, valori copiate dintr-un an mai târziu) — dar aici încă
prezent în parquet, spre deosebire de `gdp_qoq` unde a fost scos la sursă.
Verificat: **momentan inofensiv** — `_dedup_flash_final` grupează cele două
rânduri din ianuarie 2023 (1 zi distanță, mult sub `gap_days=45` trimestrial)
și alege corect rândul mai recent (2023-01-26, cel real), la fel ca la
`gdp_qoq`. 18 rânduri brute → 14 după dedup, identic ca structură cu
`gdp_qoq`. Nu recomand acțiune — dormant, rezolvat corect de dedup existent,
dar parquet-ul rămâne inconsistent (o serie curățată la sursă, cealaltă nu).
Consemnat pentru o eventuală curățare viitoare, nu de azi.

## Ce NU s-a făcut

- Nicio încercare de recuperare a Q3/Q4 2025 — nu există nicăieri.
- Nicio propunere de schimbare la `CADENCE_THRESHOLD` (oricum mort) sau la
  `fallback_min_prints`/`surprise_window_k`.
- Niciun cod sau config de producție atins.
- `gdp_price_index`'s duplicat — documentat, nu curățat.
- FAZA 2 (triplicare CHF/GBP) — nu reluată; niciun indiciu contrar fix-ului
  din `ed017cf` întâlnit în această investigație.

## Verificări făcute

- Toate numerele din §1 reproduse exact din `public/data/economic.json`
  înainte de a avea încredere în calculul separat (metodă cerută explicit).
- `CADENCE_THRESHOLD` verificat ca nefolosit prin grep pe tot `src/`, nu
  presupus din citirea unui singur fișier.
- Scanarea de gap-uri (§4) acoperă TOȚI indicatorii USD din
  `economic_indicators.yaml`, nu doar cei din ipoteza inițială.
- σ cu/fără (§5) calculat pe calea reală (`to_scoring_frame` → excludere
  carantină → `_dedup_flash_final` → pairs), nu pe parquet brut.
- Bucket-urile z verificate din `defaults.z_buckets` reale
  (`[1.54, 0.81]`), nu din fixture-uri de test.
- `git status` — niciun fișier de cod sau date modificat.
