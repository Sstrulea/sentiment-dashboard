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

## Recomandare implicită (nu o decizie — doar unde ar trebui privit)

Problema aparține unui script de curățare aplicat retroactiv pe arhivă (gen
`migrations/`), separat de orice modificare în `src/jb_actuals.py` sau
`src/ff_scoring.py` — nu se rezolvă prin extinderea gărzii din acest branch,
care e deliberat scoped doar la ce a introdus widening-ul.
