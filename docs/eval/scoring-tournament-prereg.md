# Turneu de scoring — pre-înregistrare

Data: 2026-10-16. Branch: `eval/scoring-tournament`, din `main` 77a71a3d.

Documentul e scris și pus în commit **înainte de orice metrică pe randamente**. La acest commit nu există încă codul de metrici. Există doar auditul de date (`docs/eval/scoring-tournament-data-audit.md`), reconstrucția săptămânală și scorurile. Niciun parametru de mai jos nu se schimbă după ce se văd rezultatele.

Secțiunile A–C reproduc textul cerut, așa cum a fost dat. Secțiunea D fixează sursele de preț, în forma din schimbarea de sursă de dinaintea pre-înregistrării. Secțiunea E fixează detaliile operaționale pe care textul nu le precizează. Toate au fost alese înainte de rezultate.

---

## A. Variante (parametri ficși) — textul cerut

A1 v3 — scorul de producție, reconstruit.
A2 v2 — scorul shadow (score_v2).
B1 Clonă EdgeFinder, după regulile publicate de A1 Trading:
  - Macro: pentru indicatorii EdgeFinder existenți în data/economic_indicators.yaml (GDP, PMI manufacturing și services, retail sales, consumer confidence, employment change, unemployment rate, jobless claims, JOLTS, ADP, CPI y/y, PPI y/y, PCE y/y), ultima publicare ne-stale: +1 dacă bate consensul în direcția indicatorului, −1 dacă îl ratează, 0 la egalitate. Suma pe valută; la indici și metale, cu semnele de categorie din config.
  - Rate FX: istoricul prognozelor băncilor centrale lipsește, așa că folosești 2Y − rata de politică: peste +0,10 pp → +1, sub −0,10 pp → −1, altfel 0.
  - Rate indici și metale: 2Y (US pentru SP500, NASDAQ, DJIA, GOLD, SILVER; EUR pentru DAX; GBP pentru FTSE100; JPY pentru NIKKEI) peste media pe 7 zile → −1, sub → +1.
  - COT: semnul variației săptămânale a % long al non-comercialilor, long / (long + short), pe contractul valutei: +1/−1 (USD prin DXY). La indici și metale se adaugă +1/−1 după semnul poziției nete.
  - Sezonalitate: randamentul mediu al lunii calendaristice a intrării în ultimii 10 ani încheiați (doar prețuri ≤ t). Semnul: ±1 la FX, ±2 la indici și metale.
  - Put/call, doar la indicii US: percentila pe 1 an a P/C equity ≥ 90 → +2, ≤ 10 → −1.
  - Retail: intră doar dacă data/retail_history.parquet acoperă ≥ 80% din fereastra comună. ≥ 60% long → −1, ≤ 40% long → +1.
  - Trendul tehnic EdgeFinder nu are regulă publicată, deci nu se include.
  - Total = suma componentelor.
B2 Macro v3 — doar blocul macro din v3.
B3 Indice de surprize tip Citi:
  - toate publicările din ultimele 90 de zile;
  - surpriza = direcție × (actual − consens) / σ ultimelor 12 surprize ale indicatorului, ca în compute_indicator_score;
  - medie ponderată cu decay exponențial, timp de înjumătățire 30 de zile;
  - pe valută; la indici și metale, cu semnele din config.
C1 Trend macro (stil Macrosynergy), pe valută:
  - inflație: ultimul CPI y/y minus ținta (USD 2, EUR 2, GBP 2, JPY 2, CAD 2, NZD 2, AUD 2,5, CHF 1);
  - creștere: media PMI manufacturing și services minus 50 (cel disponibil, dacă e doar unul);
  - muncă: minus variația ratei șomajului pe 6 luni;
  - standardizare: la FX cross-secțional pe cele 8 valute la t; la indici și metale z pe fereastră expandabilă, minimum 26 de săptămâni;
  - scor = media factorilor prezenți; la indici și metale, cu semnele din config.
C2 Macro momentum (stil AQR): variația pe 12 luni a mediei PMI, a CPI y/y și a 2Y.
  - FX: toate cu semn + pentru valută, standardizate cross-secțional.
  - Indici: creștere +, inflație −, 2Y −.
  - Metale: creștere și inflație cu semnele din config, 2Y −.
D1 Carry — blocul carry din v3, doar FX.
D2 Schimbarea ratelor așteptate:
  - FX: variația pe 21 de zile a 2Y, cu capete mediate pe 5 observații ca în src/rate_compute.py, minus media celor 8;
  - indici: minus variația pe 63 de zile a 2Y din valuta de bază;
  - metale: minus variația pe 63 de zile a DFII10.
E1 COT level — doar componenta level din src/cot_score.py.
E2 COT flow — doar componenta flow din src/cot_score.py.
E3 Presiune speculativă (Fan et al. 2020): (net − media netului pe 52 de săptămâni) / abaterea standard pe 52 de săptămâni, cu semn +.
  - Contracte: cele din data/contracts.yaml.
  - Pentru indicii fără contract, descarcă din același dataset CFTC folosit de repo E-mini S&P 500, Nasdaq-100, DJIA și Nikkei 225, doar pentru evaluare, în cache în afara repo-ului. Aplică src.compute.compute_metrics și src/cot_score.py și pentru E1/E2.
  - DAX și FTSE100 → n/a.
F1 Trend 12 luni: randamentul pe 252 de zile de tranzacționare (etalon de preț, nu candidat macro).
F2 Trend 3 luni: randamentul pe 63 de zile (etalon).
F3 Sezonalitate: doar componenta de sezonalitate din B1 (etalon).
G1 Compus „conceptual parity”: media scorurilor standardizate C1, D2, E3, F1, cele prezente.
G2 Același compus, fără F1.

## B. Randamente țintă și metrici — textul cerut

PASUL 3 — RANDAMENTE ȚINTĂ
- Intrare la prima închidere de luni după vineri, sau în prima zi de tranzacționare de după. Ieșire la prima închidere la sau după intrare + 7·h zile, h ∈ {1, 2, 4}, ca în scripts/measure/v3_metals.py.
- FX pe valută: randamentul logaritmic față de USD, demeaned pe cele 8, ca în v3_scales.currency_ic. FX pe pereche: randamentul perechii.
- Indici și metale: randamentul logaritmic al instrumentului.

PASUL 4 — METRICI (pe variantă, clasă FX/indici/metale și orizont)
1. Acoperire: % din unitate×săptămână cu scor definit și nenul.
2. Hit rate și balanced accuracy pentru semnul scorului. Balanced accuracy = media dintre % corecte pe săptămânile de creștere și % corecte pe cele de scădere.
3. Apeluri puternice: z = scor / RMS pe fereastră. Hit rate peste percentila 55 a |z| (echivalent Bullish/Bearish) și peste percentila 90 (echivalent Very).
4. IC: la FX, media pe săptămâni a Spearman cross-secțional pe cele 8 valute. La indici și metale, Spearman pe serie temporală per instrument, apoi media.
5. Randamentul mediu pe chintile de scor.
6. Coincident vs predictiv: IC cu randamentul ultimelor h săptămâni față de IC cu randamentul următor.
7. Valoare peste trend: coeficientul scorului în regresia randamentului următor (standardizat la volatilitate) pe scor și F1, ambele standardizate.
8. PnL naiv: poziție = semnul scorului, aceeași volatilitate pe unitate (20 de zile), Sharpe anualizat, plus Sharpe deflat cu numărul de variante testate.

Semnificație și comparație:
- Bootstrap pe blocuri de săptămâni: blocuri de 4 la h = 1 și 2, de 8 la h = 4; 2.000 de eșantioane. Săptămâna se reeșantionează întreagă, cu toate unitățile ei. CI 95%.
- Față de v3: bootstrap pereche pentru diferența de balanced accuracy și de IC, cu p corectat Holm pe numărul de variante.
- Robustețe: aceleași metrici pe cele două jumătăți ale ferestrei comune și pe fiecare valută și instrument.
- Diagnostic de semn: pentru B2, B3, C1 și C2, IC-ul fiecărei categorii (growth, inflation, labour) separat, cu semnul folosit.

## C. Regula de decizie (pre-înregistrată) — textul cerut

Metrica principală este balanced accuracy la 2 săptămâni, pe clasă. O variantă e mai precisă decât v3 într-o clasă doar dacă îndeplinește toate trei condițiile:
1. diferența de balanced accuracy la 2 săptămâni e > 0, cu p Holm < 0,05;
2. diferența e pozitivă în ambele jumătăți ale ferestrei;
3. IC-ul ei la 2 săptămâni nu e mai mic decât al lui v3.
Dacă nicio variantă nu le îndeplinește pe toate, raportul spune explicit asta, iar clasamentul rămâne descriptiv.

Fereastra lungă (Pasul 6): pentru variantele care nu folosesc calendarul FF (D2, E1, E2, E3, F1, F2, F3 și D1, dacă istoricul ratelor de politică ajunge) se repetă metricile 2 și 4 și comparația între ele pe toată perioada disponibilă, țintind de la 2014. v3 nu există aici; se raportează separat.

## D. Sursele de preț (fixate)

| Clasă | Unitate | Sursă | Serie / ticker | Coloană |
|---|---|---|---|---|
| FX | EUR, GBP, AUD, NZD | FRED H.10 (`factor_scales.FRED_FX`, `factor_scales._fred`) | DEXUSEU, DEXUSUK, DEXUSAL, DEXUSNZ (USD per unitate) | valoarea zilnică |
| FX | JPY, CAD, CHF | FRED H.10 | DEXJPUS, DEXCAUS, DEXSZUS (unități per USD, inversate) | valoarea zilnică |
| Indici | SP500 | Yahoo Finance prin yfinance | `^GSPC` | Close |
| Indici | NASDAQ | Yahoo Finance | `^IXIC` (corespunde FRED NASDAQCOM) | Close |
| Indici | DJIA | Yahoo Finance | `^DJI` | Close |
| Indici | DAX | Yahoo Finance | `^GDAXI` | Close |
| Indici | NIKKEI | Yahoo Finance | `^N225` | Close |
| Indici | FTSE100 | Yahoo Finance | `^FTSE` | Close |
| Metale | GOLD | Stooq, `~/Downloads/xauusd_d.csv`, folosit așa cum este (până la 2026-10-06) | xauusd | Close |
| Metale | SILVER | Stooq, `~/Downloads/xagusd_d.csv`, folosit așa cum este (până la 2026-10-06) | xagusd | Close |

- Nu se folosesc `data/price_history.parquet`, nimic din MT5 și nicio sursă de rezervă.
- yfinance e instalat doar în `.venv` local. Nu e adăugat în `requirements.txt`.
- CSV-urile Yahoo sunt în `~/.cache/macro-tournament/yahoo`, descărcate pe 2026-10-16 ~14:48 UTC. Bara zilei descărcării e eliminată, pentru că poate fi neînchisă.
- Cache-ul FRED e `~/.cache/macro-tournament/fred`, descărcat pe 2026-10-16.
- Verificări făcute înainte de pre-înregistrare (`prices.py check`, raportate în auditul de date):
  - Maparea acoperă exact cele 8 instrumente din `data/crossasset_instruments.yaml`, iar tipul și `home_ccy` corespund.
  - Corelația randamentelor zilnice Yahoo vs FRED pe perioada comună: SP500 0,999995, NASDAQ 0,995495, DJIA 0,999999, NIKKEI 1,000000. Toate sunt peste pragul 0,99.
- Fereastra comună se termină la ultima vineri pentru care toate seriile de preț (FRED, Yahoo, Stooq) au randament la 4 săptămâni: **2026-09-04**. O limitează FRED H.10 (ultima zi 2026-10-09) și Stooq (2026-10-06).

## E. Precizări operaționale (fixate înainte de rezultate)

### E1. Săptămâni, ferestre

- t = vinerile 21:00 UTC (`factor_scales.fridays`), de la 2023-09-22 (`factor_scales.START`, prima vineri a reconstrucției v3) până la 2026-10-09, ultima vineri reconstruită. Evaluarea se oprește la 2026-09-04.
- O variantă FX e „definită” la t dacă are scor nenul (nu None) pentru cel puțin 6 din cele 8 valute.
- Început comun = cea mai târzie primă vineri definită dintre variantele FX care încep în cel mult 13 săptămâni după A1. O variantă care începe la peste 13 săptămâni după A1 se evaluează separat, de la prima ei vineri definită până la 2026-09-04. Comparația cu v3 se face pe aceleași săptămâni, cu v3 restrâns la ele, și intră în aceeași familie Holm.
  - Constatat pe scoruri, fără randamente: toate variantele FX încep pe 2023-09-22, mai puțin E3 (2024-03-22). Fereastra comună este deci **2023-09-22 → 2026-09-04**, iar E3 FX are fereastră separată.
- La indici și metale se folosește aceeași fereastră comună. O variantă care în clasa respectivă începe la peste 13 săptămâni după 2023-09-22 se evaluează separat, după aceeași regulă: C1, C2, G1, G2 (2024-03-15, din cauza z-ului expandabil cu minimum 26 de săptămâni) și E3 (2024-03-22).
- Jumătățile ferestrei: primele ⌈n/2⌉ săptămâni și restul, pe fereastra pe care se evaluează varianta.
- Constantele v3 (σ, RMS, praguri) din `data/*.yaml` au fost măsurate pe 2023-09-22 → 2026-10-02. A1 și A2 sunt deci parțial in-sample pe această fereastră. Se raportează, nu se corectează.

### E2. Unități și ținte

- **Clasa FX (metricile 1, 2, 3, 5, 7, 8 și regula de decizie)**: cele 28 de perechi plus rândul US Dollar, adică 29 de unități, cum apar pe dashboard.
  - Scorul perechii: cel nativ la A1 (2,5 × (S_bază − S_cotată), ca în producție) și la A2 (`score_v2` al perechii). Pentru celelalte variante: S_bază − S_cotată.
  - Ținta perechii: randamentul logaritmic al perechii din cursurile FRED H.10.
  - Rândul US Dollar: la A1 e S_USD cu COT-ul DXY (rândul de producție), la A2 rândul US Dollar v2, la celelalte scorul valutei USD. Ținta lui e randamentul logaritmic al USD demeaned pe cele 8, ca valută.
- **IC FX (metricile 4 și 6) și diagnosticul pe valută**: vederea pe valute (8).
  - Scorul: S_c. La A2 e `score_v2_ccy` = media ±score_v2 a celor 7 perechi ale valutei.
  - Ținta: randamentul logaritmic față de USD, demeaned pe cele 8 (USD = 0), ca `v3_scales.currency_ic`.
  - Contează doar săptămânile cu cel puțin 6 valute cu scor și scoruri neconstante.
- Indici: SP500, NASDAQ, DJIA, DAX, NIKKEI, FTSE100. Metale: GOLD, SILVER.
- Intrare/ieșire ca `v3_metals.fwd`: prima închidere la sau după luni (t + 3 zile), apoi prima închidere la sau după intrare + 7·h zile. FX folosește zilele FRED ale fiecărei serii. La o pereche între două valute non-USD, contează zilele comune ale celor două serii.
- Randamentul trecut (metrica 6) = log(ultima închidere ≤ data vinerii / ultima închidere ≤ data vinerii − 7·h zile).

### E3. Metrici

1. **Acoperire**: % din unitate × săptămână din fereastră cu scor definit și ≠ 0.
2. **Hit rate / BA**: pe unitate × săptămână cu scor ≠ 0 și randament ≠ 0 („apel”).
   - Hit rate: semnul scorului = semnul randamentului.
   - BA = ½ × (% apeluri pozitive printre săptămânile cu randament > 0 + % apeluri negative printre cele cu randament < 0). Scorul 0 înseamnă „fără apel”: nu intră în BA și apare doar în acoperire.
3. **Apeluri puternice**: z = scor / RMS(unitate), cu RMS pe săptămânile ferestrei în care scorul e definit. Pragurile sunt p55 și p90 ale |z| din clasă, pe toate unitate × săptămânile cu scor definit, inclusiv zero. Se raportează hit rate-ul pe apelurile cu |z| ≥ prag și scor ≠ 0.
4. **IC**:
   - FX: Spearman cross-secțional pe valute în fiecare săptămână, apoi media pe săptămâni.
   - Indici și metale: Spearman pe serie temporală per instrument, cu minimum 26 de săptămâni cu scor, apoi media pe instrumentele clasei.
5. **Chintile**: `pd.qcut(scor, 5, duplicates="drop")` pe clasa întreagă, cu randamentul mediu la h = 2 pe fiecare bin. Scorurile discrete pot avea mai puțin de 5 bin-uri.
6. **Coincident vs predictiv**: IC (definit ca la 4) cu randamentul trecut pe h săptămâni, față de IC cu randamentul următor.
7. **Valoare peste trend**: pe clasă, pe unitate × săptămânile cu scor și F1 definite (F1 pe pereche = F1_b − F1_q).
   - y = randamentul următor pe h / (σ20 × √(5h)). σ20 = abaterea standard a ultimelor 20 de randamente zilnice ≤ t ale unității.
   - Scorul și F1 sunt standardizate (z) pe clasa întreagă.
   - OLS cu intercept. Se raportează β_scor, cu CI din același bootstrap pe blocuri.
8. **PnL naiv**: în fiecare săptămână, media pe unitățile cu scor ≠ 0 a sign(scor) × r_h / σ20 (risc egal pe unitate). O săptămână fără nicio poziție are PnL 0.
   - Sharpe anualizat = medie / abatere × √(52/h), pe randamentele h-săptămânale eșantionate săptămânal (suprapuse pentru h > 1).
   - Sharpe deflat (Bailey & López de Prado, 2014):
     - N = numărul de variante evaluate în clasă: 17 la FX, 16 la indici și metale;
     - varianța SR = varianța între variante a SR-urilor pe perioadă, pentru clasa și orizontul respectiv;
     - T = numărul de săptămâni; asimetria și kurtosisul vin din PnL-ul variantei.
     - Se raportează probabilitatea DSR.

- **Bootstrap**: bootstrap circular pe blocuri de săptămâni consecutive din fereastra evaluată, cu blocuri de 4 (h = 1, 2) și de 8 (h = 4). Sunt 2.000 de eșantioane, cu seed 20261016. Fiecare săptămână e luată întreagă, cu toate unitățile ei. CI 95% = percentilele 2,5 și 97,5.
  - IC FX: media IC-urilor săptămânale reeșantionate.
  - IC indici/metale: Spearman per instrument pe săptămânile reeșantionate, apoi media.
- **Comparația cu v3**: aceleași eșantioane pentru variantă și v3, Δ = metrică(variantă) − metrică(v3).
  - p unilateral (H1: Δ > 0) = (1 + #{Δ* ≤ 0}) / (2000 + 1).
  - Holm se aplică pe familia variantelor comparate cu v3 în clasa respectivă (16 la FX, 15 la indici, 15 la metale), separat pentru BA la 2 săptămâni și pentru IC la 2 săptămâni.
- **Regula de decizie**:
  - Condiția 1: p Holm al lui Δ BA la 2 săptămâni < 0,05, iar Δ estimat > 0.
  - Condiția 2: Δ BA estimat > 0 în fiecare jumătate.
  - Condiția 3: IC la 2 săptămâni estimat ≥ IC-ul v3, pe aceleași săptămâni.
  - Clasele sunt FX (29 de unități), indici și metale.
- **Robustețe pe unitate**:
  - BA la 2 săptămâni pe fiecare pereche și instrument;
  - BA și IC pe serie temporală pentru fiecare valută (vederea pe valute);
  - IC pe serie temporală pentru fiecare instrument.
- **Diagnosticul de semn**: IC la 2 săptămâni (definit ca la metrica 4) pe fiecare categorie, cu semnul folosit în variantă:
  - B2, B3, C1: growth, inflation, labour;
  - C2: growth, inflation, 2Y.

### E4. Detaliile variantelor

- **A1** = `v3_scales.v3_week` / `currency_scores` pe înregistrările `state_at`, cu constantele din config (`v3_scales.config_constants`).
  - Reconstrucția extinde `v3_scales.collect` până la ultima vineri: același `fs.Inputs` cu as_of din `public/data/economic.json` și aceeași înregistrare.
  - Testul cere ca diferența față de reconstrucția v3_scales să fie < 1e-9.
- **A2** = `score_v2` / `bias_v2` din aceeași reconstrucție. Indici și metale: scorul v2 cross (`v2_cross`).
- **B1**:
  - Macro:
    - Indicatorii existenți în config sunt gdp_qoq, manufacturing_pmi, services_pmi, retail_sales, employment_change, unemployment_rate, jobless_claims, jolts, adp, cpi_yoy și ppi_yoy.
    - Consumer confidence și PCE y/y nu există în config, deci nu intră. `core_pce` este „Core PCE Price Index m/m”, nu PCE y/y.
    - Pentru fiecare se ia intrarea din breakdown-ul de producție la t: ultima publicare, fără stale, fără flag no_consensus/direction_mismatch. Valoarea e sign(direcție efectivă × (actual − consens)).
    - La indici și metale, fiecare termen se înmulțește cu semnul categoriei lui din config.
  - Rate FX:
    - 2Y = ultima observație ≤ data lui t, fără stale după `max_age_for(sursă)`.
    - Rata de politică = `rate_at(policy_rate_history, t)`.
    - NZD și CHF nu au 2Y, deci componenta lor e absentă.
  - Rate indici și metale: ultima valoare 2Y comparată cu media ultimelor 7 observații, inclusiv ea. Egalitate → 0.
  - COT:
    - Δ `spec_exp_long` între ultimele două rapoarte ≤ t; egalitate → 0.
    - Indici/metale: plus sign(`spec_net`).
    - Contractul pentru DJIA e DOW (124603), iar NIKKEI e 240743. DAX și FTSE100 nu au contract, deci componenta e absentă.
  - Sezonalitate:
    - Luna intrării = luna zilei de luni de după t.
    - Se folosesc cei 10 ani calendaristici anteriori anului lui t. Randamentul lunar = log(ultima închidere a lunii / ultima închidere a lunii precedente). Sunt necesari toți cei 10 ani.
    - La FX, prețul e cel al valutei față de coșul egal ponderat al celorlalte 7 (log, din FRED H.10, pe zilele în care există toate cele 7 serii).
  - Put/call:
    - Doar pentru SP500, NASDAQ și DJIA.
    - Percentila (rank average × 100) a ultimei valori `equity` ≤ t, între ultimele 252 de observații.
    - ≥ 90 → +2, ≤ 10 → −1, altfel 0.
  - Retail: `data/retail_history.parquet` începe pe 2026-04-29 (auditul de date), sub 80% din fereastra comună. Componenta retail **nu intră**.
  - Total = suma componentelor prezente. Fără nicio componentă → nedefinit.
- **B2**: blocul macro v3 (`macro_mean / σ_macro`; la indici și metale `v3_macro_raw / σ_Macro[instrument]`). Categoriile pentru diagnostic: `score_precise / σ(valută, categorie)`, cu semnul categoriei la indici și metale.
- **B3**:
  - Intră toți indicatorii din config cu categoria growth/inflation/labour (fără `*_display`), din cadrul de scoring, cu release_dt în (t − 90 z, t].
  - Se face colapsul flash/final ca în `compute_indicator_score`.
  - σ = abaterea standard (ddof = 1) a ultimelor 12 diferențe (actual − consens) până la publicarea respectivă, inclusiv ea. Sunt necesare ≥ 6 perechi și σ > 0, altfel publicarea nu intră (fără fallback procentual).
  - Ponderea = 0,5^(vârstă în zile / 30). Indicele e Σ w·s / Σ w; fără clip.
  - La indici și metale, fiecare surpriză se înmulțește cu semnul categoriei.
- **C1, C2**:
  - Valoarea unui indicator la t = actual-ul ultimei publicări fără stale, după `compute_indicator_score`: colaps flash/final, fereastră de recență, regula superseded.
  - „Acum 6 luni” = aceeași valoare la t − 182 z. „Acum 12 luni” = aceeași valoare la t − 365 z.
  - CPI y/y = indicatorul `cpi_yoy`, numai dacă toate name_raw-urile seriei se termină în „y/y”.
    - CAD („CPI m/m”), CHF („CPI m/m”) și NZD („CPI q/q”) au factorul de inflație n/a, fără substitut.
    - JPY folosește „National Core CPI y/y”, cum e mapat în producție.
  - PMI vine din cadrul de scoring: EUR și AUD au PMI doar din 2026-01, JPY din 2025-12. Releasele „Final” EUR și JPY din 2023–2025 sunt telemetry și sunt excluse de `scoring_view`.
  - 2Y la 12 luni = ultima observație ≤ data − 365 z, cu verificarea de prospețime la t.
  - z cross-secțional: ddof = 1, minimum 3 valute prezente, altfel factorul e nedefinit la t.
  - z expandabil: valorile factorului la toate vinerile de la 2023-09-22 până la t, ddof = 1, minimum 26 de valori.
  - C2 la indici: 2Y-ul valutei de bază (USD, EUR, GBP, JPY). La metale: 2Y US.
- **D1**: blocul carry v3 = (rata de politică − media celor 8) / σ_carry.
- **D2**:
  - FX: `v3_yield_change(W = 21, k = 5)` pe 2Y-ul fiecărei valute (USD, EUR, GBP, JPY, AUD, CAD; NZD și CHF nu au 2Y), fără stale, minus media valutelor prezente.
  - Indici: −`v3_yield_change(W = 63, k = 5)` pe 2Y-ul valutei de bază. Metale: același calcul pe DFII10.
- **E1/E2/E3**:
  - Se aplică `src.compute.compute_metrics` pe `data/history.parquet` plus Nikkei 240743 (din același dataset CFTC, de la aceeași primă dată, 2023-03-28).
  - E1 = `level_score(ext_6m, ext_3y)` al ultimului raport, numai dacă ext_6m există.
  - E2 = `flow_score`, nedefinit dacă basis ≠ ok.
  - E3 = z pe ultimele 52 de rapoarte (ddof = 1). Sunt necesare 52.
  - USD folosește DXY. Contractele: SP500 13874A, NASDAQ 209742, DJIA 124603 (DOW), NIKKEI 240743. DAX și FTSE100 sunt n/a.
- **F1/F2**: log P(ultima ≤ t) − log P(cu 252, respectiv 63 de observații înainte). La FX se folosește coșul de mai sus.
- **F3** = componenta de sezonalitate din B1.
- **G1/G2**: FX, z cross-secțional pe fiecare componentă, apoi media celor prezente. La indici și metale, z expandabil pe fiecare componentă, minimum 26, apoi media celor prezente.
- **Momentul rapoartelor COT**: raportele cu data de raport (marți) ≤ t, ca în producție și `factor_scales.state_at`. Publicarea de vineri 15:30 ET precede 21:00 UTC. În cele ~5 săptămâni pe an cu publicare amânată de o sărbătoare, raportul e folosit cu cel mult 3 zile înainte de publicare. Efectul e moștenit și e la fel pentru toate variantele.

### E5. Fereastra lungă (Pasul 6)

- Săptămâni de la 2014-01-03 până la 2026-09-04. Variante: D2, E1, E2, E3, F1, F2, F3.
- **D1 este n/a**: `policy_rate_history` începe în 2023-01, pentru că deciziile din FF și `data/cb/decisions.parquet` nu merg mai înapoi.
- E1, E2 și E3 nu pot ajunge înainte de 2023, pentru că istoricul COT din repo începe pe 2023-03-28. Se raportează pe ce există.
- În fereastra lungă, o săptămână FX contează pentru IC dacă are cel puțin 4 valute cu scor.
- Se calculează metricile 2 (hit rate, BA) și 4 (IC) la h = 1, 2, 4, cu CI bootstrap.
- Comparația între variante e un bootstrap pereche al Δ BA la 2 săptămâni față de F1 (etalonul de trend), cu Holm. E descriptivă: în fereastra lungă nu există regulă de decizie și nici v3.
