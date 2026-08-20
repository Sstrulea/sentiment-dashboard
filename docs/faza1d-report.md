# FAZA 1D — raport final

Worktree `../macro-dev`, branch `feat/history-catalog`. HEAD după acest raport: `ba56026`.
Zero merge, zero deploy, zero modificări la `ff_scoring.py` / `economic_compute.py` /
`manual_actuals.py`.

## PARTEA 1 — verdict: NU E BUG (în calea curentă de producție)

Am trasat lanțul complet: `compute_indicator_score` ← `compute_currency_scorecard` ←
`build_economic_payload` (`economic_render.py`). Singurul apel de producție folosește
`as_of = pd.Timestamp.utcnow()` — mereu "acum", niciodată o dată istorică arbitrară.

Codul din `compute_indicator_score` (`economic_compute.py`) își filtrează singur intrarea
înainte de a calcula orice:

```python
fresh = df[df["actual"].notna() & (df["release_dt"] <= as_of)]
if fresh.empty:
    return None  # sau ramura "no data" — niciodată scorul altui rând
row = fresh.sort_values("release_dt").iloc[-1]
```

Cu `as_of=now`, `fresh` conține DOAR rândurile care chiar au un `actual` real publicat
până acum — rândul returnat (`row`) e mereu cel mai recent print real, și `res["release_dt"]`
corespunde exact acelui rând. Bug-ul pe care l-ai găsit (scor moștenit pe un rând cu
`actual` null) apare DOAR când cineva apelează funcția cu `as_of` = data unui rând
specific din trecut, pe post de backtest per-rând — exact ce face `history_compute.py`,
și exact acolo a fost fixat (P1.1, FAZA 1C). Calea de producție nu face niciodată asta.

**Verificare empirică**: am comparat, pentru toate cele 8 valute, `res["release_dt"]`
raportat de `compute_indicator_score` cu cel mai recent rând real din calendar la ora
rulării. Am găsit 12 nepotriviri — toate benigne:
- un eveniment programat, viitor, încă nepublicat (comportament normal, nu bug);
- deja marcate corect de producție cu `stale=True, superseded_missing=True` (mecanism
  existent, funcțional).

Nicio pereche (currency, indicator_key) nu afișează azi pe `/economic` un scor moștenit
de la un print anterior.

### Bug live găsit pe parcurs (RAPORTAT, NEATINS): GBP `gdp_qoq`

În timp ce investigam Partea 2, GBP `gdp_qoq` arăta 0/39 scored și un fallback `-2` live
pe `/economic`. Cauza: `data/economic_indicators.yaml`'s `gdp_qoq` are `frequency: quarterly`
declarat, dar GBP's `name_raw` real e "GDP m/m" — cadență empirică lunară, nu trimestrială
(exact tiparul deja documentat și fixat pentru CAD, via `frequency_overrides: {CAD: monthly}`
— comentariul din YAML descrie explicit acest bug pentru CAD, dar override-ul n-a fost
niciodată extins la GBP, care are exact aceeași problemă cu date reale identice ca formă).

Cu cadența declarată greșit (quarterly → `dedup_gap_days` de 45 zile), `_dedup_flash_final`
colapsează ÎNTREAGA istorie GBP gdp_qoq într-un singur cluster flash/final — de-aia 0/39.
Confirmat via cadence badge-ul nou din UI (`cadence_empirical`), care arată "M" pentru
GBP gdp_qoq exact ca la CAD, fără ca vreun override să existe pentru GBP.

Fixul e un rând YAML (`frequency_overrides: {GBP: monthly}`), dar `economic_indicators.yaml`
e adiacent lui `ff_scoring.py`/`economic_compute.py` — schimbarea afectează scoring live,
deci NU o fac fără gate explicit. Raportez, nu repar.

## PARTEA 2 — insufficient_history

### 2.1 — defalcare per serie (integral în `/tmp/p2_out.txt`, regenerabil via
`scripts/insufficient_history_audit.py`)

Agregat pe cadență:
```
monthly     insufficient=264/1452 (18.2%)
quarterly   insufficient=71/188   (37.8%)
```

Extras relevant (seriile trimestriale, toate cu N mic — GBP gdp_qoq exclus din tabel,
0 scored e bug-ul de mai sus, nu insuficiență de istoric):
```
CADENCE    CCY  KEY                       N  SCORED  INSUFF  FIRST_SCORED
quarterly  AUD  gdp_qoq                  13       8       5   2024-09-04
quarterly  CHF  gdp_qoq                  13       8       5   2025-02-27
quarterly  EUR  gdp_qoq                  14       7       7   2024-07-30
quarterly  GBP  interest_rate_decision   28      12      16   2023-09-21
quarterly  JPY  interest_rate_decision   21      12       8   2024-03-19
quarterly  NZD  cpi_yoy                  16      10       5   2024-04-16
quarterly  NZD  gdp_qoq                  13       8       5   2024-09-18
quarterly  USD  gdp_qoq                  14       8       5   2024-04-25
```

### Corectare a premisei din brief

Enunțul "window_k=12 înseamnă 3 ani de istoric până la primul scor" **nu e corect** —
verificat direct în cod (`economic_compute.py`) și confirmat de documentație deja
existentă în proiect (`docs/proposal-staleness-gate.md`, `docs/usd-gdp-shutdown-gap.md`,
scrise înainte de această fază). Poarta reală e `defaults.fallback_min_prints` (=6):
sub acest prag → `insufficient_history`; peste → scored, cu `window_k` (=12) doar ca
plafon pe eșantionul trailing pentru sigma. `window_k` NU mută niciun rând din
insufficient în scored.

### 2.2 — contrafactual (in-memory, nimic scris în YAML)

```
AUD/gdp_qoq  base(window_k=12,fallback_min_prints=6): scored=8 insufficient=5
    window_k=8   scored=  8 insufficient=  5   sigma_last 0.129→0.155
    window_k=6   scored=  8 insufficient=  5   sigma_last 0.129→0.172
    fallback_min_prints=4   scored= 10 insufficient=  3   sigma_last neschimbat
    fallback_min_prints=3   scored= 11 insufficient=  2   sigma_last neschimbat
```
(tipar identic pe toate cele 11 serii trimestriale — tabel complet în `/tmp/p2_out.txt`).

**window_k=8/6**: scored/insufficient count NU se mișcă (cu o singură excepție parțială:
NZD/interest_rate_decision și JPY/interest_rate_decision, unde window_k=6 SCADE scored-ul,
pentru că un eșantion trailing mai mic poate coborî sub `fallback_min_prints` pe puncte
unde înainte abia îl atingea — deci mai mic window_k poate doar STRICE lucrurile, nu
ajuta). Sigma se mișcă vizibil (până la ±40%) — degradare de stabilitate, nu de acoperire.

**fallback_min_prints=4/3**: scored crește direct cu 2-3 puncte pe fiecare serie
trimestrială, sigma_last NESCHIMBAT (pentru că sigma se calculează tot pe `window_k=12`
puncte trailing — schimbarea pragului nu afectează CE se calculează, doar CÂND se începe
calculul).

### 2.3 — recomandare

De acord cu tine să NU atingi `window_k=12` (parametru validat V4-V5a) — și, suplimentar,
`window_k` nu e nici măcar parametrul relevant pentru acest simptom. Dacă vreodată vrei
să muți linia insufficient/scored, pârghia reală e `fallback_min_prints`, nu `window_k` —
dar rămâne tot un parametru de scoring live, deci tot din aceeași categorie "nu-l ating
fără criterii pre-înregistrate".

Soluția UI pe care ai propus-o (nu oferi fereastra 1y pe serii trimestriale unde ar fi
100% hașurat) — implementată deja structural: `WINDOW_MIN_POINTS=4` în `history_compute.py`
elimină orice fereastră cu sub 4 puncte din `window_options`, iar UI-ul (`renderWindowSelector`)
arată doar ferestrele prezente. Pe seriile trimestriale cu N mic, `1y` (de regulă ~4 puncte,
majoritatea insufficient) fie nu apare deloc, fie apare dar transparent hașurat — nu am
adăugat o regulă separată "ascunde 1y dacă ar fi 100% insufficient" pentru că nu am găsit,
verificând tabelul de mai sus, nicio serie unde 1y ar fi 100% insufficient ȘI ar trece
pragul de 4 puncte simultan (fie sunt sub prag și dispar automat, fie au măcar un punct
scored). Dacă vezi un caz concret unde asta nu ține, spune-mi seria exactă.

Recomandare separată, mai urgentă: GBP `gdp_qoq`'s frequency_override e un bug live real,
nu o chestiune de parametru — merită decizie separată, prioritate mai mare decât 2.x.

## PARTEA 3 + 4 — UI

Toate cele 4 categorii randate (`inflation`/`growth`/`labor`/`rates`), taburi cu stare în
deep-link: `?cat=growth&ccy=CAD&range=2y`. Dropdown-ul de valută e uniunea valutelor din
toate categoriile (stabil la schimbarea tabului); o pereche (categorie, valută) fără
intrări în catalog (rates/CHF, growth/JPY) arată un mesaj explicit, niciodată tab gol.

Rates: step chart (Chart.js `stepped:true`), rază punct mai mare pe ședințele unde rata
chiar s-a schimbat, mai mică pe cele unde a fost ținută constant; forecast rămâne tick pe
ședința respectivă. Verificat vizual pe JPY (screenshot atașat mai jos) — funcționează
corect, DAR: cele 10 rânduri quarantinate ale JPY (zerouri implauzibile 2024-2026) nu
apar deloc în nicio fereastră afișată, pentru că `_window_points` (din `history_compute.py`,
FAZA 1C) le exclude explicit din display, nu doar din scoring — decizie deja testată
(`tests/test_history_compute.py::test_payload_quarantined_row_never_visible_without_override`).
Am scris codul de marker pentru puncte quarantinate (cruce distinctă, ținută la ultimul
nivel bun), dar cu datele reale de azi nu am nicio serie unde să se vadă efectiv — e cod
pregătit, nu verificat vizual pe date reale. Vezi „Ce a rămas incert" mai jos.

CHF pe rates: tab-ul arată mesajul explicit "No Rates data for CHF — this series is
omitted from the catalog", nu gol. Verificat.

Growth: serii lunare (GBP, CAD) și trimestriale coexistă pe același tab per valută; fiecare
chip poartă acum un badge de cadență empirică (M/Q/W) — GBP și CAD gdp_qoq arată amândouă
"M" (confirmă vizual bug-ul de la Partea 1, fără să-l repare). JPY growth (n=3, sub minim)
→ mesaj explicit, nu tab gol.

Labor: USD are 3 chips (NFP/unemployment/wage growth), o singură serie activă odată (fără
suprapunere — design deja existent), axa Y arată acum eticheta de unitate a seriei active
("level" pentru NFP, "pct" pentru unemployment) — confirmat vizual, unitățile nu se
amestecă niciodată pe același grafic.

### Partea 4

4.1 — `resolveEntry()` acum eșuează zgomotos (`console.error` + mesaj vizibil) dacă
`points_ref` țintește un rol inexistent, în loc de `window_options` gol tăcut. Testat
(`tests/history_js/test_resolve_entry.js`).

4.2 — marker de revizie: am adăugat un punct plin, culoare fixă (independentă de temă,
`#ab47bc`) deasupra fiecărei bare/puncte revizuite, pe lângă linia punctată existentă —
vizibil fără hover chiar pe grafice dense (Max, 40+ puncte; verificat pe Labor/USD NFP,
~45 puncte, multiple revizii vizibile simultan). Nu e o soluție perfectă (pe grafice și
mai dense ar putea începe să se suprapună), dar e o îmbunătățire reală față de linia
punctată singură, care se pierdea vizual.

4.3 — test automat pentru `history.js`: `tests/history_js/test_resolve_entry.js`, Node
simplu (fără jsdom, fără framework de test) + `tests/test_history_js.py` (wrapper pytest,
`skipif` dacă `node` nu e pe PATH — nu blochează suita pe o imagine CI fără Node). Testează
exact clasa de bug P1.4 (dedup via `points_ref`) și noul fail-loud din 4.1: payload minimal
→ `resolveEntry` → aserțiune pe numărul de puncte/bare.

## Pagina randată local

```
cd ../macro-dev
./.venv/bin/python -m src.main --mode render-all
cd public && python3 -m http.server 8934
# apoi deschide http://localhost:8934/history.html
```

## Non-regresie

- `./.venv/bin/python -m pytest tests/test_history_compute.py tests/test_data_integrity.py tests/test_history_js.py -q` → 24 passed.
- Suita completă: 746 passed, 7 failed — aceleași 7 eșecuri reproduse identic PE STASH-UL
  schimbărilor din această fază (deci preexistente, independente de FAZA 1D): drift de
  date live pe `test_aud_inflation_promotion.py`, `test_board_slot_cleanup_and_cad_promotion.py`,
  `test_trend_disabled.py` — niciunul nu atinge `history_compute`/`history_render`/`data_integrity`.
- `/economic` și `/strength`: `public/economic.html` și `public/strength.html` sunt
  byte-identice înainte/după (diff gol). `public/data/economic.json` etc. diferă cu o
  linie (timestamp `as_of`/`generated_at`) — conținutul scorurilor neschimbat.

## Ce a rămas incert

1. Markerul de puncte quarantinate pe step chart (rates) e scris dar NEVERIFICAT vizual pe
   date reale — `_window_points` exclude azi orice rând quarantinat din toate ferestrele,
   deliberat și testat (decizie FAZA 1C). Dacă vrei să vezi efectiv markerul funcționând,
   fie găsim o serie/fereastră unde asta nu se aplică, fie discutăm dacă comportamentul
   `_window_points` ar trebui relaxat (afișare cu flag, nu excludere) — dar asta contrazice
   un test deja scris intenționat, deci e o decizie de-a ta, nu ceva de schimbat din oficiu.
2. GBP `gdp_qoq` frequency_override — bug live confirmat, needs decizie separată (fix e un
   rând YAML, dar în afara scope-ului "raportezi, nu repari" din PARTEA 1).
3. Nu am găsit nicio serie trimestrială unde fereastra 1y ar fi 100% insufficient_history
   ȘI ar trece pragul de 4 puncte — deci n-am adăugat regula UI specifică cerută în 2.3;
   spune-mi dacă știi un caz concret care contrazice asta.
4. Markerul de revizie (4.2) nu a fost testat pe un grafic cu >10 revizii apropiate în
   spațiu — la acea densitate ar putea începe să se suprapună; nu am un exemplu real cu
   atât de multe revizii clusterizate ca să verific.

STOP. Zero merge, zero deploy.
