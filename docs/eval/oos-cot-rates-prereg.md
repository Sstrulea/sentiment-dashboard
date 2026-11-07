# Verificare out-of-sample: COT FX și rate la indici — pre-înregistrare

Data: 2026-10-16. Branch: `eval/oos-cot-rates`, din `main` 5eb10f1a (după merge-ul PR #47).

Documentul e scris și pus în commit **înainte de orice calcul pe randamente**. La acest commit există doar descărcările de date și verificarea lor (`scripts/measure/oos/yields_ext.py`). Codul pentru semnale și metrici nu există încă. Nicio regulă de mai jos nu se schimbă după ce se văd rezultatele.

Secțiunile A și B reproduc textul cerut, așa cum a fost dat. Secțiunea C fixează datele, verificate înainte. Secțiunea D fixează detaliile operaționale pe care textul nu le precizează.

---

## A. Contextul și ipotezele — textul cerut

Turneul de scoring (PR #47) a arătat, pe fereastra 2023-09-22 → 2026-09-04, două lucruri care ar schimba v3. Ambele au fost găsite pe aceleași date pe care s-a făcut evaluarea:
1. FX, COT: componenta level (E1, contrarian) are BA 0,542 la 2 săptămâni. Componenta flow (E2) are semnul greșit: BA 0,446, IC −0,084. Celula COT din v3 le adună (cot_cell din src/cot_score.py).
2. Indici, rate: pe 2014–2026 semnalul de rate (D2) are BA 0,542 și IC 0,100. v3 îl tratează asimetric (v3_index_combine din src/crossasset_compute.py): ratele în creștere doar anulează un macro pozitiv, iar singure nu fac niciodată un indice bearish.

Scopul: verifică ambele ipoteze pe date mai vechi, nefolosite în turneu, cu reguli de decizie fixate dinainte. Nimic din producție nu se schimbă.

### H1 — COT FX: level fără flow
- Date: istoricul CFTC pentru contractele valutelor din data/contracts.yaml (EUR, GBP, JPY, AUD, NZD, CAD, CHF) și pentru DXY, din același dataset folosit de repo (jun7-fc8e), de la prima dată disponibilă. Cache în ~/.cache/macro-tournament/cftc, în afara repo-ului. Extremele se calculează cu src.compute.compute_metrics, iar componentele cu src/cot_score.py, exact ca în producție.
- Fereastra: de la prima vineri cu 3 ani de istoric COT (necesari pentru extrema pe 3 ani) până la 2023-09-15. Nimic de după 2023-09-15 nu intră.
- Semnale pe valută (USD = contractul DXY): L = level; F = flow; C = celula v3 (cot_cell, level + flow limitat).
- Evaluare ca în turneu, la nivel de valută (8 valute, randament demeaned față de USD): BA și IC la 1, 2 și 4 săptămâni, cu CI din bootstrap pe blocuri de săptămâni. Aceleași metrici și pe cele două jumătăți ale ferestrei.
- Decizie: „scoate flow din celula COT” doar dacă toate trei sunt adevărate:
  1. BA la 2 săptămâni: L − C > 0, cu p < 0,05 (bootstrap pereche, unilateral);
  2. diferența L − C e pozitivă în ambele jumătăți;
  3. IC-ul lui F la 2 săptămâni e ≤ 0.
  Altfel celula rămâne cum e.

### H2 — Indici: ratele simetrice
- Semnal: blocul de rate v3 al fiecărui indice, R = media semnalelor −Δ63/σ_y pentru 2Y din valuta de bază și pentru randamentul real US 10Y (v3_yield_signals). Fără macro, care nu există înainte de 2023.
- Fereastra: 2014-01-03 → 2023-09-15. Unități: SP500, NASDAQ, DJIA, DAX, NIKKEI, FTSE100.
- Apeluri bearish: săptămânile cu R ≤ −p55 din |R| (pe fereastră, per indice). Apeluri bullish: R ≥ +p55.
- Metrica: randamentul log mediu la 2 săptămâni după apelurile bearish, comparat cu cel din toate celelalte săptămâni. La fel pentru apelurile bullish. Pooled pe cei 6 indici, cu bootstrap pe blocuri de săptămâni (săptămâna reeșantionată întreagă).
- Decizie: „ratele devin simetrice la indici” doar dacă ambele sunt adevărate:
  1. randamentul mediu după apelurile bearish e mai mic decât în celelalte săptămâni, cu p < 0,05 (unilateral);
  2. diferența are același semn în ambele jumătăți (2014-01 → 2018-10 și 2018-11 → 2023-09).
  Altfel regula asimetrică rămâne.

## B. Reguli generale — textul cerut

- Prețuri ca în turneu: FX din FRED H.10, indici din Yahoo (^GSPC, ^IXIC, ^DJI, ^GDAXI, ^N225, ^FTSE). Fără data/price_history.parquet și fără MT5.
- Fail-closed: dacă datele nu acoperă fereastra, oprește-te și raportează ce lipsește.

## C. Datele (verificate înainte de pre-înregistrare, fără randamente)

### C1. CFTC (H1)

Sursa e jun7-fc8e (`src.fetch.ENDPOINT`), cu aceleași coloane ca `src.fetch.SELECT_COLS`. Toate rândurile contractului sunt descărcate pe 2026-10-16, în `~/.cache/macro-tournament/cftc/cot_full_<cod>.parquet`.

| Valută | Cod | Primul raport | Rapoarte | Ultimul gol > 8 zile |
|---|---|---|---:|---|
| EUR | 099741 | 1999-01-05 | 1449 | 2001-12-28 → 2002-01-08 |
| GBP | 096742 | 1995-03-21 | 1647 | 2001-12-28 → 2002-01-08 |
| JPY | 097741 | 1995-03-21 | 1647 | 2001-12-28 → 2002-01-08 |
| AUD | 232741 | 1995-03-21 | 1621 | 2004-09-07 → 2004-09-21 |
| NZD | 112741 | 1999-01-05 | 1157 | 2006-06-13 → 2006-07-11 (golul mare: 2000-04-04 → 2003-11-11) |
| CAD | 090741 | 1995-03-21 | 1647 | 2001-12-28 → 2002-01-08 |
| CHF | 092741 | 1995-03-21 | 1645 | 2004-09-07 → 2004-09-28 |
| USD (DXY) | 098662 | 1995-03-21 | 1584 | 2003-08-05 → 2003-08-19 |

**Începutul ferestrei H1.** „3 ani de istoric” înseamnă 156 de rapoarte săptămânale consecutive, fără niciun gol > 8 zile, pentru fiecare dintre cele 8 contracte, ca extrema pe 3 ani (`_trailing_rank`, 156 de rânduri) să acopere chiar 3 ani.

- Cel mai târziu contract care atinge pragul e NZD: al 156-lea raport după ultimul gol vine pe 2009-06-30.
- Fereastra H1 este deci **2009-07-03 → 2023-09-15**, vinerile 21:00 UTC.
- În interiorul ferestrei nu există niciun gol.

### C2. 2Y EUR și GBP (H2)

În `data/rates.parquet`, 2Y EUR începe pe 2021-09-24, iar 2Y GBP pe 2016-01-04. Ambele încep după 2014-01. Istoricul anterior vine din **aceleași surse** pe care le folosește repo-ul (`scripts/measure/oos/yields_ext.py`):
- EUR: `EcbSource`, cheia `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y`, cu startPeriod 2013-01-01;
- GBP: `BoeSource.fetch_history_with_coverage`, curba nominală GLC, spot 2,0 ani.

Datele sunt doar pentru evaluare, cu cache în `~/.cache/macro-tournament/yields`. Pragul fixat de utilizator: diferența absolută maximă pe zilele comune cu `data/rates.parquet` ≤ 0,01 pp.

| Valută | Sursă | Prima zi | Obs. înainte de 2014-01-03 | Zile comune cu repo | Dif. abs. max. | Goluri > 7 zile | OK |
|---|---|---|---:|---:|---:|---|---|
| EUR | ECB YC | 2013-01-02 | 256 | 1294 | 4,4e-16 pp | — | da |
| GBP | BoE GLC | 2013-01-02 | 254 | 2715 | 8,9e-16 pp | — | da |

Ambele trec, deci **toți cei 6 indici intră în H2**. Seria folosită este `data/rates.parquet` acolo unde există și istoricul extern înainte de prima zi din repo, cu aceeași etichetă de sursă (`ecb`, `boe_glc`), ca regula de prospețime `max_age_for` să fie aceeași. USD 2Y (din 1976), JPY 2Y (din 1974) și DFII10 (din 2003) din repo acoperă fereastra.

### C3. Prețuri

Prețurile sunt ca în turneu (`scripts/measure/tournament/prices.py`):
- FX: FRED H.10, cache-ul turneului (2026-10-16), EUR din 1999 și celelalte valute din 1971;
- indici: Yahoo, cache-ul turneului (2026-10-16), Close, din 2000.

Ambele acoperă ferestrele plus orizontul de 4 săptămâni.

## D. Precizări operaționale (fixate înainte de rezultate)

### D1. H1

- La fiecare vineri t (21:00 UTC) intră rapoartele cu data de raport (marți) ≤ t, ca în producție și în turneu. `compute_metrics` rulează pe toate rândurile brute ale contractului. Rangurile sunt trailing, deci la t ele depind doar de rândurile ≤ t, ceea ce verifică testul de look-ahead.
- Pentru o valută, la t:
  - L = `level_score(ext_6m, ext_3y)` al ultimului raport;
  - F = `flow_score(spec_net al tuturor rapoartelor ≤ t)`;
  - C = `cot_cell(ext_6m, ext_3y, spec_net)` = clamp(L + F, ±4).
  - Ca în producție, o celulă există numai dacă ext_6m al ultimului raport există.
  - F cu basis „insufficient_history” e nedefinit. În fereastră nu apare, pentru că are ≥ 156 de rapoarte.
- USD folosește contractul DXY. Contractele sunt cotate față de USD, deci semnul e cel al valutei față de dolar, fără inversare.
- Țintele sunt cele din turneu (`metrics.targets`, vederea pe valute): randamentul log față de USD (USD = 0), demeaned pe cele 8. Intrarea e prima fixare FRED la sau după luni, iar ieșirea prima la sau după intrare + 7·h zile, cu h ∈ {1, 2, 4}.
- BA ca în turneu: pe valută × săptămânile cu scor ≠ 0 și randament ≠ 0. Scorul 0 înseamnă „fără apel”. Se raportează și acoperirea.
- IC ca în turneu: Spearman cross-secțional pe valute în fiecare săptămână (minimum 6 valute, scoruri neconstante), apoi media pe săptămâni.
- Bootstrap ca în turneu (`metrics.boot_idx`): circular, pe blocuri de săptămâni (4 la h = 1 și 2, 8 la h = 4), 2.000 de eșantioane, seed 20261016, CI 95% percentil.
  - p pentru L − C: aceleași eșantioane pentru L și C, p unilateral = (1 + #{Δ* ≤ 0}) / 2001.
  - Se face un singur test, fără Holm.
- Jumătățile: primele ⌈n/2⌉ vineri ale ferestrei și restul.
- Regula de decizie:
  - Condiția 1: Δ BA estimat > 0 și p < 0,05.
  - Condiția 2: Δ BA estimat > 0 în fiecare jumătate.
  - Condiția 3: estimarea punctuală a IC-ului lui F la 2 săptămâni ≤ 0, pe toată fereastra.

### D2. H2

- R la t = media semnalelor prezente din `v3_yield_signals(rates_ext, real_yields, t.date(), sigma_y din config)` pentru 2Y-ul valutei de bază (SP500, NASDAQ, DJIA: USD; DAX: EUR; NIKKEI: JPY; FTSE100: GBP) și pentru REAL10 (DFII10).
  - Un semnal e absent dacă e stale sau dacă seria e prea scurtă, ca în producție.
  - Fără niciun semnal, R e nedefinit.
  - Nu se împarte la σ_Rates, pentru că pragul p55 per indice nu depinde de scară.
- Pragul p55 = cuantila 0,55 a |R| pe vinerile ferestrei cu R definit, calculată per indice pe toată fereastra. Bearish: R ≤ −p55. Bullish: R ≥ +p55.
- Ținta: randamentul log la 2 săptămâni al indicelui (Yahoo), cu intrare și ieșire ca în turneu.
- Metrica: pooled pe indice × săptămânile cu R definit și randament definit.
  - Δ_bear = media(r | bearish) − media(r | toate celelalte indice × săptămâni, inclusiv bullish);
  - Δ_bull = media(r | bullish) − media(r | celelalte);
  - se raportează și mediile pe cele trei grupuri (bearish, bullish, rest = nici bearish, nici bullish).
- Bootstrap: circular, pe blocuri de 4 săptămâni (h = 2), 2.000 de eșantioane, seed 20261016. Fiecare săptămână e luată întreagă, cu toți indicii ei. p unilateral pentru H1: Δ_bear < 0 este (1 + #{Δ_bear* ≥ 0}) / 2001.
- Jumătățile: vinerile ≤ 2018-10-26 și vinerile ≥ 2018-11-02. Pragurile p55 rămân cele de pe toată fereastra.
- Regula de decizie:
  - Condiția 1: Δ_bear estimat < 0 și p < 0,05.
  - Condiția 2: Δ_bear estimat < 0 în ambele jumătăți.
  - Δ_bull se raportează, dar nu intră în decizie.

### D3. Teste

- **Fără look-ahead:** L, F, C și R la 3 vineri aleatoare din fiecare fereastră (seed fix) sunt identice cu toate datele și cu datele tăiate la t (CFTC: rândurile brute ≤ t; randamente: observațiile ≤ t.date()).
- **Concordanța cu `data/history.parquet`:**
  - Rândurile brute descărcate sunt identice cu cele din `history.parquet` la datele comune (2023-03-28 →).
  - L, F și C calculate din istoricul descărcat sunt identice cu cele calculate din `history.parquet` în săptămânile în care ferestrele trailing din `history.parquet` sunt complete: 156 de rapoarte pentru ext_3y (L, C) și 57 pentru F.
  - Înainte de acest punct, `history.parquet` nu are încă 3 ani de istoric. Acolo valorile diferă legitim (L pe bază „6m_only”), iar raportul arată câte săptămâni sunt comparabile.
