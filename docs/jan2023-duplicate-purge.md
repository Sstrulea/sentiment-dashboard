# FAZA 2 — purja duplicatelor ianuarie 2023 (MĂSURARE, nescris în parquet)

Status: **măsurare, zero scriere**. Branch `fix/remaining-data-defects`,
worktree `../macro-dev`. `data/economic_calendar_ff.parquet` NEATINS —
toate numerele de mai jos vin din interogări read-only + o copie în memorie
cu rândurile candidate scoase, nescrisă niciodată pe disc.
**STOP înainte de scriere, cum s-a cerut — aștept confirmare.**

## Adăugare sau înlocuire — verificat pe toate seriile atinse (>30, nu doar 10)

Pentru fiecare (valută, canonical_id) implicat, am listat TOATE rândurile
ianuarie/februarie 2023 din arhivă. **În fiecare caz verificat, rândul
duplicat coexistă cu alte rânduri reale, distincte, din aceeași lună** — de
exemplu:

- `AUD CPI y/y`: 4 rânduri în ian 2023 (10, 11, 25, 31) — 2 sunt duplicate
  (10 și 31), 2 sunt printuri reale distincte (11, 25).
- `CAD Manufacturing PMI`: pe 2023-01-03 apar DOUĂ rânduri la ore diferite —
  08:30 (duplicatul, actual=forecast=43.0, suspect chiar el) și 14:30
  (49.2/0.0, alt rând). Acesta e unul din cele 6 rânduri care se suprapun cu
  contaminarea PMI (secțiunea următoare).
- `GBP Final Manufacturing/Services PMI`: 8-10 rânduri fiecare în ian/feb
  2023, cu multiple ore pe aceeași zi — tipar deja cunoscut din purja PMI.

**Concluzie: adăugare, nu înlocuire, confirmată consecvent.** Nicio serie
verificată nu arată un gol în locul printului real — duplicatul e mereu un
rând ÎN PLUS.

## Dependența cu FAZA 3 — rezolvată prin excludere, nu prin secvențiere

Din cele 72 de perechi găsite în arhivă cu criteriul strict, **6 implică unul
din cele 3 `canonical_id` contaminate de purja PMI din 30 iulie**
(`gbp_s_p_global_cips_manufacturing_pmi`, `gbp_s_p_global_cips_services_pmi`,
`cad_s_p_global_manufacturing_pmi`). Le-am **exclus explicit din această
fază** — ele aparțin metodologiei FAZA 3 (atribuire după ora locală), nu
criteriului „valoare identică peste un an" de aici; amestecarea celor două
ar risca fie o dublă-purjare, fie o clasificare greșită a unui rând care are
nevoie de re-atribuire, nu de ștergere. FAZA 3 va re-aplica acest FILTRU
STRICT (nu doar exclude aceste 6) pe subsetul PMI din arhivă înainte de
re-atribuire, exact cum a cerut task-ul.

**Pe parquet-ul LIVE** (nu arhivă): din cele 72, doar 65 sunt prezente azi (7
lipsesc — 5 din cele 6 PMI, deja purjate pe 30 iulie, plus `CAD Common/
Trimmed CPI y/y`, serii care n-au fost niciodată backfilled pe live, deci n-au
niciun rând de purjat). Scoțând și cel de-al 6-lea rând PMI (încă prezent,
`GBP Final Services PMI` 09:30 2023-01-04 — probabil printul real UK, păstrat
corect de purja din 30 iulie): **64 rânduri candidate, curate, pe parquet-ul
live**.

Un rând suplimentar (`CAD Core CPI m/m`, 2023-01-16) e candidat la purjare
dar **nu mai are niciun `indicator_key`** — matcher-ul Canada a fost
repunctat spre `Median CPI y/y` în branch-ul anterior
(`fix/no-consensus-and-promotions`), deci acest rând stă deja neatribuit,
fără impact de scoring în ambele sensuri. Inclus în lista de purjare (curăță
date brute), dar irelevant pentru măsurarea de mai jos.

## Criteriul strict, aplicat

Aceeași valută + `name_raw` + `actual` + `forecast` + ziua din lună +
exact un an mai târziu (ian/feb 2023 → ian/feb 2024). Nicio potrivire
parțială. Rezultat: **64 rânduri candidate** pe parquet-ul live (listă
completă: `docs/jan2023-purge-candidates-live-parquet.csv`).

## Măsurare înainte → după (simulat în memorie, negrăvat pe disc)

**43 de perechi (valută, indicator) scorate afectate.** Pentru fiecare:
`n_rows` (total actual valid), `n_pairs`/`mean`/`sigma` (fereastra
trailing-12) și scorul celulei celei mai recente, înainte → după.

Rezultatul e uniform: **`n_rows` scade cu 1 pe fiecare serie atinsă (exact
rândul șters); `n_pairs`, `mean`, `sigma` și scorul celulei rămân
IDENTICE** pentru toate cele 43 de perechi, CU O SINGURĂ EXCEPȚIE:

| valută | indicator | n_rows înainte→după | sigma înainte→după | scor celulă |
|---|---|---|---|---|
| **AUD** | **core_cpi** | 13→12 | **0.16026 → 0.17056** | 1 → 1 (neschimbat) |
| (restul, 42 perechi) | — | -1 fiecare | **neschimbat, bit-identic** | **neschimbat** |

**De ce doar AUD core_cpi mișcă sigma**: e singura serie cu adevărat
trimestrială (RBA Trimmed Mean CPI, 4 printuri/an) suficient de rară încât
fereastra trailing-12 (perechi, nu luni) ajunge înapoi până în ianuarie
2023 (~3 ani). Toate celelalte serii "q/q" din listă (CAD/GBP GDP) sunt de
fapt alimentate lunar (`GDP m/m` aliniat), deci fereastra lor de 12 perechi
nu ajunge nici măcar la un an în urmă — rândul din 2023 e mult în afara
oricărei ferestre folosite azi. **Chiar și pentru AUD core_cpi, sigma se
mișcă (µ nu se schimbă, era deja ~0), dar NU suficient cât să treacă un prag
de bucket — scorul celulei rămâne 1 înainte și după.**

**Notă încrucișată cu FAZA 1**: rândul candidat `EUR Prelim Flash GDP q/q`
(2023-01-30) are `actual=0.0` — deja unul din cele 3 rânduri EUR gdp_qoq
semnalate ca „suspect" în inventarul zero-placeholder. Fiindcă rândul era
deja carantinat (actual nulat la scoring, independent de purjă), `n_rows`
pentru EUR gdp_qoq **nu scade** la scoring (rândul era deja invizibil
scoring-ului înainte de orice purjă) — coincidență între cele două defecte,
nu o eroare de măsurare.

## Categorii, index, perechi — 0 schimbări

- **Nicio celulă de categorie nu se schimbă** (verificat pe toate valutele
  afectate — AUD, CAD, CHF, EUR, GBP, JPY, NZD, USD).
- **0 din cele 29 instrumente (28 perechi + US-DOLLAR) își schimbă scorul**,
  nu doar biasul — bit-identic înainte/după, peste tot.

Motivul e structural, nu coincidență: fereastra trailing-12 (perechi) a
fiecărei serii scorate azi nu ajunge, cu o singură excepție marginală, până
în ianuarie 2023 — rândurile duplicate sunt prea vechi ca să conteze pentru
orice scor curent. Purjarea e corectă istoric (curăță o eroare de date reală)
dar **inertă pentru scoring-ul de azi**.

## Ce urmează, dacă se confirmă

1. Backup `data/economic_calendar_ff.parquet.pre-jan2023-purge` — creat
   imediat înainte de scriere (nu încă — nimic scris până la confirmare).
2. Șterge cele 64 rânduri din `docs/jan2023-purge-candidates-live-parquet.csv`
   (identificate prin `(canonical_id, datetime_utc)`, cheie exactă, nicio
   potrivire parțială).
3. Cele 6 rânduri PMI-contaminate rămân neatinse aici — tratate în FAZA 3.

## Reproducere

```bash
.venv/bin/python3 scripts/measure/jan2023_duplicates.py           # găsire pe arhivă (read-only)
.venv/bin/python3 scripts/measure/jan2023_purge_before_after.py   # măsurare pe parquet-ul live (read-only)
```
