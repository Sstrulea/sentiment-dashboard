# Arhivarea payload-urilor faireconomy (implementat)

Status: **implementat**. Branch `fix/alert-noise-and-ff-archive`, worktree
`../macro-dev`.

## De ce

`docs/calendar-freshness-per-currency.md` a semnalat limita: nu există
niciun payload weekly (program complet) salvat în repo — `data/jb_raw/`
conține doar actualele JBlanked, orientate retrospectiv. Distincția „nu
s-a publicat nimic" vs. „nu era nimic de publicat" rămâne aproximare fără
o arhivă a PROGRAMULUI.

Feed-ul faireconomy (`ff_calendar_thisweek.json`) conține exact ce
lipsește: titlu, țară, dată, impact, forecast, previous — programul
complet, nu doar ce s-a materializat.

**Utilitatea vine în timp, nu azi** — cum s-a cerut explicit, nu s-a încercat
rezolvarea discriminatorului acum, doar construirea colectării.

## Decizii, cu argument

### Cât de des

**O dată pe zi calendaristică, nu la fiecare tick** (~8/zi în Actions).
Argument: feed-ul SCHEDULE se schimbă mult mai rar decât actualele
JBlanked — programul unei săptămâni economice nu se rescrie de 8 ori/zi.
Un fișier per tick ar fi risipă (commit-uri repetate cu conținut aproape
identic). Poarta de cadență e chiar convenția de numire —
`ff_weekly_<YYYY-MM-DD>.json`, un singur fișier per zi — dacă fișierul
zilei există deja, salvarea e sărită, fără fișier de stare separat.

### Câte se păstrează

**90 de instantanee zilnice** (~3 luni), nu 14 ca la `jb_raw`. Argument:
scopul e diferit — `jb_raw` acoperă o fereastră scurtă pentru actualele
recente; arhiva asta trebuie să acopere **cicluri complete de publicare**
pentru discriminator. 90 de zile dă 2-3 cicluri complete pentru orice
indicator lunar (aceeași bară „cel puțin 2 printuri înainte de a judeca un
tipar" folosită la datele de revizuire din FAZA 1) și un ciclu complet
pentru serii trimestriale. La ~30-90 KB per payload (estimarea din task),
90 de fișiere înseamnă câțiva MB — mărginit, nu nelimitat, aceeași
mecanică de rotație (păstrează cele mai noi N) ca `jb_raw`.

### Deduplicare

**Da — comparație directă de conținut (bytes) cu ultimul instantaneu
salvat**, nu hash separat (echivalent, dar fără nevoia unui fișier de
stare suplimentar cu hash-uri). Dacă payload-ul de azi e identic cu cel
mai recent salvat, salvarea e sărită complet — evită commit-uri fără
informație nouă. Verificat cu teste: conținut neschimbat pe zi nouă →
`unchanged`, nimic scris; conținut schimbat → `saved`, fișier nou.

### Unde se leagă

**`src/ff_refresh.py::refresh()`, aditiv, propriul fetch independent** —
nu prin `parse_ff_weekly` (care face fetch+parsare într-un singur apel,
fără să expună textul brut). Motivul deciziei: a expune textul brut prin
`parse_ff_weekly`/`_load_json` ar atinge o funcție PARTAJATĂ (folosită și
de calea JBlanked-range), pentru o funcționalitate pur aditivă — un al
doilea GET, independent, pe același URL public fără cheie, ține riscul
izolat la un singur fișier nou (`src/ff_raw_archive.py`).

**Nu atinge contractul fail-open** — verificat, nu presupus:
- Apelul e învelit în propriul `try/except` (oglindește exact blocul
  FRED cross-check deja existent în `refresh()`), deci o eroare la arhivare
  loghează un warning și nu schimbă niciodată `rep["status"]`.
- Testat explicit: `test_archive_hook_failure_never_breaks_refresh` —
  arhivarea aruncă o excepție, `refresh()` tot raportează `"ok"` și
  merge-ul tot se scrie normal.
- **Bug prins și reparat înainte de commit**: legarea inițială făcea ca
  cele 6 teste existente din `tests/test_ff_refresh.py` care apelează
  `R.refresh()` direct să facă un apel de rețea REAL (fișierul avea
  documentat explicit „No network (fetchers injected)" — promisiune ruptă
  de schimbarea mea). Reparat: toate cele 6 apeluri au primit
  `"archive_ff_weekly": False`, la fel cum au deja `"run_fred_crosscheck":
  False` pentru exact același motiv. Timpul de rulare al fișierului a
  scăzut de la 1.27s la 0.62s după reparare — confirmă că apelurile de
  rețea chiar se întâmplau înainte.

## Verificări cerute — toate confirmate

| cerință | rezultat |
|---|---|
| Payload salvat, parsabil, acoperă toate cele 8 valute | ✓ `test_saved_payload_is_parsable_and_covers_all_8_currencies` |
| Rotația funcționează, nu crește nelimitat | ✓ `test_rotation_bounds_retained_files`, `test_rotation_reports_what_was_removed` |
| Ingest eșuat nu salvează payload gol/parțial | ✓ `test_failed_ingest_never_saves_empty_payload`, `test_failed_ingest_never_saves_garbled_json`, `test_non_array_payload_rejected`, `test_empty_array_rejected_as_invalid_not_saved` |
| Deduplicarea previne rescrierea | ✓ `test_dedup_skips_identical_content_on_a_new_day` (și confirmă invers: `test_dedup_does_not_skip_changed_content`) |
| `pytest` verde | ✓ 475 (463 + 12: 10 în `test_ff_raw_archive.py` + 2 în `test_ff_refresh.py`) |

## Ce NU s-a făcut

- Rezolvarea discriminatorului „programat vs. nepublicat" — colectarea
  construită acum, folosirea ei rămâne pentru peste câteva săptămâni/luni,
  cum s-a cerut explicit.
- Nicio schimbare de logică în `src/ff_refresh.py` — doar apelul aditiv,
  învelit, cu propriul eșec izolat.
- Niciun flag nou în `config/pipeline.yaml` — `archive_ff_weekly` are
  default `True` în cod, aceeași convenție ca `run_fred_crosscheck` (care
  nici el nu apare documentat în YAML).

## Reproducere

```bash
.venv/bin/python3 -m pytest tests/test_ff_raw_archive.py tests/test_ff_refresh.py -v
.venv/bin/python3 -m pytest -q   # 475 verzi
```
