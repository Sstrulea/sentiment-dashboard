# Staleness gate — constatare (nu propunere de recalibrare)

Status: raport de constatare, verificat pe date live (`as_of = 2026-07-30`).
**Nu propune nicio schimbare de prag.** Scope inițial (Faza 2 din audit)
cerea o specificație pentru un gate de vechime care lipsește — verificarea de
mai jos a arătat că gate-ul deja există și deja exclude corect print-urile
stale din agregare. Documentul a fost redirecționat: ce urmează e inventarul
excluderilor cronice curente + o singură gaură reală identificată (§5).

## 1. Mecanismul existent (`src/economic_compute.py`)

Gate-ul de vechime NU e în `src/ff_scoring.py` (unde Faza 0 #1 a căutat) — e
în `economic_compute.py`, un fișier mai vechi, independent de refactorizarea
FF, și e activ pe toată calea de producție (`ff_scoring.score_calendar` →
`economic_compute.build_payload` → `compute_currency_scorecard` →
`compute_indicator_score`).

**Determinarea bucket-ului de frecvență per serie** (`_max_age_for`,
`economic_compute.py:71-78`): fiecare indicator are o `frequency` (monthly/
quarterly/weekly), cu `frequency_overrides` per-valută în
`data/economic_indicators.yaml` (ex. AUD/NZD CPI e quarterly, restul monthly).
Fereastra de vechime vine din `defaults.max_age_by_frequency`:

| frecvență | max_age_days |
|---|---|
| weekly | 14 |
| monthly | 45 |
| quarterly | 110 |
| (fallback, fără frecvență cunoscută) | 120 |

**Ce se întâmplă cu rândul exclus** (`compute_indicator_score`,
`economic_compute.py:182-241`): dacă cel mai recent print cu `actual` e mai
vechi decât `as_of − max_age_days`, funcția e chemată de două ori cu semantici
diferite:
- `allow_stale=False` (n-ar mai exista drum de agregare fără marcaj): întoarce
  `None` — exclus complet.
- `allow_stale=True` (calea reală, `compute_currency_scorecard:369`): scorul
  se calculează normal (z sau fallback), dar rezultatul poartă `stale: True`.

La agregarea pe categorie (`compute_currency_scorecard:380`):
```python
if cat in per_cat and not scored.get("stale"):
    per_cat[cat].append((scored["score"], weight))
```
Un rând `stale` **nu intră în `per_cat`** → nu contribuie la `score_precise`
și nu crește `coverage`. Rămâne doar în `breakdown[key]` — folosit exclusiv
pentru afișare (badge stale). Verificat că `economic_render.py` și
`crossasset_compute.py` respectă `stale` peste tot unde citesc `breakdown`.

Al doilea strat, `_superseded_missing` (STRAT 2, linia 160): marchează stale
și un print AFLAT în fereastră, dacă un release mai nou e overdue (peste
`_SUPERSEDE_GRACE`) și încă lipsește — un caz mai subtil decât simpla
depășire de vârstă (vezi CHF CPI y/y, §3).

**Verificare live** (as_of 2026-07-30, `AUD` `core_cpi` = RBA Trimmed Mean CPI
y/y, ultim print 2025-10-29, 274 zile):
```
breakdown['core_cpi'] = {..., 'release_dt': 2025-10-29, 'stale': True, ...}
categories['inflation'] = {'coverage': 2, 'score_precise': -1.0, ...}
```
`inflation` are 3 indicatori configurați pentru AUD (`cpi_yoy`, `core_cpi`,
`ppi_yoy`); coverage=2 confirmă că `core_cpi` a fost scos din medie.

## 2. Corecție la Faza 0 #1

„Nu există gate de vechime la scoring" a fost o concluzie trasă dintr-un grep
îngust (`grep "stale\|max_age\|age_days" src/ff_scoring.py` → gol). Grep-ul
era corect — chiar nu există nimic în acel fișier — dar mecanismul trăiește
în alt fișier, netrecut prin refactorizarea FF. Un print de 274 zile **nu**
scorează ca unul de 8 zile; e deja exclus din indexul de azi, afișat doar ca
valoare istorică marcată. Concluzia trebuie revizuită oriunde a fost citată.

## 3. Inventarul excluderilor cronice (LIVRABILUL PRINCIPAL)

Toate perechile (currency, indicator) marcate `stale=True` azi, din toate
cele 8 valute × toți indicatorii configurați (`as_of=2026-07-30`):

| valută | indicator | categorie | cadență | ultim print | zile excluse | zile peste prag | publicări ratate (est.) | superseded_missing |
|---|---|---|---|---|---|---|---|---|
| CHF | interest_rate_decision (SNB) | rates | monthly | 2025-06-19 | 405 | 360 | 12 | False |
| AUD | retail_sales | growth | monthly | 2025-07-31 | 363 | 318 | 11 | False |
| AUD | core_cpi (RBA Trimmed Mean CPI y/y) | inflation | quarterly | 2025-10-29 | 273 | 163 | 2 | False |
| CHF | cpi_yoy | inflation | monthly | 2026-06-04 | 55 | 10 | 0 | **True** |
| USD | core_cpi | inflation | monthly | 2026-06-10 | 49 | 4 | 0 | False |

„Publicări ratate" = `zile_excluse // cadență_nominală − 1` (cadență
nominală: monthly≈30d, quarterly≈91d) — o estimare, nu un număr oficial de
comunicate lipsă.

**Note per rând:**
- **CHF interest_rate_decision** — deja semnalat ca display-only în afara
  scopului (406d, impact mic). Confirmare structurală suplimentară: categoria
  lui e `rates`, care nici măcar nu apare în `categories:` (doar growth/
  inflation/labour sunt agregate) — deci **acest indicator n-ar fi intrat
  niciodată în index, indiferent de vechime**; excluderea lui e complet
  cosmetică, nu doar din cauza `weight: 0.0`.
- **AUD retail_sales** — confirmă Faza 0.0b. Categoria `growth` AUD are
  coverage 3/4 (gdp_qoq, manufacturing_pmi, services_pmi contează;
  retail_sales nu).
- **AUD core_cpi** — confirmă Faza 0 constatarea #2. Categoria `inflation`
  AUD: coverage 2/3.
- **CHF cpi_yoy** — **descoperire nouă, nesemnalată în Faza 0**. La 55 zile
  e abia peste pragul monthly (45d), dar mai important: `superseded_missing
  = True` — există un release mai nou, scadent, încă neapărut cu `actual`.
  Categoria `inflation` CHF: coverage 1/2 (doar `ppi_yoy` contează).
- **USD core_cpi** — exact cazul de investigat la Faza 4 (49 zile, 4 peste
  prag). Categoria `inflation` USD: coverage 3/4. Fiind marginal (4 zile
  peste), următorul print (dacă vine în alias corect) ar rezolva-o singură —
  vezi ancheta din Faza 4 pentru DE CE lipsește.

**Perechi FX afectate (context, nu simulare completă):** orice pereche care
include AUD, CHF sau USD compară o categorie cu coverage redus contra
counterpart-ului. AUD apare în 7 perechi (AUDUSD, EURAUD, GBPAUD, AUDJPY,
AUDNZD, AUDCAD, AUDCHF), CHF în 7 (USDCHF, EURCHF, GBPCHF, CHFJPY, AUDCHF,
NZDCHF, CADCHF), USD în 7. Fiecare din acestea rulează azi cu `inflation`
(și, pentru AUD, `growth`) calculat pe mai puțini indicatori decât nominal —
NU cu date greșite, ci cu un eșantion mai mic, corect marcat prin `coverage`.

## 4. Interacțiunea cu pragul de eșantion minim

Specificația inițială cerea documentarea interacțiunii cu
`ff_scoring.CADENCE_THRESHOLD` ({weekly:24, monthly:24, quarterly:8,
annual:3}). Verificare: **acest prag nu e conectat la nimic în producție.**
E definit în `ff_scoring.py` și folosit DOAR de `scripts/ff_phase2.py` (audit-ul
one-off de backfill din 2026-07-05), pentru raportul lui de below-threshold —
nu e citit de `economic_compute.py`, `economic_render.py` sau orice altă cale
live. E cod mort în raport cu scoring-ul curent.

Pragul REAL, activ, e altul: `defaults.fallback_min_prints` (=6,
`economic_compute.py:210,274`). Nu exclude nimic — schimbă FORMULA:
- ≥6 perechi (actual, consensus) în fereastra trailing-K ȘI sigma valid
  (≠0, non-NaN) → scor z (`direction * surprise / sigma`).
- <6 perechi, SAU sigma==0/NaN → fallback procentual
  (`direction * surprise / |consensus|`), bucket separat (`pct_buckets`).

**Precedență**: cele două verificări sunt ortogonale, nu concurente pe
aceeași axă. Gate-ul de vechime rulează ÎNTÂI (decide DACĂ un print
contribuie la categorie); pragul de eșantion minim rulează DOAR pentru
print-urile care au trecut de vechime, și decide CUM se calculează scorul lor
(z vs. procent), nu dacă contează. Un indicator poate fi simultan „fresh" (nu
stale) ȘI sub `fallback_min_prints` — atunci contează în categorie, dar cu
scor procentual, nu z. Un indicator „stale" nu ajunge niciodată la decizia de
fallback, pentru că e deja scos înainte de agregare (rămâne totuși scorat —
cu z SAU fallback, oricare se aplică — doar pentru breakdown/afișare).

Nu există niciun caz din §3 unde ambele praguri s-ar aplica simultan în sens
conflictual, pentru că nu sunt pe aceeași axă: „sub amândouă" ar însemna doar
„stale ȘI puține perechi" — indicatorul tot iese din agregare din cauza
vechimii, indiferent de eșantion.

## 5. Gaura reală: gate-ul e tăcut

Mecanismul funcționează corect per-celulă (exclude, marchează, afișează), dar
**nu produce niciun semnal agregat despre excludere cronică**. AUD Trimmed
Mean CPI e afară de categorie de 273 zile, AUD Retail Sales de 363 — ambele
au trecut prin zeci de refresh-uri fără ca vreun log sau raport să le
semnaleze ca fiind structural moarte, nu doar temporar întârziate. Diferența
contează: un print stale de 46 zile (o zi peste prag) e normal — se rezolvă
singur la următoarea publicare. Un print stale de 273+ zile NU se va rezolva
singur — seria fie s-a mutat sub alt nume la sursă (cazul AUD core_cpi,
rezolvat parțial în Faza 1), fie sursa a încetat s-o publice (AUD retail_sales,
încă neconcluziv per Faza 0.0b).

**Propunere (doar text, fără cod):** un raport de excludere cronică, generat
la fiecare ingest (alături de sumarul „unmapped" deja existent în
`_canonicalize`), care listează orice (currency, indicator) `stale=True` de
mai mult de **2 cicluri consecutive de refresh** ale gate-ului însuși — adică
`days_past_cutoff > 0` la DOUĂ ingestii succesive, nu doar una. Pragul „2
cicluri" (nu "1") evită zgomotul pentru cazuri marginale ca USD core_cpi (4
zile peste, posibil tranzitoriu); un raport care ar aprinde alarma din prima
zi peste prag ar semnala inclusiv print-uri normale, în curs de-a fi
înlocuite la următoarea publicare.

Format sugerat (analog cu logul „FF ingest: N unmapped event(s)"): o linie
INFO per indicator afectat, cu `currency`, `indicator`, `last_release`,
`days_past_cutoff`, reținută în timp (nu doar o singură rulare) astfel încât
cineva să observe „e afară de 3 refresh-uri la rând" înainte să treacă un an,
nu după. Nu implementat aici — decizia de adopție și forma exactă (log vs.
fișier vs. secțiune în `economic.json`) rămân de discutat separat.

## Referințe

- Faza 0 a acestui audit (constatarea #1, corectată în §2 de mai sus).
- `src/economic_compute.py:71-78` (`_max_age_for`), `:160-179`
  (`_superseded_missing`), `:182-314` (`compute_indicator_score`), `:380`
  (excluderea din `per_cat`).
- `src/ff_scoring.py:94-113` (`CADENCE_THRESHOLD`/`detect_cadence` — cod mort
  în producție, folosit doar de `scripts/ff_phase2.py`).
- `data/economic_indicators.yaml` (`defaults.max_age_by_frequency`,
  `defaults.fallback_min_prints`, `frequency_overrides` per indicator).
