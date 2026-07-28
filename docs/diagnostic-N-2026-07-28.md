# Diagnostic §N — cele două calcule bilaterale divergente

**Read-only.** Continuare pe `diag/scoring-audit` (după `8d7f78a`). Nimic modificat în `src/`, YAML sau config. `main` neatins. Nu s-a reparat nimic.

---

## N1. Localizarea celor două căi de cod

| | cale 1 (dense table) | cale 2 (`inst["categories"]`) |
|---|---|---|
| fișier · funcție | `src/economic_render.py` → `_build_indicator_cells`, linia **261** | `src/economic_compute.py` → `_category_cells`, linia **466** |
| consumă | `breakdown[key]["score"]` — scorul **per indicator** (întreg, −2..+2), din fiecare leg | `card["categories"][cat]["score_precise"]` — media **per categorie** (float), din fiecare leg |
| nivel | indicator (14 coloane: `manufacturing_pmi`…`jobless_claims`, + `rate_expectations`) | categorie (4 coloane: `growth`, `inflation`, `labour`, `monetary`) |
| transformare | `v = (eb.score dacă eb else 0) − (eq.score dacă eq else 0)` — **fără** `/pair_divisor` | `precise = (base_precise − quote_precise) / pair_divisor` |
| ieșire | `inst["indicator_cells"][key]` | `inst["categories"][cat]` |

### Istoric git — origine comună, înlocuire incompletă (CONFIRMAT)

```
4408e0c  2026-06-08 17:30:21  economic dashboard: MT5 bridge + UI + refresh script
                              -> introduce _category_cells (cale 2)
b6872df  2026-06-08 17:54:19  economic UI: per-indicator columns, 5-level bias, rounding, color, layout
                              -> introduce _build_indicator_cells (cale 1), 24 min mai târziu
```

**Cale 2 a apărut prima** (commit-ul inițial al dashboard-ului). **Cale 1 a apărut la 24 de minute distanță**, în același commit care a adăugat tabelul dens per-indicator ("per-indicator columns" chiar în titlu).

Diff-ul lui `b6872df` pe `static/economic-chart.js` arată explicit înlocuirea la nivel de UI:

```diff
- function categoryCellHtml(inst, catKey) {
-   const c = (inst.categories || {})[catKey] || { score_cell: 0, coverage: 0 };
    ...
- const catCells = cats.map(c => categoryCellHtml(inst, c)).join("");
+ function indicatorCellHtml(inst, key) { ... }
```

**Înainte de `b6872df`, tabelul principal chiar RANDA `inst.categories` (cale 2)**, prin `categoryCellHtml`. Commit-ul l-a **eliminat** din UI și l-a înlocuit cu tabelul dens per-indicator (`indicatorCellHtml`, cale 1). Dar acest commit **nu a atins deloc `src/economic_compute.py`** (0 linii schimbate acolo, confirmat din `git show b6872df --stat`) — `_category_cells` a rămas complet neschimbată, tot calculată, tot livrată în JSON, dar **nimeni nu o mai citește din 2026-06-08, ora 17:54**, de atunci (~7 săptămâni).

**Verdict N1: cele două căi au origine comună (același commit inițial → commit de refactor UI), iar înlocuirea a rămas incompletă — backend-ul nu a fost curățat când frontend-ul a fost migrat.**

### Trace numeric, NZDUSD monetary, ambele căi (azi, 2026-07-28)

| | cale 1 | cale 2 |
|---|---|---|
| leg NZD | absent (`eb=None`, tratat ca 0) | absent (`base_precise` implicit 0.0 via `.get(cat,{}).get('score_precise',0.0)`) |
| leg USD | `rate_expectations.score = +2` | `monetary.score_precise = +2.0` |
| formulă | `0 − 2 = −2` | `(0.0 − 2.0) / pair_divisor(2) = −1.0` |
| **rezultat** | **−2** | **−1.0** (`score_cell=−1`) |

Diferența exactă: cale 2 împarte la `pair_divisor=2`; cale 1 nu împarte deloc. Ambele tratează leg-ul absent identic (implicit 0).

---

## N2. Întinderea divergenței

**Comparație directă (aceeași relație, 1:1)**: doar `monetary` are exact UN indicator (`rate_expectations`), deci e singura categorie unde cale 1 (per-indicator) și cale 2 (per-categorie) descriu literal aceeași cantitate. Pentru `growth`/`inflation`/`labour` (3-6 indicatori/categorie), cale 2 nu are un echivalent 1:1 pe cale 1 — orice comparație acolo necesită o agregare construită (secțiune separată mai jos, clar etichetată ca atare).

### Monetary — azi, toate cele 28 perechi

| divergențe azi | valoare |
|---|---:|
| perechi cu divergență (\|Δ\|>0) | **16 / 28** |
| perechi fără divergență (Δ=0) | 12 / 28 |

Cele 12 fără divergență: fie ambele legs au monetary "both prezent" cu valori EGALE (diferența brută e 0 pe ambele căi), fie ambele legs lipsesc monetary (NZDCHF).

### Raportul EXACT — sistematic, nu dependent de caz (CONFIRMAT decisiv)

Verificat programatic pe TOATE cele 811 celule cu `path2_precise ≠ 0`, pe toată fereastra de 53 săptămâni × 28 perechi:

```
path1_agg / path2_precise == 2.0   ->  811/811 cazuri (100%)
```

**Fără nicio excepție.** Când `path2_precise = 0` (620 cazuri), `path1_agg` e de asemenea exact 0 (0 = 2×0, consistent). **Divergența e perfect sistematică — un factor constant de exact 2× (= `pair_divisor`), în fiecare celulă divergentă, în fiecare zi, fără nicio excepție.** Nu există niciun caz dependent-de-caz; leg-absența (tratată identic pe ambele căi) nu contribuie la NEÎNȚELEGEREA dintre cele două căi — doar la faptul că valoarea de bază e nenulă. Singura sursă a divergenței dintre cale 1 și cale 2 e omiterea `/pair_divisor` pe cale 1.

Distribuția `|Δ|` (811 celule divergente, fereastră întreagă): min 0.5, p25 0.5, **mediană 0.5**, p75 1.0, max 1.5 — toate valorile posibile ale lui `|path2_precise|` însuși (0.5, 1.0, 1.5 — scorurile rate_expectations sunt întregi ∈{-2..2}, deci `path2_precise ∈ {0, ±0.5, ±1, ±1.5, ±2}·(diferența)/2`).

### Evoluție pe fereastră (53 puncte) — stabilă, nu crește/scade

% perechi cu divergență monetary, pe zi: variază între 0% (2025-08-08) și 82.1% (2025-12-19, 2026-05-22), cu media pe fereastră ≈ **56%**. Seria completă în `diag-N2-divergence-by-day-monetary.csv`. **Nu există un trend clar crescător sau descrescător** — fluctuează cu starea zilnică de acoperire monetary (câte valute au monetary live vs stale/absent în ziua respectivă, cf. raportului 2 §M), nu cu vreo schimbare structurală în timp. E o funcție a câte valute au `monetary` prezent-cu-valoare-nenulă în ziua respectivă, nu a unei derive sistemice.

### growth/inflation/labour — comparație construită, NU o relație 1:1 (caveat explicit)

Pentru aceste 3 categorii am construit `path1_agg = ΣΣ(indicator diffs)/pair_divisor` (suma tuturor diferențelor per-indicator din categorie, împărțită la `pair_divisor`, ca eventuala "echivalență" cea mai directă cu cale 2). Rezultatul: raportul `path1_agg/path2_precise` NU e constant — ia valori 2, 3, 4, 6, 2.4, 3.27 etc., pentru că numărul de indicatori care contribuie diferă între cele două legs (un indicator prezent doar pe un leg contribuie la sumă altfel decât la media per-categorie a acelui leg). **Asta NU e un bug nou** — e o consecință așteptată a faptului că cale 2 nu are deloc un echivalent per-categorie pe cale 1; comparația în sine e o construcție a mea pentru diagnostic, nu o relație pe care sistemul o calculează undeva. Nu raportez un "verdict sistematic" pentru aceste 3 categorii — doar monetary permite concluzia fermă de mai sus.

CSV: `diag-N2-divergence-today.csv` (toate cele 28×4 = 112 rânduri azi), `diag-N2-divergence-window.csv` (toate cele 53×28×4 = 5936 rânduri), `diag-N2-divergence-by-day-monetary.csv`.

---

## N3. Care cale intră în `score_precise` — DECISIV, și diferit de ipoteza din prompt

Am trasat dependența direct în cod (`src/economic_compute.py::compute_instrument`): `score = _fold_trend(macro_score, ...)`, unde `macro_score = (base_idx − quote_idx) / pair_divisor`, iar `base_idx`/`quote_idx` vin din `_augmented_index(card, ...)`, care citește `card["index_num"]` / `card["index_wsum"]` — **acumulatoarele proprii ale monedei, calculate în `compute_currency_scorecard`, ÎNAINTE ca `_category_cells` sau `_build_indicator_cells` să fie apelate**. `_category_cells` e apelată DUPĂ ce `score` e deja calculat, DOAR ca să populeze `inst["categories"]` pentru afișare. `_build_indicator_cells` rulează ȘI MAI TÂRZIU, în `economic_render.py`, complet în afara `build_payload`.

**Verdict: NICIUNA din cele două căi intră în `score_precise`.** Nu doar una — nici una. `score_precise` e o A TREIA cantitate, calculată independent din acumulatoarele brute ale monedei.

### Consecință — verificată prin grep exhaustiv în `static/economic-chart.js`

- **Cale 1** (`inst.indicator_cells`) apare în `indicatorCellHtml` (linia 268) → randată efectiv, în tabelul dens. **Vizibilă utilizatorului.**
- **Cale 2** (`inst.categories`) — am căutat exhaustiv (`grep -n "\.categories\b\|categories\["`) în tot fișierul JS (979 linii) și în `templates/economic.html.j2`: **singura potrivire e `card.categories` din `legHtml` (linia 485), care e o structură DIFERITĂ — categoriile PROPRII ale unei MONEDE (din `payload.currencies[ccy].categories`, calculate de `compute_currency_scorecard`), nu `inst.categories` (calea 2, calculată de `_category_cells` pentru PERECHE).** `inst.categories` **nu apare nicăieri** în codul de randare. E calculată, e trimisă în `economic.json`, dar **niciun cod din interfață nu o citește.**

**Asta contrazice premisa din prompt** ("afișate simultan în aceeași interfață") — corectez explicit: **doar cale 1 e vizibilă azi. Cale 2 e complet moartă în UI**, nu doar "decorativă" — e calculată pe fiecare refresh (cost computațional mic dar real) și transmisă în payload-ul JSON, fără ca vreun pixel să depindă de ea. Verificarea a fost posibilă exact pentru că §L/§N din raportul 2 au folosit direct funcțiile Python (nu UI-ul), deci am putut vedea o valoare (`−1.0`) care de fapt nu ajunge niciodată pe ecran.

### N3.2 / N3.3 — care caz se aplică

- **N3.2 se confirmă PARȚIAL**: dacă doar una intră în scor, cealaltă e decorativă — corect, dar situația reală e mai severă: **niciuna nu intră în scor**, deci AMBELE sunt, strict vorbind, "decorative" față de `score_precise`. Cale 1 e totuși randată (utilizatorul o vede, chiar dacă nu influențează scorul); cale 2 nu e randată deloc (nici măcar decorativă vizibil — e complet oarbă).
- **N3.3 nu se aplică**: fără dublă numărare în scor, pentru că niciuna din cele două căi nu alimentează `score_precise`.

---

## N4. Tratamentul leg-ului absent

### Ambele căi: propagă tăcut 0, niciodată None-passthrough

- Cale 1: `eb["score"] if eb else 0` / `eq["score"] if eq else 0` — leg absent (`eb is None`) → literal `0`.
- Cale 2: `float(base_cat.get("score_precise", 0.0))` — leg absent (`base_cat = {}`) → literal `0.0`.

**Identic — ambele convertesc absența în zero, fără marcaj.** Asta explică de ce leg-absența NU cauzează divergență ÎNTRE cele două căi (§N2) — ambele fac aceeași conversie greșită, deci "greșeala" li se anulează reciproc când le compari una cu alta. Dar fiecare, individual, contrazice tratamentul de la nivelul indexului.

### Confirmare/infirmare numerică vs decizia §M

**CONFIRMAT — contradicție directă.** La nivelul indexului (`compute_currency_scorecard`), absența/staleness-ul sunt **EXCLUSE** din numărător și numitor (verificat numeric pe AUD în prereg §13.2: `index_wsum=3.0`, nu 4.0). La nivelul celulei bilaterale (ambele căi 1 și 2), absența devine **0 inclus explicit** în formulă — exact opusul. **Același sistem, aceeași situație de date (o categorie/indicator lipsă), două tratamente contradictorii ale absenței, în funcție de la ce nivel te uiți.**

### N4.3 — single-leg, pe cale, pe coloană (azi)

| cale | n celule totale | both | neither | single-leg | %single-leg |
|---|---:|---:|---:|---:|---:|
| **cale 1** (28 perechi × 15 coloane) | 420 | 226 | 93 | **101** | **24.0%** |
| **cale 2** (28 perechi × 4 categorii) | 112 | 90 | 6 | **16** | **14.3%** |

Pe cale 1, single-leg pe coloană: `wage_growth` (15), `services_pmi` (15), `employment_change` (12), `core_cpi` (12), **`rate_expectations` (12)**, `core_pce`/`adp`/`jolts`/`jobless_claims` (7 fiecare, toate USD-only), `ppi_yoy` (7).

Pe cale 2, single-leg pe categorie: **toate cele 16 cazuri sunt în `monetary`** — `growth`/`inflation`/`labour` sunt 100% "both prezent" pentru toate cele 8 valute (confirmat în raportul 2 §M: acele 3 categorii nu au niciodată absență structurală).

### N4.4 — există vreun flag care ar permite distincția?

**Informația NU e pierdută din payload** — `breakdown` (per monedă) păstrează fidelitatea completă (cheia `rate_expectations` lipsește literal din dict pentru NZD/CHF, e distinctă de o cheie prezentă cu `stale=True` pentru AUD/JPY). Dar **NICIUNA din funcțiile de celulă bilaterală (cale 1 sau cale 2) nu propagă această distincție în STRUCTURA proprie de ieșire**: cale 1 produce `{v, stale}` (fără câmp pentru "leg lipsă" vs "leg prezent dar 0"); cale 2 produce `{score_cell, score_precise, coverage}` (coverage agregă AMBELE legs, deci nu poți ști DIN celulă dacă lipsește un leg sau ambele au coverage parțial). Un strat de afișare care ar vrea să distingă ar trebui să interogheze `breakdown`-ul brut al ambelor legs separat — informația e acolo, dar NU e expusă prin celula bilaterală însăși.

---

## N5. Contrafactual — unificare pe o singură cale (simetric, nicio recomandare)

**Scop**: izolat la stadiul FUND (diferențierea de bază, ÎNAINTE de SENTIMENT/TREND — niciuna din cele două căi nu atinge sentiment/trend, deci contrafactualul rămâne la acest nivel, ca să nu amestec efecte). Extins de la "o coloană" la "toată diferențierea FUND": pentru fiecare pereche, sumez TOATE cele 15 coloane (`TABLE_COLUMN_KEYS`) pe cale 1 (fără `/pair_divisor`, exact convenția căii 1) vs. principiul căii 2 aplicat la întregul index (medie-peste-prezente, `/pair_divisor` — asta e, de fapt, EXACT ce face deja `macro_score` din producție azi).

| | cale 1 exclusiv (sumă brută, fără /pair_divisor) | cale 2 exclusiv (= FUND-ul de producție de azi, media/pair_divisor) |
|---|---:|---:|
| flip-uri vs starea curentă (scor final, cu SENTIMENT+TREND) | **71.7%** | 37.5%* |
| σ mediu | **3.965** | 1.303 |
| \|score\| p50 / p90 / p95 / max | 3.00 / 7.00 / 8.00 / **14.00** | 0.94 / 2.34 / 2.81 / 5.47 |
| split bias | Neutral 27.2% · **Very (combinat) 57.3%** | Neutral 63.8% · Very (combinat) 4.5% |

*cale 2 exclusiv = principiul deja folosit azi pentru FUND (înainte de SENTIMENT/TREND); cei 37.5% flip-uri vin EXCLUSIV din lipsa foldului SENTIMENT+TREND din acest contrafactual, nu din vreo diferență cale1/cale2 — e o comparație "cu/fără fold", nu "cale1 vs cale2".

**Comparație izolată, cale 1 vs cale 2, ambele FĂRĂ sentiment/trend (testul curat al diferenței de filozofie)**: **68.6%** din pereche-zile își schimbă eticheta de bias între cele două convenții. Asta e cifra care izolează efectiv "ce s-ar schimba dacă tot sistemul ar folosi convenția căii 1 (sumă brută, fără pair_divisor) în loc de convenția căii 2 (medie, cu pair_divisor — cea deja folosită)".

**Raportare simetrică, fără recomandare**: cale 1 exclusiv produce o scală de ~2.9× mai mare (σ 3.97 vs 1.30) și o coadă "Very" de 57.3% (vs 4.5% pentru cale 2) — un artefact direct al însumării nenormalizate a până la 15 diferențe brute ±2..±4 fără nicio împărțire. Cale 2 exclusiv e, de fapt, identică cu ce sistemul deja face pentru FUND. Nu recomand niciuna — raportez ambele consecințe, așa cum au ieșit.

CSV: `diag-N5-counterfactual-long.csv` (53×28 = 1484 rânduri).

---

## Gaps

- **growth/inflation/labour în N2**: comparația cale1-vs-cale2 pentru aceste 3 categorii necesită o agregare construită de mine (sumă/pair_divisor) — nu există o relație 1:1 nativă în sistem ca la `monetary`. Raportul lor variabil (2×–6×) nu trebuie citit ca "divergență sistematică suplimentară" — e un artefact al agregării mele, explicat, nu ascuns.
- **N4.4**: nu am construit un prototip de flag/UI — doar am confirmat că informația de leg-absent EXISTĂ în `breakdown` dar nu e propagată în structurile de celulă. Nu am evaluat cât ar costa (arhitectural) să se adauge.
- **N5**: contrafactualul "cale 1 exclusiv" nu a fost testat și cu SENTIMENT/TREND foldate peste el (ar necesita o decizie despre cum s-ar adapta foldul de sentiment/trend la o convenție "sumă brută fără normalizare" — nu evidentă, nu am inventat-o).
- Nu am verificat dacă `public/economic-chart.js` (copia publicată, vs `static/economic-chart.js`, sursa) diferă — am citit doar sursa din `static/`, care e cea din care se copiază (`copy_static_assets`, verificat indirect din `economic_render.py`). Presupun sincronizare, nu am diff-uit explicit cele două fișiere.

---

## Verdicte mecanice

1. **Cele două căi au origine comună și una a rămas incompletă după un refactor — CONFIRMAT.**
   `_category_cells` (cale 2) apare în commit-ul inițial `4408e0c` (2026-06-08 17:30). `_build_indicator_cells` (cale 1) apare 24 minute mai târziu, în `b6872df`, ACELAȘI commit care **elimină** `categoryCellHtml` (consumatorul UI al căii 2) din `static/economic-chart.js` — dar **nu atinge deloc** `src/economic_compute.py`. Backend-ul căii 2 a rămas neșters de atunci (~7 săptămâni).

2. **Doar una dintre căi intră în `score_precise` — INFIRMAT (parțial), mai sever decât presupune întrebarea.**
   Niciuna din cele două căi nu intră în `score_precise` — verificat prin trasarea directă a `compute_instrument` (§N3): scorul vine din acumulatoarele proprii ale monedei, calculate ÎNAINTE ca oricare din cele două funcții de celulă bilaterală să ruleze. Cale 1 e totuși vizibilă utilizatorului (randată în tabelul dens); cale 2 nu e randată deloc nicăieri în UI (confirmat prin grep exhaustiv în `static/economic-chart.js`).

3. **Tratamentul leg-ului absent contrazice decizia §M (excludere la index, neutru la celulă) — CONFIRMAT.**
   Ambele căi convertesc explicit leg-absent → 0 (`eb.score if eb else 0` / `.get(...,0.0)`), fără marcaj. La index, absența/staleness-ul sunt EXCLUSE din numărător/numitor (confirmat numeric pe AUD, prereg §13.2: `index_wsum=3.0`). Contradicție directă, în același sistem, aceeași categorie de date de intrare.

4. **Divergența e sistematică, nu dependentă de caz — CONFIRMAT, exact.**
   Pentru `monetary` (singura comparație 1:1 validă), raportul `cale1/cale2` e **exact 2.0 în toate cele 811 celule divergente** din fereastra de 53 săptămâni × 28 perechi, fără nicio excepție — factorul e chiar `pair_divisor`, omis pe cale 1. Nu e o eroare de rotunjire, nu variază cu ziua sau perechea — e o omisiune structurală constantă.

---

*Raport generat de `scripts/diag/section_n_divergence.py` + calcule inline pentru N5. Nicio reparație aplicată. Niciun PR.*
