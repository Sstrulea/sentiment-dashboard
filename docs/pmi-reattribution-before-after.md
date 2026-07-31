# FAZA 3 — re-atribuirea celor 219 rânduri PMI (MĂSURARE, nescris în parquet)

Status: **măsurare, zero scriere**. Branch `fix/remaining-data-defects`,
worktree `../macro-dev`. `data/economic_calendar_ff.parquet` NEATINS.
**STOP înainte de scriere, cum s-a cerut.**

## Filtrul din FAZA 2, aplicat pe subsetul PMI, întâi

Cele 219 rânduri purjate pe 30 iulie (commit `8223035`) — reconstruite exact
prin diff `data/economic_calendar_ff.parquet.pre-purge` minus parquet-ul
curent, cheie `(canonical_id, datetime_utc)`: **39 CAD Manufacturing PMI +
107 GBP Final Manufacturing PMI + 73 GBP Final Services PMI = 219,
confirmat**.

Aplicând criteriul strict din FAZA 2 (aceeași valută+`name_raw`+`actual`+
`forecast`+ziua din lună, exact un an mai târziu) **pe acest subset**: **6
din cele 219 sunt ele însele duplicate ianuarie-2023-din-2024** (deja
identificate în măsurarea FAZA 2, unde erau excluse din purja generală
tocmai pentru că aparțin acestui subset PMI). Din cele 6, 5 sunt încă în
setul de 219 (1 — `GBP Final Services PMI` 09:30 2023-01-04 — era deja
păstrat ca real de purja din 30 iulie, deci nu mai există în cele 219 de
purjat acum).

## Validare empirică — ora locală, confirmată cu date

Copiile proprii, corect etichetate, ale EUR/JPY/CHF (live, din 2026-01) —
ora UTC își schimbă valoarea EXACT la trecerea DST, ora locală rămâne fixă:

| valută | serie | ora UTC (vară/iarnă) | ora locală (fixă) |
|---|---|---|---|
| CHF | Manufacturing PMI | 07:30 / 08:30 | **09:30** (CET/CEST) |
| JPY | Manufacturing PMI (au Jibun) | 00:30 / 00:30 (fără DST) | **09:30** JST |
| EUR | Manufacturing + Services PMI | 08:00 / 09:00 | **10:00** CET/CEST |

Distribuția orei UTC în cele 219 rânduri (grupate pe `canonical_id`):

| canonical_id (etichetă greșită) | oră UTC | n | potrivire |
|---|---|---|---|
| `cad_s_p_global_manufacturing_pmi` | 07:30 / 08:30 | 21+17=38 | **CHF** (09:30 local, confirmat) |
| `gbp_s_p_global_cips_manufacturing_pmi` | 00:30 | 34 | **JPY** (09:30 JST, confirmat) |
| `gbp_s_p_global_cips_manufacturing_pmi` | 08:00 / 09:00 | 21+14=35 | **EUR** (10:00 local, confirmat) |
| `gbp_s_p_global_cips_services_pmi` | 08:00 / 09:00 | 21+15=36 | **EUR** (10:00 local, confirmat) |

**Toate patru coincid exact** cu tiparul DST-shift al copiilor proprii —
regula de atribuire e confirmată cu date, nu presupusă.

### Cluster NEIDENTIFICAT — lăsat nereatribuit, nu ghicit

**76 din cele 219 rânduri NU se potrivesc cu niciun tipar cunoscut** (EUR,
JPY, CHF, sau chiar orele reale ale GBP/CAD proprii, verificate separat —
GBP Manufacturing/Services PMI real: 08:30/09:30 UTC; CAD Manufacturing PMI
real: 13:30/14:30 UTC):

| canonical_id | oră UTC | n |
|---|---|---|
| `gbp_s_p_global_cips_manufacturing_pmi` | 13:45 | 23 |
| `gbp_s_p_global_cips_services_pmi` | 13:45 | 22 |
| `gbp_s_p_global_cips_manufacturing_pmi` | 14:45 | 13 |
| `gbp_s_p_global_cips_services_pmi` | 14:45 | 15 |
| `gbp_s_p_global_cips_manufacturing_pmi` | 12:30 | 1 |
| `cad_s_p_global_manufacturing_pmi` | 12:30 | 1 |
| `gbp_s_p_global_cips_manufacturing_pmi` | 21:00 | 1 |

Clusterul de 13:45/14:45 (75 din cele 76) nu se potrivește cu nicio valută
din cele 8 aflate în scop — nici cu ora reală GBP, nici cu CAD, nici cu
EUR/JPY/CHF.

### Investigație separată — ipoteza US (S&P Global Final PMI)

**Punctul 1 — oră locală America/New_York, verificat cu `zoneinfo`:**
convertind cele 76 de rânduri neidentificate din UTC în `America/New_York`
(DST-aware): **73 din 76 converg exact pe 09:45 local** (36 din clusterul
GBP-Manufacturing + 37 din clusterul GBP-Services); restul de 3 sunt outlieri
singulari (7:30, 8:30, 17:00 ET) fără tipar. Verificat pe lunile reale ale
fiecărei ore UTC: rândurile de **13:45 UTC** cad în aprilie–noiembrie (EDT,
UTC-4) → **09:45 ET**; rândurile de **14:45 UTC** cad în ianuarie–martie +
noiembrie–decembrie (EST, UTC-5) → **09:45 ET**. Ambele sezoane converg pe
aceeași oră locală fixă — consistent cu ora publică cunoscută a S&P Global
Final Manufacturing/Services PMI pentru SUA (9:45 AM ET), distinctă de ISM
(10:00 AM ET). Ipoteza orei US e **confirmată pe punctul 1**.

**Punctul 2 — copii proprii USD, ca la EUR/JPY/CHF:** **nu există.**
Căutat explicit `currency=='USD' & name_canonical.str.contains('S&P Global')`
atât în parquet-ul live, cât și în arhiva completă re-parsată — **0 rânduri
în ambele.** Spre deosebire de EUR/JPY/CHF (unde JBlanked a început să emită
copii corect etichetate din ianuarie 2026, permițând validarea prin
suprapunere), pentru USD **nu există niciodată o copie proprie S&P Global
PMI în datele disponibile** — nu doar sub prag, ci absentă complet.
**Metoda de validare prin suprapunere cerută la punctul 2 nu se poate aplica
aici — nu din lipsă de coincidență, ci din lipsă de date de comparat.**

**Punctul 3 — canonic în taxonomie:** confirmat, **nu există**. Matcher-ul
`United States` (`data/economic_indicators.yaml`) are exact 15 reguli,
DOAR `^ISM Manufacturing PMI$` → `manufacturing_pmi` și `^ISM Non-
Manufacturing PMI$` → `services_pmi` pentru PMI — nicio regulă pentru
"S&P Global Manufacturing/Services PMI". O re-atribuire ar cere un
`indicator_key` NOU (nu se poate repunta pe `manufacturing_pmi`/
`services_pmi` existent — ar amesteca două serii cu scale/distribuții
diferite, ISM vs S&P Global, sub o singură bază de sigma, exact capcana
deja documentată pentru alte serii). **Asta e o decizie de taxonomie
(indicator nou + regulă de matcher nouă + pondere în categoria growth),
nu o reatribuire de date — în afara scopului acestei faze.**

**Concluzie: per regula ta explicită ("dacă nu sunt US sau nu se poate
valida prin suprapunere, rămân neatribuite") — ora locală confirmă ipoteza
US (punctul 1), dar validarea prin suprapunere e imposibilă (punctul 2, nu
eșuată — absentă), iar chiar dacă ar fi validată, nu există un slot de
atribuit fără o decizie de taxonomie (punctul 3). Aceste 76 de rânduri
RĂMÂN NEREATRIBUITE. Punctul 4 (re-măsurare pe 215 rânduri) nu se aplică —
condiția lui nu e îndeplinită.**

Rămân purjate cum sunt azi. Dacă se decide vreodată promovarea unui canonic
USD S&P Global PMI (decizie de taxonomie, separată), aceste 76 de rânduri
ar deveni istoricul lui de backfill — păstrate identificate, nu șterse din
analiză, doar nescrise acum.

> **FIR DESCHIS — 76 rânduri, identificate dar nereatribuite.** Ora locală
> converge pe 09:45 America/New_York (73/76, confirmat programatic) —
> semnătura S&P Global Final Manufacturing/Services PMI pentru SUA. Nu s-au
> reatribuit din două motive: (1) nicio copie proprie USD S&P Global PMI nu
> există în parquet sau arhivă pentru validare prin suprapunere (spre
> deosebire de EUR/JPY/CHF), (2) taxonomia n-are niciun `indicator_key`
> pentru asta — matcher-ul `United States` mapează PMI doar pe ISM.
> Rămân purjate. **Redeschiderea cere o decizie separată**: dacă USD
> primește un canonic S&P Global PMI distinct de ISM (indicator nou +
> regulă de matcher + pondere în categoria growth) — nu s-a decis aici.
> Identificate în `docs/pmi-219-classified.csv` (rândurile cu
> `reatt_ccy` gol și `hm` în {13.75, 14.75, 12.5, 21.0}), pentru cine
> reia firul.

## Setul final de re-atribuire

219 total → 76 neidentificate (lăsate deoparte) → 143 identificate (CHF/JPY/
EUR, confirmate pe oră locală) → dintre acestea, 4 sunt duplicate ianuarie-
2023 (excluse) → **139 rânduri finale de re-atribuit**:

| valută | indicator | rânduri re-atribuite |
|---|---|---|
| CHF | manufacturing_pmi | 37 |
| JPY | manufacturing_pmi | 32 |
| EUR | manufacturing_pmi | 35 |
| EUR | services_pmi | 35 |

Listă completă: `docs/pmi-reattribution-candidates.csv`.

## Măsurare înainte → după (simulat, nescris pe disc)

| serie | rows | n_pairs | mean surprise | sigma | scor celulă |
|---|---|---|---|---|---|
| CHF manufacturing_pmi | 7→**40** | 7→**12** | 0.729→1.075 | 3.619→3.073 | 0→0 (neschimbat) |
| JPY manufacturing_pmi | 7→**36** | 7→**12** | 0.714→0.367 | 1.759→1.371 | 0→0 (neschimbat) |
| EUR manufacturing_pmi | 7→**40** | 7→**12** | 0.629→0.375 | 0.842→0.702 | 0→0 (neschimbat) |
| EUR services_pmi | 7→**41** | 7→**12** | -0.500→-0.258 | 1.342→1.055 | **1→2** |

Spre deosebire de FAZA 2 (inertă — duplicatele erau prea vechi pentru
fereastra trailing-12), **aici efectul e real**: toate patru serii aveau
azi doar 7 perechi disponibile (sub fereastra completă de 12), deci
re-atribuirea COMPLETEAZĂ fereastra la 12 perechi reale în loc de 7 — sigma
se recalculează pe o bază mai bogată, nu doar se adaugă istoric mort.

**EUR services_pmi trece pragul de bucket** (scor 1→2, sigma scade de la
1.342 la 1.055, aceeași magnitudine de surpriză cade acum într-un bucket mai
extrem).

### Categorie afectată

`EUR growth`: `score_precise` 0.75 → 1.0 (coverage neschimbat, 4) — tras în
sus de `services_pmi` 1→2.

### Bias flip — 1 pereche

| pereche | înainte | după |
|---|---|---|
| **EURNZD** | Bearish (-1.181) | **Neutral (-0.972)** |

### Alte 6 instrumente își schimbă scorul (fără flip de bias)

EURUSD (1.667→1.875), EURGBP (0.069→0.278), EURJPY (-0.208→0.0), EURCHF
(-0.764→-0.556), EURAUD (-0.347→-0.139), EURCAD (0.069→0.278) — toate
perechile cu picior EUR, consecință directă a mutării categoriei `growth`
EUR.

## Scris — confirmat de George, executat

`migrations/2026-07-31_reattribute_pmi_rows.py`: backup
`data/economic_calendar_ff.parquet.pre-pmi-reattribution` creat, cele 139
rânduri relabelate ADĂUGATE (nu suprascrise) la parquet-ul live —
2889 → 3028 rânduri, delta exact 139. Gardă PMI verificată neschimbată
înainte și după (43/43/44). Cele 76 de rânduri neidentificate + cele 4
duplicate ian.2023 rămân exact cum erau, nescrise, nedatinse. 430 teste
verzi după scriere.

## Reproducere

```bash
.venv/bin/python3 scripts/measure/pmi_reattribution_before_after.py
```
