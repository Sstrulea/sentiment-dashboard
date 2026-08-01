# Panelul counterfactual — frecvența cu care contează, nu magnitudinea într-o zi

Status: **investigație încheiată.** Branch `eval/bucket-c-candidates`,
worktree `../macro-dev`. Zero scriere în parquet, zero modificări de
config — panelul e calculat integral în memorie.

**Decizie ulterioară (după acest document)**: toți cei 17 aprobați pentru
adăugare — vezi `docs/bucket-c-implementation.md` pentru FAZA 3.

## Metodă — counterfactual, aceeași ca la măsurarea de asimetrie

**Aceeași limitare, marcată explicit ca acolo**: regulile de scoring de
AZI aplicate retroactiv peste calendarul istoric (2023-01-08 → 2026-07-31,
1301 zile de evaluare), NU o redare literală a ce ar fi afișat dashboard-ul
la acea dată (config-ul și datele s-au schimbat de mai multe ori în
istorie — vezi `docs/measurement-coverage-asymmetry.md`). Întrebarea la
care răspunde: **"cât de des ar conta acest indicator sub regulile de
azi"**, nu "ce vedea dashboard-ul atunci" — exact distincția cerută.

Fiecare din cei 17 candidați e măsurat **individual** (adăugat singur,
nu cumulat cu ceilalți), la fiecare din cele 1301 zile, comparând
categoria (valută, categorie) și toate instrumentele cu picior în acea
valută, înainte/după adăugarea candidatului respectiv la acea zi. 21.753
observații de categorie, 154.422 observații de instrument.

## GBP/USD Industrial Production m/m — arhiva înghețată, verificat direct

Ambele au fost măsurate pe toată perioada cât NU erau stale (95.3%,
respectiv 90.8% din cele 1301 zile — ultimul print arhivat susține
prospețimea până pe **2026-07-26** pentru GBP și **2026-07-29** pentru
USD; după aceste date, în panel, devin stale, exact ca la măsurarea
punctuală de dinainte).

**Verificat în `data/jb_raw/`** (pull-urile recente JBlanked, nu arhiva
înghețată): ambele serii publică ÎN CONTINUARE, cu printuri reale mai
noi decât ultimul din arhivă —

```
USD Industrial Production m/m: print 2026-07-17 (actual=0.1, forecast=0.2)
GBP Industrial Production m/m: print 2026-07-16 (actual=-0.5, forecast=-0.1)
```

Ambele apar identic în toate cele 14 fișiere `jb_range_*.json` din
2026-07-19 până în 2026-08-01. **Confirmat: seriile sunt vii în feed-ul
curent — faptul că `data/archive/ff_calendar_range.json` se oprește pe
2026-07-03 e artefact de arhivă, nu proprietate a indicatorilor.** Dacă
ar fi legate azi în pipeline (nu în arhiva înghețată), ar continua să
primească printuri lunare ca oricare alt indicator activ.

## Rezultatul complet, ordonat după FRECVENȚĂ (nu după meritul FAZA 1)

| Frecvență | Candidat | Rang FAZA 1 | Zile active | % zile ≥0.25 (din active) | Zile cu flip bias | % flip (din active) | Δ median | Δ max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **AUD Private Capital Expenditure q/q** | 16 | 1255 | **69.6%** | 867 | 69.1% | 0.500 | 2.000 |
| 2 | AUD Import Prices q/q | 19 | 1280 | 59.3% | 820 | 64.1% | 0.333 | 0.917 |
| 3 | JPY Prelim GDP Price Index y/y | 21 | 1265 | 58.7% | 756 | 59.8% | 0.333 | 1.500 |
| 4 | AUD Company Operating Profits q/q | 18 | 1107 | 57.1% | 617 | 55.7% | 0.333 | 2.000 |
| 5 | JPY Core Machinery Orders m/m | 6 | 1273 | 51.8% | 636 | 50.0% | 0.333 | 2.000 |
| 6 | JPY SPPI y/y | 20 | 1269 | 46.0% | 626 | 49.3% | 0.167 | 1.500 |
| 7 | JPY Prelim Industrial Production m/m | 24 | 1233 | 45.6% | 578 | 46.9% | 0.167 | 2.000 |
| 8 | JPY Capital Spending q/y | 17 | 1176 | 45.4% | 582 | 49.5% | 0.167 | 2.000 |
| 9 | **JPY Tokyo Core CPI y/y (top pick FAZA 1)** | **1** | 1272 | 42.3% | 601 | 47.2% | 0.167 | 1.000 |
| 10 | USD Import Prices m/m | 15 | 1245 | 40.7% | 617 | 49.6% | 0.167 | 1.000 |
| 11 | USD Durable Goods Orders m/m | 5 | 1248 | 38.5% | 609 | 48.8% | 0.150 | 1.000 |
| 12 | USD Advance GDP Price Index q/q | 22 | 1103 | 37.0% | 480 | 43.5% | 0.100 | 1.000 |
| 13 | USD Personal Income m/m | 14 | 1227 | 36.3% | 644 | 52.5% | 0.200 | 0.700 |
| 14 | USD Prelim Unit Labor Costs q/q | 23 | 1232 | 31.8% | 564 | 45.8% | 0.167 | 2.000 |
| 15 | USD Personal Spending m/m | 13 | 1243 | 30.5% | 514 | 41.4% | 0.100 | 0.700 |
| 16 | GBP Industrial Production m/m | 3 | 1240 | 27.8% | 436 | 35.2% | 0.150 | 0.667 |
| 17 | USD Industrial Production m/m | 4 | 1181 | 26.3% | 484 | 41.0% | 0.100 | 0.833 |

Detaliu complet (toate cele 21.753 + 154.422 observații):
`docs/bucket-c-panel-categories.csv`, `docs/bucket-c-panel-instruments.csv`,
sumar per candidat: `docs/bucket-c-panel-summary.csv`.

## Ierarhia SE SCHIMBĂ față de FAZA 1 — nu marginal, substanțial

**Corelația Spearman între rangul de merit FAZA 1 și rangul de frecvență:
ρ = -0.38** (n=17, p=0.13 — nesemnificativ statistic la acest eșantion
mic, dar direcția contează): **ușor NEGATIVĂ**, nu pozitivă. Meritul
FAZA 1 (impact FF, independență, calitate, direcție clară) **nu prezice**
cine mișcă scorurile cel mai des.

- **Cei 4 candidați AUD/JPY clasați 16-21 pe merit (cei mai slabi dintre
  cei "adaugă") ocupă locurile 1-4 la frecvență.** Suspiciunea din
  raportul anterior — "AUD Private Capex arată mult dintr-o coincidență
  de dată" — **e infirmată de panel**: 69.6% din zilele active mișcă
  categoria cu ≥0.25, cel mai constant din tot lotul, nu o coincidență de
  moment.
- **Tokyo Core CPI (singurul impact High, primul pe merit) ajunge #9 din
  17 la frecvență** — nici cel mai slab, nici cel mai puternic. Impactul
  FF marcat de piață (relevanță de tranzacționare) și frecvența cu care
  mișcă efectiv scorul intern **sunt lucruri diferite**, măsurate aici
  separat pentru prima dată.
- **GBP și USD Industrial Production (rang 3 și 4 pe merit) sunt ultimele
  două la frecvență** (27.8%, 26.3%) — dar NU din cauza stale-ului de
  arhivă (măsurate doar pe zilele active, cum s-a cerut); pur și simplu
  mișcă scorul mai rar decât restul lotului, chiar și atunci când
  publică.
- Cei 4 candidați growth USD (rangurile 4,5,13,14 pe merit) sunt TOȚI în
  jumătatea inferioară la frecvență (locurile 11,13,15,17) — consistent
  cu observația din FAZA 2 că adăugarea lor cumulată la USD/growth nu a
  schimbat nimic net.

## Interpretarea corelației negative — ce măsoară de fapt frecvența

**Aprobare primită**: se adaugă toți cei 17, pentru că niciun clasament
disponibil nu justifică o selecție — meritul (FAZA 1) nu prezice frecvența
(ρ = -0.38), iar frecvența nu prezice valoarea de tranzacționare (mai jos).

De ce corelația e negativă, nu doar zero: **frecvența cu care un candidat
mișcă scorul cu ≥0.25 măsoară cât de IMPREVIZIBIL e, nu cât de
INFORMATIV**. `score_precise` se mișcă atunci când `actual` diferă de
`consensus` — un consens SLAB (piața nu are o estimare bună) produce
surprize mari și frecvente, indiferent dacă indicatorul contează pentru
cineva. Un indicator **High-impact** ca Tokyo Core CPI are consens STRÂNS
tocmai pentru că e urmărit intens — mulți analiști îl modelează, deci
`actual` rareori se abate mult de la `consensus`, deci mișcă scorul mai
rar. `AUD Private Capital Expenditure q/q`, cu impact FF `None`, e urmărit
de puțini, consensul e mai slab calibrat, iar surprizele sunt mari și
frecvente — nu pentru că informația contează mai mult, ci pentru că
nimeni nu a "prețuit-o" bine în avans.

**Deci: frecvența e proxy pentru predictibilitatea consensului, nu pentru
valoarea indicatorului.** Cele două măsurători din această evaluare —
meritul (FAZA 1, folosind impactul FF ca proxy de relevanță) și frecvența
(acest document, folosind mișcarea scorului ca proxy de imprevizibilitate)
— răspund la întrebări diferite, nu la variante ale aceleiași întrebări.
Aici e coerent: ρ negativă e explicabilă mecanic, nu o coincidență.

### Fir deschis, fără acțiune

Nici meritul FF, nici frecvența nu măsoară ce contează de fapt pentru un
dashboard de trading: **dacă surpriza mișcă efectiv prețul**. Asta ar
cere corelarea surprizei (`actual - consensus`) cu mișcarea prețului
instrumentului în fereastra post-publicare (ex. rentabilitatea pe 15-60
min după release) — o măsurătoare complet diferită de tot ce s-a făcut
până acum în această evaluare (care a folosit doar `score_precise`/bias
intern, niciodată prețul de piață). Ar atinge **toți indicatorii
existenți deja scorați, nu doar cei 17 candidați** — e o întrebare despre
tot modelul, nu despre bucket C. Notat aici ca fir deschis explicit,
nicio acțiune luată.

## Ce nu se schimbă

Toate cele 17 au frecvențe de contribuție ridicate (zile active 84.8%-
98.4% din panel) — niciunul nu e absent structural, doar `GBP`/`USD
Industrial Production` au o coadă stale spre finalul ferestrei (artefact
de arhivă, documentat mai sus). Toate cele 17 ating pragul de ≥0.25 în
cel puțin **26%** din zilele lor active — niciunul nu e complet inert.
Frecvența globală de flip bias pe tot panelul: **19.551 din 154.422
observații instrument-zi (12.7%)**.

## Ce NU s-a făcut

- Nicio implementare, nicio decizie luată — raport, cum s-a cerut.
- Nicio schimbare de rang FAZA 1 aplicată retroactiv acolo — acel
  document rămâne intact ca evaluare de merit; acesta e un document
  separat, de frecvență măsurată, nu o revizuire a FAZA 1.
- Cei 7 "monetary" și cei 14 "întreabă" — neatinse.

## Livrabile

- `docs/bucket-c-panel-measurement.md` — acest document.
- `docs/bucket-c-panel-categories.csv` — 21.753 rânduri (candidat × zi
  × categorie, `contributed`/`delta`).
- `docs/bucket-c-panel-instruments.csv` — 154.422 rânduri (candidat × zi
  × instrument, `flip`/scoruri).
- `docs/bucket-c-panel-summary.csv` — sumar per candidat, ordonat după
  frecvență.
- `scripts/measure/bucket_c_panel.py`, `analyze_panel.py` — instrumentar.
