# FAZA 1 — conectarea gărzii PMI: blocată, un al treilea obstacol găsit

Status: **investigație — cod scris, testat, apoi REVERTAT înainte de commit.**
Branch `fix/guard-units-visibility`, worktree `../macro-dev`. FAZA 1 se
oprește aici; FAZA 2 și FAZA 3 continuă independent, cum a permis task-ul.

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

## Opțiuni pentru continuare — decizie a utilizatorului

1. **Limitează garda la datele NOI ale ciclului curent**, nu la tot
   istoricul. `country_hour_guard(merged)` tot primește istoricul complet
   (are nevoie de el pentru fereastra de referință), dar scrierea în
   carantină s-ar limita la rândurile a căror cheie
   `(canonical_id, datetime_utc)` există în payload-ul `weekly` proaspăt
   parsat — adică "a apărut ceva nou azi care deviază", nu "orice a
   deviat vreodată în istorie". Cea mai mică schimbare, cel mai apropiată
   de intenția inițială (prinde contaminare NOUĂ la ingest, nu re-judecă
   istoricul).
2. **Curăță manual cele 23 cunoscute** (documentate mai sus) ca excepții
   explicite înainte de a activa scanarea completă — risc: clusterul JPY
   rămâne nerezolvat, deci orice excepție acolo ar fi o presupunere, nu o
   verificare.
3. **Altă propunere** — dacă niciuna din cele de mai sus nu se potrivește.

Recomand opțiunea 1 — e cea mai mică, cea mai aproape de ce task-ul a
cerut literal, și nu cere să rezolv acum cele trei fenomene găsite (rămân
documentate, nu ascunse).

## Ce NU s-a făcut

- Nicio schimbare în `src/ff_refresh.py` sau `tests/test_ff_refresh.py` —
  scrise, testate, apoi revenite (`git checkout --`).
- `src/pmi_ingest_guard.py` neatins — rămâne pur, testat separat.
- Nicio decizie luată pe cele 23 de rânduri — documentate, nu carantinate.

## Livrabile

- `docs/faza1-pmi-guard-wiring.md` — acest document.
