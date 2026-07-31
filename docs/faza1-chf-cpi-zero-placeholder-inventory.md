# FAZA 1 — CHF CPI + inventarul zero-placeholder

Status: **investigație, zero cod scris**. Branch `fix/remaining-data-defects`,
worktree `../macro-dev`. Toate numerele de mai jos vin din interogări
read-only pe `data/economic_calendar_ff.parquet` + `data/archive/` (nescrise)
+ `data/jb_raw/` (payload-urile zilnice reținute, în git).

## 1. CHF CPI y/y — verificare punctuală

Confirmat exact, pe date reale:

| # | fapt | verificat |
|---|---|---|
| 1 | Printuri CHF CPI y/y cu `actual == 0.0` | **13 din 45 (28.9% ≈ 29%)** |
| 2 | Printul din 2026-07-02 e unul dintre ele | **Da** — `actual=0.0`, `forecast=0.1`, în DOUĂ copii (05:30 și 06:30 UTC, artefact DST) |
| 3 | Ce i se întâmplă la scoring | **Quarantine** — `to_scoring_frame` nulează `actual` (nu doar `consensus`) pentru ambele copii, pentru că `cpi_yoy` nu e în `can_be_zero`. Ultimul `actual` valid rămas: **2026-06-04** (0.2), acum stale (fereastra lunară 45d) |

Raw feed pentru CHF CPI y/y e `"CPI m/m"` (aliniat via `config/ff_aliases.yaml`
`CHF: "CPI m/m": "CPI y/y" # xf`) — defectul de unitate deja cunoscut.

### Descoperire suplimentară #3 — de ce parquet-ul păstrează 0.0 literal (nu NaN)

`src/jb_actuals.py` are deja logica exact cerută
(`clean_jblanked_actuals`, liniile 120-177): per (canonical_id, dată UTC), dacă
NICIUN exemplar din grup n-are un `actual` real (non-NaN, != 0.0), grupul e
COLAPSAT la un singur rând și `actual` e setat explicit la `NaN`
("placeholder 0.0 → schedule row").

**Verificat: această funcție NU s-a aplicat rândurilor din 2026-07-02.**
Dovadă directă — `data/archive/ff_calendar_range.json` (read-only) conține
deja, identic, AMBELE copii (05:30 și 06:30, ambele `actual=0.0`) ca rânduri
SEPARATE. Dacă `clean_jblanked_actuals` ar fi rulat pe aceste date, grupul ar
fi fost colapsat la UN singur rând cu `actual=NaN` — nu la două rânduri cu
`0.0` literal.

**Concluzie, cu drum precizat**: rândurile provin din backfill-ul istoric
unic (`data/archive/ff_calendar_range.json`, ingerat via `parse_jblanked_range`
direct, commit `d9e656d` "archive: backfill JBlanked 2023-01 → 2026-07-05"),
care NU trece prin `clean_jblanked_actuals` — acea funcție e chemată doar din
fluxul zilnic `pull_actuals` (activ abia din 2026-07-12, per docstring-ul
modulului). **`src/jb_actuals.py` e deja corect pentru tot ce vine de acum
înainte prin pull-ul zilnic; problema e strict în datele istorice deja
scrise, nu în codul curent.** Dacă s-ar face vreodată un fix, locul lui e un
script de curățare aplicat retroactiv pe parquet (gen
`migrations/2026-07-30_backfill_median_household.py`), nu o schimbare în
`jb_actuals.py` sau `ff_refresh.py` (ambele deja corecte / în afara scopului).

### Descoperire suplimentară #1 — discriminator real-vs-placeholder pe `data/jb_raw/`

Metoda cerută (compară același eveniment peste toate payload-urile zilnice
reținute) e validă, dar **nu se poate aplica retroactiv la 2026-07-02**:
`data/jb_raw/` reține doar ultimele 14 payload-uri (`RAW_KEEP=14`,
`src/jb_actuals.py`), acoperind azi doar **2026-07-13 → 2026-07-31** — o
fereastră de ~2.5 săptămâni care nu ajunge la 2 iulie.

Verificat mai departe: **niciunul din cele 108 rânduri `actual==0.0`** din
inventarul de mai jos (nici măcar cel mai recent) cade în fereastra reținută
— metoda, deși corectă, nu are pe ce se aplica retroactiv pe niciun caz din
setul curent. Ca demonstrație pe date reale unde metoda CHIAR funcționează:
CAD Median CPI y/y din 2026-07-20 apare identic (actual=1.9, forecast=2.1) în
DOUĂ payload-uri distincte (07-26 și 07-27) — consistență peste capturi
independente, exact semnătura unui print real (control pozitiv).

Pentru viitor: fiecare zero nou apărut e verificabil astfel DOAR dacă mai
apare într-un payload zilnic ulterior de-a lungul ferestrei de 14 zile —
merită păstrat ca procedură de verificare curentă (nu retroactivă).

## 2. Inventar complet — pierderi la scoring din `actual == 0.0`

**Discrepanță de raportare, semnalată explicit (regulă: nu ascund o
neconcordanță):** log-ul de producție (`to_scoring_frame`, rulat acum, pe
parquet-ul curent) raportează **108 actual + 206 consensus**, nu 131/267 cum
era citat. Am verificat și pe alte instantanee disponibile — nici unul nu dă
131/267:

| sursă | actual==0.0 | consensus==0.0 |
|---|---|---|
| parquet curent (`data/economic_calendar_ff.parquet`) | **108** | **206** |
| `.pre-backfill` (înainte de promovarea CAD/AUD) | 108 | 206 |
| `.pre-purge` (înainte de purja PMI, 30 iulie) | 121 | 213 |
| arhiva completă re-parsată (`parse_jblanked_range`) | 123 | 207 |

Nu am putut reproduce 131/267 pe niciuna din stările disponibile —
posibil un instantaneu intermediar nesalvat undeva, sau o metodă de numărare
diferită. Folosesc **108/206** mai jos (verificabil direct, chiar acum, pe
parquet-ul curent).

### Clasificare: legitim vs. suspect

**legitim** — indicatori unde 0.0 e imposibil fizic (rate, PMI, niveluri):

| valută | indicator | raw | total | zero | % |
|---|---|---|---|---|---|
| AUD/EUR/JPY | manufacturing_pmi / services_pmi | Flash Mfg/Services PMI | 8 | 1 | 12.5% |
| CHF | unemployment_rate | Unemployment Rate | 45 | 3 | 6.7% |
| AUD/CAD/JPY/USD | unemployment_rate | Unemployment Rate | 43-44 | 1-2 | 2.3-4.7% |
| CAD/GBP/USD | manufacturing_pmi/services_pmi | Mfg/Services PMI | 43-44 | 1-2 | 2.3-4.7% |
| USD | jolts, jobless_claims | JOLTS / Unemployment Claims | 45/187 | 1/4 | 2.2%/2.1% |

**legitim-by-convention** — serii y/y unde 0.0 e rar dar posibil (JPY/AUD au
istorie de inflație aproape de zero, deci convenția "y/y nu poate fi 0" e mai
puțin sigură acolo decât în altă parte — semnalat, nu rezolvat) + serii
`can_be_zero: true` deja corect tratate:

| valută | indicator | zero/total | notă |
|---|---|---|---|
| JPY | core_cpi | 5/47 (10.6%) | y/y — Japonia are istoric de inflație aproape-zero, deci convenția e mai slabă aici decât în altă parte |
| AUD | cpi_yoy | 2/45 (4.4%) | y/y — coincide cu tiparul de corupție JBlanked deja documentat (AUD CPI 0.0-vs-3.8, `trimmed_mean_cpi_monthly` comment) — posibil corupt, nu neapărat citire reală |
| USD | adp | 2/45 (4.4%) | **notă**: ADP e o serie de tip net-change (ca `employment_change`), dar NU e în `can_be_zero` — pare o inconsecvență de configurare față de `employment_change`, care E `can_be_zero: true` pentru exact același TIP de serie |
| (restul) `can_be_zero: true` deja | — | — | tratate corect, incluse doar pentru completitudine |

**suspect** — serii m/m sau q/q (direct sau prin alias `# xf`) unde 0.0 e o
citire plauzibil reală, NU `can_be_zero`, deci pierdute din N:

| valută | indicator | raw feed | total | zero | % | xf? |
|---|---|---|---|---|---|---|
| **CHF** | **cpi_yoy** | CPI m/m | 45 | **13** | **28.9%** | da |
| CAD | gdp_qoq | GDP m/m | 41 | 9 | 22.0% | da |
| GBP | ppi_yoy | PPI Output m/m | 38 | 8 | 21.1% | da |
| CHF | gdp_qoq | GDP q/q (real) | 16 | 3 | 18.8% | nu |
| EUR | gdp_qoq | Prelim Flash GDP q/q (real) | 16 | 3 | 18.8% | nu |
| CHF | ppi_yoy | PPI m/m | 45 | 7 | 15.6% | da |
| GBP | gdp_qoq | GDP m/m (documentat, UK a renunțat la q/q) | 43 | 6 | 14.0% | nu (deja acceptat) |
| CAD | ppi_yoy | IPPI m/m | 43 | 4 | 9.3% | da |
| CAD | cpi_yoy | CPI m/m | 44 | 4 | 9.1% | da |
| USD | core_pce | Core PCE Price Index m/m | 44 | 4 | 9.1% | da |
| USD | ppi_yoy | PPI m/m | 41 | 3 | 7.3% | da |
| USD | gdp_qoq | Advance GDP q/q (real) | 15 | 1 | 6.7% | nu |
| AUD | ppi_yoy | PPI q/q | 16 | 1 | 6.2% | da |
| USD | wage_growth | Average Hourly Earnings m/m | 45 | 2 | 4.4% | da |

**Total pierdut din N, grupul suspect, ne-`can_be_zero`: 68 rânduri, 14
serii.** CHF CPI y/y e, așa cum era de așteptat, cazul cel mai grav (13
rânduri, 28.9%) — dar nu e izolat: CAD GDP (9 rânduri) și GBP PPI (8 rânduri)
sunt aproape la fel de grave.

### Descoperire care nu era în spec — scop mai larg al defectului de unitate

Task-ul citează "7 din 9" cazuri cunoscute de incoerență de unitate (`USD/CAD
Core CPI, CHF/NZD CPI, USD/EUR/CHF/AUD PPI`). Am numărat direct etichetele
`# xf` din `config/ff_aliases.yaml` (transform-mismatch, documentat de sesiuni
anterioare chiar în acel fișier): **18 perechi, nu 9** — inclusiv, în afara
celor citate: **CAD GDP** (`GDP m/m` → canonic `GDP q/q`), **CAD CPI y/y**
non-core (`CPI m/m` → `CPI y/y`, separat de Core CPI deja cunoscut), **USD
Core PCE** (`m/m` → `y/y`), **USD Wage Growth** (`Average Hourly Earnings
m/m` → `y/y`), **AUD Wage Price Index** (`q/q` → `y/y`), **AUD Trimmed Mean
CPI** (`q/q` → `y/y`), **NZD Labor Cost Index** (`q/q` → `y/y`), **JPY Retail
Sales** (`y/y` → `m/m`, singurul caz cu sensul invers). Unele (NZD CPI, GBP
GDP) sunt deja documentate ca acceptate structural (țara nu publică
alternativa), altele par pur și simplu necatalogate până acum. **Nu e o
infirmare a vreunei decizii — coerența unităților rămâne în afara scopului —
dar setul real de cazuri e dublu față de ce era citat, relevant pentru orice
măsurare viitoare de acest tip.**

## 3. Granularitate `can_be_zero`: per-indicator azi, ce ar cere per-valută

Confirmat: `can_be_zero` e un flag pe DEFINIȚIA indicatorului
(`data/economic_indicators.yaml`), aplicat uniform peste toate valutele
mapate la acel `indicator_key`. CHF `cpi_yoy` (alimentat de `CPI m/m`, unde
0.0 e plauzibil) și USD/GBP/EUR `cpi_yoy` (alimentate de `y/y` real, unde 0.0
e rar) împart azi ACELAȘI flag — nu poate exista o excepție doar pentru CHF
fără să afecteze toate celelalte valute ale aceluiași `indicator_key`.

Trei opțiuni, doar documentate (nicio decizie, nicio implementare):

1. **`can_be_zero_overrides: {CHF: true}`** — oglindește exact
   `frequency_overrides` deja existent în același fișier. Cere: (a) o nouă
   cheie YAML pe definiția indicatorului, (b) `ff_scoring.load_can_be_zero()`
   să returneze un set de `(currency, indicator_key)` în loc de doar
   `indicator_key`, (c) verificarea din `to_scoring_frame` extinsă de la
   `key not in cbz` la `(r.currency, key) not in cbz`. Cel mai mic blast
   radius — tipar deja folosit în același fișier pentru altă problemă.
2. **Canonic separat per valută** (ce s-a făcut deja pentru CAD Core CPI →
   Median CPI y/y) — elimină problema la sursă, dar cere o serie alternativă
   cu unitate corectă în arhivă. Explicit NU disponibil pentru 7 din cele 9
   (sau 18, per descoperirea de mai sus) cazuri cunoscute.
3. **`can_be_zero` derivat din tag-ul `# xf` însuși**, nu întreținut manual —
   dacă raw feed-ul e deja marcat `# xf` ca fiind un m/m/q/q aliniat pe un
   nume y/y, 0.0 ar putea fi tratat automat ca legitim pentru ACEA pereche
   (valută, raw), fără flag separat de întreținut. Auto-documentat, dar
   schimbă semnificația `# xf` dintr-un comentariu în ceva citit de cod —
   schimbare de scoring mai mare decât opțiunea 1.

Nu implementez niciuna — cere măsurare separată, explicit în afara scopului
acestei faze.

## Reproducere

```bash
# CHF CPI y/y: total, zero-count, ultimele rânduri
.venv/bin/python3 -c "
import pandas as pd
df = pd.read_parquet('data/economic_calendar_ff.parquet')
sub = df[(df.currency=='CHF') & (df.name_canonical=='CPI y/y')]
print(len(sub), (sub['actual']==0.0).sum())
"

# log-ul autoritativ de quarantine (108/206 pe parquet-ul curent)
.venv/bin/python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
import pandas as pd
from src.ff_scoring import build_matcher, to_scoring_frame
df = pd.read_parquet('data/economic_calendar_ff.parquet')
df['datetime_utc'] = pd.to_datetime(df['datetime_utc'])
to_scoring_frame(df, build_matcher())
"
```
