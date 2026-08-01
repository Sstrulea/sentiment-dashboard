# Corelația surprizelor macro cu mișcarea prețului — nefezabilă cu datele disponibile

Status: **FAZA 1 încheiată — măsurarea NU e fezabilă, oprit înainte de
FAZA 2, per instrucțiunea explicită a task-ului.** Branch
`measure/surprise-price-link`, worktree `../macro-dev`. Zero cod de
producție, zero config — investigație în `scripts/measure/`.

## Verdict

**Nu se poate măsura util cu datele actuale.** Doar **7 perechi
(indicator, instrument)** ating pragul de ≥30 observații suprapuse —
sub pragul de ~10 perechi cerut explicit pentru a continua — și cele 7
sunt **un singur indicator** (`USD jobless_claims`, cadență săptămânală)
combinat cu cele 7 instrumente unde USD e bază sau cotă. Zero perechi
apropiate de prag (nimic în intervalul 25-29). Asta nu e o măsurătoare
diagnostică pe pilonul fundamental — e o singură serie, nu un eșantion.
**FAZA 2 nu a rulat.** Niciun cod de scoring, config, sau taxonomie
atins.

## De ce — constrângerea de fereastră, confirmată exact cum a prezis task-ul

`data/price_history.parquet` acoperă instrumentele FX de la
**2024-12-11/12/13** (excepție: FTSE100 din 2021, dar e cross-asset,
în afara scopului) până la 2026-07-31 — **19.6 luni**. Pentru un
indicator:

- **săptămânal** (doar `jobless_claims`, singurul din taxonomie): ~85
  printuri posibile în 19.6 luni → suficient, de-aici cele 7 perechi.
- **lunar** (majoritatea celor ~26 indicatori): ~19-22 printuri posibile
  → **sub pragul de 30, mereu, structural**, indiferent de aliniere.
- **trimestrial** (GDP, capex, productivitate etc.): ~6-8 printuri
  posibile → exact cele "~7 observații — insuficient" prezise în task.

Verificat direct, nu presupus: cel mai bun candidat NON-săptămânal e
`CHF retail_sales` cu 22-23 observații suprapuse — sub prag cu o marjă
clară, nu la limită. Fereastra de preț ar trebui să crească cu încă
~8-10 luni (spre mijlocul lui 2027) înainte ca indicatorii lunari să
apropie pragul de 30, și mult mai mult pentru cei trimestriali.

## Verificările FAZA 1, în ordine

### 1. Suprapunerea temporală

679 de perechi (indicator, instrument) au fost calculate (fiecare din
cele ~26 `indicator_key` × instrumentele FX relevante pentru valuta
lui). Detaliu complet: `docs/surprise-price-overlap.csv`. Top 25 e
dominat de `jobless_claims` (79-80 obs.) apoi cade direct la
`retail_sales`(CHF, 22-23) și `unemployment_rate`/`wage_growth` (GBP/EUR,
21) — o cădere abruptă, nu o pantă lină, exact tiparul pe care
cadența (săptămânal vs. lunar) îl prezice.

### 2. Pragul minim (≥30 observații)

**7 din 679 perechi** — toate `USD/jobless_claims`. Niciun alt indicator
nu se apropie. Rezultat identic (7, aceleași 7 perechi) sub ambele
alinieri de fus testate (vezi punctul 3) — alegerea de aliniere **nu
schimbă verdictul de fezabilitate**, deci nu a fost nevoie să fie
rezolvată definitiv pentru concluzia asta (rămâne totuși un risc real
pentru orice măsurare viitoare pe JPY/NZD specific, vezi mai jos).

### 3. Alinierea fuselor — o presupunere infirmată, nerezolvată complet

**Verificat, nu presupus — și rezultatul e ambiguitate reală, nu o
confirmare curată.** `src/price_fetch.py` documentează explicit că
`date` e "the daily bar's SERVER date" (data zilei barei D1, pe fusul
serverului MT5) — NU explicit UTC. Codul EA (`mt5/PriceHistoryExport.mq5`)
citește `CopyRates(sym, PERIOD_D1, ...)`, ale cărui timestamp-uri sunt pe
fusul serverului, nu UTC.

Distribuția orei de release (UTC) pe valută arată o problemă concretă:
**JPY are 80% din evenimente la ora ≥21 UTC** (413 din 539 la ora exactă
23), **NZD 87%** (181 din 208 la orele 21-22). Un decalaj de server de
+2/+3h (convenție comună la brokerii MT5, aceeași folosită deja de
feed-ul JBlanked/FF în acest proiect — `docs/spike-report-jblanked.md`)
ar muta aceste evenimente în ziua calendaristică URMĂTOARE față de data
UTC brută.

Am încercat să verific empiric care aliniere e corectă:
- **Test agregat** (corelația Spearman între |surpriză normalizată| și
  range-ul zilnic USDJPY, pe toate evenimentele JPY, ambele alinieri):
  ambele ies aproape zero (-0.058 naiv, -0.080 EET) — neconcludent,
  amestecă indicatori de calitate foarte diferită.
- **Test pe un eveniment concret** (cea mai mare surpriză reală Tokyo
  Core CPI din fereastra de preț, 2025-09-25 23:30 UTC, -0.8 sub
  consens): bara USDJPY din ziua respectivă ȘI din ziua următoare arată
  amândouă mișcare, dar contopită cu o tendință mai largă de-a lungul
  săptămânii — **exact dizolvarea la care task-ul se aștepta**, imposibil
  de atribuit vizual unei singure publicări.

**Concluzie onestă**: nu am putut confirma sau infirma alinierea corectă
pentru JPY/NZD cu efort rezonabil — verificarea circulară (am nevoie de
un semnal curat ca să confirm alinierea, dar diluarea D1 e exact
problema pe care task-ul o semnalează). Pentru pragul de fezabilitate
nu a contat (7 perechi oricum, identic sub ambele alinieri) — dar dacă
fereastra de preț crește suficient încât JPY/NZD să devină testabile pe
viitor, **alinierea trebuie rezolvată înainte, nu presupusă** — 80-87%
din evenimentele acestor două valute sunt exact în zona ambiguă.

## Ce NU spune această măsurare

Per instrucțiune: **o corelație prezentă ar fi informativă, una absentă
e neconcludentă** — dar aici nu am ajuns nici măcar la "absentă", am
oprit la "nemăsurabilă cu încredere". Nu pot spune:
- că niciun indicator macro nu mișcă prețul (fereastra e prea scurtă
  pentru majoritatea, nu am testat suficient de multe printuri per
  indicator ca să disting semnal de zgomot)
- că `jobless_claims` (singurul testabil) chiar corelează sau nu — nu
  am rulat testul, per regula "oprește-te înainte de FAZA 2"
- nimic despre JPY/NZD specific — alinierea rămâne nerezolvată

## Recomandare pentru viitor

Măsurarea devine parțial fezabilă (pentru indicatorii lunari) pe măsură
ce `data/price_history.parquet` acumulează mai multe luni — reverifică
pragul de 30 peste ~8-10 luni. Pentru indicatorii trimestriali, ar avea
nevoie de ani. Pentru JPY/NZD specific, alinierea de fus a prețului D1
trebuie confirmată direct cu brokerul/EA înainte de orice măsurare —
nu poate fi dedusă din date cu încredere, cum s-a verificat aici.

## Ce NU s-a făcut (conform scope)

- FAZA 2 nu a rulat — nicio corelație surpriză↔preț calculată.
- Nicio pre-înregistrare de predicții scrisă — nu se aplică, FAZA 2 nu
  a pornit.
- Nicio modificare de scoring, ponderi, taxonomie.
- Housing, monetary, garda PMI, cele 18 perechi cu unitate greșită,
  `can_be_zero`, `.github/workflows/`, `src/jb_actuals.py`,
  `src/ff_refresh.py`, cross-asset — neatinse.

## Livrabile

- `docs/measurement-surprise-price-link.md` — acest document.
- `docs/surprise-price-overlap.csv` — toate cele 679 de perechi
  (indicator, instrument), cu suprapunerea sub ambele alinieri.
- `scripts/measure/surprise_price_feasibility.py` — instrumentar FAZA 1.
