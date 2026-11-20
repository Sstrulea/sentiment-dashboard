# Verificare out-of-sample: COT FX și rate la indici

Evaluare read-only pe date anterioare ferestrei turneului (până la 2023-09-15). Producția nu se schimbă.

## 1. H1 — COT FX: level fără flow

Fereastra: 2009-07-03 → 2023-09-15 (742 de vineri). Jumătatea a doua începe la 2016-08-12. Unitățile sunt cele 8 valute (USD = DXY), cu randament demeaned față de USD. BA se calculează pe apeluri (scor ≠ 0), iar IC e media Spearman-ului cross-secțional. Între paranteze drepte e CI 95% din bootstrap pe blocuri.

| Semnal | Prima / ultima săpt. | Acoperire | BA 1s | BA 2s | BA 4s | IC 1s | IC 2s | IC 4s |
|---|---|---:|---|---|---|---|---|---|
| **L — level** | 2009-07-03 / 2023-09-15 | 90.92% | 0.508 [0.493; 0.521] | 0.505 [0.488; 0.522] | 0.505 [0.483; 0.527] | 0.021 [-0.009; 0.051] | 0.024 [-0.011; 0.060] | 0.027 [-0.018; 0.070] |
| **F — flow** | 2009-07-03 / 2023-09-15 | 59.20% | 0.487 [0.471; 0.505] | 0.486 [0.466; 0.507] | 0.483 [0.458; 0.507] | -0.028 [-0.057; 0.001] | -0.040 [-0.073; -0.007] | -0.035 [-0.075; 0.006] |
| **C — celula v3 (cot_cell)** | 2009-07-03 / 2023-09-15 | 85.65% | 0.507 [0.492; 0.522] | 0.505 [0.487; 0.522] | 0.504 [0.480; 0.525] | 0.017 [-0.014; 0.047] | 0.012 [-0.025; 0.046] | 0.013 [-0.034; 0.058] |

Pe jumătăți (jum. 1 / jum. 2):

| Semnal | BA 1s | BA 2s | BA 4s | IC 1s | IC 2s | IC 4s |
|---|---|---|---|---|---|---|
| L | 0.500 / 0.514 | 0.492 / 0.519 | 0.493 / 0.517 | 0.018 / 0.025 | 0.007 / 0.042 | -0.010 / 0.065 |
| F | 0.483 / 0.491 | 0.480 / 0.494 | 0.483 / 0.482 | -0.036 / -0.021 | -0.048 / -0.032 | -0.021 / -0.050 |
| C | 0.500 / 0.514 | 0.491 / 0.518 | 0.490 / 0.518 | 0.003 / 0.030 | -0.013 / 0.036 | -0.025 / 0.052 |

**Decizia (pre-înregistrată):**

1. BA la 2 săptămâni, L − C = 0.0006 [-0.0061; 0.0070], p unilateral 0.446 → nu
2. L − C în ambele jumătăți: 0.0003 / 0.0003 → **da**
3. IC-ul lui F la 2 săptămâni = -0.040 ≤ 0 → **da**

**Rezultat: nu toate trei sunt adevărate → celula COT rămâne cum e.**

## 2. H2 — Indici: ratele simetrice

Fereastra: 2014-01-03 → 2023-09-15 (507 de vineri). Datele sunt pooled pe cei 6 indici (3042 indice × săptămâni cu R și randament). R este blocul de rate v3, adică media semnalelor −Δ63/σ_y pentru 2Y din valuta de bază și pentru DFII10. Pragul e p55 din |R| per indice, pe toată fereastra. Ținta e randamentul log la 2 săptămâni (Yahoo). CI 95% vine din bootstrap pe blocuri de 4 săptămâni.

| Grup | Toată fereastra: medie [CI] | N | Jum. 1 (2014-01 → 2018-10) | N | Jum. 2 (2018-11 → 2023-09) | N |
|---|---|---:|---|---:|---|---:|
| După apeluri bearish (R ≤ −p55) | -0.14% [-0.78%; 0.50%] | 769 | 0.13% | 279 | -0.30% | 490 |
| După apeluri bullish (R ≥ +p55) | 0.47% [-0.89%; 1.53%] | 599 | 0.65% | 163 | 0.40% | 436 |
| Restul săptămânilor (nici bearish, nici bullish) | 0.41% [0.08%; 0.73%] | 1674 | 0.25% | 1070 | 0.70% | 604 |
| Δ bearish − toate celelalte | -0.57% [-1.30%; 0.17%], p unilateral 0.068 | | -0.17% | | -0.87% | |
| Δ bullish − toate celelalte | 0.23% [-1.14%; 1.35%], p unilateral (> 0) 0.338 | | 0.43% | | 0.15% | |

Per indice (informativ): SP500 Δ_bear -0.73% (133 apeluri bearish, 95 bullish); NASDAQ Δ_bear -1.04% (133 apeluri bearish, 95 bullish); DJIA Δ_bear -0.56% (133 apeluri bearish, 95 bullish); DAX Δ_bear -0.33% (119 apeluri bearish, 109 bullish); NIKKEI Δ_bear -0.72% (120 apeluri bearish, 108 bullish); FTSE100 Δ_bear -0.03% (131 apeluri bearish, 97 bullish).

**Decizia (pre-înregistrată):**

1. Randamentul după apelurile bearish e mai mic decât în celelalte săptămâni, cu p < 0,05: Δ -0.57%, p 0.068 → nu
2. Același semn (negativ) în ambele jumătăți: -0.17% / -0.87% → **da**

**Rezultat: nu ambele sunt adevărate → regula asimetrică rămâne.**

## 3. Datele

### Istoricul CFTC (jun7-fc8e, descărcat pe 2026-10-16)

| Valută | Cod | Primul raport | Ultimul raport | Rapoarte | Goluri > 8 zile (toate) | Goluri în H1 sau în cei 3 ani dinainte |
|---|---|---|---|---:|---|---|
| EUR | 099741 | 1999-01-05 | 2026-10-06 | 1449 | 2001-12-28→2002-01-08 (11z) | — |
| GBP | 096742 | 1995-03-21 | 2026-10-06 | 1647 | 2001-12-28→2002-01-08 (11z) | — |
| JPY | 097741 | 1995-03-21 | 2026-10-06 | 1647 | 2001-12-28→2002-01-08 (11z) | — |
| AUD | 232741 | 1995-03-21 | 2026-10-06 | 1621 | 2000-06-13→2000-08-29 (77z); 2000-12-12→2001-01-02 (21z); 2001-06-12→2001-07-03 (21z); 2001-12-11→2002-01-29 (49z); 2002-02-12→2002-02-26 (14z); 2002-02-26→2002-03-12 (14z); 2002-07-23→2002-08-06 (14z); 2002-09-10→2002-10-01 (21z); 2004-09-07→2004-09-21 (14z) | — |
| NZD | 112741 | 1999-01-05 | 2026-10-06 | 1157 | 1999-02-23→1999-03-09 (14z); 1999-03-09→1999-04-20 (42z); 1999-09-07→1999-10-19 (42z); 2000-03-07→2000-04-04 (28z); 2000-04-04→2003-11-11 (1316z); 2003-11-25→2004-08-17 (266z); 2004-08-17→2004-10-19 (63z); 2004-12-07→2005-03-08 (91z); 2005-03-08→2005-03-29 (21z); 2005-04-05→2005-08-16 (133z); 2005-08-16→2005-08-30 (14z); 2005-09-13→2005-10-11 (28z); 2005-10-18→2005-12-06 (49z); 2005-12-13→2005-12-27 (14z); 2006-06-13→2006-07-11 (28z) | 2006-06-13→2006-07-11 (28z) |
| CAD | 090741 | 1995-03-21 | 2026-10-06 | 1647 | 2001-12-28→2002-01-08 (11z) | — |
| CHF | 092741 | 1995-03-21 | 2026-10-06 | 1645 | 2001-12-28→2002-01-08 (11z); 2004-09-07→2004-09-28 (21z) | — |
| USD (DXY) | 098662 | 1995-03-21 | 2026-10-06 | 1584 | 1995-04-04→1995-04-18 (14z); 2000-05-30→2000-07-18 (49z); 2000-07-18→2000-08-01 (14z); 2000-09-05→2000-09-19 (14z); 2000-09-19→2000-10-17 (28z); 2000-10-31→2000-12-19 (49z); 2001-02-13→2001-03-27 (42z); 2001-03-27→2001-05-22 (56z); 2001-07-31→2001-08-21 (21z); 2001-08-28→2002-01-29 (154z); 2002-03-12→2002-04-23 (42z); 2002-07-23→2002-08-06 (14z); 2002-08-13→2002-08-27 (14z); 2003-02-14→2003-02-25 (11z); 2003-04-01→2003-04-22 (21z); 2003-08-05→2003-08-19 (14z) | — |

- Începutul H1 cere 156 de rapoarte consecutive, fără goluri, la toate cele 8 contracte. NZD îl fixează: al 156-lea raport după golul 2006-06-13 → 2006-07-11 vine pe 2009-06-30.
- Concordanța cu `data/history.parquet`: rândurile brute sunt identice pe cele 1480 de rapoarte × contracte comune, cu 0 diferențe. L, F și C au fost comparate pe 1512 de celule (valută × vineri × semnal) din 2023-03 încoace. Celulele sunt cele pentru care `history.parquet` are fereastra trailing completă: 156 de rapoarte pentru L și C, adică din 2026-03, și 57 pentru F. Diferențe: 0. Înainte de 2026-03, `history.parquet` nu are 3 ani de istoric, deci L și C diferă legitim (bază „6m_only”). Testul este în `tests/eval/test_oos_cot_rates.py`.
- 2Y EUR/GBP înainte de `data/rates.parquet`:
  - EUR din ECB YC, de la 2013-01-02, cu diferența max. 4e-16 pp pe 1.294 de zile comune;
  - GBP din BoE GLC, de la 2013-01-02, cu diferența max. 9e-16 pp pe 2.715 zile comune;
  - cache în `~/.cache/macro-tournament/yields`, verificat de `scripts/measure/oos/yields_ext.py`.
- Prima vineri cu R, per indice: SP500 2014-01-03, NASDAQ 2014-01-03, DJIA 2014-01-03, DAX 2014-01-03, NIKKEI 2014-01-03, FTSE100 2014-01-03.
- Prețurile vin din cache-ul turneului: FRED H.10 pentru FX și Yahoo pentru indici. Nu s-au folosit `price_history.parquet` și MT5.
- Pre-înregistrarea: `docs/eval/oos-cot-rates-prereg.md` (commit 2094be9e), făcută înainte de orice calcul pe randamente.
