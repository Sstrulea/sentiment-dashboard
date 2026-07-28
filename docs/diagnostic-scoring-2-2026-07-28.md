# Diagnostic scoring audit 2 — 2026-07-28

**Read-only, continuare pe `diag/scoring-audit` (commit 3e18483).** Nimic modificat în `src/`, YAML sau config. Contrafactualele (O2) rulează pe `deepcopy()` în memorie. Scripturi noi în `scripts/diag/section_{l,m,n,o,p,q,q1_history}.py`.

Fereastra de replay (aceeași ca raportul 1): 2025-07-25 → 2026-07-21, săptămânal, 53 puncte. Ziua curentă (L, M, N, O, P): 2026-07-28. Secțiunea Q1 folosește o sursă **diferită și mai autoritară** — istoricul git real al `public/data/economic.json` — vezi acolo de ce.

---

## L. Reconciliere aritmetică — prioritate maximă

### Funcția de fold, exact, cu ponderile citite din `data/economic_instruments.yaml` la runtime

```
SCALE = 5.0 · PAIR_DIVISOR = 2.0 · SENTIMENT_WEIGHT = 0.5 · TREND_WEIGHT = 0.5
```

Lanț (`src/economic_compute.py`): `_augmented_index` (SENTIMENT, pe fiecare leg, ÎNAINTE de diferențiere) → `(base_idx_aug − quote_idx_aug) / PAIR_DIVISOR` (`macro_score`) → `_fold_trend` (TREND, DUPĂ diferențiere, la nivel de pereche).

### Trace numeric NZDUSD (azi, 2026-07-28)

| pas (variabilă din cod) | valoare |
|---|---:|
| `base_index_num` (NZD) | 1.166667 |
| `base_index_wsum` (NZD) | 3.0 |
| `quote_index_num` (USD) | 1.666667 |
| `quote_index_wsum` (USD) | 4.0 |
| `base_idx_raw = card['index']` (NZD, **afișat în header-ul leg-ului**) | **1.944444** |
| `quote_idx_raw = card['index']` (USD) | **2.083333** |
| `v_s_base` (sentiment pe leg-ul NZD) | **+3** |
| `v_s_quote` (sentiment pe leg-ul USD — exclus, USD e leg de pereche) | None |
| `base_idx_aug = _augmented_index(NZD, +3, 0.5, 5)` | **3.809524** |
| `quote_idx_aug = _augmented_index(USD, None, 0.5, 5)` | 2.083333 (neschimbat) |
| `macro_score = (base_idx_aug − quote_idx_aug) / 2` | **0.863095** |
| `base_wsum_eff = _leg_eff_wsum(NZD, +3, 0.5)` | 3.0 + 0.5 = 3.5 |
| `quote_wsum_eff = _leg_eff_wsum(USD, None, 0.5)` | 4.0 (neschimbat) |
| `macro_weight = (3.5 + 4.0)/2` | **3.75** |
| `trend_value` (pereche NZDUSD) | **−3** |
| `m = macro_score/scale` | 0.172619 |
| `new_mean = (m·w + trend_weight·trend)/(w+trend_weight) = (0.172619·3.75 + 0.5·(−3)) / 4.25` | **−0.200630** |
| `score = new_mean·scale` | **−1.003151** |
| `inst['score']` (din payload-ul de producție) | −1.003151 |
| **reconciliere (\|diff\|<1e-6)** | **True** |
| `bias_label(score, thresholds)` | Neutral |

### De ce nu e −0.07: SENTIMENT și TREND NU sunt comensurabile deși ambele au "weight 0.5"

Ipoteza din prompt (SENTIMENT +3 și TREND −3 la weight egal 0.5 ar trebui să se anuleze, lăsând scorul la ≈−0.07 = `macro_score` fără sentiment) presupune că cei doi factori intră în aceeași ecuație, cu același numitor. **Nu e cazul:**

1. **SENTIMENT** intră în `_augmented_index` a UNUI SINGUR leg (NZD, pentru că USD e leg de pereche → exclus), cu numitorul acelui leg propriu (`index_wsum(NZD)=3`, deci greutatea efectivă e `0.5/3.5 ≈ 14%` din media NZD), apoi diferența e împărțită la `pair_divisor=2`.
2. **TREND** intră DUPĂ diferențiere, la nivelul PERECHII, cu numitorul `macro_weight=3.75` (media greutăților efective ale ambelor legs) — un context complet diferit de `pair_divisor`.

Rezultatul (`−1.00`) e corect matematic — **aritmetica se închide** — dar intuiția "weight 0.5 = weight 0.5 deci se anulează" e falsă: cei doi termeni operează în etaje și numitori diferiți.

### Ce se afișează unde

Din `static/economic-chart.js`:
- **Header-ul modalului** (`'<span class="modal-score">Score ' + fmtSigned(inst.score, 2) + ...`, linia 615): `inst.score` — **scorul precis**, formatat la 2 zecimale (`fmtSigned`). Pentru NZDUSD: `−1.00` = `-1.003151` rotunjit la afișare, **nu** o valoare recalculată/clamped separat.
- **Coloana "Score" din tabelul dens** (linia 329, `fmtScoreInt(inst.score)`): `Math.round(inst.score)` — un ÎNTREG. Pentru NZDUSD: `round(-1.003151) = -1`.
- **Index-ul monedei afișat în panoul leg-ului** (linia 513-517, `card.index`): indexul BRUT (fără sentiment) — `1.94`/`2.08` pentru NZD/USD, exact `base_idx_raw`/`quote_idx_raw` de mai sus.
- Nicio rotunjire/clamp între calculul `macro_score`→`_fold_trend` și `inst["score"]` — `_clamp_cell` există DOAR pentru celulele de categorie afișate (`_category_cells`), nu pentru scorul final al perechii.

**Verdict: aritmetica afișată se închide.** Nu există nicio divergență de cod — discrepanța percepută vine din supoziția incorectă că SENTIMENT/TREND se anulează la weight egal.

### Test de simetrie: USDJPY (trend +3 / sentiment −3, "oglinda" lui NZDUSD)

| pas | valoare |
|---|---:|
| `base_idx_raw` (USD) | 2.083333 |
| `quote_idx_raw` (JPY) | 2.222222 |
| `v_s_base` (USD, exclus) | None |
| `v_s_quote` (JPY) | **+3** (nu −3 — JPY are propria celulă COT +3; semnul "oglindă" apare abia în celula AFIȘATĂ pereche-COT, vezi mai jos) |
| `base_idx_aug` | 2.083333 (neschimbat) |
| `quote_idx_aug = _augmented_index(JPY, +3, 0.5, 5)` | **4.047619** |
| `macro_score = (2.083333 − 4.047619)/2` | **−0.982143** |
| `macro_weight` | 3.75 (identic cu NZDUSD — coincidență structurală: ambele legs "cu sentiment" au wsum=3) |
| `trend_value` | **+3** |
| `score = _fold_trend(...)` | **0.898109** |
| `inst['score']` (producție) | 0.898109 |
| **reconciliere** | **True** |
| `bias_label` | Neutral |
| **afișare tabel** (`round`) | **+1** |
| **afișare modal** (2 zecimale) | **+0.90** |

**NU e o oglindă algebrică perfectă a lui NZDUSD** (`+0.90`, nu `+1.00`), deși celulele AFIȘATE sunt oglindă exactă: celula COT-pereche pentru USDJPY = `pair_cot(0, 3) = −3` (JPY e quote, deci semnul se inversează la afișare), iar cea pentru NZDUSD = `pair_cot(3, 0) = +3` — celulele afișate SUNT +3/−3 exact cum spune promptul. Dar rezultatul aritmetic diferă (`−1.00` vs `+0.90`, nu `−1.00`/`+1.00`) pentru că SENTIMENT intră pe legs diferite (base la NZDUSD, quote la USDJPY) — adăugarea lui pe partea `+` a scăderii vs pe partea `−` a scăderii nu produce magnitudini identice odată combinat cu compoziția proprie FUND a fiecărui leg necomun (NZD ≠ JPY). **"+1" pe care îl vede utilizatorul în tabel e valoarea ROTUNJITĂ a lui 0.898, nu semnul unei anulări perfect simetrice.**

CSV: `diag-L-arithmetic-trace.csv`.

---

## M. Audit de categorii absente

### Azi (2026-07-28)

| valută | growth | inflation | labour | monetary |
|---|---|---|---|---|
| AUD | PREZENTĂ | PREZENTĂ | PREZENTĂ | **STALE** (13 zile lag, rba) |
| CAD | PREZENTĂ | PREZENTĂ | PREZENTĂ | PREZENTĂ |
| CHF | PREZENTĂ | PREZENTĂ | PREZENTĂ | **ABSENTĂ** (0 rânduri în rates.parquet) |
| EUR | PREZENTĂ | PREZENTĂ | PREZENTĂ | PREZENTĂ |
| GBP | PREZENTĂ | PREZENTĂ | PREZENTĂ | PREZENTĂ |
| JPY | PREZENTĂ | PREZENTĂ | PREZENTĂ | **STALE** (mof_jgb, lag mare) |
| NZD | PREZENTĂ | PREZENTĂ | PREZENTĂ | **ABSENTĂ** (0 rânduri în rates.parquet) |
| USD | PREZENTĂ | PREZENTĂ | PREZENTĂ | PREZENTĂ |

**growth/inflation/labour sunt PREZENTE pentru toate cele 8 valute, 100% din fereastra de 53 zile** (`diag-M-category-presence-window.csv`) — absența e concentrată exclusiv în `monetary`.

**Ferestre-wide, monetary**:

| valută | %PREZENTĂ | %ABSENTĂ | %STALE |
|---|---:|---:|---:|
| CHF | 0.0 | **100.0** | 0.0 |
| NZD | 0.0 | **100.0** | 0.0 |
| AUD | 100.0 | 0.0 | 0.0 |
| JPY | 94.3 | 0.0 | 5.7 |
| CAD/EUR/GBP/USD | 100.0 | 0.0 | 0.0 |

**AUD era 100% PREZENTĂ pe toată fereastra (până la 2026-07-21) și abia azi (2026-07-28) a trecut STALE** — pragul `MAX_AGE_BD=7` zile lucrătoare a fost depășit undeva între cele două date (ultima observație AUD e 2026-07-15; la 2026-07-21 eraau ~4 zile lucrătoare, sub prag; la 2026-07-28 sunt ~9, peste prag). E un eveniment FOARTE recent, nu o stare persistentă ca la NZD/CHF.

### Cauza rădăcină

- **NZD, CHF**: `data/rates.parquet` are **0 rânduri** pentru fiecare — niciodată nu a existat o serie persistată (RBNZ B2 / SNB rendoblid + fallback Stooq nu au produs niciodată date reținute, conform `src/rate_sources/__init__.py`).
- **AUD**: date există (sursă `rba`, 3233 rânduri), dar seria s-a oprit la 2026-07-15 → azi stale.
- **JPY**: date există (`mof_jgb`), dar lag intermitent (5.7% din fereastră stale).

### Întrebarea decisivă: (a) medie peste categorii prezente, sau (b) medie cu absență=0?

Citind codul (`compute_currency_scorecard`): `cat_scores_for_index` primește DOAR categoriile cu `coverage>0`; `index_wsum` = suma ponderilor categoriilor PREZENTE (nu 4 fix). **Confirmat (a).**

Numeric, NZD azi: `categories = {growth: 0.0, inflation: 0.5, labour: 0.667}` (monetary absentă, deci exclusă din calcul, nu tratată ca 0):

| variantă | formulă | rezultat |
|---|---|---:|
| **(a) — medie peste prezente** | `mean(0, 0.5, 0.667) × 5` | **1.944444** ✓ (= `card['index']` real) |
| (b) — medie cu absență=0 | `mean(0, 0.5, 0.667, 0) × 5` | 1.458333 ✗ |

**Confirmat: varianta (a).** Consecință directă: indicii valutari NU sunt comparabili unu-la-unu între monede — NZD/CHF/AUD/JPY (azi) au un index calculat din 3 categorii cu greutate totală normalizată la 1, în timp ce CAD/EUR/GBP/USD au 4. Un index de +2 pe 3 categorii NU înseamnă același lucru ca un +2 pe 4.

CSV: `diag-M-category-presence-{today,window}.csv`.

---

## N. Propagarea în celula bilaterală

**Descoperire cheie: există DOUĂ calcule bilaterale diferite pentru aceeași relație conceptuală, și NU sunt de acord.**

1. **`src/economic_render._build_indicator_cells`** (coloana densă "Rate Exp (2y)", cea din prompt): `v = (eb.score dacă eb else 0) − (eq.score dacă eq else 0)` — diferența BRUTĂ, **fără** împărțire la `pair_divisor`.
2. **`src/economic_compute._category_cells`** (dicționarul `categories["monetary"]` al perechii — un calcul SEPARAT): `precise = (base_precise − quote_precise) / pair_divisor`, unde un leg absent își ia `score_precise` implicit 0.0 via `.get(cat, {}).get('score_precise', 0.0)`.

Pentru NZDUSD, "Rate Exp 2y": NZD (`eb`) absent → 0; USD (`eq`) score +2 → `v = 0 − 2 = −2.0`. **Exact −2, cum a raportat inspecția modalului.**

Pentru NZDUSD, categoria `monetary` (dacă ar fi afișată separat): NZD `base_precise=0.0` (absent→0 implicit), USD `quote_precise=2.0` → `precise = (0−2)/2 = −1.0`, `score_cell=−1`.

**Aceeași informație (NZD n-are monetary, USD are +2) produce −2 într-un loc și −1 în altul**, pentru că unul divide la `pair_divisor` și celălalt nu.

### 1-2. Cum tratează codul "leg absent" vs "leg=0"?

**Nu se distinge, în niciunul din cele două calcule** (decât în cazul "ambele legs absente", tratat separat ca celulă goală `None`/"—"). Un leg absent (`None`) e convertit SILENȚIOS la 0 în ambele funcții (`eb["score"] if eb else 0` / `.get(..., 0.0)`). E exact conversia "unknown → neutral" bănuită în prompt.

### 3. Câte celule bilaterale afișate (tabelul dens) sunt calculate dintr-un singur leg? (azi, toate cele 28 perechi × 15 coloane = 420 celule)

| leg_presence | n celule | % |
|---|---:|---:|
| both (ambele legs) | 226 | 53.8% |
| neither (ambele absente → gol) | 93 | 22.1% |
| base_only | 70 | 16.7% |
| quote_only | 31 | 7.4% |
| **single-leg total** | **101** | **24.0%** |

Pe coloană, cele mai afectate: `wage_growth` (15), `services_pmi` (15), `employment_change` (12), `core_cpi` (12), **`rate_expectations` (12)**, `core_pce`/`adp`/`jolts`/`jobless_claims` (7 fiecare, toate USD-only), `ppi_yoy` (7).

**Aproape 1 din 4 celule afișate în tabelul dens e calculată dintr-un singur leg real, tratat identic cu "celălalt leg = 0 exact".**

### 4. Flag vizual?

**Niciunul.** `static/economic-chart.js` are badge-uri distincte pentru `stale` (`flag-stale`) și `no_consensus` (`flag-nc`), dar **niciun** badge pentru "un singur leg prezent" — nici la nivelul celulei dense, nici la nivelul categoriei. Vizual, o celulă `base_only` arată identic cu o celulă `both`.

CSV: `diag-N-indicator-cells-today.csv`, `diag-N-category-cells-today.csv`.

---

## O. Contrafactual — restaurarea categoriei monetary

### O1 — căutare locală (fără fetch de rețea)

Verificat toate parquet-urile din `data/`: `rates.parquet` (37313 rânduri, NICIUN rând NZD/CHF), `price_history.parquet` (simboluri FX/index — NZDCHF etc. sunt PERECHI de preț, nu randamente), `economic_calendar_ff.parquet`/`economic_calendar.parquet` (calendar economic, nu randamente), `real_yields.parquet`/`net_liquidity.parquet` (doar US), `pc_history.parquet`, `retail_history.parquet`, `sentiment_history.parquet`, `data/jb_raw/*.json` (payload JBlanked — calendar economic, nu randamente). **Nicio serie 2y pentru NZD sau CHF nu există local, sub niciun simbol.** Consistent cu `src/rate_sources/__init__.py`: RBNZ/SNB + fallback Stooq nu au produs niciodată o serie persistată în acest repo.

### O2 — sweep de sensibilitate (obligatoriu)

Pentru NZD și CHF, injectat `monetary = X ∈ {−2,−1,0,+1,+2}` (coverage=1, ca și cum ar fi o citire proaspătă live), recalculat FULL fold (sentiment+trend neschimbate) pentru fiecare pereche care conține valuta. Tabel complet: `diag-O2-monetary-sweep.csv` (70 rânduri); flip-uri: `diag-O2-flips-summary.csv`.

**Exemplu NZDUSD** (scor curent −1.003, Neutral):

| X (NZD monetary) | index NZD nou | scor nou | label nou | flip? |
|---:|---:|---:|---|---|
| −2 | −1.042 | −2.180 | **Bearish** | DA |
| −1 | 0.208 | −1.683 | **Bearish** | DA |
| 0 | 1.458 | −1.185 | Neutral | nu |
| +1 | 2.708 | −0.688 | Neutral | nu |
| +2 | 3.958 | −0.191 | Neutral | nu |

**Exemplu USDCHF** (scor curent 1.949, Bullish):

| X (CHF monetary) | index CHF nou | scor nou | label nou | flip? |
|---:|---:|---:|---|---|
| −2 | −1.667 | 2.925 | Bullish | nu |
| −1 | −0.417 | 2.428 | Bullish | nu |
| 0 | 0.833 | 1.931 | Bullish | nu |
| +1 | 2.083 | 1.434 | Bullish | nu |
| +2 | 3.333 | 0.937 | **Neutral** | DA |

**Flip-uri complete, ambele direcții (simetric — atât X negativ cât și pozitiv produc flips)**:

| valută | X | perechi care flipează |
|---|---:|---|
| CHF | −2 | CADCHF, CHFJPY, GBPCHF, NZDCHF |
| CHF | −1 | CADCHF, CHFJPY |
| CHF | 0 | CADCHF |
| CHF | +2 | EURCHF, NZDCHF, USDCHF |
| NZD | −2 | AUDNZD, EURNZD, GBPNZD, NZDCAD, NZDCHF, NZDUSD |
| NZD | −1 | AUDNZD, EURNZD, GBPNZD, NZDCAD, NZDCHF, NZDUSD |
| NZD | 0 | EURNZD, NZDCAD |
| NZD | +2 | NZDJPY |

**Observație importantă: chiar X=0 (o citire "neutră" live) produce flip-uri** (CADCHF pentru CHF; EURNZD, NZDCAD pentru NZD). Restaurarea categoriei ca 0 NU e un no-op: schimbă numitorul (`index_wsum` 3→4), diluând semnalul growth/inflation/labour existent al valutei — un efect real, separat de valoarea direcțională a lui X însuși.

### O3 — perechi care compară azi un index cu 3 categorii vs unul cu 4

Categorii prezente azi (index_wsum): AUD=3, CAD=4, **CHF=3**, EUR=4, GBP=4, JPY=3, **NZD=3**, USD=4 (AUD și JPY sunt la 3 azi DIN CAUZA STALE, nu doar absență structurală — vezi §M).

**16 din 28 perechi FX (57.1%) compară azi un index cu 3 categorii împotriva unuia cu 4**:

USDJPY, USDCHF, AUDUSD, NZDUSD, EURJPY, EURCHF, EURAUD, EURNZD, GBPJPY, GBPCHF, GBPAUD, GBPNZD, CADJPY, AUDCAD, NZDCAD, CADCHF.

CSV: `diag-O3-mismatched-category-count-pairs.csv`.

---

## P. Asimetrie de vechime a datelor (azi)

Vârsta (zile de la eliberare/observație) a indicatorilor care contribuie EFECTIV la index (ne-stale):

| valută | vârstă medie | vârstă mediană | n indicatori |
|---|---:|---:|---:|
| **NZD** | **52.9** | **68.0** | 9 |
| AUD | 34.1 | 19.5 | 8 |
| CHF | 29.4 | 27.0 | 5 |
| EUR | 30.1 | 24.0 | 10 |
| JPY | 28.0 | 29.0 | 7 |
| USD | 25.1 | 26.0 | 14 |
| GBP | 13.4 | 7.0 | 11 |
| CAD | 13.2 | 8.0 | 9 |

**NZD are, cu diferență clară, cel mai "bătrân" index** dintre toate cele 8 valute (52.9 zile medie — de ~4× mai vechi decât GBP/CAD).

Top perechi după diferența de vârstă între legs (sortat descrescător):

| pereche | leg mai vechi | vârstă | leg mai tânăr | vârstă | diferență | raport |
|---|---|---:|---|---:|---:|---:|
| NZDCAD | NZD | 52.9 | CAD | 13.2 | 39.7 | 4.01× |
| GBPNZD | NZD | 52.9 | GBP | 13.4 | 39.5 | 3.95× |
| NZDUSD | NZD | 52.9 | USD | 25.1 | 27.8 | 2.11× |
| NZDJPY | NZD | 52.9 | JPY | 28.0 | 24.9 | 1.89× |
| NZDCHF | NZD | 52.9 | CHF | 29.4 | 23.5 | 1.80× |
| EURNZD | NZD | 52.9 | EUR | 30.1 | 22.8 | 1.76× |
| AUDCAD | AUD | 34.1 | CAD | 13.2 | 20.9 | 2.58× |
| GBPAUD | AUD | 34.1 | GBP | 13.4 | 20.7 | 2.54× |
| AUDNZD | NZD | 52.9 | AUD | 34.1 | 18.8 | 1.55× |

(tabel complet, toate cele 28 de perechi: `diag-P-pair-age-diff.csv`)

**11 din 28 perechi (39.3%) au raport de vârstă între legs > 2×.**

Indicatori forțați la 0 din `no_consensus` (nu dead zone), per valută × categorie:

| valută | categorie | n aplicabile | n no_consensus |
|---|---|---:|---:|
| **NZD** | **growth** | **4** | **2** |
| AUD | growth | 4 | 2 |
| CAD | growth | 4 | 1 |
| CAD | inflation | 3 | 1 |
| GBP | growth | 4 | 1 |
| USD | inflation | 4 | 1 |
| toate celelalte (ccy,cat) | — | — | 0 |

**Confirmat: NZD are 2/4 la growth** (manufacturing_pmi, services_pmi — fără forecast în feed-ul FF). AUD are aceeași problemă (2/4 la growth). Restul valutelor au cel mult 1 indicator no_consensus per categorie.

CSV: `diag-P-contributing-ages.csv`, `diag-P-age-per-currency.csv`, `diag-P-pair-age-diff.csv`, `diag-P-no-consensus-counts.csv`.

---

## Q. Secțiuni restante din raportul 1

### Q1 — seria lunară/zilnică reală + data quarantine

**Metodologie diferită de restul raportului, deliberat**: un replay cu codul de AZI aplică gate-ul `can_be_zero` (quarantine) UNIFORM peste tot istoricul, indiferent de as-of (`src.ff_scoring.to_scoring_frame` nu are nicio condiție de dată) — deci un replay NU poate arăta vreodată un "înainte/după" real al quarantine-ului, pentru că retroactiv "curăță" toată fereastra la fel. Am folosit în schimb **istoricul git real** al `public/data/economic.json` (ce a fost EFECTIV live, zi de zi) — singura sursă care poate arăta tranziția reală.

Acest istoric merge doar până la **2026-06-08** (~7 săptămâni, mai scurt decât fereastra de 12 luni folosită în rest) — raportat ca atare, nu extrapolat.

Commit-ul care a introdus quarantine: `bdcd431` "Merge fix/zero-placeholder-quarantine", **2026-07-07 00:38:23 +0300**. Verificat: pragurile `bias_thresholds` (mild=1.3, very=3.0) sunt NESCHIMBATE din 2026-06-11 (commit `95e214f`) — nu există un confound de recalibrare a pragurilor chiar în acea fereastră.

| dată | %Neutral | %direcțional | %Very |
|---|---:|---:|---:|
| 2026-06-08 | 39.3 | 57.1 | 3.6 |
| 2026-06-10..06-19 | 39–61 | 18–43 | **11–21** |
| 2026-06-22..07-04 | 29–36 | 25–46 | **17–46** |
| 2026-07-05 | 50.0 | 35.7 | 14.3 |
| 2026-07-06 | 57.1 | 32.1 | 10.7 |
| **2026-07-07 (quarantine merge)** | **67.9** | 25.0 | **7.1** |
| 2026-07-08..07-11 | 54–68 | 21–36 | 7–11 |
| **2026-07-12** | 60.7 | 39.3 | **0.0** |
| 2026-07-13..07-27 (16 zile consecutive) | 43–71 | 28–57 | **0.0, fără excepție** |

**FORMA E UN SALT, NU UN DECLIN GRADUAL.** %Very era 10–46% pe tot intervalul iunie–începutul lui iulie (medie ~21%), scade brusc pe 05–11 iulie (14→7%) chiar în jurul commit-ului de merge (07-07), apoi **cade la exact 0.0% pe 2026-07-12 și rămâne 0.0% neîntrerupt timp de 16 zile consecutive, până azi**. Nu există NICIUN "Very" pe board-ul FX din 2026-07-12 încoace. Divergența de calibrare raportată în raportul 1 (§C, 3.8% Very vs ținta 10%, măsurată pe fereastra de 12 luni) e, de fapt, media unei ferestre care conține atât regimul vechi (Very frecvent) cât și regimul nou (Very complet absent) — cifra de 3.8% subestimează cât de radicală e schimbarea recentă.

CSV: `diag-Q1-real-history-bias-series.csv`.

### Q2 — tabel complet flip-uri reponderare (FUND 0.5 / TREND 1.0 / SENT 0.5)

Deja livrat integral în raportul 1 (`diag-H-counterfactual-v1-long.csv`, 1484 rânduri = 53 zile × 28 perechi; `diag-H-counterfactual-v1-flip-matrix.csv`; `diag-H-counterfactual-v1-today.csv`, 28 rânduri pentru ultima zi din fereastră). Recapitulare: rată de flip 69.0% (1024/1484), σ 1.374→2.753, Neutral 59.4%→25.5%, Very 3.8%→39.8%.

Matrice flip (rânduri=înainte, coloane=după), din `diag-H-counterfactual-v1-flip-matrix.csv`:

| înainte \ după | Bearish | Bullish | Neutral | Very Bearish | Very Bullish |
|---|---:|---:|---:|---:|---:|
| Bearish | 35 | 4 | 43 | 118 | 0 |
| Bullish | 6 | 73 | 37 | 0 | 230 |
| Neutral | 161 | 236 | 297 | 61 | 126 |
| Very Bearish | 0 | 0 | 0 | 21 | 0 |
| Very Bullish | 0 | 0 | 2 | 0 | 34 |

### Q3 — rank cross-secțional, tabel complet + spread adiacent (azi)

Toate cele 28 perechi, sortate după `score_precise`, cu gap-ul către următoarea pereche în clasament (`diag-Q3-rank-adjacency.csv`):

| pereche | scor | percentilă | bandă rang | gap → următoarea |
|---|---:|---:|---|---:|
| GBPCHF | 2.366 | 100.0 | very_bullish | 0.179 |
| NZDCHF | 2.188 | 96.4 | very_bullish | 0.239 |
| USDCHF | 1.949 | 92.9 | very_bullish | 0.097 |
| EURCHF | 1.852 | 89.3 | very_bullish | 0.094 |
| GBPAUD | 1.758 | 85.7 | very_bullish | 0.300 |
| GBPCAD | 1.458 | 82.1 | very_bullish | 0.136 |
| NZDCAD | 1.323 | 78.6 | bullish | 0.015 |
| GBPJPY | 1.308 | 75.0 | bullish | 0.012 |
| CADCHF | 1.296 | 71.4 | bullish | 0.150 |
| NZDJPY | 1.146 | 67.9 | bullish | 0.126 |
| USDCAD | 1.020 | 64.3 | bullish | 0.122 |
| **USDJPY** | **0.898** | **60.7** | **bullish** | **0.040** |
| GBPNZD | 0.858 | 57.1 | neutral | 0.065 |
| EURJPY | 0.794 | 51.8 | neutral | 0.000 |
| CADJPY | 0.794 | 51.8 | neutral | 0.106 |
| EURAUD | 0.688 | 46.4 | neutral | 0.480 |
| AUDCHF | 0.208 | 42.9 | neutral | 0.208 |
| EURCAD | 0.000 | 39.3 | bearish | 0.833 |
| AUDJPY | −0.833 | 35.7 | bearish | 0.170 |
| NZDUSD | −1.003 | 32.1 | bearish | 0.017 |
| EURUSD | −1.020 | 28.6 | bearish | 0.022 |
| CHFJPY | −1.042 | 25.0 | bearish | 0.020 |
| GBPUSD | −1.061 | 21.4 | bearish | 0.173 |
| AUDUSD | −1.234 | 17.9 | very_bearish | 0.009 |
| AUDCAD | −1.243 | 14.3 | very_bearish | 0.079 |
| EURNZD | −1.323 | 10.7 | very_bearish | 0.136 |
| EURGBP | −1.458 | 7.1 | very_bearish | 0.521 |
| AUDNZD | −1.979 | 3.6 | very_bearish | — |

**Verificare specifică: percentilele separă perechi cu scoruri aproape identice?** Da, **1 caz azi**: **USDJPY** (0.898, bandă "bullish" la p60.7) e la doar **0.040** distanță de **GBPNZD** (0.858, bandă "neutral" la p57.1) — practic același scor afișat (+0.90 vs +0.86), dar V3 le pune în bandă diferită doar pentru că USDJPY trece pragul p60. (Notă: EURJPY și CADJPY au scor IDENTIC — 0.793651 — și sunt corect în aceeași bandă, deci nu se numără ca separare artificială.) **N=1** din 28 perechi azi.

### Q4 — half-life-uri, complete, sortate (zero contact cu randamente)

Rezumat pe familie deja în raportul 1 (`diag-K-halflife-family-summary.csv`); extrase complete (`diag-K-halflife-sorted.csv`, 122 serii rezolvate din 132):

**Cele mai rapide 15** (half-life cel mai scurt):

| componentă | serie | ρ₁ | half-life (zile) |
|---|---|---:|---:|
| TREND[regime] | CADCHF | 0.751 | 2.42 |
| TREND[regime] | GBPAUD | 0.798 | 3.08 |
| TREND[regime] | SP500 | 0.822 | 3.54 |
| TREND[regime] | DAX | 0.830 | 3.73 |
| TREND[regime] | GBPUSD | 0.831 | 3.75 |
| TREND[regime] | EURCHF | 0.839 | 3.95 |
| TREND[regime] | DJIA | 0.849 | 4.24 |
| TREND[regime] | NZDUSD | 0.858 | 4.54 |
| TREND[regime] | NZDCHF | 0.861 | 4.62 |
| TREND[regime] | GBPCAD | 0.861 | 4.62 |
| TREND[regime] | GBPNZD | 0.864 | 4.75 |
| FUND[labour] | USD | 0.385 | 5.08 |
| COT[percentile_6m] | JPY | 0.393 | 5.20 |
| TREND[regime] | NZDCAD | 0.885 | 5.67 |
| TREND[regime] | NASDAQ | 0.898 | 6.45 |

**Cele mai lente 15** (half-life cel mai lung):

| componentă | serie | ρ₁ | half-life (zile) |
|---|---|---:|---:|
| FUND[growth] | NZD | 0.906 | 49.0 |
| TREND[momentum] | SILVER | 0.986 | 50.8 |
| TREND[momentum] | AUDCHF | 0.988 | 58.7 |
| TREND[momentum] | EURAUD | 0.988 | 59.0 |
| COT[percentile_6m] | USD | 0.932 | 68.9 |
| COT[net] | NZD | 0.935 | 72.7 |
| COT[net] | USD | 0.936 | 73.7 |
| FUND[inflation] | AUD | 0.937 | 74.2 |
| TREND[momentum] | GOLD | 0.992 | 82.8 |
| rate_exp_2y[level] | EUR | 0.992 | 85.0 |
| COT[net] | EUR | 0.947 | 88.6 |
| COT[percentile_6m] | CAD | 0.953 | 100.9 |
| COT[net] | CAD | 0.962 | 126.5 |
| COT[net] | JPY | 0.970 | 160.3 |
| **COT[net]** | **AUD** | **0.979** | **224.0** |

Zero contact cu prețuri/randamente în orice etapă a acestui calcul — doar autocorelația seriei proprii.

---

## Gaps

- **Q1 folosește o fereastră mai scurtă și o sursă diferită** (istoric git al `economic.json`, 2026-06-08→azi) decât restul raportului (replay pe 12 luni) — necesar, pentru că un replay cu codul curent nu poate arăta niciodată tranziția reală pre/post-quarantine (gate-ul nu e condiționat de dată). Nu am extins acest istoric git înapoi (nu există commit-uri mai vechi pentru acel fișier).
- **AUD a devenit STALE pentru monetary chiar în cursul acestei săptămâni** (prezent pe toată fereastra de 12 luni până la 2026-07-21, stale azi 2026-07-28) — o fotografie de o singură zi (L, M, N, O, P) surprinde o tranziție de graniță; peste câteva zile, dacă sursa RBA rămâne înghețată, AUD ar putea deveni complet comparabil cu profilul NZD/CHF (stale persistent), sau ar putea reveni PREZENTĂ dacă sursa se reia. Nu am extrapolat starea viitoare.
- **O2 (sweep monetary)** ține sentiment/trend FIXATE la valorile de azi — nu am re-simulat cum ar evolua COT/TREND într-o lume contrafactuală cu monetary NZD/CHF restaurat istoric (ar necesita alt tip de replay, în afara scopului cerut).
- **P (vârsta datelor)** foloseşte doar ziua curentă — nu am construit o serie temporală a asimetriei de vârstă pe toată fereastra de 12 luni (ar fi al doilea mare bloc de calcul; task-ul cerea explicit tabelul "pentru fiecare pereche", nu o serie temporală).
- **Secțiunea N** a tras două funcții de calcul bilateral (`_build_indicator_cells` și `_category_cells`); nu am verificat dacă există o A TREIA cale de afișare (de ex. sub-celule cross-asset) care ar putea introduce o a treia valoare pentru aceeași relație — nu era cerută explicit, dar semnalez posibilitatea.
- **Q3**: pragul de "aproape identic" (gap<0.05) e o alegere a mea, nu una din prompt — cu un prag mai larg (ex. 0.10 sau 0.15) numărul de cazuri "separate artificial" ar crește (majoritatea gap-urilor din tabel sunt <0.2). Am raportat rezultatul la pragul declarat, explicit, nu am ales pragul care dă cifra "cea mai interesantă".

---

## Verdicte mecanice

1. **Aritmetica afișată în modal se închide — CONFIRMAT.**
   Trace-ul complet NZDUSD (§L) reconciliază `inst["score"]` la 1e-6 din `_augmented_index`/`_leg_eff_wsum`/`_fold_trend` cu ponderile citite din YAML la runtime; la fel USDJPY. Discrepanța percepută ("ar trebui să fie −0.07") venea din supoziția greșită că SENTIMENT (weight 0.5, aplicat pe UN leg, ÎNAINTE de `/pair_divisor`) și TREND (weight 0.5, aplicat pe PERECHE, DUPĂ diferențiere, cu numitor `macro_weight` diferit) sunt comensurabile — nu sunt, deși ambele poartă eticheta "weight 0.5".

2. **Absența unei categorii e tratată ca excludere din medie, nu ca zero — CONFIRMAT, cu o excepție locală importantă.**
   La nivelul INDEXULUI VALUTAR (`compute_currency_scorecard`): confirmat numeric — NZD index = `mean(categorii PREZENTE)×5 = 1.944`, nu `mean(cu absență=0)×5 = 1.458` (§M). DAR la nivelul CELULELOR BILATERALE AFIȘATE (`_build_indicator_cells`, `_category_cells`), un leg absent E convertit tăcut la 0 în calculul acelei celule specifice (§N) — cele două straturi ale sistemului tratează absența diferit: indexul propriu al monedei o exclude corect; celula de PERECHE afișată o zero-umple silențios.

3. **Celulele bilaterale cu un singur leg prezent nu sunt distinse vizual de cele cu ambele — CONFIRMAT.**
   24.0% din cele 420 de celule afișate în tabelul dens (azi) sunt calculate dintr-un singur leg real (§N). Niciun badge vizual nu marchează asta — doar `stale` și `no_consensus` au indicatori vizuali în `static/economic-chart.js`. O celulă `base_only` e indistinctă de una `both`.

4. **Divergența de calibrare e salt la quarantine, nu declin gradual — CONFIRMAT, decisiv.**
   Istoricul REAL (git, `economic.json`) arată %Very la 10–46% pe tot iunie–începutul lui iulie, prăbușire pe 05–11 iulie (chiar în jurul merge-ului din 07-07), apoi **exact 0.0% neîntrerupt din 2026-07-12 până azi** (16 zile consecutive) — un salt clar, nu o eroziune treptată (§Q1).

---

*Raport generat de `scripts/diag/section_{l,m,n,o,p,q,q1_history}.py`. Nicio recomandare de adopție. Nicio modificare în `src/`, YAML sau config.*
