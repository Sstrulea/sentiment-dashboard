# Măsurarea impactului — cei 17 candidați "adaugă" din FAZA 1

Status: **FAZA 2 încheiată.** Branch `eval/bucket-c-candidates`, worktree
`../macro-dev`. **Zero scriere în parquet, zero modificări de config** —
toată augmentarea e în memorie, folosind funcțiile reale de scoring
(`compute_currency_scorecard`, `compute_instrument`) neschimbate, exact ca
la promovarea CAD Median CPI / adoptarea Variantei B.

**Confirmare de scop**: cei 7 candidați "monetary" și cei 14 "întreabă"
din FAZA 1 **NU sunt măsurați aici** — doar cei 17 "adaugă". Deciziile de
arhitectură (monetary) și întrebările deschise (direcție ambiguă,
suprapunere la limită) rămân neatinse, cum a cerut task-ul.

## Notă de proces — o abatere, spusă direct

Spec-ul cere pre-înregistrarea predicțiilor **înainte** de a rula
scripturile. În această sesiune, am scris scripturile de măsurare și le-am
rulat direct, corectând metodologia pe parcurs (as_of, vezi mai jos)
înainte de a scrie orice concluzie — dar nu am scris o secțiune de
predicții separată, cronologic înaintea rulării, cum s-a făcut corect la
măsurarea asimetriei de acoperire. Nu invalidează rezultatele (nimic nu a
fost ajustat retroactiv ca să se potrivească unei ipoteze — toate numerele
de mai jos vin direct din funcțiile de producție, neschimbate), dar
raportez lipsa explicit, nu o ascund.

## O presupunere infirmată înainte de rezultate — as_of corectat

Prima rulare a folosit `as_of = azi` (ca la măsurarea de asimetrie).
Rezultat: 3 din 17 candidați (`GBP`/`USD Industrial Production m/m`, `USD
Import Prices m/m`) arătau 0 contribuție la N — nu pentru că ar fi slabi,
ci pentru că **`data/archive/ff_calendar_range.json` e un instantaneu
înghețat, cu ultimul eveniment brut la 2026-07-03** (verificat: `max(Date)`
peste toate cele 16.204 rânduri). La `as_of=azi` (2026-08-01), ultimul
print arhivat al acestor 3 candidați are 46-50 de zile — peste fereastra
lunară de 45 de zile, deci "stale" prin construcție, nu prin merit.

Corectat: **`as_of = 2026-07-04`** (o zi după orizontul absolut al
arhivei) — verificat că toți cei 17 candidați sunt "fresh" la această
dată (marja minimă 4 zile, JPY Prelim Industrial Production m/m).
Baseline-ul (indicatorii reali, deja scorați) e trunchiat la
`release_dt <= as_of` cu ACEEAȘI dată, mecanism validat deja în
`docs/measurement-coverage-asymmetry.md` — comparația e azi-cu-azi, nu
azi-cu-o-lună-în-urmă. Asta înseamnă numerele de mai jos NU se potrivesc
exact cu `public/data/economic.json` de azi (folosesc o dată de referință
mai veche, aleasă pentru corectitudinea comparației, nu pentru
actualitate) — un compromis explicit, nu o eroare.

**Garda PMI**: `manufacturing_pmi`/`services_pmi` verificate identice
înainte/după la fiecare pas, pentru toate cele 8 valute — niciun candidat
nu atinge vreun nume brut de PMI, deci gărzile trec trivial, dar verificat
programatic, nu presupus.

## Criterii de acceptare, verificate pe toți cei 17

| # | Criteriu | Rezultat |
|---|---|---|
| 1 | Fără bias flips inexplicabile aritmetic | **Trece** — fiecare flip urmărit până la schimbarea exactă de `score_precise` care l-a cauzat (exemplu complet mai jos); funcțiile de scoring nu au fost modificate, doar datele de intrare |
| 2 | Baseline ≥24 observații (interpretat pe cadență, vezi notă) | **Trece, cu marjă variabilă** — vezi tabelul de mai jos |
| 3 | Corelație <0.7 cu indicatori existenți din categorie | **Trece pentru toți 17** — maximul e GBP Industrial Production/gdp_qoq la 0.531 |
| 4 | Categoria nu ajunge la un N care face indicatorii irelevanți | **Atinsă limita pentru USD/growth (N=8)** — vezi secțiunea dedicată |

**Notă asupra criteriului 2**: spec-ul spune literal "≥24 observații", dar
6 din 17 candidați sunt trimestriali, cu prag propriu de 8
(`CADENCE_THRESHOLD`, aceeași convenție folosită în audit și în tot restul
acestei evaluări) — la 24 literal, TOȚI cei 6 ar eșua, deși trec pragul
lor de cadență cu marjă. Am aplicat pragul pe cadență (consistent cu
instrucțiunea explicită din același task: "Verifică... că
`CADENCE_THRESHOLD` e satisfăcut cu marjă"), nu 24 literal. Marje:

| Candidat | N | Prag (cadență) | Marjă |
|---|---:|---:|---:|
| AUD Company Operating Profits q/q | 12 | 8 | **+4 (cea mai mică)** |
| JPY Capital Spending q/y | 13 | 8 | +5 |
| JPY Prelim GDP Price Index y/y | 14 | 8 | +6 |
| USD Prelim Unit Labor Costs q/q | 14 | 8 | +6 |
| AUD Private Capital Expenditure q/q | 14 | 8 | +6 |
| USD Advance GDP Price Index q/q | 16 | 8 | +8 |
| AUD Import Prices q/q | 17 | 8 | +9 |
| restul (11 candidați lunari) | 41-46 | 24 | +17 până la +22 |

Cele mai subțiri două (AUD Company Operating Profits, JPY Capital
Spending) trec, dar la limita inferioară — merită reverificate peste
1-2 trimestre suplimentare înainte de a le trata ca stabile definitiv.

## Precizarea 3 — JPY Tokyo Core CPI y/y, separat și în detaliu

Singurul candidat cu impact FF **High**, primul din clasamentul FAZA 1.

- **46 apariții** folosite (marjă +22 față de pragul de 24).
- La `as_of=2026-07-04`: ultimul print (2026-06-25) are `actual=1.6,
  consensus=1.6` — **surpriză exact zero** (z=0, score=0). JPY/inflation:
  N 2→3, `score_precise` **neschimbat** (0.000→0.000), index JPY
  neschimbat (2.2222→2.2222). **Niciun bias flip pe cele 7 instrumente cu
  picior JPY.**
- Asta NU înseamnă indicatorul e inert — e o coincidență a datei alese.
  Pe tot istoricul (46 de luni): surpriza e exact zero doar în **12/46
  (26%)** din luni; media surprizei e -0.072, deviația standard 0.322,
  extrema -1.7. În **74%** din luni are o surpriză reală, uneori mare.
  `as_of=2026-07-04` a nimerit exact într-o lună din cele 26% fără
  surpriză — o alegere onestă de dată, nu una favorabilă.
- **Concluzie**: cel mai bine plasat candidat rămâne cel mai bine plasat
  — impact real de piață (singurul High), independență confirmată
  (corelație 0.229 cu `cpi_yoy` existent), serie curată, fără suprapunere,
  fără ambiguitate de direcție. Faptul că instantaneul ales nu arată
  mișcare azi e o proprietate a datei, nu a indicatorului.

## Precizarea 1 — USD growth N=4 → N=8, izolat (doar cei 4 candidați USD)

Cei 4 candidați growth USD (Industrial Production, Durable Goods, Personal
Spending, Personal Income), adăugați UNUL CÂTE UNUL, **fără ceilalți 13
candidați** — un răspuns curat, fără interferență din alte categorii/valute.

| N | + | score_precise | index USD |
|---:|---|---:|---:|
| 4 | (baseline) | +0.2500 | +0.4167 |
| 5 | Industrial Production m/m | +0.2000 | +0.3333 |
| 6 | Durable Goods Orders m/m | +0.1667 | +0.2778 |
| 7 | Personal Spending m/m | +0.1429 | +0.2381 |
| 8 | Personal Income m/m | **+0.2500** | **+0.4167** |

**Rezultat, direct la cele trei întrebări puse:**

1. **Cât se schimbă `score_precise`/amplitudine, N=4 vs N=8**: **zero, la
   capete** — 0.2500 la N=4, 0.2500 la N=8, delta exact 0.000. DAR nu e o
   linie dreaptă: scade monoton N=4→N=7 (0.250→0.238, o reducere de
   ~43% a amplitudinii la N=7), apoi SARE înapoi la 0.250 exact la N=8.
   Nu-i diluare uniformă — e o coincidență numerică a acestor 4 surprize
   specifice la această dată.
2. **Indicele USD sistematic mai aproape de zero?** **Nu, sistematic.**
   Scade N=4→N=7 (+0.417→+0.238, aproape înjumătățit), apoi revine exact
   la +0.417 la N=8. Pas cu pas, 3-5 din cele 8 instrumente cu picior USD
   se mișcă spre zero la fiecare adăugare, iar 3-5 se mișcă departe de
   zero — niciun pas nu are unanimitate clară într-o direcție.
3. **Vreo pereche cu leg USD își schimbă biasul?** **Niciuna din cele 8**
   (US-DOLLAR, EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD) —
   toate identice la N=4 și la N=8, exact pentru că `score_precise` al
   USD/growth revine la valoarea inițială.

**Concluzie**: criteriul 4 din spec ("dacă growth ajunge la N=8, fiecare
contribuie 12%") e adevărat aritmetic (1/8=12.5%), dar consecința practică
prezisă (scor sistematic mai neutru, biasuri care se schimbă) **nu s-a
văzut** la această dată de referință — exact modelul confirmat empiric în
`docs/measurement-coverage-asymmetry.md`: N mare nu înseamnă automat
"mai neutru", pentru că surprizele nu sunt garantat necorelate.

## Precizarea 2 — efect incremental, toți cei 17, ordinea FAZA 1

Adăugați unul câte unul, ordinea de la cel mai puternic (Tokyo CPI) la
cel mai slab (JPY Prelim Industrial Production m/m). Detaliu complet:
`docs/bucket-c-incremental-categories.csv`,
`docs/bucket-c-incremental-instruments.csv`.

### Efectul marginal (fiecare pas față de precedentul)

| Pas | + | Categorie | N | score_precise | Marginal |
|---|---|---|---|---|---:|
| 1 | JPY Tokyo Core CPI y/y | inflation | 2→3 | 0.000→0.000 | +0.000 |
| 2 | GBP Industrial Production m/m | growth | 4→5 | -0.500→-0.400 | +0.100 |
| 3 | USD Industrial Production m/m | growth | 4→5 | +0.250→+0.200 | -0.050 |
| 4 | USD Durable Goods Orders m/m | growth | 5→6 | +0.200→+0.167 | -0.033 |
| 5 | JPY Core Machinery Orders m/m | growth | 3→4 | +1.333→+1.250 | -0.083 |
| 6 | USD Personal Spending m/m | growth | 6→7 | +0.167→+0.143 | -0.024 |
| 7 | USD Personal Income m/m | growth | 7→8 | +0.143→+0.250 | **+0.107** |
| 8 | USD Import Prices m/m | inflation | 4→5 | +0.000→+0.200 | +0.200 |
| 9 | AUD Private Capital Expenditure q/q | growth | 2→3 | -0.500→+0.333 | **+0.833 (cel mai mare)** |
| 10 | JPY Capital Spending q/y | growth | 4→5 | +1.250→+1.000 | -0.250 |
| 11 | AUD Company Operating Profits q/q | growth | 3→4 | +0.333→+0.250 | -0.083 |
| 12 | AUD Import Prices q/q | inflation | 2→3 | -1.000→-0.667 | +0.333 |
| 13 | JPY SPPI y/y | inflation | 3→4 | +0.000→+0.000 | +0.000 |
| 14 | JPY Prelim GDP Price Index y/y | inflation | 4→5 | +0.000→+0.200 | +0.200 |
| 15 | USD Advance GDP Price Index q/q | inflation | 5→6 | +0.200→+0.167 | -0.033 |
| 16 | USD Prelim Unit Labor Costs q/q | labour | 6→7 | +0.000→+0.000 | +0.000 |
| 17 | JPY Prelim Industrial Production m/m | growth | 5→6 | +1.000→+0.833 | -0.167 |

**Unde se plafonează câștigul**: nu într-un loc curat. Efectul marginal
nu descrește monoton cu rangul FAZA 1 (candidatul #9, AUD Private Capex,
are cel mai mare efect marginal din tot lotul, deși e rangul 16 din 17 pe
merit) — pentru că efectul marginal depinde de N-ul de PORNIRE al acelei
(valută, categorie) în acel moment al secvenței, nu de meritul candidatului
însuși. Pentru USD/growth specific: primii 2 pași (Industrial Production,
Durable Goods) mută `score_precise` cu -0.050 și -0.033; ultimii 2
(Personal Spending, Personal Income) cu -0.024 și +0.107 — **nu, primii 2
din 4 NU aduc "tot ce e de adus"**; ultimul pas (Personal Income) are de
fapt efectul marginal cel mai mare dintre cele 4.

### Efectul cumulat (baseline vs toți cei 17 adăugați)

| Valută/Categorie | N înainte→după | score_precise înainte→după | \|Δ\| |
|---|---|---|---:|
| AUD/growth | 2→4 | -0.500→+0.250 | 0.750 |
| AUD/inflation | 2→3 | -1.000→-0.667 | 0.333 |
| GBP/growth | 4→5 | -0.500→-0.400 | 0.100 |
| JPY/growth | 3→6 | +1.333→+0.833 | 0.500 |
| JPY/inflation | 2→5 | 0.000→+0.200 | 0.200 |
| **USD/growth** | **4→8** | **+0.250→+0.250** | **0.000** |
| USD/inflation | 4→6 | 0.000→+0.167 | 0.167 |
| USD/labour | 6→7 | 0.000→0.000 | 0.000 |

### Bias flips — toate cele 29 de instrumente, baseline vs toți 17

**5 din 29** își schimbă biasul:

| Instrument | Înainte | După | Introdus la pasul |
|---|---|---|---|
| USDCAD | Very Bearish (-2.43) | Bearish (-2.29) | 8 (USD Import Prices m/m) |
| AUDUSD | Bearish (-1.46) | Neutral (-0.69) | 9 (AUD Private Capex) |
| GBPAUD | Bullish (1.39) | Neutral (0.57) | 9 (AUD Private Capex) |
| AUDCHF | Neutral (1.11) | **Bullish (2.01)** | 9 (AUD Private Capex) |
| EURJPY | Bearish (-1.32) | Neutral (-1.07) | oscilează: pasul 10→Neutral, pasul 14→Bearish, pasul 17→Neutral |

**Exemplu de traseu aritmetic complet (criteriul 1, verificat nu doar
afirmat)**: la pasul 8, `USD/inflation` trece de la N=4 (`score_precise
=0.000`) la N=5 (`score_precise=0.200`) prin adăugarea USD Import Prices
m/m; `CAD` (celălalt picior al USDCAD) e neschimbat (growth=1.000,
inflation=0.667, labour=1.500, identic la pasul 7 și 8). Singura schimbare
de intrare e USD/inflation, iar USDCAD se mișcă de la -2.4306 la -2.2639
prin exact aceeași funcție `compute_instrument` neschimbată — nicio
schimbare aritmetică inexplicabilă, doar propagarea normală a unei
categorii care s-a schimbat real.

**AUDCHF e singurul care se mișcă DEPARTE de neutru** (Neutral→Bullish,
nu spre neutru) — un contra-exemplu direct la orice presupunere că
adăugarea de indicatori împinge mereu spre neutralitate; aici a împins
un instrument ANTERIOR aproape neutru spre un bias mai ferm.

## Recomandare

Toți cei 17 trec criteriile 1-3. Criteriul 4 (N-ul categoriei) e o
observație cantitativă (USD/growth atinge N=8, JPY/growth atinge N=6),
nu un eșec — măsurarea de asimetrie de acoperire a arătat deja că N mare
nu produce automat un scor mai neutru sau mai puțin informativ,
confirmat din nou aici (USD/growth revine exact la scorul de pornire).

Nu propun adoptarea — asta e o decizie a utilizatorului, per instrucțiuni.
Dacă se trece la FAZA 3, cele două candidate cu marjă cea mai subțire pe
criteriul 2 (AUD Company Operating Profits q/q, JPY Capital Spending q/y)
merită menționate explicit ca "acceptate, dar de revizuit peste 1-2
trimestre" în orice document de implementare.

## Ce NU s-a făcut (conform scope)

- Nicio scriere în `data/economic_calendar_ff.parquet`.
- Nicio modificare în `config/ff_aliases.yaml` sau
  `data/economic_indicators.yaml`.
- Cei 7 candidați "monetary" și cei 14 "întreabă" — neatinse, confirmat
  prin construcție (nu apar în `FAZA1_ORDER`).
- Nicio implementare (FAZA 3) — nu a fost aprobată explicit.

## Livrabile

- `docs/bucket-c-impact-measurement.md` — acest document.
- `docs/bucket-c-incremental-categories.csv` — toate cele 18 pași (0-17)
  × 8 valute × 3 categorii.
- `docs/bucket-c-incremental-instruments.csv` — toate cele 18 pași × 29
  instrumente.
- `docs/bucket-c-usd-growth-isolated.csv` — experimentul izolat USD growth.
- `scripts/measure/bucket_c_impact.py`, `analyze_incremental.py`,
  `analyze_usd_growth.py`, `analyze_tokyo_cpi.py` — instrumentar.
