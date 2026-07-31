# FAZA 4 — implementarea gărzii de ingest (docs/proposal-pmi-ingest-guard.md)

Status: **implementat, testat, NEwired în pipeline-ul live** (motiv: punctul
de scriere e în `src/ff_refresh.py`, explicit în afara scopului acestei
faze). Branch `fix/remaining-data-defects`, worktree `../macro-dev`.

## Ce s-a implementat

`src/pmi_ingest_guard.py` — funcție pură `country_hour_guard(df, ...)`,
oglindind exact arhitectura `src/ff_fred_crosscheck.py::crosscheck_us`:
primește un cadru canonic (schema `_canonicalize`), întoarce rânduri
candidate la carantină `(currency, canonical_id, datetime_utc, local_hm,
dominant_local_hm, deviation_hours, reason='country_mismatch')`. **Nu scrie
nimic** — la fel ca `crosscheck_us`, scrierea în `data/ff_quarantine.parquet`
e responsabilitatea `src/ff_refresh.py`, care nu a fost atins.

Regula, exact cum era propusă: pentru fiecare rând `released`, conversie
`datetime_utc` → ora locală a propriei valute (harta de 8 valute deja
validată în Faza 3a/3c), comparație cu **modul orei locale** peste
fereastra trailing (`trailing_n=24` rânduri `released` anterioare, în
aceeași `canonical_id`), semnalare dacă abaterea (circulară, peste
miezul-nopții) depășește `deviation_hours=2.0`. Exclus explicit:
`interest_rate_decision` (fără oră fixă) și orice serie cu mai puțin de
`min_prints=8` printuri anterioare (bază prea subțire pentru un mod
fiabil).

**De ce ora locală, nu UTC**: rămâne constantă peste schimbarea DST —
verificat direct (Faza 3c) pe toate cele 8 valute, deci un mod calculat pe
ora locală nu are nevoie de nicio corecție DST și nu marchează niciodată o
schimbare normală de oră ca abatere.

## Teste — 7, toate trei cerute + 4 suplimentare

`tests/test_pmi_ingest_guard.py`:

1. `test_foreign_hour_is_quarantined` — un rând cu oră străină e carantinat.
2. `test_correct_hour_passes` — o serie cu oră constantă nu semnalează nimic.
3. `test_dst_shift_is_not_a_false_positive` — o serie care își schimbă ora
   UTC exact la tranziția DST (păstrând ora locală fixă) nu semnalează nimic.
4. `test_interest_rate_decision_excluded` — exclus indiferent de cât de
   erratică e ora.
5. `test_thin_series_below_min_prints_is_skipped` — sub prag, nicio
   semnalare.
6. `test_unreleased_rows_never_checked` — un rând viitor (actual NaN) nu e
   verificat niciodată.
7. `test_pmi_ingest_guard_jb_raw_regression` — rulează garda pe TOATE
   payload-urile reale reținute în `data/jb_raw/` (14 fișiere, parsate prin
   `parse_jblanked_range` de producție) — **0 rânduri carantinate**,
   confirmă că ingestul normal de azi trece curat.

Suita completă: **437 teste verzi** (430 + 7 noi).

## Validare suplimentară — garda ar fi prins contaminarea reală

Rulat (informal, nu parte din suita de teste) pe
`data/economic_calendar_ff.parquet.pre-purge` (instantaneul cu cele 219
rânduri PMI contaminate încă prezente, dinainte de purja din 30 iulie):
**197 rânduri semnalate**, din care 55+82+40=177 cad exact în cele 3
`canonical_id` cunoscute contaminate (`cad_s_p_global_manufacturing_pmi`,
`gbp_s_p_global_cips_manufacturing_pmi`, `gbp_s_p_global_cips_services_pmi`).
Restul (20 de rânduri, pe alte 11 serii — CAD Core CPI, CHF PPI/Retail
Sales/Unemployment, GBP PPI, JPY Core CPI/Unemployment, USD Average Hourly
Earnings/Core PCE/Jobless Claims/JOLTS/NFP/PPI/Unemployment) sunt cazuri de
drift legitim de orar — exact riscul deja documentat în propunere
("Legitimate schedule changes" — CHF Unemployment, CAD BoC — genera o mână
de semnalări false-pozitive până se stabilizează modul), nu contaminare
nouă. **Nu s-a acționat pe acestea aici** — sunt semnal pentru un om care
citește log-ul de carantină, exact cum descrie propunerea, nu motiv de
ajustare a pragului.

## Ce NU s-a făcut (explicit în afara scopului)

- **Wiring în `src/ff_refresh.py`** — punctul unde `crosscheck_us` e deja
  apelat și scris în `data/ff_quarantine.parquet`; fișierul e explicit
  exclus din scopul acestei faze. Funcția e completă și testată, gata de
  conectat printr-o schimbare separată.
- **Adăugarea coloanei `reason` în schema `data/ff_quarantine.parquet`** —
  parte din wiring, aceeași excludere.
- **Nicio recalibrare a pragurilor** (`deviation_hours`, `trailing_n`,
  `min_prints`) — valorile din propunere, nemodificate.

## Reproducere

```bash
.venv/bin/python3 -m pytest tests/test_pmi_ingest_guard.py -v
.venv/bin/python3 -m pytest -q   # suita completă, 437 verzi
```
