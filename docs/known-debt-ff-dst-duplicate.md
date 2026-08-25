# Cunoscut, neremediat: duplicat ±1h pe partea FF (merge_weekly)

FAZA 1I, Partea 2. Statut: **debt cunoscută, nu se repară acum**. Vezi
`docs/faza1i-report.md` pentru diagnosticul complet și fixul de afișare aplicat.

## Ce e

`src/ff_refresh.py::merge_weekly` — funcția care construiește/menține
`data/economic_calendar_ff.parquet` — dedupe pe cheia **exactă**
`(canonical_id, datetime_utc)`. Dacă feedul FF livrează același eveniment sub
DOUĂ valori `datetime_utc` diferite (de regulă la ~1h distanță — un artefact de
tip DST, aceeași familie ca cel deja documentat pentru JBlanked în
`src/jb_actuals.py::clean_jblanked_actuals`, dar pe altă sursă), `merge_weekly`
le tratează ca două rânduri distincte, permanent, în parquet-ul canonic.

Confirmat pe date reale: 55 de grupuri `(canonical_id, dată calendaristică)`
cu un rând la ≤1h distanță, pe tot parquet-ul, toate valutele. La nivelul
rândurilor brute (unele grupuri au 3 rânduri, deci mai multe perechi):
**31 rânduri sunt duplicate identice** (actual/forecast/previous exact
egale — același eveniment, înregistrat de două ori) și **26 perechi rămân
divergente** (conflicte reale între două livrări ale aceluiași eveniment
nominal, valori diferite — vezi mai jos).

## De ce nu se repară acum

1. **Impact zero în scoring.** `_dedup_flash_final` (calea de scoring, în
   `economic_compute.py`) cade pe clustering după proximitate de dată (acest
   feed nu are coloană `period` MT5 utilizabilă) — orice `dedup_gap_days`
   (minim 3 zile) e mult mai larg decât 1 oră, deci cele două rânduri se
   colapsează oricum la scoring. Verificat: în toate cele 31 de rânduri cu
   valori identice, nu contează care rând "câștigă" clusterul.
2. **Valorile sunt identice.** Acolo unde duplicatul real există, ambele
   copii poartă exact aceleași actual/forecast/previous — nu e un conflict
   de date, e un artefact de livrare dublă.
3. **Risc de ingestion nejustificat.** A repara la sursă (în `merge_weekly`)
   înseamnă lărgirea cheii de dedup dintr-o potrivire exactă la una tolerantă
   la ±1h — o schimbare în calea de scoring (`ff_refresh.py` alimentează
   direct `data/economic_calendar_ff.parquet`, consumat de
   `to_scoring_frame`/`compute_indicator_score`). Orice schimbare acolo
   necesită gate explicit (constrângere FAZA 1E-1I), și beneficiul (curățenie
   cosmetică pe o pagină nouă) nu justifică riscul de a atinge calea de
   producție pentru un impact deja confirmat nul.

## Ce s-a reparat în loc (FAZA 1I, `src/history_compute.py`)

Un filtru **doar de afișare**, `_drop_display_duplicate_rows`, aplicat în
`build_full_frame` — operează pe o copie, nu atinge `data/economic_calendar_ff
.parquet` și nu e importat de `economic_render.py`. Colapsează o pereche DOAR
când același `canonical_id`, aceeași dată calendaristică, sub 1h distanță, ȘI
actual/forecast/previous sunt identice pe ambele. Orice diferență de valoare
→ ambele rânduri păstrate, log de avertizare (nu e un duplicat, e un conflict
real între două livrări ale aceluiași eveniment nominal).

`apply_overrides` continuă să primească `ff`-ul ORIGINAL, nededuplicat — o
corecție manuală poate fi înregistrată exact pe timestamp-ul unui rând pe care
acest filtru l-ar elimina altfel (găsit empiric: JPY BOJ 2026-07-31, override
la 03:11:00, coliza cu tripletul brut 02:30/02:50/03:11 — vezi
`tests/test_history_compute.py::test_build_full_frame_passes_the_undeduped_ff_to_apply_overrides`).

Rezultat: 17 bare dispar de pe `/history` (verificat precis, payload
before/after, zero bare adăugate — vezi `docs/faza1i-report.md`). Diferența
față de cele 31 de rânduri identice confirmate la nivel brut: restul privesc
fie perechi unde ambele rânduri colapsează într-un singur canonical_id fără
a corespunde unui indicator afișat pe `/history` (catalogul,
`data/econ_catalog.yml`, nu afișează deloc, de exemplu, `ppi_yoy` pentru
CHF sau `industrial_production_mm`/`retail_sales` pentru GBP sub growth —
nu exista bară de eliminat acolo), fie cazuri unde 2-3 rânduri brute
colapsează la un singur punct pe pagină (un singur bar dispărut poate
proveni din eliminarea a 2 rânduri brute, ca la JPY BOJ 2026-07-31).

## Dacă se decide vreodată să se repare la sursă

Fix propus (neaplicat): lărgirea cheii de dedup din `merge_weekly` la
`(canonical_id, dată calendaristică ± ~1-2h)`, pe modelul deja validat din
`clean_jblanked_actuals` (grupare pe dată, păstrează ultimul rând cu actual
real, realiniază la rândul de schedule existent). Necesită: gate explicit,
re-verificare non-regresie pe `/economic`+`/strength`, și o decizie separată
despre ce se întâmplă cu override-urile manuale existente care ar putea fi
ancorate pe unul din rândurile care ar dispărea.
