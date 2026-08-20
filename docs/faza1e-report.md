# FAZA 1E — Fix GBP gdp_qoq (calea de scoring, gate deschis)

Worktree `../macro-dev`, branch NOU `fix/gbp-gdp-frequency`, creat din `main` (nu din
`feat/history-catalog`). Zero merge. Gate deschis DOAR pentru acest fix (config, aceeași
formă ca la CAD) — `ff_scoring.py`, `economic_compute.py`, `manual_actuals.py` neatinse.

## PARTEA 1 — diagnostic

### 1.1 — fixul CAD

Commit `013b40c` ("econ: CAD gdp_qoq frequency_override, fixing a dedup-collapse to pct
fallback"). Diff: un singur rând YAML în `data/economic_indicators.yaml`, sub `gdp_qoq`:

```yaml
frequency_overrides: { CAD: monthly }
```

Mecanism (din mesajul de commit): CAD's gdp_qoq e alimentat via `config/ff_aliases.yaml`
(`"GDP m/m": "GDP q/q"  # xf`) — date lunare reale (gap-uri 24-39 zile) rutate într-un slot
declarat trimestrial. `frequency: quarterly` dă lui `_dedup_flash_final` un `dedup_gap` de
45 zile; cum niciun gap real CAD nu depășește 45 zile, întreaga istorie (42 rânduri)
colapsează într-un singur cluster flash/final → 2 păstrate → 1 cu consensus valid →
`n_pairs=1`, sigma=nan, fallback procentual forțat, saturând un beat obișnuit de 0.1pp pe
un consensus de 0.40 (25%) la +2. Aceleași date z-scored: sigma=0.109, z=0.923, scor +1.
A funcționat pentru că a corectat exact premisa greșită pe care se baza `_dedup_flash_final`
(cadența DECLARATĂ, nu cea reală).

### 1.2 — de ce nu s-a extins la GBP atunci

Nu a fost o scanare incompletă a aceluiași bug — GBP a ajuns la simptom identic pe o cale
DIFERITĂ, decisă mai devreme, pentru alt motiv:

- Ruta GBP nu trece prin `config/ff_aliases.yaml` (acolo GBP are `"GDP m/m": "GDP m/m"`,
  identitate, nu remapare spre "GDP q/q"). GBP ajunge la `indicator_key: gdp_qoq` prin
  propriul matcher din `data/economic_indicators.yaml`:
  ```yaml
  United Kingdom:
    # UK discontinued "GDP q/q" (last 2025-05); the live monthly GDP release with
    # consensus is "GDP m/m". ...
    - { pattern: '^GDP m/m$', indicator: gdp_qoq }
  ```
  Acest rând (commit `95e214fd`, **2026-06-11**) e anterior fixului CAD (`013b40c`,
  **2026-08-05**) cu aproape două luni — decizie separată, documentată, motivată de ONS
  întrerupând publicarea "GDP q/q" în 2025-05, nu o extensie a soluției CAD.
- Comentariul de atunci ("the gdp_qoq quarterly window (110d) keeps this ~2-monthly print
  current") arată că autorul a raționat corect despre FERESTREAA DE PROSPEȚIME
  (`max_age_days`, 110 zile e suficient pentru un print la ~2 luni), dar nu a legat asta
  de consecința asupra `dedup_gap` — un consumator DIFERIT al aceluiași câmp `frequency`,
  mecanism care abia în august a fost înțeles (fixul CAD).
- Commit-ul CAD însuși spune explicit "checked every other (currency, indicator_key) pair"
  — dar acel pre-flight a verificat EFECTE SECUNDARE ale noului override (nimic altceva nu
  se schimbă), nu o scanare exhaustivă pentru ALTE cazuri ale aceluiași mecanism. GBP nu
  era în domeniul acelei verificări.

Verdict: parțial scăpare (mecanismul e identic și era descoperibil), parțial cauză
diferită (ruta de matching a fost o decizie GBP-specifică, mai veche, motivată corect
pentru alt aspect al aceleiași configurații).

### 1.3 — confirmare cu date (mecanismul, nu doar simptomul)

Cadență declarată: `quarterly` (implicit pe `gdp_qoq`, fără override GBP). Cadență
empirică (`detect_cadence` pe cele 44 de printuri reale GBP "GDP m/m" din parquet,
2023-01-12 → 2026-08-13): **monthly** (gap median 29 zile, min 1, max 63).

Lanțul exact, rulat direct pe `to_scoring_frame`'s output (44 rânduri, fără coloană
`period` utilizabilă în acest feed → `_dedup_flash_final` cade pe fallback-ul de
proximitate):

```
_dedup_flash_final(sub, gap_days=45)   # cadență declarată (quarterly)
  -> 2 rânduri păstrate (2023-11-10, 2026-08-13)

_dedup_flash_final(sub, gap_days=18)   # cadență empirică (monthly)
  -> 43 rânduri păstrate
```

`compute_indicator_score` ASTĂZI, config curent (fără override):
```
actual=-0.5, consensus=-0.1, surprise=-0.4, z=None, score=-2, flag='fallback'
```
Aceeași intrare, cu `frequency_overrides={GBP: monthly}` simulat in-memory:
```
actual=-0.5, consensus=-0.1, surprise=-0.4, z=-1.474, score=-1, flag=None
```

Mecanism identic CAD, confirmat cu numere reale, nu presupus.

### 1.4 — scanare pe toate cele 8 valute × toate indicator_key

`scripts/diag/cadence_dedup_scan.py` (regenerabil) — pentru fiecare (currency,
indicator_key) din catalog: cadență declarată (via `effective_frequency`, deci ține cont
de override-urile deja existente) vs empirică (`detect_cadence`), și impactul REAL al
dedup (nu doar mismatch-ul de etichetă): `len(_dedup_flash_final(sub, declared_gap))` vs
`len(_dedup_flash_final(sub, empirical_gap))`.

**Un singur caz cu mismatch ȘI colaps periculos** (cadență declarată mai LARGĂ decât cea
reală, ducând la supra-colaps):
```
GBP/gdp_qoq: declared=quarterly empirical=monthly n_printed=38
             n_after_dedup=2 (vs 43 la cadența corectă)
```

Alte 4 mismatch-uri găsite, TOATE benigne — direcție inversă, sigură (cadență declarată
MAI ÎNGUSTĂ decât cea empirică, deci `dedup_gap` mai STRÂNS, niciun risc de supra-colaps),
și toate pe `interest_rate_decision`, care are `weight: 0.0` (display-only, nu alimentează
niciun scor agregat):
```
CHF/interest_rate_decision: declared=monthly empirical=quarterly (9 vs 9, neschimbat)
GBP/interest_rate_decision: declared=monthly empirical=quarterly (28 vs 15 — declararea
    păstrează MAI MULTE rânduri, nu mai puține)
JPY/interest_rate_decision: declared=monthly empirical=quarterly (28 vs 16)
NZD/interest_rate_decision: declared=monthly empirical=quarterly (25 vs 17)
```
Ședințele de politică monetară au o cadență reală de ~6-7 săptămâni, pe care
`detect_cadence` o clasifică "quarterly"; declararea "monthly" dă un `dedup_gap` mai mic
(18d), deci fiecare ședință rămâne propriul cluster — exact opusul mecanismului periculos.
Zero impact pe scoring (weight=0).

**Concluzie 1.4: GBP gdp_qoq e singurul caz neremediat al acestui mecanism periculos în
tot catalogul, azi.** Nu există un AUD/EUR/etc. ascuns de descoperit peste o lună.

## PARTEA 2 — criterii pre-înregistrate (scrise ÎNAINTE de fix)

Scrise după diagnosticul 1.1-1.4, înainte de a edita `data/economic_indicators.yaml`:

1. **GBP gdp_qoq, ultimul print (calea de producție /economic)**: `flag` trece din
   `'fallback'` în `None`; `z` din `None` într-o valoare reală; `score` din `-2` într-un
   scor mai puțin saturat — predicție punctuală `z≈-1.474, score=-1` (calculat direct,
   vezi 1.3).
2. **Categoria growth GBP**: `score_precise` azi `-0.4` (coverage=5, media include -2 de
   la gdp_qoq); așteptare: crește către mai puțin negativ (un singur din 5 sloturi trece
   de la -2 la -1 ⇒ +1/5 = +0.2 ⇒ predicție `-0.2`).
3. **Index agregat GBP** (`/economic` și `/strength`, identic — `/strength` reafișează
   `index` din același payload): azi `0.333`; așteptare: creștere modestă, direcție clară,
   magnitudine exactă necunoscută dinainte (depinde de ponderea relativă a categoriilor).
4. **Celelalte 7 valute**: identice byte-cu-byte — categorii, index, breakdown — pentru că
   fixul atinge DOAR `indicators.gdp_qoq.frequency_overrides.GBP`, iar
   `effective_frequency`/`_dedup_flash_final`/`compute_indicator_score` sunt funcții pure
   de (sub_df, cfg, defaults, as_of, currency); niciun alt (currency, cfg) nu se schimbă.
5. **Contra-verificare informativă, cross-branch** (nu face parte din livrabilul acestei
   ramuri — `history_compute.py` nu există pe `fix/gbp-gdp-frequency`, doar pe
   `feat/history-catalog`): backtest-ul per-rând pentru GBP gdp_qoq trece de la 0/39
   scored la un număr substanțial mai mare — predicție aproximativă bazată pe mecanism
   (43 rânduri supraviețuiesc dedup-ului corect, minus fereastra de încălzire
   `fallback_min_prints=6`): **aproximativ 33-37 scored**, nu neapărat "aproape toate cele
   39" (nu toate rândurile devin scored — primele ~6 rămân legitim insufficient_history).

Dacă rezultatul real diferă semnificativ de aceste predicții (mai ales #4), opresc și
raportez, nu ajustez retroactiv.

## PARTEA 3 — fix

Un singur rând extins în `data/economic_indicators.yaml`, exact pe tiparul CAD:

```diff
-    frequency_overrides: { CAD: monthly }
+    frequency_overrides: { CAD: monthly, GBP: monthly }
```

(plus comentariul explicativ, în același stil ca la CAD — vezi diff-ul complet mai jos).
Config, nu cod — nicio atingere la `_dedup_flash_final`, `compute_indicator_score`, sau
orice logică de dedup/cadență.

```
$ git diff main -- data/economic_indicators.yaml
```
(diff complet reprodus în commit-ul acestei faze — 17 linii adăugate, o linie modificată,
toate în interiorul comentariului/override-ului lui `gdp_qoq`, nimic altceva în fișier).

### Garda automată

`tests/test_cadence_dedup_collapse_guard.py` — scanează la fiecare rulare a suitei TOATE
perechile (currency, indicator_key) cu `weight > 0` (adică orice alimentează un scor
agregat), calculează cadența empirică reală și verifică dacă `dedup_gap`-ul cadenței
DECLARATE ar colapsa istoricul relativ la cadența corectă. Eșuează zgomotos, cu mesaj care
numește exact perechea și override-ul de adăugat, dacă găsește vreun caz — exclude
deliberat `weight=0` (display-only, direcție sigură, ca la `interest_rate_decision` de mai
sus — documentat explicit în docstring-ul testului, cu trimitere la 1.4). Rulat acum:
verde (singurul caz periculog, GBP, tocmai a fost fixat).

Am adăugat și `tests/test_gbp_gdp_cadence_override.py` (pin numeric, oglinda fișierului
CAD existent) și am actualizat 2 asserții din `tests/test_cad_gdp_cadence_override.py`
care presupuneau explicit "doar CAD are override" — presupunere acum falsă prin design,
nu o regresie.

## PARTEA 4 — verificare

### Tabel before/after, toate 8 valutele (`as_of` fixat identic în ambele rulări,
`scripts/diag/gbp_gdp_before_after.py`, cu `build_payload` real — aceeași funcție folosită
de `/economic`/`/strength` în producție, minus sentiment/trend, care nu depind de
`economic_indicators.yaml` și nu pot fi afectate de acest fix):

```
CCY          growth       inflation          labour        monetary   INDEX before   INDEX after
AUD        0.2500          0.2000         -0.3333          0.0000         0.1458        0.1458
CAD        0.6667          0.4000          0.5000          1.0000         3.2083        3.2083
CHF        0.0000          0.0000          0.0000             —           0.0000        0.0000
EUR        0.5000          0.3333         -0.5000          0.0000         0.4167        0.4167
GBP  -0.4000->-0.2000      0.3333          0.3333          0.0000         0.3333        0.5833
JPY       -0.8000          0.0000          0.0000          2.0000        -1.3333       -1.3333
NZD        0.0000          1.0000          0.3333             —           2.2222        2.2222
USD       -0.2500          0.0000         -0.5714          0.0000        -1.0268       -1.0268
```

**Exact conform criteriilor pre-înregistrate**: GBP growth `-0.4 → -0.2` (predicție exactă
`-0.2`, potrivire perfectă); index GBP `0.333 → 0.583` (creștere, direcție corectă); GBP
gdp_qoq breakdown `flag: fallback→None, z: None→-1.474, score: -2→-1` (potrivire exactă cu
1.3). **Toate celelalte 7 valute: byte-cu-byte identice** (verificat prin egalitate directă
de dict Python pe JSON-ul complet, nu doar prin inspecție vizuală a tabelului).

Bonus, neprevăzut inițial dar corect: `prior_actual` în breakdown-ul GBP s-a schimbat și
el (`0.2 → 0.1`) — pentru că dedup-ul corect identifică acum corect "printul anterior real"
(imediat precedent cronologic), nu un rând arbitrar rămas dintr-un cluster colapsat greșit.
Semn suplimentar că fixul corectează mecanismul, nu doar simptomul de suprafață.

### Contra-verificare cross-branch (informativ, `feat/history-catalog`, worktree temporar
`git worktree add --detach`, șters după verificare — nu a atins niciuna din cele două
ramuri reale):

```
ÎNAINTE: GBP gdp_qoq — 39 printed, 0 scored, 38 insufficient_history, 1 quarantined
DUPĂ:    GBP gdp_qoq — 39 printed, 29 scored, 9 insufficient_history, 1 quarantined
```

29/39 (74%), nu "aproape toate" — puțin sub predicția aproximativă (33-37), dar direcția
și ordinul de mărime se potrivesc; cele 9 rămase `insufficient_history` sunt plauzibile ca
fereastră de încălzire + câteva flaguri `no_consensus`/`direction_mismatch` mapate la
insufficient_history (design existent din FAZA 1D, nu ceva nou introdus aici).

### Suita de teste

```
./.venv/bin/python -m pytest -q
7 failed, 730 passed, 5 warnings
```
Aceleași 7 eșecuri pre-existente (drift de date live, `test_aud_inflation_promotion.py`,
`test_board_slot_cleanup_and_cad_promotion.py`, `test_trend_disabled.py`) — niciunul nu
atinge `gdp_qoq`/GBP/`economic_indicators.yaml`. Nimic nou a picat.

## Ce a rămas incert

1. Cele 9 rânduri `insufficient_history` rămase pe GBP gdp_qoq (din perspectiva
   `/history`, informativ) — nu le-am defalcat individual (câte sunt fereastră de
   încălzire vs `no_consensus`); dacă vrei defalcarea exactă, e un calcul mic separat.
2. Predicția aproximativă din criteriul 5 (33-37) vs rezultatul real (29) — diferență de
   ~15%, direcție și mecanism confirmate, dar nu am investigat exact de ce sunt 9 și nu 6
   insufficient_history; nu blochează verdictul fixului (care e despre calea de PRODUCȚIE,
   nu despre `/history`).
3. Nu am atins `feat/history-catalog` — odată ce se decide ordinea de merge, acea ramură
   va moșteni automat acest fix prin `data/economic_indicators.yaml` (fișier comun),
   presupunând un merge fără conflicte (probabil, singura modificare e în interiorul
   blocului `gdp_qoq`, pe care `feat/history-catalog` nu l-a atins).

STOP. Fără merge. Aștept confirmarea ta pe fix, apoi discutăm ordinea de merge cu
`feat/history-catalog`.
