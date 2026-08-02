# FAZA 1 — `can_be_zero: true` cu placeholder în date

Status: **investigație încheiată, zero cod.** Branch `fix/dedup-and-zero-guards`,
worktree `../macro-dev`.

## Rezumat

Criteriul propus inițial (`|consensus| > k·σ`, unde σ e sigma surprizei seriei)
**e infirmat de date** — nu prinde exact cazul-ancoră care l-a motivat
(USD NFP 2025-10-02), la niciun prag rezonabil. Un al doilea criteriu testat
(z-ul nivelului `0` față de distribuția valorilor reale) e la fel de slab —
nu discriminează per-rând deloc.

Ce funcționează, verificat pe date reale: **corroborarea prin gol de cadență
cross-indicator** — mai multe serii americane independente (jobless claims,
JOLTS, ADP) arată exact aceeași fereastră de întrerupere ca NFP-ul suspect.
Nu e o coincidență statistică pe un singur rând, e un fapt structural
verificabil independent. Recomand construirea acestui criteriu într-o fază
viitoare, nu azi.

Găsite pe parcurs, separate de întrebarea inițială: un bug activ de ordonare
în `_dedup_flash_final` (JPY Retail Sales, aprilie 2026 — valoare reală
înlocuită de un placeholder mai nou) și un pattern distinct la
`interest_rate_decision` JPY (întârziere de feed la BOJ, 9 din 31 rânduri
afectate, dar indicatorul e display-only, deci fără impact de scor).

## Metodologie

Toate numerele de mai jos vin din calea reală de producție: parquet →
`to_scoring_frame` → excludere carantină (`ff_quarantine.parquet`, gol la
momentul investigației) → `_dedup_flash_final` (exact cum face
`compute_indicator_score`) → `compute_indicator_score`/`build_payload`.
Fără asta, numărătoarea brută pe parquet supraestimează problema — vezi
mai jos cât rezolvă deja dedup-ul.

## 1. Inventar brut vs. ce ajunge efectiv la scoring

Rânduri cu `actual == 0.0`, pe cele trei indicatoare cu `can_be_zero: true`
relevante aici (`household_spending` e display-only, exclus — task separat):

| indicator | rânduri brute (actual==0.0) | rânduri care SUPRAVIEȚUIESC dedup-ului |
|---|---|---|
| `employment_change` | 8 | 7 |
| `retail_sales` | 26 | 20 |
| `interest_rate_decision` | 11 | 11 |

`_dedup_flash_final` (existent, folosit deja de `compute_indicator_score`)
rezolvă corect 1 din 8 rânduri `employment_change` și 6 din 26 rânduri
`retail_sales` — toate cazuri unde un rând-fantomă cu `actual=forecast=
previous=0.0` apare la 1-9 ore/zile de rândul real corect, iar dedup-ul
alege corect rândul cu `release_dt` mai târziu (ex. AUD employment change:
fantomă 2024-01-16 → real 2024-01-18, `-65.1`). Acesta e exact mecanismul pe
care FAZA 2 îl investighează separat pentru cazul CHF/GBP — confirmă aceeași
ipoteză aici, pentru alți indicatori.

`interest_rate_decision` nu beneficiază deloc de dedup (0/11 rezolvate) —
rândurile suspecte sunt la 1-3 luni distanță una de alta (întâlniri BOJ
diferite), deci nu se grupează sub `dedup_gap_days.monthly=18`.

**7 + 20 + 11 = 38 de rânduri rămân cu `actual=0.0` după calea reală de
producție.** Astea sunt candidații reali pentru orice carantină nouă.

## 2. Cazul-ancoră: USD NFP 2025-10-02

```
USD employment_change  2025-10-02  actual=0.0  forecast=52.0  previous=22.0
```

Confirmat activ azi: acest rând e al 6-lea din fereastra `surprise_window_k=12`
folosită pentru sigma NFP curentă (mai aproape de coadă decât în sesiunea
anterioară, pe măsură ce timpul trece rândul se va scoate singur din
fereastră — dar încă înăuntru acum).

### Corroborare structurală (nu statistică)

Verificat pe alte serii americane independente, în aceeași fereastră
(sept–nov 2025):

| serie | cadență normală | gol observat |
|---|---|---|
| `usd_jobless_claims` (săptămânal) | 6-8 zile | **53 zile** (2025-09-25 → 2025-11-18) |
| `usd_jolts` (lunar) | ~28 zile | **69 zile** (2025-09-30 → 2025-12-08) |
| `usd_adp` (lunar) | ~28 zile | print **lipsă complet** la 2025-10-01 (`actual=NaN`) |
| `usd_nonfarm_payrolls` | ~28 zile | print următor la 48 zile (2025-10-02 → 2025-11-20) |

Patru serii americane independente, publicate de agenții diferite
(BLS pentru claims/NFP, JOLTS tot BLS dar echipă diferită, ADP privat),
arată aceeași fereastră de întrerupere. Asta e shutdown-ul guvernamental
SUA oct-nov 2025, confirmat independent de forma calendarului, nu de
plauzibilitatea valorii `0.0` în sine.

### De ce contează distincția

Un test de plauzibilitate bazat pe valoare (surpriză/σ sau nivel/σ) tratează
fiecare rând izolat. Corroborarea prin gol de cadență tratează evenimentul
ca ceea ce e cu adevărat: un fapt despre calendarul de publicare al țării,
verificabil din mai multe surse independente, nu o judecată statistică
asupra unui singur număr.

## 3. Criteriul propus inițial: `|consensus| > k·σ` — INFIRMAT

Calculat leave-one-out (σ exclude rândul suspect însuși, ca să nu se
autoinfleze) pe toate cele 38 de rânduri supraviețuitoare:

| currency/indicator (selecție) | consensus | σ (leave-one-out) | \|consensus\|/σ |
|---|---|---|---|
| **USD employment_change 2025-10-02 (cazul-ancoră)** | 52.0 | 84.5 | **0.615** |
| AUD employment_change 2025-09-18 | 21.2 | 36.2 | 0.586 |
| NZD employment_change 2025-11-04 | 0.1 | 0.33 | 0.303 |
| JPY retail_sales 2025-05-29 | 2.9 | 1.23 | 2.366 |
| JPY interest_rate 2025-12-17 | 0.75 | 0.26 | 2.897 |

**Cazul-ancoră are un raport de doar 0.615** — mai mic decât multe rânduri
despre care nu există niciun motiv să le suspectăm (retail_sales JPY/CHF cu
rapoarte >2). Testat exhaustiv pe toate cele 38:

| prag k | rânduri prinse din 38 |
|---|---|
| 0.5 | 25 |
| 1.0 | 13 |
| 1.5 | 11 |
| 2.0 | 4 (nu include cazul-ancoră) |

Ca să prindem cazul-ancoră, pragul ar trebui să fie ≤0.615 — la care prinde
și 25/38 (66%) din rânduri, majoritatea evident plauzibile (retail sales cu
consensus mic pe serii cu σ mic e normal). **Criteriul nu discriminează**:
NFP e o serie cu σ mare (118 pe nivelul absolut, 84.5 pe surpriză) — orice
surpriză unică, oricât de "imposibilă" domeniul-o-arate (NFP exact 0.0 nu
s-a mai văzut în istoric, vezi §4), rămâne statistic "normală" în termeni
de sigma proprie. Exact motivul pentru care sesiunea anterioară (garda PMI)
a respins un test de plauzibilitate a valorii pentru contaminarea
cross-country — riscul de curve-fit pe un singur caz confirmat se repetă
identic aici.

## 4. Alternativă testată: z-ul nivelului `0` — la fel de slab

Ideea: comparați `0.0` cu media/σ ale valorilor reale (nu ale surprizei)
pentru acea (currency, indicator). Rezultat:

- **Nu discriminează per rând** — toate rândurile suspecte din același
  grup currency/indicator primesc EXACT același z (depinde doar de
  media/σ al grupului, nu de `consensus`-ul rândului specific). Nu poate
  separa un rând-fantomă inert (consensus=0, previous=0) de cazul-ancoră
  (consensus=52) din același grup.
- Pentru USD employment_change: z = -1.489 — sub orice prag rezonabil
  (1.5-2.0) care ar evita fals-pozitive masive pe alte serii.
- Totuși, un fapt izolat util: valoarea reală cea mai apropiată de zero
  din toată istoria USD employment_change (44 rânduri) e **12.0** — NFP
  SUA nu a publicat NICIODATĂ o valoare reală în banda [-12, 12]. Pentru
  AUD, GBP, CAD însă, valori reale de 0.1-0.5 chiar apar — deci "0.0 exact"
  nu e la fel de suspect pentru toate valutele. Observație corectă, dar nu
  suficientă singură ca criteriu (nu ține cont de `consensus`, deci tot nu
  separă rândul-fantomă inert de cazul real).

## 5. Ce recomand — și ce NU

**Nu adopt niciun criteriu statistic per-rând azi.** Ambele idei testate
eșuează exact pe cazul care le-a motivat, iar pragul care le-ar face să-l
prindă generează fals-pozitive masive pe rânduri fără niciun motiv
independent de suspiciune.

**Recomand construirea, într-o fază viitoare**, a corroborării prin gol de
cadență cross-indicator (§2) ca mecanism real — verificat aici să
funcționeze pe cazul-ancoră, spre deosebire de ambele alternative
statistice. Sfera: pentru un `actual=0.0` cu `can_be_zero=true` pe o serie
labour/growth SUA, verifică dacă ALTE serii labour/growth SUA independente
au un gol de cadență >1.5× normal în aceeași fereastră de ±30 zile. Cere
cod nou (scanare cross-indicator, nu doar cross-country ca garda PMI) —
în afara scopului "zero cod" al fazei ăsteia.

**Nu recomand nimic pentru cele două rânduri AUD** (2025-09-18, 2025-10-16,
consensus 21.2/20.5) — nu există corroborare structurală comparabilă
(niciun gol de cadență neobișnuit la alte serii AUD în aceeași fereastră,
spre deosebire de USD) și AUD employment_change chiar publică valori reale
apropiate de zero (min istoric 0.5). Rămân un fir deschis, nerezolvat —
un rezultat negativ documentat, nu o presupunere.

## 6. Efectul măsurat (dacă am carantina toate cele 38 azi)

Simulat: toate cele 38 de rânduri supraviețuitoare setate la `NaN`,
rulat prin `build_payload` complet (nu doar `compute_indicator_score`
izolat), comparat cu starea curentă.

**N (coverage): NEschimbat pentru orice valută/categorie.** Niciunul din
cele 38 de rânduri nu e azi PRINTUL ACTIV al seriei sale — toate sunt în
fereastra `surprise_window_k=12` doar ca zgomot în calculul σ, nu ca
valoarea afișată. Deci "cât s-ar carantina în plus" schimbă doar volatilitatea
de referință a printului curent, nu numărul de indicatori vizibili.

**score_precise — o singură schimbare reală, restul neglijabile:**

| currency | categorie | coverage | score_precise înainte | score_precise după |
|---|---|---|---|---|
| AUD | growth | 4→4 (neschimbat) | 0.2500 | **0.5000** |

Toate celelalte 6 grupuri (employment_change AUD/CAD/GBP/NZD/USD,
retail_sales CHF/GBP/JPY etc.) au variații de z <0.02 — sub pragul de
schimbare a bucket-ului de scor. AUD growth se schimbă pentru că 4 din cele
7 rânduri employment_change suspecte NU au fost incluse (nu-i afectau pe
AUD retail, era o coincidență de grupare) — de fapt schimbarea vine din
cluster-ul AUD retail_sales (4 rânduri, toate consensus=0.3), suficient de
concentrat încât să mute σ.

**Bias pereche — 7 instrumente afectate vizibil:**

| instrument | bias înainte → după | score înainte → după |
|---|---|---|
| AUDCAD | Neutral → **Bullish** | 1.04 → 1.25 |
| AUDJPY | Neutral → **Bullish** | 0.93 → 1.14 |
| AUDUSD | Bullish → Bullish | 1.28 → 1.49 |
| AUDCHF | Neutral → Neutral | 0.21 → 0.42 |
| AUDNZD | Neutral → Neutral | -0.21 → 0.00 |
| EURAUD | Neutral → Neutral | -0.76 → -0.97 |
| GBPAUD | Neutral → Neutral | -0.90 → -1.11 |

Toate cele 7 implică AUD — efectul e concentrat aproape integral în
cluster-ul AUD retail_sales (4 rânduri consensus=0.3), nu în cazul-ancoră
USD (a cărui contribuție la σ NFP e prea mică relativ la σ-ul mare al
seriei ca să miște vreun bias vizibil azi).

## 7. Găsite pe parcurs — fire separate

### a. Bug activ de ordonare în `_dedup_flash_final` — JPY Retail Sales

```
2026-04-29 22:50  actual=1.7  (valoarea REALĂ, publicată prima)
2026-04-29 23:50  actual=0.0  (placeholder, publicat AL DOILEA, o oră mai târziu)
```

`_keep_latest_published` alege rândul cu `release_dt` cel mai târziu dintre
cele cu `actual` nenul — presupune implicit că "mai târziu = mai autoritar".
Aici placeholder-ul e publicat DUPĂ valoarea reală, deci dedup-ul alege
greșit `0.0` în loc de `1.7`. Activ acum (aprilie 2026, în fereastra de
scoring). Diferit de tiparul CHF/GBP din FAZA 2 (acolo placeholder-ul vine
ÎNTOTDEAUNA înainte) — merită menționat acolo ca variantă a aceluiași defect
de dedup, cu ordine inversată. Zero cod aici, per scopul fazei; las firul
pentru FAZA 2 sau un follow-up dedicat.

### b. `interest_rate_decision` JPY — sentinelă de întârziere feed, nu eveniment real

```
2024-12-17  actual=0.00  forecast=0.25  previous=0.25
2025-01-22  actual=0.00  forecast=0.50  previous=0.25
2025-03-17  actual=0.00  forecast=0.50  previous=0.50
... (9 din 31 rânduri BOJ, între 2024-12 și 2026-04)
```

Pattern clar: JBlanked publică `0.00` ca sentinelă ori de câte ori nu preiau
la timp decizia reală de rată BOJ (o serie de NIVEL, nu de flux — `0.00%`
n-a fost niciodată reală în fereastra asta, rata a fost mereu ≥0.10%).
Valoarea corectă apare cu întârziere de una sau mai multe întâlniri (ex.
`2025-10-30 actual=0.50`, prima valoare corectă după 6 luni de sentinelă).

Impact: **zero pentru scoring** — `interest_rate_decision` e display-only
(`weight: 0.0`, exclus explicit din z-scoring, vezi
`NON_ZSCORED_INDICATORS` în `ff_scoring.py`). Impactul e doar de afișare
(calendarul ar arăta rata BOJ curentă ca `0.00%` quando de fapt e `0.50%`+).
Nu recalibrez nimic — doar documentez, per instrucțiune ("orice recalibrare
de scoring" e în afara scopului).

## Verificări făcute

- Toate numărătorile (§1, §3, §4, §6) rulate pe calea reală de producție
  (`to_scoring_frame` → excludere carantină → `_dedup_flash_final` →
  `compute_indicator_score`/`build_payload`), nu pe parquet brut.
- Corroborarea de cadență (§2) verificată cu date reale din parquet pentru
  4 serii USD independente, nu presupusă.
- Cazul AUD (§5) verificat explicit pentru corroborare — negativ, documentat
  ca atare, nu ignorat.
- `git status` — niciun fișier de date sau cod modificat în această fază.

## Ce NU s-a făcut

- Niciun cod de producție atins (`src/`, `config/`, `data/economic_indicators.yaml`).
- Niciun `can_be_zero` scos sau modificat.
- Nicio recalibrare de scoring aplicată — §6 e o simulare, nu un commit.
