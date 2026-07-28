# Diagnostic — a treia cale (score_precise) vs cale 1 (afișată)

**Read-only.** Continuare pe `diag/scoring-audit` (după `774c29e`). Nimic modificat, nimic reparat, nimic șters. `main` neatins.

---

## 1. Trasarea completă a celei de-a treia căi

| pas | fișier · funcție · linie | ce face |
|---|---|---|
| 1 | `src/economic_compute.py::compute_indicator_score`, linia **182** | scor per indicator (−2..+2), din z/pct bucketed surprise |
| 2 | `src/economic_compute.py::compute_currency_scorecard`, linia **326** | agregă indicatorii ne-stale în `per_cat[cat]` (linia 375-376), calculează `categories_out[cat]["score_precise"]` = medie per categorie (linia 387), acumulează `cat_scores_for_index` (linia 396, 419) |
| 3 | *(același, linia **421-424**)* | `index_num = Σ(precise_c · weight_c)`, `index_wsum = Σ(weight_c)`, `index = (index_num/index_wsum) · scale` — **acumulatoarele exacte care produc indexul monedei** |
| 4 | `src/economic_compute.py::_augmented_index`, linia **498** | citește `index_num`/`index_wsum` direct, adaugă SENTIMENT (`w_sent`), scalează |
| 5 | `src/economic_compute.py::_leg_eff_wsum`, linia **515** | greutatea efectivă a leg-ului după SENTIMENT |
| 6 | `src/economic_compute.py::compute_instrument`, linia **550** | `macro_score = (base_idx − quote_idx) / pair_divisor` |
| 7 | `src/economic_compute.py::_fold_trend`, linia **528** | fold TREND peste `macro_score` → `score` |
| 8 | `src/economic_compute.py::build_payload`, linia **685** | `"score": float(score)` — valoarea finală, `inst["score"]` |

**Nici pasul 2 (`categories_out`, sursa căii 2) nici randarea din `economic_render.py` (cale 1) nu apar în acest lanț** — `score` derivă EXCLUSIV din `index_num`/`index_wsum` (pas 3), calculate ÎNAINTE ca `categories_out` să fie folosit pentru altceva decât afișare.

---

## 2. Divergența: valoarea AFIȘATĂ (cale 1) vs contribuția reală la `score_precise`

Am descompus liniar `index_num`/`index_wsum` per indicator: contribuția exactă a unui indicator `k` (categoria `c`, moneda `ccy`) la `macro_score` e

```
contribuție(k) = ± score_k · [weight_c / (index_wsum_ccy · coverage_c)] · scale / pair_divisor
```

**Validare**: suma contribuțiilor tuturor indicatorilor unei perechi reconstruiește `macro_score` exact — verificat pe toate cele 28 perechi azi, `max|diff| = 2.78×10⁻¹⁶` (zgomot de virgulă mobilă, adică zero). E o descompunere liniară exactă, nu o aproximare.

Comparație cale 1 (`v = eb.score − eq.score`, nenormalizat) vs contribuția reală de mai sus, pe toate cele 28 perechi × 15 coloane × 53 săptămâni (16995 celule):

| | valoare |
|---|---:|
| celule divergente | **9503 / 16995 (55.9%)** |
| Δ (path1 − path3), pe celulele divergente: min / p25 / mediană / p75 / max | −3.69 / −0.79 / **+0.38** / +0.84 / +3.58 |

**Divergența e DEPENDENTĂ DE CAZ, nu un factor sistematic unic** (spre deosebire de §N, unde cale1/cale2 divergeau mereu la exact ×2). Pentru celule cu un singur leg contribuind, raportul `path1/path3` e explicat exact de:

```
path1 / path3 = index_wsum_ccy · coverage_c · pair_divisor / scale
```

— o formulă deterministă, dar cu valori DIFERITE în funcție de câte categorii are moneda azi (`index_wsum` ∈ {3,4}) și câți indicatori are categoria respectivă (`coverage_c` ∈ {1..6}). Rapoartele observate (4.8, 3.2, 6.4, 1.6, 9.6, 3.6, 2.4, …) sunt exact combinațiile `index_wsum × coverage_c × 2/5` posibile — de ex. `USD` labour (index_wsum=4, coverage=6) → 4×6×2/5=**9.6** (697 celule, exact); o valută cu 3 categorii și growth cu 4 indicatori (index_wsum=3, coverage=4) → 3×4×2/5=**4.8** (2367 celule, cel mai frecvent). Când AMBELE legs contribuie cu multiplicatori diferiți, raportul nu se mai reduce la o singură constantă (valorile 4.11, 5.49, 5.24, 7.2 etc. din coadă) — dependent de caz în acel sens, dar întotdeauna reconstruibil din formulă, nu zgomot.

**Concluzie N2-nouă**: divergența cale1-vs-cale3 nu e (doar) despre `pair_divisor` lipsă (ca la cale1-vs-cale2, §N) — e despre faptul că cale 1 nu normalizează DELOC prin câți indicatori/categorii concurează la scor. Un indicator dintr-o categorie cu 6 frați (USD labour) apasă real pe scor de 6× mai puțin decât unul singur în categoria lui, dar tabelul dens îl arată cu ACEEAȘI magnitudine brută.

CSV: `diag-3P-today.csv` (420 rânduri), `diag-3P-window.csv` (16995 rânduri).

---

## 3. Tratamentul leg-ului absent pe a treia cale — EXCLUDERE, confirmat numeric

| caz | multiplicator (`_leg_multiplier`) | interpretare |
|---|---:|---|
| NZDUSD monetary, leg NZD (bază) | **0.0** | absență structurală → exclus complet din lanțul care produce `score_precise` |
| NZDUSD monetary, leg USD (cotație) | **1.25** | live, inclus (= `1.0/(4·1)·5/2`) |
| AUD monetary (stale) | **0.0** | exclus, identic cu absența structurală — a treia cale nu distinge stale de absent, dar EXCLUDE pe amândouă |

**Confirmat: a treia cale tratează absența (și staleness-ul) prin EXCLUDERE, consecvent cu decizia §M** (verificată acolo numeric pe AUD: `index_wsum=3.0`, nu 4 — aceeași mărime, aceeași sursă de date, doar reconfirmată aici la nivel de indicator individual, nu doar de categorie). Asta era de așteptat: a treia cale ȘI decizia §M citesc EXACT aceleași acumulatoare (`index_num`/`index_wsum`) — nu sunt două verificări independente care întâmplător coincid, e literalmente același număr.

**Contrast cu cale 1/cale 2** (ambele zero-umplu tăcut, §N): a treia cale (reală, cea care produce scorul) exclude; cele două căi de AFIȘARE includ tăcut cu 0. Sistemul e intern consecvent la nivelul care contează (scorul), dar inconsecvent la nivelul afișării — utilizatorul vede un tratament (0 tăcut) diferit de ce se întâmplă de fapt în spate (excludere).

---

## 4. Perechi unde suma afișată dă un semn diferit de `score_precise`

Două comparații, ca să separ cauzele:

**(a) suma cale-1 vs `macro_score` (FUND, fără sentiment/trend) — izolează EXACT efectul din §2:**
**8/28 perechi azi**: NZDUSD, EURGBP, EURJPY, EURNZD, GBPJPY, GBPNZD, CADJPY, NZDCAD.

**(b) suma cale-1 vs scorul final afișat (`inst["score"]`, cu SENTIMENT+TREND) — comparația literală cerută:**
**9/28 perechi azi**: GBPUSD, USDJPY, USDCAD, NZDUSD, EURJPY, GBPJPY, GBPNZD, NZDJPY, CADJPY.

Cele două seturi NU coincid — 5 perechi sunt în ambele (NZDUSD, EURJPY, GBPJPY, GBPNZD, CADJPY: divergența de la §2 e suficientă singură), 3 dispar la (b) pentru că SENTIMENT+TREND corectează semnul înapoi (EURGBP, EURNZD, NZDCAD), iar 4 apar NOU la (b), introduse exclusiv de SENTIMENT+TREND (GBPUSD, USDJPY, USDCAD, NZDJPY — deja documentate ca inversări de semn în raportul 2 §F/§L).

**Pe fereastra de 53 săptămâni**: rata de nepotrivire de semn (comparația literală (b)) e **20.6%** din toate pereche-zile — variază de la 5.7% (AUDCAD) la 39.6% (EURCHF) per pereche. Adică, aproximativ 1 din 5 pereche-zile, dacă un utilizator ar aduna manual ce vede în tabelul dens, ar ajunge la o concluzie de direcție OPUSĂ scorului/etichetei afișate în același ecran.

CSV: `diag-3P-sign-check-today.csv`, `diag-3P-sign-check-window.csv`.

---

## Gaps

- Formula de contribuție marginală (§2) presupune liniaritate strictă (confirmată prin validare, `diff≈0`) — dar SENTIMENT și TREND nu sunt incluse în ea (nu fac parte din niciuna dintre cele două căi de afișare comparate în §N/aici). §4(b) le include doar la nivelul scorului final, nu descompus per-indicator.
- Nu am verificat dacă rata de 20.6% (§4) variază sistematic în timp (crescător/descrescător) — am raportat doar media pe fereastră și distribuția per-pereche, nu o serie zilnică (nu a fost cerută explicit aici, spre deosebire de §N2 pentru monetary).
- Coeficienții `weight_c` (categorii) sunt confirmați 1.0 pentru toate cele 4 categorii din `data/economic_indicators.yaml`/`economic_instruments.yaml` — nu am verificat dacă vreo valută/instrument viitor ar putea avea o pondere de categorie diferită de 1.0 (azi nu există niciunul, dar formula din §2 ar rămâne corectă oricum, `weight_c` fiind deja parametrul din formulă).

---

*Nimic reparat. Codul mort din §N (`_category_cells`/`inst["categories"]`) rămâne neatins, cum s-a cerut. Niciun PR.*
