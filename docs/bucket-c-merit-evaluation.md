# Evaluarea celor 33 de candidați din găleata C — merit, nu doar utilizabilitate

Status: **FAZA 1 încheiată — investigație, zero cod, zero config.** Branch
`eval/bucket-c-candidates`, worktree `../macro-dev`.

## Verdict, pe scurt

**17 adaugă, 14 întreabă, 2 respinge.** Nu e o distribuție întâmplătoare:

- **2 respingeri sunt defecte concrete, nu opinii.** `USD Prelim UoM
  Inflation Expectations` are `Forecast = 0.0` în TOATE cele 42 apariții —
  confirmat că nu-i consensus real, ci un placeholder constant. E chiar
  marcat `real_signal: False` în `docs/unmapped-coverage-audit.csv` (rândul
  235) — **a fost inclus greșit în găleata C de sesiunea anterioară**,
  contrazicând propriul criteriu de filtrare. `GBP MPC Official Bank Rate
  Votes` are 6/15 valori zero pe actual ȘI forecast (40%) și un gol de 364
  de zile — calitate insuficientă indiferent de arhitectură.
- **Cei 7 candidați "monetary" nu au unde să meargă azi.**
  `data/economic_indicators.yaml`'s `categories:` are DOAR
  `growth/inflation/labour` — verificat direct în fișier, nu presupus. Ce
  se afișează ca "monetary" în `public/data/economic.json` vine dintr-un
  motor separat (`data/rates.parquet`, rate expectations), NU din
  `per_cat`. Adăugarea acestor 7 cu `category: monetary` i-ar face inerți —
  calculați în `breakdown` dar NICIODATĂ agregați într-un scor, exact ca
  `rates`/`inflation_display` azi. E aceeași clasă de decizie ca housing-ul
  (deja parcat în găleata D) — arhitectură, nu adăugare de indicator. Toți
  7 sunt "întreabă", nu "respinge": meritul lor individual (mai jos) rămâne
  valabil DACĂ se decide vreodată un pilon monetary scorat.
- **Direcția e neclară, pe bune, pentru 5+3 candidați** — nu doar "greu de
  ghicit": Trade Balance (5 valute) și productivitatea muncii (2 candidați)
  au interpretări economice concurente documentate (mai jos), nu doar
  incertitudine mea.
- **O suprapunere e chiar la limită**: `GBP RPI y/y` vs `cpi_yoy` existent,
  corelație **0.707** — peste pragul de 0.7 din criteriile FAZA 2, deci
  ar eșua acolo dacă ar ajunge acolo; marcat "întreabă" aici ca avertisment
  timpuriu.

## Metodă

- **Relevanța de piață**: `Impact` din `data/archive/ff_calendar_range.json`
  (proxy obiectiv al FF — Low/Medium/High pentru toate aparițiile
  candidatului sunt identice, deci un singur tag per candidat). 11/33 au un
  tag real (1 High, 1 Medium, 9 Low); restul de 22 sunt `None`. **Asta e un
  rezultat, nu doar context**: majoritatea candidaților statistic-calificați
  nu sunt considerați de FF drept market-moving.
- **Independența informațională**: corelația Pearson lunară
  (surpriză = actual−forecast, agregat pe lună calendaristică, ultima
  apariție a lunii) între candidat și fiecare indicator existent scorat
  din aceeași (valută, categorie), pe istoricul comun. Detaliu complet:
  `docs/bucket-c-correlation.csv`.
- **Calitatea seriei**: din arhivă — goluri mari (>1.8× intervalul așteptat
  al cadenței), apariții cu `actual`/`forecast` exact 0.0, outlieri
  (>6×MAD de mediana proprie). Detaliu complet: `docs/bucket-c-quality.csv`.
- **Categoria + direcția**: evaluare manuală, marcată explicit "clar" sau
  "întrebare" — nicio presupunere ascunsă ca fapt.

## Tabel — ordonat, cel mai puternic candidat primul

| # | Categorie | Valută | Nume brut | Impact FF | Corelație max (cu) | Calitate | Direcție | Recomandare |
|---|---|---|---|---|---|---|---|---|
| 1 | inflation | JPY | Tokyo Core CPI y/y | **High** | 0.229 (cpi_yoy) | curat, 0 goluri mari, 0 outlieri | CPI↑ = bullish, clar | **ADAUGĂ** |
| 2 | monetary | JPY | Monetary Base y/y | Medium | — (nimic scorat) | curat | QE↑ = de obicei bearish JPY, dar regim-dependent | **ÎNTREABĂ** — arhitectură pilon nou |
| 3 | growth | GBP | Industrial Production m/m | Low | 0.531 (gdp_qoq) — real, dar sub prag | curat | IP↑ = bullish, clar | **ADAUGĂ** (notă: suprapunere structurală cu GDP, IP e componentă) |
| 4 | growth | USD | Industrial Production m/m | Low | 0.143 (services_pmi) | 7/41 forecast=0.0 (17%), posibil legitim (consens rotunjit la 0%) | IP↑ = bullish, clar | **ADAUGĂ** |
| 5 | growth | USD | Durable Goods Orders m/m | Low | -0.306 (gdp_qoq, n=12) | 4/43 actual=0.0, 1 outlier | comenzi↑ = bullish, clar | **ADAUGĂ** |
| 6 | growth | JPY | Core Machinery Orders m/m | Low | 0.416 (manufacturing_pmi, n=34) | curat | comenzi↑ = bullish, clar (indicator avansat pt. capex) | **ADAUGĂ** |
| 7 | monetary | JPY | M2 Money Stock y/y | Low | — | curat | ambiguă (masă monetară↑: creștere sănătoasă SAU debazare) | **ÎNTREABĂ** — arhitectură + direcție |
| 8 | monetary | JPY | Bank Lending y/y | Low | — | **9/42 outlieri (21%)** — posibil trend real, de verificat | ambiguă (credit↑: expansiune SAU risc) | **ÎNTREABĂ** — arhitectură + calitate de verificat |
| 9 | growth | CAD | Trade Balance | Low | 0.220 (retail_sales) | curat | **surplus↑ = bullish teoretic, dar impact empiric slab pe G10** | **ÎNTREABĂ** — direcție |
| 10 | growth | JPY | Trade Balance | Low | -0.049 (manufacturing_pmi) | curat | idem | **ÎNTREABĂ** — direcție |
| 11 | growth | NZD | Trade Balance | Low | -0.511 (gdp_qoq, n=13 — mic) | curat | idem | **ÎNTREABĂ** — direcție + eșantion mic pe corelație |
| 12 | growth | USD | Trade Balance | Low | 0.489 (gdp_qoq, n=12 — mic) | 5 outlieri (11%) | idem | **ÎNTREABĂ** — direcție + outlieri de verificat |
| 13 | growth | USD | Personal Spending m/m | None | 0.343 (retail_sales) | curat | cheltuieli↑ = bullish, clar | **ADAUGĂ** |
| 14 | growth | USD | Personal Income m/m | None | -0.461 (gdp_qoq, n=11 — mic) | curat | venit↑ = bullish, clar | **ADAUGĂ** |
| 15 | inflation | USD | Import Prices m/m | None | 0.479 (ppi_yoy) | 5/41 actual=0.0 (12%) | preț import↑ = inflaționist = bullish, clar | **ADAUGĂ** |
| 16 | growth | AUD | Private Capital Expenditure q/q | None | -0.044 (retail_sales, n=12 — mic) | curat | capex↑ = bullish, clar | **ADAUGĂ** (eșantion mic — reverifică la ~24 obs.) |
| 17 | growth | JPY | Capital Spending q/y | None | -0.421 (manufacturing_pmi, n=11 — mic) | curat | capex↑ = bullish, clar | **ADAUGĂ** (eșantion mic) |
| 18 | growth | AUD | Company Operating Profits q/q | None | -0.110 (retail_sales, n=8 — mic) | curat | profit↑ = bullish-ish, clar-ish | **ADAUGĂ** (eșantion mic, cel mai subțire din tot lotul) |
| 19 | inflation | AUD | Import Prices q/q | None | 0.407 (core_cpi, n=10 — mic) | curat | preț import↑ = bullish, clar | **ADAUGĂ** |
| 20 | inflation | JPY | SPPI y/y | None | -0.094 (core_cpi) | curat | PPI servicii↑ = bullish, clar | **ADAUGĂ** |
| 21 | inflation | JPY | Prelim GDP Price Index y/y | None | -0.561 (core_cpi, n=14 — mic) | curat | deflator↑ = bullish, clar | **ADAUGĂ** (variantă flash — vezi nota de vintage) |
| 22 | inflation | USD | Advance GDP Price Index q/q | None | 0.185 (cpi_yoy, n=13 — mic) | curat | deflator↑ = bullish, clar | **ADAUGĂ** (variantă flash) |
| 23 | labour | USD | Prelim Unit Labor Costs q/q | None | 0.507 (adp, n=9 — mic, subputernic) | curat | cost unitar↑ = presiune inflaționistă = bullish, rezonabil clar | **ADAUGĂ** (variantă flash) |
| 24 | growth | JPY | Prelim Industrial Production m/m | None | -0.039 (manufacturing_pmi) | curat | IP↑ = bullish, clar | **ADAUGĂ** (variantă flash) |
| 25 | growth | CHF | Trade Balance | None | 0.233 (retail_sales) | curat | surplus↑ teoretic bullish, empiric slab (ca la celelalte 4) | **ÎNTREABĂ** — direcție |
| 26 | monetary | AUD | Private Sector Credit m/m | None | — | 7/44 actual=0.0 (16%) | credit↑ = bullish-ish, clar-ish | **ÎNTREABĂ** — arhitectură |
| 27 | monetary | GBP | Net Lending to Individuals m/m | None | — | curat | creditare↑ = bullish-ish, clar-ish | **ÎNTREABĂ** — arhitectură |
| 28 | monetary | GBP | M4 Money Supply m/m | None | — | curat | ambiguă (ca M2 JPY) | **ÎNTREABĂ** — arhitectură + direcție |
| 29 | inflation | GBP | RPI y/y | None | **0.707 (cpi_yoy)** — PESTE pragul de 0.7 | curat | RPI↑ = bullish, clar | **ÎNTREABĂ** — suprapunere la limită cu cpi_yoy |
| 30 | labour | CAD | Labor Productivity q/q | None | 0.295 (unemployment_rate, n=12) | curat | ambiguă (productivitate: growth-pozitiv SAU jobs-negativ) | **ÎNTREABĂ** — direcție |
| 31 | labour | USD | Prelim Nonfarm Productivity q/q | None | -0.655 (adp, n=9 — mic, subputernic) | curat | ambiguă (idem) | **ÎNTREABĂ** — direcție |
| 32 | monetary | GBP | MPC Official Bank Rate Votes | None | — | **6/15 actual=0.0 ȘI forecast=0.0 (40%), gol de 364 zile** | vot hawkish↑ = bullish, clar | **RESPINGE** — calitate insuficientă, indiferent de arhitectură |
| 33 | inflation | USD | Prelim UoM Inflation Expectations | None | -0.361 (ppi_yoy) — irelevant, vezi motiv | — | — | **RESPINGE** — `Forecast=0.0` în toate cele 42 apariții; defect de clasificare confirmat (`real_signal: False` în audit) |

## Note care contează mai mult decât tabelul

**Trade Balance (5 valute — CAD/CHF/JPY/NZD/USD) — direcția, exact
exemplul din task.** Teoria manualelor spune surplus mai mare decât
așteptat = cerere valutară din export net = bullish. Practica de piață
pentru perechile G10 majore arată frecvent un impact slab/inconsecvent —
trade balance rar mișcă prețul la fel de fiabil ca CPI sau NFP. Direcția
implicită (+1, surplus=bullish) e defensibilă ca CONVENȚIE, dar tăria
semnalului rămâne o presupunere netestată aici — marcat "întreabă", nu
"respinge", pentru că indicatorul în sine e curat și fără suprapunere.

**Productivitatea muncii (CAD Labor Productivity, USD Prelim Nonfarm
Productivity) — ambiguitate reală, nu incertitudine mea.** Productivitatea
mai mare poate însemna (a) creștere economică sănătoasă, fără presiune
inflaționistă — bullish moderat, SAU (b) nevoie mai mică de angajări noi
pentru aceeași producție — bearish pentru piața muncii. Cele două citiri
nu converg spre același semn. `USD Prelim Unit Labor Costs q/q` (al
treilea candidat labour) NU are aceeași problemă — cost unitar al muncii
în creștere e aproape universal citit ca presiune inflaționistă
(cost-push), deci "adaugă" cu direcție rezonabil clară.

**Eșantioane mici pe corelație (n<15 luni comune)**: AUD Private Capex,
JPY Capital Spending, AUD Company Operating Profits, AUD Import Prices
q/q, JPY Prelim GDP Price Index y/y, USD Advance GDP Price Index q/q, CAD
Trade Balance(gdp_qoq), NZD Trade Balance, USD Trade Balance(gdp_qoq),
USD Personal Income, USD/CAD labour vs adp — toate trimestriale sau cu
istoric scurt de suprapunere. Corelația raportată e cea mai bună estimare
disponibilă, dar cu eșantion mic un 0.4-0.6 nu exclude ferm o suprapunere
mai mare care s-ar vedea abia cu mai multe trimestre. Nu blochează
"adaugă" (nimic nu trece 0.7 nici așa), dar merită reverificat după ~2-3
trimestre suplimentare de istorie.

**Variante flash (Prelim/Advance)**: 5 candidați (`JPY Prelim GDP Price
Index y/y`, `USD Advance GDP Price Index q/q`, `USD Prelim Unit Labor
Costs q/q`, `JPY Prelim Industrial Production m/m`) sunt prima estimare a
unei serii cu revizuiri ulterioare — consistent cu convenția deja folosită
în `config/ff_aliases.yaml` (flash e scorat, Final/Revised e telemetrie).
Nu-i un motiv de respingere, doar o notă că varianta ALEASĂ contează
(varianta greșită ar fi fost deja prinsă ca "redundant" în găleata B).

**Atenție înainte de FAZA 2 — diluarea N, deja declanșată.** Cei 10
candidați "adaugă" din growth includ 4 pentru USD (Durable Goods, Personal
Spending, Personal Income, Industrial Production). USD/growth are azi
N=4. Adăugarea tuturor 4 ar duce N la **8** — exact pragul din criteriul 4
de acceptare FAZA 2 ("dacă growth ajunge la N=8, fiecare contribuie 12%").
Măsurarea asimetriei de acoperire a infirmat ipoteza că N mic distorsionează
sistematic (`docs/measurement-coverage-asymmetry.md`) — dar asta nu spune
nimic despre ce se întâmplă la celălalt capăt (N mare, diluare). FAZA 2
trebuie să testeze explicit acest scenariu cumulat pentru USD/growth, nu
doar adăugări individuale.

## Ce NU s-a făcut (conform scope)

- Nicio adăugare de cod/config — evaluare pură.
- Găleata D (housing inclus), găleata B, găleata A — neatinse.
- Nicio decizie luată pe cele 14 "întreabă" — întrebările sunt explicite
  mai sus, răspunsul rămâne al utilizatorului.

## Livrabile

- `docs/bucket-c-merit-evaluation.md` — acest document.
- `docs/bucket-c-correlation.csv` — corelația completă candidat × fiecare
  indicator existent din aceeași (valută, categorie).
- `docs/bucket-c-quality.csv` — goluri, valori zero, outlieri per candidat.
- `scripts/measure/bucket_c_correlation.py`, `bucket_c_quality.py` —
  instrumentar, investigație.
