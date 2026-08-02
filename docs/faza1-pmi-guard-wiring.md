# FAZA 1 — conectarea gărzii PMI

Status: **implementat (opțiunea 1, aprobată).** Branch
`fix/guard-units-visibility`, worktree `../macro-dev`.

## Cele două obstacole cunoscute — rezolvate, cu design concret

### Decizia de arhitectură: un singur fișier partajat, nu două

**`data/ff_quarantine.parquet` rămâne partajat între FRED și garda PMI.**

Argument, pe cele trei criterii cerute:
- **Cum e citit azi**: `economic_render.py::_load_calendar_frame` face O
  singură citire + UN singur join de excludere, cheiat pe
  `(currency, indicator_key, release_dt)`. Mecanismul de excludere e
  IDENTIC pentru orice motiv de carantină — consumatorul nu are nevoie
  să știe DE CE un rând e exclus, doar CĂ e exclus.
- **Ce presupune consumatorul**: exact acea cheie de trei coloane. Nimic
  altceva. Un fișier separat ar cere un al doilea `read_parquet` + un al
  doilea join în `economic_render.py` — cod nou exact în locul unde
  defectul de schemă (obstacolul #2) s-a produs deja o dată, nedetectat.
- **Ce e mai simplu de întreținut**: un fișier, o schemă lată (uniunea
  coloanelor FRED + PMI, cu `NaN` pe coloanele nerelevante per rând), o
  coloană `reason` care distinge `fred_mismatch` de `country_mismatch`
  pentru orice audit manual. Verificat: fișierul curent (`ff_quarantine.
  parquet`) e gol azi (0 rânduri) — nu există migrare de schemă reală de
  făcut, doar convenția de scriere trebuie extinsă.

### Obstacolul 1 (`to_parquet` suprascrie) — rezolvat

Ambele verificări (FRED, PMI) își recalculează STAREA COMPLETĂ curentă la
fiecare rulare (nu sunt incrementale) — asta explică de ce codul actual
funcționează "din întâmplare" azi (FRED e singurul scriitor). Fix
proiectat: la scriere, se citește fișierul existent, se ÎNLOCUIESC doar
rândurile cu `reason` corespunzător verificării care tocmai a rulat cu
SUCCES, păstrând neatinse rândurile provenite din verificarea care a EȘUAT
în acest ciclu (fail-open per verificare, nu doar per bloc). Testat: codul
scris trecea `pytest` (485 verzi) și supraviețuia unui ciclu FRED-apoi-PMI.

### Obstacolul 2 (schema `canonical_id` vs `indicator_key`) — rezolvat

`country_hour_guard` întoarce `canonical_id` (nu `indicator_key`).
Rezolvare: în stratul de legare din `ff_refresh.py` (NU în
`pmi_ingest_guard.py`, care rămâne neschimbat, pur, testat separat), se
recuperează `name_canonical` din `merged` (join pe
`currency, canonical_id, datetime_utc`) și se trece prin `build_matcher()`
— exact tehnica deja folosită de `crosscheck_us` pentru USD. Rândurile
care nu rezolvă la niciun `indicator_key` (deja nemapate, deci deja
excluse din scoring) sunt scoase din scrierea finală, cu un log INFO, nu
WARNING.

## Un al treilea obstacol, negăsit până acum — de ce m-am oprit

Verificarea cerută explicit ("Rulat pe `data/jb_raw/` curent: zero
carantinări... Dacă apare vreuna, e fals pozitiv și oprești") **trece** —
`tests/test_pmi_ingest_guard.py::test_pmi_ingest_guard_jb_raw_regression`
confirmă deja zero pe pull-urile recente.

**Dar legarea reală nu rulează garda pe `jb_raw` — rulează pe `merged`,
parquet-ul istoric COMPLET**, mirroring exact blocul FRED. Am rulat asta
direct, înainte de a avea încredere în cod: **23 de rânduri carantinate**,
pe 6 valute, inclusiv indicatori centrali — `usd_nonfarm_payrolls`,
`usd_core_pce_price_index`, `usd_personal_income`, `usd_personal_spending`,
`usd_ppi`, `usd_average_hourly_earnings`, `usd_jolts_job_openings`,
`usd_initial_jobless_claims`, `usd_unemployment_rate`, `usd_industrial_
production`, plus `cad_core_cpi` (×4), `chf_ppi`, `chf_retail_sales`,
`chf_unemployment_rate`, `gbp_ppi_output`, `jpy_core_cpi`,
`jpy_unemployment_rate`.

**Asta infirmă direct presupunerea din task** ("oglindind blocul FRED" ar
produce un rezultat la fel de sigur) — motivul e structural: `crosscheck_
us` verifică DOAR cel mai recent print per indicator US (rază de acțiune
mică, per design). `country_hour_guard`, cum e scris, evaluează FIECARE
rând istoric care are o fereastră anterioară suficientă — aplicat pe
`merged` (ani de istorie), scanează totul, nu doar ce-i nou.

### Ce sunt de fapt cele 23 — verificat, nu presupus

Nu e contaminare de tip "PMI GBP/CAD pe oră SUA", tiparul pe care garda a
fost proiectată și validată să-l prindă. Sunt (cel puțin) **trei fenomene
diferite**, niciunul din ele fiind acel tipar:

**Clusterul USD (12 din 23)** — toate căzute pe 2025-10-02, 2025-12-15,
2026-02-08: exact fereastra shutdown-ului guvernamental SUA din
octombrie-noiembrie 2025 (documentat public, 43 de zile, a întârziat
publicările BLS/BEA). `usd_nonfarm_payrolls`: un print anormal pe
2025-10-02 21:00 (actual=0.0), apoi un gol de **7 săptămâni** până la
următorul print normal (2025-11-20 13:30, actual=119.0) — exact
calendarul dezorganizat al unei publicări întârziate de shutdown, nu o
etichetă de țară greșită. Carantinarea lor ar șterge date istorice reale
și neobișnuite, nu date corupte.

**Clusterul CHF/GBP (4 din 23)** — un tipar complet diferit: același
eveniment apare de 2-3 ori la ~9-10 ore distanță, cu `actual=0.0`
(placeholder, neeliberat încă) urmat de valoarea reală a doua zi la ora
normală (`chf_ppi` 2025-04-13 21:00 actual=0.0 → 2025-04-14 06:30
actual=0.0 → 2025-04-14 06:35 actual=0.1). Pare un rând "fantomă"
programat/preluat cu o zi înainte, păstrat separat de releasul real —
un defect real, dar diferit (rânduri duplicate pentru aceeași perioadă
de referință), nu contaminare de țară.

**Clusterul JPY (2 din 23)** — ambele cad exact pe 2024-02-29 (zi
bisectă). Nerezolvat — insuficient timp alocat să confirm dacă e coincidență de dată sau un bug de conversie specific anilor bisecți; flag deschis, nu presupun un răspuns.

## De ce m-am oprit aici, nu am continuat să "repar" garda

Task-ul cere explicit: "Dacă o verificare infirmă o presupunere,
oprește-te înainte de a scrie cod și raportează." Scrisesem deja codul de
legare (funcțional, testat, 485 verzi) înainte de a rula verificarea de
mai sus pe parquet-ul real — verificarea a venit ultima, cum s-a cerut
explicit în secțiunea de verificări, dar rezultatul infirmă o presupunere
de bază (că "oglindirea FRED" e suficientă). Am **revenit codul** (`git
checkout --`) în loc să-l păstrez necomis — nu era sigur de lăsat pe disc
ca și cum ar fi gata.

## Decizia adoptată: opțiunea 1 — doar rândurile noi ale ciclului curent

`country_hour_guard(merged)` tot primește istoricul complet (are nevoie
de el pentru fereastra de referință a orei dominante), dar scrierea în
carantină se limitează la rândurile a căror cheie
`(canonical_id, datetime_utc)` există în payload-ul `weekly` proaspăt
parsat din acest ciclu — "a apărut ceva nou care deviază", nu "orice a
deviat vreodată în istorie".

**Motivul, nu doar dimensiunea**: contaminarea cross-country e o
proprietate a sursei LA MOMENTUL LIVRĂRII. Un rând nou cu oră deviantă
e suspect acum, pentru că tocmai a sosit. Unul vechi poate fi contaminare
— dar la fel de bine poate fi un shutdown, o publicare de urgență, sau o
revizuire (exact ce s-a găsit în clusterul USD). Garda judecă doar ce
poate judeca, prin construcție — fără să inventeze o distincție pe care
n-o poate face din oră singură.

### Implementare

`src/ff_refresh.py`, în blocul de după merge (fostul bloc FRED, acum
FRED + PMI unificate): `country_hour_guard(merged)` rulează pe tot
istoricul (pentru fereastra de referință), apoi rezultatul e filtrat la
`(canonical_id, datetime_utc)` prezente în `weekly` — nu în `merged`.
Restul (enrichment `indicator_key` via matcher, scrierea partajată în
`ff_quarantine.parquet` cu păstrarea rândurilor din verificarea care a
eșuat acest ciclu) rămâne cum a fost proiectat la obstacolele 1 și 2.

### Verificări — toate 5, rulate concret

1. **Rulat pe starea curentă reală**: `test_pmi_guard_real_current_
   parquet_zero_quarantines` — parquet-ul de producție ca `existing`,
   un payload `weekly` normal (doar evenimente din "săptămâna curentă",
   fără nicio legătură cu cele 23 de anomalii istorice) → **zero**
   carantinări cu `reason=country_mismatch`. Cele 23 rămân neatinse.
2. **Rând nou cu oră străină** (`test_pmi_guard_quarantines_a_new_
   foreign_hour_row`) → carantinat, cu `indicator_key` rezolvat corect.
3. **Același rând, doar în istoric** (`test_pmi_guard_ignores_the_same_
   anomaly_when_only_in_history`) → ignorat; verificat explicit că
   `country_hour_guard` NEFILTRAT tot l-ar fi prins (dovadă că excluderea
   vine din filtrul de ciclu, nu dintr-un accident).
4. **Carantina FRED supraviețuiește** unui ciclu complet cu garda PMI
   activă simultan (`test_fred_quarantine_survives_a_cycle_with_pmi_
   guard_also_enabled`).
5. **`pytest`**: 489 verzi (485 + 4 noi).

## Ce NU s-a adăugat: verificarea de plauzibilitate a valorii

**Respinsă, deliberat, pentru acum** — nu pentru că ideea e greșită
(motivul dat e corect: contaminarea aduce o valoare dintr-o distribuție
greșită, o publicare întârziată rămâne în distribuția proprie a seriei),
ci pentru că **singurele 23 de rânduri disponibile pentru a o calibra
nu conțin niciun al doilea caz confirmat de contaminare reală** — sunt
shutdown SUA, triplicare CHF/GBP, și 2 rânduri JPY nerezolvate. Construi
o regulă de plauzibilitate acum ar însemna curve-fit pe aceste 23,
optimizat să le clasifice corect retroactiv, fără nicio garanție că
generalizează la un caz NOU de contaminare reală (singurul lucru pentru
care garda a fost construită și validată — cele 219 rânduri PMI
GBP/CAD din decontaminarea 2026-07-30).

**Consemnat ca variantă complementară pentru viitor**: dacă apare un al
doilea caz confirmat de contaminare cross-country (nu doar o disruptare
de tip shutdown), o verificare de plauzibilitate a valorii (ex.
comparație cu distribuția robustă — mediană/MAD — a seriei proprii,
similar cu ce s-a folosit deja în auditul candidaților bucket-C pentru
detectarea seriilor compuse) ar putea completa ora ca al doilea semnal
independent. Nu se construiește azi.

## Fire deschise, separate — consemnate, nu rezolvate azi

### 1. Triplicarea CHF/GBP — defect de deduplicare, nu de gardă

Același eveniment apare de 2-3 ori la ~9-10 ore distanță: primele 1-2
apariții cu `actual=0.0` (placeholder, neeliberat încă), ultima cu
valoarea reală (`chf_ppi`: 2025-04-13 21:00 actual=0.0 → 2025-04-14
06:30 actual=0.0 → 2025-04-14 06:35 actual=0.1). Nu-i o decizie
"contaminare sau nu" — ambele rânduri vin de la sursa corectă, pentru
perioada corectă. E o problemă de **care rând păstrezi** dintre mai
multe pentru aceeași perioadă de referință — deduplicare, nu carantină.
Nici verificarea de oră, nici cea de plauzibilitate a valorii (dacă s-ar
construi vreodată) nu rezolvă asta direct — ar cere o regulă separată
(ex. dacă există un rând mai nou pentru aceeași perioadă de referință
cu `actual` valid, la <24h distanță, și rândul mai vechi are
`actual=0.0` — păstrează doar cel mai nou). Nefăcut azi — în afara
scopului FAZA 1.

### 2. USD GDP — Q3 și Q4 2025 lipsesc complet, nerecuperabil

`usd_gdp` (Advance GDP q/q) are cadență trimestrială curată din 2023
până la 2025-07-30 — apoi Q3 2025 (aștept ~2025-10-30) și Q4 2025
(aștept ~2026-01-30) **lipsesc complet, nu doar întârziate**. Un singur
print de "recuperare" apare la **2026-02-20** (`actual=0.0,
forecast=2.8`) — nici măcar prezent în `data/archive/ff_calendar_range.
json`. Aceeași fereastră ca shutdown-ul SUA din clusterul de mai sus.
**Nerecuperabil din sursele disponibile** (arhivă, `jb_raw`, `ff_raw` —
niciuna nu are Q3/Q4 2025 GDP pentru SUA). Consemnat, nu reparat — ar
cere o sursă externă (BEA direct) în afara scopului acestei task.

### 3. `employment_change: can_be_zero: true` — verificat, e o problemă reală și ACTIVĂ azi

Verificat direct, nu presupus: `wage_growth` și `unemployment_rate` (fără
`can_be_zero`) au propriile placeholder-uri `0.0` din 2025-10-02 corect
neutralizate (NaN) de carantina zero-placeholder existentă. **`employment_
change` (NFP), cu `can_be_zero: true`, NU** — rândul din 2025-10-02
(`actual=0.0, consensus=52.0`) rămâne activ ca o "citire legitimă de
0 locuri de muncă noi".

Verificat și impactul: numărând înapoi de la cel mai recent print USD
NFP (azi, 2026-08), acest rând e al **11-lea din ultimele 12** —
**înăuntrul ferestrei curente de rulare a sigma** (`surprise_window_k:
12`). O surpriză falsă de -52 (actual 0 vs consensus 52) infla artificial
sigma pentru `employment_change`/USD chiar acum, ceea ce comprimă
(dampens) toate scorurile z recente ale acestui indicator pentru USD —
nu doar o curiozitate istorică. Nefăcut azi (ar fi o schimbare de
scoring/carantină în afara scopului FAZA 1) — dar semnalat explicit ca
o problemă REALĂ, verificată, nu o presupunere.

## Ce NU s-a făcut

- Nicio verificare de plauzibilitate a valorii — argumentat mai sus,
  consemnată ca variantă viitoare, nu construită.
- Cele 3 fire deschise (deduplicare CHF/GBP, USD GDP lipsă, `can_be_zero`
  pe employment_change) — documentate, nicio reparație.
- `src/pmi_ingest_guard.py` neatins — rămâne pur, testat separat.

## Livrabile

- `docs/faza1-pmi-guard-wiring.md` — acest document.
- `src/ff_refresh.py` — legarea (opțiunea 1).
- `tests/test_ff_refresh.py` — 4 teste noi.
