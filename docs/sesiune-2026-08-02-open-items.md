# Sesiune 2026-08-02 — ce rămâne deschis

Status: constatări, fără propuneri de fix.

## a) AUD, print 2026-07-29 — NERECUPERABIL din sursa curentă

JBlanked a livrat ambele serii marcate `Quality=Bad Data`.

- "Trimmed Mean CPI m/m" a venit `Actual=0.0` — indistinct de placeholder.
- "CPI m/m" a venit `-0.1` și ESTE validă în parquet, dar `cpi_monthly` e
  `inflation_display` (weight 0), deci nu apare la scoring.

Orice regulă care ar recupera printul aruncă date bune în altă parte
(dovedit pe CAD Employment Change 18.2, fixture 2026-07-12).

Deblocare posibilă doar prin: a doua sursă pentru AUD, sau promovarea
`cpi_monthly` din display-only (blocată de baseline insuficient: 7 rânduri
în arhivă, 1 în parquet).

## b) AUD `core_cpi` — 277 zile, serie moartă (ABS a schimbat cadența)

Înlocuitorul `trimmed_mean_cpi_monthly` are n=6 chiar cu backfill, sub prag.

Decizie luată, neexecutată: retragere AUD din `core_cpi.currencies` +
eliminare `frequency_overrides: {AUD: quarterly}` devenit mort.

## c) GBP/CAD PMI — 107/150 și 39/82 rânduri din arhivă lipsă din parquet

Alias vechi de o lună, deci NU e "alias mai nou decât ultima ingerare".

Aceleași serii apar în cele 89 de grupuri "aceeași zi, multi-h" din
`docs/faza1c-archive-duplicate-integrity.md`.

Ipoteză neverificată: merge-ul în parquet colapsează pe o cheie fără oră,
deci suprascrie în loc să păstreze.

Consecință: verdictul "duplicare, sub prag" din faza1c a fost măsurat în
parquet, adică pe date deja trunchiate — nu e validat.

## d) Backfill arhivă — CAD `common_cpi` și `trimmed_cpi` lipsesc 100%

Excluse deliberat din migrarea 2026-07-30. Ambele display-only, impact zero
azi.

## e) USD `core_cpi` — variantă nativă y/y există în feed sub raw name nemapat

n=3 (start 2026-05-12), prag n>=24 atins ~mijlocul lui 2028.

Opțiune neexecutată: mapare display-only ca să acumuleze istoric.

## f) Watchdog — review-uri programate

- 2026-08-20 — USD `core_cpi`
- 2026-09-01 — AUD `core_cpi`

Plus decizia dacă devine blocant, după o săptămână de rulare non-blocantă.
