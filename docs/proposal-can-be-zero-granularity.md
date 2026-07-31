# Proposal — granularitate `can_be_zero` per (valută, indicator)

Status: **investigație + măsurare, zero cod, zero config scris**. Branch
`fix/watchdog-per-instrument`, worktree `../macro-dev`. Adopția e decizie
separată — acest document e livrabilul FAZA 2, nu o implementare.

## Recapitulare — problema

`can_be_zero` (`data/economic_indicators.yaml`) e un flag pe DEFINIȚIA
indicatorului, aplicat uniform peste toate valutele mapate la acel
`indicator_key`. CHF `cpi_yoy` (alimentat de raw `CPI m/m`, unde 0.0 e un
citire lunară plauzibilă — inflația elvețiană e frecvent exact zero) și
USD/GBP/EUR `cpi_yoy` (alimentate de y/y real, unde 0.0 e rar/suspect)
împart azi ACELAȘI flag global (`false`). Rezultat: 68 rânduri pe 14 serii
pierdute din N — CHF CPI y/y cel mai grav, 13/45 (28.9%).

## Defectul e recurent, nu doar istoric — verificat pe codul viu

Raportul anterior (`docs/faza1-chf-cpi-zero-placeholder-inventory.md`)
concluziona corect că rândurile deja problematice vin din backfill-ul
istoric (`parse_jblanked_range` direct, fără `clean_jblanked_actuals`). Dar
`src/jb_actuals.py::clean_jblanked_actuals` — codul care rulează AZI, la
fiecare pull zilnic — are exact aceeași orbire:

```python
real = g[g["actual"].notna() & (g["actual"] != 0.0)]
pick = (real if len(real) else g).sort_values("datetime_utc").iloc[-1].copy()
if not len(real):
    pick["actual"] = float("nan")   # placeholder 0.0 → schedule row
```

Nu consultă `can_be_zero` deloc. Un print CHF CPI cu `0.0` real (ambele copii
DST) devine `NaN` **la ingest**, înainte să ajungă în parquet — la fel ca
`ff_scoring.to_scoring_frame`, dar mai devreme în lanț.

**Test falsificabil, pre-înregistrat**: CHF CPI se publică la începutul
lunii — următorul print e așteptat ~2026-08-04 (ultimul: 2026-06-04 → gap
tipic ~28-30 zile; verificat pe parquet-ul curent, ultimele 3 rânduri:
2026-06-04, apoi dublura DST 2026-07-02×2, ambele `0.0`). **Predicție**: dacă
printul din ~4 august e `0.0`, va ajunge în parquet ca `NaN`, nu ca `0.0` —
verificabil direct pe `data/jb_raw/` (payload-ul zilei) și pe
`data/economic_calendar_ff.parquet` după următorul refresh. Nu s-a putut
verifica acum (data n-a trecut încă) — consemnat pentru verificare ulterioară,
nu executat aici.

## 1. Trei opțiuni — cost în cod și config

### Opțiunea A — `can_be_zero_overrides: {CCY: true}` per indicator

Oglindește exact `frequency_overrides`, deja existent în același fișier
pentru același tip de problemă (cadență diferită per valută).

- **Config**: o cheie nouă pe fiecare din cele ~10 indicatori afectați
  (`cpi_yoy`, `gdp_qoq`, `ppi_yoy`, `core_pce`, `wage_growth`) — ex.
  `cpi_yoy: { can_be_zero_overrides: { CHF: true } }`.
- **Cod**: 2 locuri.
  1. `src/ff_scoring.py::load_can_be_zero()` — azi întoarce `set[str]`
     (indicator_key). Ar trebui să întoarcă `set[tuple[str,str]]`
     ((currency, indicator_key)) SAU să rămână `set[str]` pentru cazul
     global și să adauge un al doilea set pentru override-uri per-valută —
     ~15-20 linii, funcție nouă `load_can_be_zero_overrides()` +
     modificarea semnăturii lui `to_scoring_frame` (`if (r.currency, key)
     not in cbz_per_currency and key not in cbz_global`).
  2. `src/jb_actuals.py::clean_jblanked_actuals(jb, schedule=None)` — azi nu
     primește deloc `currency`/`can_be_zero` ca parametru (operează per
     `canonical_id`, nu știe de indicator taxonomy). Ar cere: (a) trecerea
     unui parametru `can_be_zero_map: dict[tuple[str,str], bool]` prin
     apelul din `pull_actuals`, (b) rezolvarea `canonical_id` → `indicator_key`
     via matcher ÎNAINTE de dedup (azi grupează strict pe `canonical_id`,
     nu pe `indicator_key` — canonical_id e mai granular, deci maparea e
     posibilă, dar cere importul `economic_fetch.CompiledMatcher` +
     `economic_indicators.yaml` în `jb_actuals.py`, o dependență nouă).
- **Cost total**: schimbare de config mică, schimbare de cod moderată în 2
  fișiere, o dependență nouă (`jb_actuals.py` → taxonomia de indicatori,
  azi complet independentă de ea).

### Opțiunea B — canonic separat per valută

Ce s-a făcut deja pentru CAD Core CPI → Median CPI y/y (branch anterior).
Elimină problema la sursă (seria nouă are consensus real, deci
`can_be_zero` devine irelevant pentru ea).

- **Config**: repunctare matcher (1-2 linii per valută afectată).
- **Cod**: ZERO — nu atinge `ff_scoring.py` sau `jb_actuals.py` deloc.
- **Cost**: necesită o serie alternativă cu unitate/consensus corecte în
  arhivă. **Nu disponibilă pentru 7 din cele 9 (sau 18, per descoperirea din
  FAZA 1 a auditului anterior) cazuri cunoscute de unitate greșită** — pentru
  CHF CPI specific, nu există (încă) o alternativă y/y nativă identificată
  în arhivă (spre deosebire de CAD, care avea Median/Common/Trimmed CPI y/y
  gata). Verificarea existenței unei alternative pentru CHF nu s-a făcut
  aici — ar fi primul pas înainte de a alege această opțiune pentru CHF.

### Opțiunea C — `can_be_zero` derivat din tag-ul `# xf`

Dacă raw feed-ul e deja marcat `# xf` (transform mismatch — m/m sau q/q
aliniat pe un nume y/y), 0.0 e automat tratat ca legitim pentru ACEA pereche
(valută, raw), fără flag separat de întreținut.

- **Config**: ZERO — reutilizează adnotarea `# xf` deja existentă în
  `config/ff_aliases.yaml` (comentariu, nu structură citită de cod azi).
- **Cod**: ar cere ca `# xf` să devină o structură CITITĂ (nu doar
  comentariu) — parsare YAML cu comentarii inline nu e fiabilă; ar necesita
  reformatarea aliasurilor `# xf` într-o structură explicită (ex. un al
  doilea dicționar `xf_transforms: {CCY: [raw_names]}`), o schimbare de
  SCHEMĂ pe `config/ff_aliases.yaml`, plus logica de citire în AMBELE locuri
  (`ff_scoring.py` și `jb_actuals.py`).
- **Cost**: cel mai mare dintre cele trei — schimbă semnificația `# xf`
  dintr-un comentariu într-o sursă de adevăr citită de cod, și tot cere
  modificarea acelorași 2 fișiere ca opțiunea A, plus o migrare de schemă.
  **Notă**: nu acoperă cazurile "reale, nu xf" din lista suspectă (CHF/EUR/
  USD `gdp_qoq` cu raw `GDP q/q`/`Prelim Flash GDP q/q`/`Advance GDP q/q` —
  3 din 14 serii — sunt q/q REALE, nu aliniate greșit, deci n-ar primi
  `can_be_zero` prin acest mecanism deloc; ar rămâne nerezolvate).

### Comparație rapidă

| opțiune | cost config | cost cod | acoperă toate cele 14 serii? | dependență nouă |
|---|---|---|---|---|
| A — override per valută | mic | moderat, 2 fișiere | da | `jb_actuals.py` → taxonomie |
| B — canonic separat | mic (per caz) | zero | doar unde există alternativă (necunoscut pt. CHF) | niciuna |
| C — derivat din `# xf` | zero (dar schimbă semnificația) | mare, 2 fișiere + schemă nouă | **nu** — ratează 3/14 (q/q reale) | `ff_aliases.yaml` → schemă nouă |

## 2. Ambele locuri, și dacă pot partaja o sursă de adevăr

Da — **dacă** se alege opțiunea A sau C, ambele fixuri (`ff_scoring.
load_can_be_zero` și `jb_actuals.clean_jblanked_actuals`) pot citi din
ACEEAȘI sursă (`data/economic_indicators.yaml` extins, sau
`config/ff_aliases.yaml` extins). Complicația practică: `jb_actuals.py` azi
n-are NICIO dependență de `economic_indicators.yaml` sau de matcher — e
intenționat independent (citește doar `canonical_id`, nu `indicator_key`).
Partajarea sursei de adevăr ar introduce prima legătură între cele două
module. Nu evaluat dacă asta e o problemă arhitecturală reală sau doar o
observație — semnalat pentru cine decide.

## 3. Efectul măsurat — pentru fiecare din cele 14 serii

Simulare (nu scriere): `to_scoring_frame` reprodus cu o excepție per
(valută, indicator_key) — restul logicii identică, inclusiv carantina pe
`consensus`. Fiecare serie testată IZOLAT (o singură excepție activă), plus
un scenariu combinat (toate 14 simultan).

| valută | indicator | rânduri recuperate | N cat. înainte→după | precise înainte→după | cell înainte→după | bias flips (izolat) |
|---|---|---|---|---|---|---|
| CHF | cpi_yoy | +12 | 1→2 | 0.0→0.0 | 0→0 | niciunul |
| CAD | gdp_qoq | +9 | 2→2 | 1.0→1.0 | 1→1 | niciunul |
| GBP | ppi_yoy | +8 | 3→3 | 0.333→0.333 | 0→0 | niciunul |
| **CHF** | **gdp_qoq** | +3 | 3→3 | **0.667→0.333** | **1→0** | **USDCHF** |
| EUR | gdp_qoq | +2 | 4→4 | 1.0→1.0 | 1→1 | niciunul |
| CHF | ppi_yoy | +7 | 1→1 | 0.0→0.0 | 0→0 | niciunul |
| GBP | gdp_qoq | +6 | 3→3 | 0.0→0.0 | 0→0 | niciunul |
| CAD | ppi_yoy | +4 | 3→3 | -1.333→-1.333 | -1→-1 | niciunul |
| CAD | cpi_yoy | +4 | 3→3 | -1.333→-1.333 | -1→-1 | niciunul |
| **USD** | **core_pce** | +4 | 2→2 | **-2.0→-1.5** | -2→-2 | **USDCHF** |
| USD | ppi_yoy | +3 | 2→2 | -2.0→-2.0 | -2→-2 | niciunul |
| **USD** | **gdp_qoq** | +1 | 4→4 | **-0.25→0.0** | 0→0 | **USDCHF** |
| AUD | ppi_yoy | +1 | 2→2 | 0.0→0.0 | 0→0 | niciunul |
| USD | wage_growth | +2 | 6→6 | 0.0→0.0 | 0→0 | niciunul |

**Niciuna dintre cele 14, izolat, nu schimbă N-ul categoriei** (rândurile
recuperate intră mai ales în fereastra de sigma trailing-12, nu neapărat ca
"ultimul print curent" — coverage-ul de azi rămâne compus din aceleași
intrări). **3 din 14 mișcă totuși biasul unei perechi, izolat — toate
USDCHF**: CHF gdp_qoq, USD core_pce, USD gdp_qoq. Coincidență notabilă: CHF
și USD sunt exact valutele ale căror categorii se mișcă cel mai mult per
serie individuală.

### Scenariul combinat (toate 14 simultan)

**4 perechi își schimbă biasul**: GBPUSD (Bullish→Neutral), USDCHF (Very
Bearish→Bearish), USDCAD (Bearish→Neutral), NZDUSD (Very Bullish→Bullish).

Categorii mișcate: CHF growth (cell 1→0), CHF inflation (N 1→2, precise
neschimbat), USD growth (precise -0.25→0.0, cell neschimbat 0), USD
inflation (precise -2.0→-1.5, cell neschimbat -2). Toate cele 4 flip-uri
implică USD sau CHF — nu întâmplător, sunt exact valutele ale căror
categorii se mișcă.

## 4. Riscul invers — cuantificat pe `data/jb_raw/`, nu presupus

Din cele 14 serii, **10 au cel puțin o apariție** (orice valoare, nu
neapărat 0.0) în fereastra reținută (`data/jb_raw/`, 2026-07-13→07-31).
Două cazuri concrete, opuse:

- **GBP ppi_yoy, print 2026-07-22**: `actual=0.0, forecast=-0.1` — apare
  **identic, 0.0, în 7 payload-uri consecutive** (23-29 iulie). Per
  discriminator ("0.0 peste tot → citire reală"): **citire reală**,
  confirmă clasificarea "suspect" (nu placeholder) pentru acest print
  concret.
- **AUD ppi_yoy, print 2026-07-31 (azi)**: în ACELAȘI payload
  (`jb_range_2026-07-31...`), **două capturi ale aceleiași zile — 03:30
  `actual=0.0`, 04:30 `actual=1.3`**. Exact tiparul placeholder-apoi-real
  descris în docstring-ul `clean_jblanked_actuals`. **Risc real, observat
  live, nu ipotetic** — dar: mecanismul de dedup EXISTENT (`real = g[actual
  != 0.0]`, deja activ, independent de `can_be_zero`) preferă deja
  valoarea ne-zero când există în ACELAȘI grup zi-calendaristică — deci
  acest caz specific s-ar rezolva corect chiar și cu `can_be_zero` activat
  pentru AUD ppi_yoy (0.0 timpuriu e oricum înlocuit de 1.3, înainte ca
  vreo decizie de `can_be_zero` să conteze). **Riscul rezidual real**: dacă
  TOATE capturile unei zile ar arăta 0.0 (niciuna corectată), `can_be_zero`
  ar accepta acel 0.0 ca final — exact ce nu se poate verifica retroactiv
  pentru rândurile deja din parquet (fereastra `jb_raw` nu ajunge înapoi la
  ele, cum s-a stabilit în FAZA 1 a auditului anterior).

**Concluzie cuantificată**: pe cele 10 serii verificabile azi, evidența
directă arată 1 caz clar de citire reală (GBP ppi_yoy) și 1 caz care
demonstrează mecanismul de placeholder e activ dar deja neutralizat de
dedup-ul existent (AUD ppi_yoy) — 0 cazuri confirmate unde `can_be_zero`
per-valută ar fi acceptat un placeholder netratat. Nu e o garanție pentru
restul de 4 serii neverificabile (CHF cpi_yoy, CHF gdp_qoq, CAD gdp_qoq,
CAD ppi_yoy — în afara ferestrei `jb_raw`) — risc rezidual necuantificabil
cu datele disponibile azi.

## 5. Criterii de acceptare pre-înregistrate

Scrise înainte de orice decizie de adopție — pentru cine alege o opțiune:

1. **Opțiunea aleasă trebuie să acopere toate cele 14 serii**, sau să
   documenteze explicit de ce unele rămân excluse (opțiunea C ratează
   structural 3/14 — vezi §1).
2. **Niciun flip de bias neașteptat** — cele 4 perechi identificate în
   scenariul combinat (§3) sunt limita cunoscută; o implementare care
   produce mai multe la adopție cere reverificare înainte de acceptare.
3. **`jb_actuals.py` rămâne fail-open** — orice schimbare la
   `clean_jblanked_actuals` trebuie să păstreze contractul "un pull eșuat
   nu degradează parquet-ul" (docstring-ul modulului, neschimbat).
4. **Testul falsificabil din partea de sus** (printul CHF CPI ~4 august) se
   verifică efectiv înainte de a considera problema "doar istorică" din nou
   închisă — dacă printul viu confirmă `NaN` pentru un `0.0` real, e dovada
   directă că fix-ul trebuie să atingă și codul de ingest zilnic, nu doar
   backfill-ul.
5. **Nicio opțiune nu se adoptă fără o verificare explicită a riscului
   invers** (§4) pe seria specifică — GBP ppi_yoy și AUD ppi_yoy au acum
   evidență directă; celelalte 12 nu, și ar trebui verificate similar de
   îndată ce printurile lor viitoare intră în fereastra `jb_raw`.

## Reproducere

```bash
.venv/bin/python3 scripts/measure/can_be_zero_granularity_effect.py
```

Zero fișier de config sau cod de producție modificat în această fază.
