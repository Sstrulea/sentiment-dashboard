# FAZA 3 — implementarea celor 17 candidați aprobați

Status: **implementat.** Branch `eval/bucket-c-candidates`, worktree
`../macro-dev`.

**Aprobare**: toți cei 17 candidați "adaugă" din
`docs/bucket-c-merit-evaluation.md`, motivată de faptul că niciun
clasament disponibil (merit FAZA 1, frecvență FAZA 2/panel) nu justifică
o selecție parțială — vezi `docs/bucket-c-panel-measurement.md` pentru
interpretarea completă a corelației negative merit↔frecvență.

## Ce s-a schimbat

- **`config/ff_aliases.yaml`** — 17 alias-uri identitate noi (nume brut FF
  → același nume ca `name_canonical`; nu există un echivalent MT5 de
  reutilizat, aceeași convenție ca la CAD Median CPI / AUD Household
  Spending).
- **`data/economic_indicators.yaml`** — 12 `indicator_key` noi (câteva
  partajate între valute pentru același concept: `industrial_production_mm`
  la USD/GBP/JPY, `import_prices` la USD/AUD, `capital_expenditure` la
  AUD/JPY, `gdp_price_index` la JPY/USD) + 17 reguli de matcher, câte una
  per (țară, nume canonic), pe cele 4 țări atinse (United States, United
  Kingdom, Japan, Australia). Toate cu `direction: 1` (toate cele 17 au
  fost evaluate "clar bullish la creștere" în FAZA 1 — niciun candidat cu
  direcție ambiguă din găleata "întreabă" nu e aici), `weight: 1.0`
  (scorate real, nu display-only).
- **`src/economic_render.py`** — 12 etichete noi în `INDICATOR_LABELS`
  (pentru modalul de detaliu). **Nu** s-au adăugat coloane noi în
  `TABLE_LAYOUT` (tabelul dens de pe pagina principală) — task-ul a cerut
  explicit doar eticheta, nu o schimbare de layout vizual; asta rămâne o
  decizie separată dacă se dorește.
- **`data/economic_calendar_ff.parquet`** — 543 rânduri noi, via
  `migrations/2026-08-01_backfill_bucket_c_candidates.py`. Backup la
  `data/economic_calendar_ff.parquet.pre-backfill-bucket-c` (păstrat,
  aceeași convenție ca backfill-ul CAD Median CPI).
- **Teste noi**: `tests/test_bucket_c_indicators.py` (rezoluția matcher-ului,
  `currencies:` whitelist per indicator, direcție, etichete, cazul JPY
  flash vs. USD/GBP headline pentru `industrial_production_mm`),
  `tests/test_backfill_bucket_c_pmi_guard.py` (garda PMI, mirror exact al
  `tests/test_backfill_pmi_guard.py`).

## Verificare explicită: GBP/USD Industrial Production, cerută direct

Migrarea combină **DOUĂ surse**, nu doar arhiva înghețată: fiecare fișier
`data/jb_raw/jb_range_*.json` (14 instantanee recente JBlanked) e adăugat
la `data/archive/ff_calendar_range.json`, deduplicat pe (Currency, Name,
Date) exact. Rezultat, verificat direct în parquet-ul de producție după
migrare:

```
GBP Industrial Production m/m: n=44, 2023-01-12 → 2026-07-16
USD Industrial Production m/m: n=42, 2023-01-17 → 2026-07-17
```

Ambele acoperă acum până la mijlocul lui iulie 2026 — **nu mai sunt
permanent stale**. Verificat prin randare: `USD/growth` a ajuns la N=8
(patru noi: Industrial Production, Durable Goods, Personal Spending,
Personal Income — toate proaspete), `GBP/growth` la N=4 (Industrial
Production proaspăt).

## Două constatări de date, verificate — nu bug-uri

Randarea a arătat 2 din 17 candidați cu contribuție mai mică decât
"naiv" așteptat. Ambele verificate până la evenimentul brut — sunt
carantina zero-placeholder existentă funcționând corect pe date noi, nu
un defect de implementare:

- **`JPY capital_expenditure`** (Capital Spending q/y): ultimul print cu
  `actual` valid e 2026-03-02 (stale azi, >110 zile). Printul următor
  (2026-05-31 în arhivă / 2026-06-01 brut) are `"Actual": 0.0,
  "Quality": "Bad Data"` — carantina zero-placeholder deja existentă
  (`ff_scoring.to_scoring_frame`, `can_be_zero: False` implicit) îl
  transformă corect în NaN, exact ca la orice alt indicator. `JPY/growth`
  a ajuns la N=5, nu N=6 "naiv" — corect, nu o eroare.
- **`AUD import_prices`** (Import Prices q/q): ultimul print bun e
  2026-01-29. Următoarele DOUĂ printuri așteptate (aprox. aprilie și
  iulie 2026) sunt AMBELE `"Actual": 0.0, "Outcome": "Data Not Loaded"`
  în sursă — un gol real de date la sursă (JBlanked), nu ceva introdus de
  migrare. `AUD/inflation` a rămas la N=2 (nu N=3) — corect, se va
  actualiza singur când sosește un print curat.

Ambele sunt exact mecanismul deja documentat în proiect (carantina
zero-placeholder) funcționând ca de obicei — nicio schimbare de cod aici,
doar verificare că funcționează corect pe cei 12 indicatori noi.

## Verificare finală

- **Garda PMI**: neschimbată înainte/după (verificat programatic în
  migrare + test de regresie).
- **485 teste verzi** (475 existente + 10 noi).
- **Randare completă**: `public/data/economic.json` regenerat, verificat
  manual N-ul fiecărei (valută, categorie) atinse against așteptările
  (cu cele 2 excepții explicate mai sus).

## Ce NU s-a făcut

- Nicio coloană nouă în tabelul dens de pe pagina principală
  (`TABLE_LAYOUT`) — doar etichetă pentru modal, cum s-a cerut.
- Cei 7 candidați "monetary" — neatinse (decizie de arhitectură separată).
- Cei 14 "întreabă" — neatinse.
- `can_be_zero` per-indicator/per-currency — neatins (blocat până
  ~2026-08-04); cele două goluri de date de mai sus rămân sub carantina
  globală existentă.

## Livrabile

- `docs/bucket-c-implementation.md` — acest document.
- `config/ff_aliases.yaml`, `data/economic_indicators.yaml`,
  `src/economic_render.py` — modificate.
- `data/economic_calendar_ff.parquet` — 543 rânduri noi.
- `data/economic_calendar_ff.parquet.pre-backfill-bucket-c` — backup.
- `migrations/2026-08-01_backfill_bucket_c_candidates.py`.
- `tests/test_bucket_c_indicators.py`,
  `tests/test_backfill_bucket_c_pmi_guard.py`.
