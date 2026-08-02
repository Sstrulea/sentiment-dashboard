# FAZA 1c — problemă de integritate a arhivei, descoperită, NErezolvată

Status: **constatare, zero propunere de fix**. Descoperită în timpul auditului
`fix/can-be-zero-transform` (widening `can_be_zero` pe transformul real al
seriei). Separată de acel branch — widening-ul a introdus doar 3 grupuri noi
de dubluri (rezolvate acolo, narrow-scoped), dar măsurătoarea a scos la iveală
o problemă mult mai largă, preexistentă, în `data/archive/ff_calendar_range.json`.

## Ce s-a găsit

Peste toată arhiva (3724 rânduri mapate), grupate pe `(canonical_id, dată
calendaristică)`:

```
(canonical_id, date) pairs with >=2 rows: 146
Total rows involved: 373
Identical-actual groups: 32
Divergent-actual groups: 114   (78%)
```

Distribuția diferenței pe cele 114 divergente: mediană **3.4**, medie 7.9, max
**206** (`usd_initial_jobless_claims`, 2026-02-19: 206.0 vs 0.0). Marea
majoritate a diferențelor mari nu sunt artefacte de rotunjire — sunt fie
placeholder-uri necolapsate (0.0 vs valoare reală), fie republicări genuine cu
valori diferite.

**Doar 26% din divergențe cad în fereastra ±1h** (artefactul DST deja cunoscut,
cel care motivează acest branch). Restul de 74% se întind pe **până la 14.25
ore în aceeași zi calendaristică** — nu e dublura DST, e un fenomen diferit:
republicări multiple ale aceluiași eveniment, aceeași zi.

**GBP PMI domină**: `gbp_s_p_global_cips_manufacturing_pmi` (36 perechi) +
`gbp_s_p_global_cips_services_pmi` (36 perechi) = 72/114 (63%) din toate
divergențele. Exemplu (`docs/`-reproducibil mai jos): 4 rânduri diferite în
aceeași zi (13:45, 08:30, 08:00, 00:30), valori 52.0 / 46.4 / 49.4 / 0.0 —
structural distinct de o dublură ±1h.

**96.5% din perechile divergente ating o serie scorată azi** (110/114) — nu e
un colț mort al datelor, e activ în producție.

## Tiebreak-ul NU e automatizabil fără revizuire suplimentară

Semnalul propus ("care copie e mai aproape de `previous`-ul printului
următor din serie") oferă un câștigător unic în 109/114 cazuri, dar e
nesigur: `cad_s_p_global_manufacturing_pmi`, 2023-11-01 — actuals=[40.6, 48.6],
`next_previous`=0.0. Semnalul de tiebreak însuși (`0.0`) arată ca un
placeholder necolapsat din lanțul următor — adică tiebreak-ul poate fi
corupt de exact același fenomen pe care încearcă să-l rezolve, auto-confirmând
o alegere greșită fără avertisment vizibil.

## Ce NU s-a făcut

Niciun fix. `to_scoring_frame` conține (din `fix/can-be-zero-transform`) o
gardă **îngustă**, scoped strict la grupurile care trec de la <2 la >=2
rânduri valide *ca urmare a widening-ului* — cele 143 de grupuri preexistente
(deja duplicate înainte de acest branch, deja în producție) rămân complet
neatinse.

## Comenzi de reproducere

```python
# peste toată arhiva, grupare (canonical_id, dată), identic vs divergent
import json, sys; sys.path.insert(0, ".")
import pandas as pd
from src.econ_calendar_ff import canonical_id, jblanked_to_utc, load_aliases, load_eur_whitelist

archive = json.loads(open("data/archive/ff_calendar_range.json").read())
aliases = load_aliases(); eur_wl = load_eur_whitelist()
OUR_CCYS = {"USD","EUR","GBP","JPY","AUD","NZD","CAD","CHF"}
recs = []
for e in archive:
    ccy = str(e.get("Currency","")).strip()
    if ccy not in OUR_CCYS: continue
    name_raw = str(e.get("Name","")).strip()
    if ccy == "EUR" and name_raw not in eur_wl: continue
    name_canon = (aliases.get(ccy, {}) or {}).get(name_raw)
    if name_canon is None: continue
    dt = jblanked_to_utc(e.get("Date",""))
    if dt is None: continue
    recs.append({"canonical_id": canonical_id(ccy, name_canon), "currency": ccy,
                 "name_raw": name_raw, "date": dt.date(), "datetime_utc": dt,
                 "actual": e.get("Actual"), "previous": e.get("Previous")})
df = pd.DataFrame(recs)
groups = df.groupby(["canonical_id","date"]).size()
dup = groups[groups >= 2]
# dup.shape[0] == 146
```

Pentru distribuția identic/divergent, spread orar, și breakdown GBP PMI —
vezi transcriptul complet din conversația FAZA 1b (aceleași comenzi, extinse
cu `g["actual"].nunique()` per grup și `(g["datetime_utc"].max() -
g["datetime_utc"].min())`).

## Cost cunoscut al gărzii fail-safe

Printul CHF `ppi_yoy` 2025-04-14 (`actual=0.1`) era valid ÎNAINTE de
`fix/can-be-zero-transform` și e exclus DUPĂ, ca urmare a excluderii
fail-safe a perechii divergente (0.0 vs 0.1, fără tiebreak defendabil).
Netul pe serie e n=37→37 doar pentru că widening-ul adaugă un print în altă
parte — nu e "neschimbat", e un print real schimbat cu altul.

Recuperarea lui depinde de rezolvarea problemei generale de duplicare
descrise mai sus. Nu se rezolvă separat.

## FAZA 0 (2026-08) — diagnostic D1-D5, verdict: SUB PRAG

Măsurătoare de continuare, `scripts/measure/archive_duplicate_diagnosis.py`
(read-only). Criteriul de acceptare fixat înainte de măsurătoare: un fix se
justifică doar dacă colapsarea tuturor celor 146 de grupuri produce ≥3 bias
flips pe perechi FX, sau ≥1 flip plus ≥10 serii cu schimbare de scor de
celulă.

### Verdict

Colapsând toate cele 146 de grupuri (regula 1c: identice → un rând,
divergente → excluse ambele/toate, fail-safe) — 22 grupuri identice
colapsate, 17 divergente excluse, pe 29 perechi (currency, indicator_key)
cu `n` modificat:

```
BIAS FLIPS: 0
Serii cu schimbare de scor de celulă: 0
```

**SUB PRAG.** Impactul pe scoring azi e zero. Cauza: majoritatea rândurilor
afectate cad în afara ferestrei de rulare K=12 folosite la z-score — sunt
printuri istorice (2023-2025), nu cele mai recente K prints ale seriei.

### D1 — DOUĂ fenomene distincte, separare curată

Max span în clasa ±1h = 1.25h; min span în cealaltă clasă = 4.25h — zero
suprapunere, nu e o distribuție cu coadă lungă.

| clasă | grupuri | % divergente | valute | indicator_key |
|---|---|---|---|---|
| ±1h DST | 57 | 52.6% | toate 8 valutele | 19 tipuri |
| aceeași zi, multi-h | 89 | **94.4%** | **doar CAD, GBP** | **doar manufacturing_pmi, services_pmi** |

**Ipoteză neverificată, notată pentru viitor**: clasa a doua nu arată ca
corupție — arată ca o serie de republicare STRUCTURATĂ de sursă: span-uri
discrete recurente (0.5h, 4.25h, 5.25h, 5.75h, 8h, 13.25h, 14.25h — nu
distribuție continuă), grupuri de 3-4 copii, fiecare cu `forecast` ȘI
`previous` diferit (nu doar `actual`). Dacă ipoteza se confirmă, cheia
noastră de grupare pe zi calendaristică e prea largă pentru acest tipar —
nu s-a investigat mai departe în această fază.

### D2 — GBP PMI (fenomenul dominant al clasei a doua)

`name_raw` 100% uniform (`Final Manufacturing PMI` / `Final Services PMI`)
— NU e coliziune Flash/Final, aliasul funcționează corect. Quality/Strength
diferă între copii în **61/75 (81%)** din grupuri. Niciun câștigător
sistematic la tiebreak (prima copie mai aproape de `previous`-ul
următorului print: 31 cazuri; ultima: 19; egalitate: 0) — **confirmă că
tiebreak-ul nu e automatizabil**, consistent cu observația independentă de
mai jos.

### D3 — 100% origine archive, ZERO chei brute duplicate

```
Rânduri COMPLET IDENTICE duplicate: 0
(Name, Currency, Date wall-clock) duplicate: 0
```

NU e artefact de download sau concatenare a arhivei — fiecare rând are un
(Name, Currency, Date) unic; sursa a trimis efectiv timestamp-uri distincte.
`data/jb_raw/` are 60 de grupuri proprii (înainte de curățare), dar
`clean_jblanked_actuals` grupează pe exact aceeași cheie
(`canonical_id`, `_date` — `src/jb_actuals.py:148,151`) și le colapsează
deja înainte de merge în parquet. Fenomenul e confinat structural la
backfill-ul din arhivă — nu e risc activ pe calea live.

### D5 — propagare prin `previous`: parțial confirmată

```
BACKWARD (previous-ul grupului == placeholder mai vechi din serie): 0/114
FORWARD (previous-ul printului URMĂTOR == o copie a acestui grup): 30/114 (26.3%)
```

Ipoteza specifică (propagare înapoi a unui 0.0 placeholder) infirmată
(0%). Tiparul mai larg — o dublură lasă urmă vizibilă în `previous`-ul
printului următor din serie — e real și nu izolat (26.3%), dar
neacționabil azi (vezi verdictul de mai sus).

### Dată de expirare a verdictului

"Sub prag" e valabil pentru fereastra K=12 curentă, nu permanent. Un grup
duplicat NOU care apare într-o zi scorată recent (ultimele K printuri ale
unei serii) NU are impact zero — verdictul de azi se bazează pe faptul că
duplicatele cunoscute sunt istorice. Reevaluează dacă watchdog-ul de
freshness semnalează un print CAD/GBP PMI anormal, sau dacă o nouă rulare a
`archive_duplicate_diagnosis.py` arată grupuri duplicate în ultimele ~12
luni pentru o serie scorată.

### Comenzi de reproducere

```
./.venv/bin/python scripts/measure/archive_duplicate_diagnosis.py
```

## Recomandare implicită (nu o decizie — doar unde ar trebui privit)

Problema aparține unui script de curățare aplicat retroactiv pe arhivă (gen
`migrations/`), separat de orice modificare în `src/jb_actuals.py` sau
`src/ff_scoring.py` — nu se rezolvă prin extinderea gărzii din acest branch,
care e deliberat scoped doar la ce a introdus widening-ul.
