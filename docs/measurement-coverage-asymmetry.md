# Măsurarea efectului asimetriei de acoperire asupra scorurilor de pereche

Status: **investigație încheiată.** Branch `measure/coverage-asymmetry`,
worktree `../macro-dev`. Zero cod de producție, zero config modificat —
instrumentar în `scripts/measure/`.

> **⚠️ Istoricul dinaintea lui 2026-07-31T12:01Z NU e reproductibil cu
> codul curent** (corectat 2026-08-01 — versiunea anterioară a acestei
> note spunea greșit "2026-07-29"; verificat direct prin testarea mai
> multor commit-uri istorice ale `public/data/economic.json`: `aa61011`
> la 09:46Z tot eșuează, `597faeb` la 12:01Z e primul care se potrivește
> exact — eroarea de dată a fost prinsă în `eval/bucket-c-candidates`
> când o măsurare ulterioară a încercat să folosească fereastra validată
> pentru un panel și a recalculat granița). Șase commit-uri de config
> (`z_buckets` recalibrat de două ori, adoptarea Variantei B, promovarea
> CAD Median CPI y/y, alias-uri AUD noi pentru CPI lunar) și trei corecții
> retroactive ale `data/economic_calendar_ff.parquet` (purjarea a 64 de
> rânduri duplicate din ianuarie 2023, reatribuirea a 139 de rânduri PMI
> la CHF/JPY/EUR, replay-ul CAD Median CPI din 20 iulie) cad toate în
> intervalul 2026-07-30T21:13Z — 2026-07-31T09:58Z, imediat înainte de
> graniță.
> Reconstrucția din acest document folosește DELIBERAT regulile și datele
> de AZI aplicate retroactiv (un counterfactual controlat — vezi secțiunea
> de validare mai jos pentru de ce și cum a fost verificat), NU o redare
> literală a ce s-a afișat pe dashboard la acea dată. **Orice analiză
> viitoare care presupune un replay literal al istoricului dinaintea
> acestei date va fi greșită** — parquet-ul nu e strict append-only, iar
> regulile de scoring s-au schimbat de mai multe ori în fereastră.

## Context stabilit deja

Acoperire per (valută, categorie), din `public/data/economic.json` (azi,
2026-08-01):

```
     growth  inflation  labour  monetary  TOTAL
USD       4          2       6         1     13
EUR       4          3       2         1     10
GBP       3          2       3         1      9
AUD       2          2       3         1      8
CAD       2          3       2         1      8
JPY       3          2       2         0      7
NZD       2          2       3         0      7
CHF       3          1       1         0      5
```

Notă de mecanism, verificată înainte de a măsura orice: categoria
`monetary` din `src/economic_compute.py::compute_currency_scorecard` **nu
vine din calendar** — e un slot standing separat, populat din
`data/rates.parquet` (motorul de rate expectations), niciodată din
`per_cat` (indicatorii de calendar). Structural, `coverage` pentru
`monetary` e mereu 0 sau 1 — nu poate exista un N=6 monetary. Subiectul
real al acestei măsurători e **growth/inflation/labour**, singurele
categorii construite din mai mulți indicatori de calendar cu N variabil.
`docs/unmapped-coverage-audit.md` a găsit acoperire nouă posibilă și pentru
"monetary" (masă monetară/credit) — dar asta ar fi un al 4-lea slot în
`per_cat`, nu extinderea celui existent; nu schimbă observația de mai sus.

## Pre-înregistrare — ce mă aștept să găsesc, ÎNAINTE de a rula

Scris înainte de a rula orice script de măsurare. Comparat cu rezultatele
în secțiunea de verdict.

**(1) N ↔ amplitudine.** Mă aștept ca pragul (amplitudine mediană la N≤2
cu ≥30% mai mare decât la N≥4) să fie **atins, confortabil**. Mecanismul e
aproape aritmetic: `score_precise` e o medie ponderată de scoruri
discrete în {-2,-1,0,1,2}; varianța unei medii scade cu 1/N indiferent de
domeniul aplicației, deci N mic → medie mai aproape de un singur eșantion
extrem → amplitudine mai mare. Aș fi surprins dacă NU s-ar confirma; ar
însemna că scorurile individuale dintr-o categorie sunt puternic
anti-corelate (posibil, dar neverificat încă).

**(2) Dominanța în celula de pereche.** Mă aștept ca partea cu N mai mic
să domine diferența în **peste 60%** din cazuri (pragul 2), pentru că (1)
implică direct asta ori de câte ori N diferă substanțial între cele două
picioare ale unei perechi.

**(3) USD labour (N=6) vs CHF labour (N=1).** Mă aștept la efectul cel mai
curat aici — N=1 înseamnă `score_precise` = scorul unui singur indicator,
fără nicio mediere/amortizare, în timp ce N=6 amortizează structural spre
interior. Mă aștept ca CHF să determine diferența în **peste 80%** din
observații (pragul 3) — dacă nici aici nu se vede efectul, ipoteza e moartă,
cum spune task-ul.

**(4) Shrinkage azi.** Aici sunt mai puțin sigur — precedentul direct
relevant: într-o fază anterioară am prezis greșit că Household Spending nu
va schimba niciun bias flip; a rezolvat toate cele verificate. Nu repet
acea greșeală presupunând un rezultat mic. Predicție onestă, largă:
**o fracțiune minoritară dar ne-neglijabilă** de celule de categorie
(estimare largă: 10-30%, concentrat la N≤2) își schimbă `score_cell`
rotunjit; pentru bias flips la nivel de pereche sunt mai precaut — pragurile
de bias sunt grosiere (mild=3, very=7 pe o scală ~[-10,10]), deci o singură
categorie shrunk ar putea să nu treacă pragul în multe perechi, dar nu
presupun un număr mic doar pe baza acestei intuiții. Măsor, nu ghicesc.

## Metodă de validare (înainte de orice concluzie istorică)

`data/economic_calendar_ff.parquet` e history-preserving (merge, nu
overwrite) din 2026-07-05 — 144 de commit-uri ating
`public/data/economic.json` în fereastra FF, ~5.3/zi (cron orar în zilele
lucrătoare + la 4h weekend + câteva manuale). Reconstrucția istorică:
trunchiez calendarul la `release_dt <= as_of` (necesar manual — 
`compute_indicator_score` are doar prag INFERIOR pe recency, nu superior
pe `as_of`; parquet-ul de azi conține deja rânduri viitoare față de orice
`as_of` din trecut) și rulez `compute_currency_scorecard` cu acel `as_of`.

Validare: reconstrucția la `as_of=azi` trebuie să reproducă EXACT
`score_precise`/`coverage` din `public/data/economic.json` curent — verificat
mai jos înainte de orice analiză istorică. Dar asta singură NU verifică
trunchierea manuală (parquet-ul de azi nu are rânduri viitoare față de azi,
deci un bug de trunchiere ar trece neobservat la `as_of=azi`). Validare
suplimentară: reconstrucția la 2-3 date DIN TRECUT trebuie să reproducă
`public/data/economic.json` așa cum era COMMIS la acea dată (via `git show
<commit>:public/data/economic.json`) — asta prinde exact bug-ul de
trunchiere, dacă există.

---

## Validare — o presupunere infirmată, apoi confirmată izolat

Prima rulare a comparat reconstrucția la 3 date istorice (2026-07-06,
07-15, 07-25) împotriva `git show <commit>:public/data/economic.json`.
**Toate 3 au eșuat** — zeci de nepotriviri pe majoritatea valutelor.
`as_of=azi` a reprodus exact valorile live — deci mecanismul de trunchiere
nu era vizibil rupt la suprafață, dar ceva era greșit istoric.

Diagnostic: `data/economic_indicators.yaml`/`config/ff_aliases.yaml` s-au
schimbat de 6 ori doar în fereastra FF (recalibrare `z_buckets` de două ori,
quarantine zero-placeholder, adoptarea Variantei B, promovarea CAD Median
CPI, alias-uri AUD noi). Reconstrucția folosește config-ul de AZI aplicat
retroactiv — nepotrivire AȘTEPTATĂ față de un JSON randat cu reguli vechi,
nu un bug de trunchiere.

Repetat DOAR în fereastra unde config-ul e stabil (după ultimul commit de
config, `9f76d68`, 2026-07-30T21:13:40Z): **tot eșuează** la punctul cel
mai vechi din fereastră (`fdbafbe`, 2026-07-30T23:09). Al doilea diagnostic:
`data/economic_calendar_ff.parquet` NU e strict append-only — trei
commit-uri ulterioare mută retroactiv rânduri istorice: `6f5c737` (replay
CAD Median CPI), `89784a6` (purjare 64 rânduri duplicate ianuarie 2023),
`fd610c0` (reatribuire 139 rânduri PMI la CHF/JPY/EUR). Toate trei sunt
DUPĂ `fdbafbe` dar înainte de următoarele două puncte testate — care core

**Rezultat**: la cele 2 puncte de test POSTERIOARE tuturor celor 6+3
commit-uri de config+date (`cf50f54`=azi, `1b8bd15`=2026-07-31T18:29),
reconstrucția reproduce EXACT (`score_precise` la 1e-9, `coverage` exact)
fiecare (valută, categorie). Mecanismul de trunchiere e corect —
verificat, nu presupus.

**Metodologie rezultată, explicită**: reconstrucția istorică din secțiunile
de mai jos e un **counterfactual controlat** — regulile ȘI datele de AZI
aplicate retroactiv la fiecare `as_of` din 2023-2026, NU o redare literală
a ce s-a afișat efectiv pe dashboard la acea dată (imposibil fără
parquet-uri istorice per-commit, nepăstrate). Asta e de fapt mai potrivit
pentru întrebarea de măsurat: aceleași reguli aplicate uniform izolează
efectul N-ului de schimbările interimare de regulă/curățare de date, care
altfel ar fi confuzie (confound) în orice comparație de-a lungul timpului.

## Verdict

**Efectul e real la nivel statistic pur (media unei categorii cu N mic
variază mai mult), dar infirmat ca afirmație cu consecință practică
(partea cu N mic NU domină sistematic celula de pereche) — inclusiv chiar
în cazul-far care a motivat exercițiul (USD vs CHF labour).**

| Criteriu pre-înregistrat | Prag | Rezultat | Verdict |
|---|---|---|---|
| (1) amplitudine mediană N≤2 vs N≥4 | ≥30% mai mare | **+50.0%** (pooled) | **ATINS** |
| (2) partea cu N mic domină celula de pereche | ≥60% din cazuri | **46.0%** | **NEATINS** (sub 50%, direcție ușor inversată) |
| (3) CHF (N=1) determină diferența USD/CHF labour | ≥80% din observații | **29.4%** | **NEATINS, cu mult** |

Mecanismul din (1) e confirmat — dar mecanismul care ar fi trebuit să-l
transmită în celula de pereche (2) și în cazul extrem (3) NU se vede.
Motivul, măsurat direct: CHF/labour (N=1, un singur indicator) e
**exact zero în 72% din zile** — indicatorul unic e, cel mai adesea, pur
și simplu un non-eveniment (nicio surpriză), nu o citire extremă. USD/labour
(N=6) e exact zero doar în **24%** din zile — cele 6 componente ale sale
par corelate suficient încât media rareori se anulează complet. Ipoteza a
presupus implicit că N mic = aproape întotdeauna extrem; datele arată că
N mic aici înseamnă adesea "fără semnal deloc", nu "semnal amplificat".
Asta e exact confuzia pe care task-ul a cerut să fie controlată — și
controlul a schimbat verdictul.

## (1) Relația N ↔ amplitudine, istoric

Reconstrucție zilnică 2023-01-02 → 2026-07-31 (1307 zile × 8 valute × 3
categorii = 31.368 observații), config+parquet de azi aplicate retroactiv
(vezi validarea de mai sus).

**Pooled** (toate seriile, toate datele) — confund cu efectul de valută,
raportat ca ATARE:

| N | obs | median &#124;score&#124; | mean &#124;score&#124; |
|---|---:|---:|---:|
| 0 | 550 | 0.000 | 0.000 |
| 1 | 2.752 | 0.000 | 0.664 |
| 2 | 12.687 | 0.500 | 0.575 |
| 3 | 10.181 | 0.333 | 0.479 |
| 4 | 3.965 | 0.500 | 0.466 |
| 5 | 32 | 0.200 | 0.156 |
| 6 | 1.201 | 0.167 | 0.292 |

N≤2: median 0.500 (n=15.989) vs N≥4: median 0.333 (n=5.198) →
**+50.0%**. Notă: relația nu e strict monotonă termen-cu-termen (N=4
median 0.500 > N=3 median 0.333) — artefact al valorilor discrete
disponibile unei medii de N scoruri întregi (N=4 permite pași de 0.25,
N=3 pași de 0.333), nu o inversare reală a tendinței; comparația pe
găleți (N≤2 vs N≥4, cum a cerut task-ul) e robustă la asta.

**Within-series** (control pentru efectul de valută — aceeași
valută-categorie, comparată cu ea însăși în perioade cu N diferit): doar
**5 din 24** serii (valută, categorie) vizitează vreodată AMBELE stări
(N≤2 ȘI N≥4) cu ≥3 observații fiecare — restul stau structural aproape de
propriul N constant pe toată fereastra (ex. CHF/labour nu atinge niciodată
N≥3). Dintre cele 5:

| Valută | Categorie | N obs (N≤2) | median (N≤2) | N obs (N≥4) | median (N≥4) | Δ relativ |
|---|---|---:|---:|---:|---:|---:|
| EUR | growth | 95 | 1.000 | 917 | 0.250 | +300% |
| GBP | growth | 43 | 1.000 | 1.082 | 0.500 | +100% |
| USD | growth | 57 | 0.500 | 1.051 | 0.250 | +100% |
| USD | inflation | 107 | 0.500 | 914 | 0.500 | 0% |
| USD | labour | 42 | 0.000 | 1.234 | 0.167 | **-100%** |

Mediana creșterii relative: +100%. 3/5 (60%) susțin direcția, 1 egalitate,
**1 inversată — chiar USD/labour**, categoria din cazul extrem de la
punctul (3). Eșantion mic (5 serii), dar direcția pooled se confirmă
parțial, nu universal — și excepția e exact acolo unde task-ul a cerut cea
mai atentă privire.

## (2) Dominanța în celula de pereche (toate cele 28 de perechi FX)

Pentru fiecare (pereche, categorie, dată) cu ambele picioare prezente
(coverage>0): partea cu N mai mic vs. partea care contribuie mai mult la
&#124;diferență&#124; (pondere = &#124;score_precise&#124; propriu / suma
&#124;score_precise&#124; ambelor picioare, >50% = domină).

- 106.646 observații cu ambele picioare prezente; 77.048 unde N chiar
  diferă între picioare (excis egalitățile de N).
- **Partea cu N mic domină în 46.0%** din cazuri (35.439/77.048) — SUB
  50%, deci ușor mai probabil ca partea cu N MARE să domine, nu invers.
- Pe categorii: growth 47.6%, inflation 44.5%, labour 45.8% — consistent,
  nu un artefact al unei singure categorii.

Pragul (2) (≥60%) **nu e atins nicăieri** — de fapt rezultatul e sub
pragul de 50% care ar însemna "fără efect / întâmplare".

## (3) Cazul extrem: USD labour (N=6) vs CHF labour (N=1)

1.249 zile cu ambele prezente. CHF are N mai mic în 1.245/1.249 dintre ele
(aproape mereu, cum era de așteptat din tabelul de acoperire).

**CHF (indicatorul unic) determină diferența în doar 29.4%** din cele
1.245 observații unde are N mai mic — sub pragul de 80%, sub și pragul
de 50% al întâmplării. Pe tot eșantionul (1.249 zile), CHF domină în
29.3%.

Motivul (verificat, nu presupus): CHF/labour e exact 0 în 72% din zile —
indicatorul unic e, cel mai des, o lună fără surpriză. USD/labour e exact
0 doar în 24% din zile. Când CHF chiar surprinde, surpriza e maximă
(&#124;score&#124;=2, fără nicio mediere care s-o tempereze) — dar asta
se întâmplă rar destul încât, per total, USD (cu 6 indicatori parțial
corelați, rareori toți neutri simultan) domină diferența mai des decât
indicatorul unic al CHF.

**Ăsta e cazul-far al exercițiului, și nici aici efectul nu se vede la
magnitudinea prezisă — per regula pre-înregistrată, asta ar trebui tratat
ca ipoteza fiind moartă pentru afirmația de dominanță**, chiar dacă
mecanismul de bază (media unui N mic variază mai mult) rămâne adevărat la
nivel pur statistic (punctul 1).

## (4) Ce s-ar schimba cu shrinkage — azi, magnitudine, nicio adoptare

Formă folosită (argumentată, nu singura posibilă): `factor = min(1,
sqrt(N/4))`. N=4 refolosește pragul deja folosit în alte măsurători din
acest proiect (`CADENCE_THRESHOLD`, `src/ff_scoring.py`) drept "eșantion
suficient" — nu un prag nou inventat. Scalarea cu √N urmează eroarea
standard a unei medii (SE ~ σ/√N pentru surprize aproximativ independente):
N=1→0.5, N=2→0.707, N=3→0.866, N≥4→1.0 (plafonat, nu amplifică).

> **Notă (2026-08-02)**: `CADENCE_THRESHOLD` e cod mort în raport cu
> scoring-ul live (vezi `docs/proposal-staleness-gate.md` §4) — pragul real
> care guvernează scoring-ul e `fallback_min_prints=6`. Referința de mai sus
> folosește constanta doar ca precedent de convenție pentru N=4, nu ca
> afirmație că un gate activ o citește; nicio schimbare la propunerea de
> shrinkage (neadoptată oricum, per titlul secțiunii).

Scop: doar categoriile calendaristice (growth/inflation/labour);
`monetary` neatins (nu vine din N de calendar). Comparație macro-only
(fără sentiment/trend/rate — mirror la `ff_scoring.score_calendar`, ca
tot restul măsurătorii) — bias-urile comparate sunt bias-ul macro, nu
bias-ul complet afișat live (care include și COT+trend).

- **1 din 24** celule de categorie își schimbă `score_cell` rotunjit:
  USD/inflation (N=2): -2.000 → -1.414 → celulă -2 → -1.
- **3 din 29** instrumente își schimbă bias-ul:
  - USDCHF: Very Bearish (-2.43) → Bearish (-1.87)
  - USDCAD: Bearish (-1.60) → Neutral (-1.01)
  - NZDUSD: Very Bullish (2.85) → Bullish (2.16)

Magnitudine azi: mică la nivel de celulă (~4%), moderată la nivel de
bias de pereche (~10%) — niciuna din cele două nu e "aproape tot" sau
"aproape nimic". Repet: doar magnitudine, nicio propunere de adoptare.

## Ce NU s-a făcut (conform scope)

- Nicio modificare la taxonomie, config, sau scoring.
- Cei 33 de candidați din găleata C — neadăugați.
- Categoria housing/construction, garda PMI, cele 18 perechi cu unitate
  greșită, `can_be_zero`, FTSE100, workflows — neatinse.
- Shrinkage-ul (4) e o SIMULARE, nu o implementare — niciun cod de
  producție nu a fost schimbat, doar rulat pentru o comparație în memorie.

## Livrabile

- `docs/measurement-coverage-asymmetry.md` — acest document.
- `docs/coverage-asymmetry-history.csv` — panel zilnic (as_of, valută,
  categorie, score_precise, coverage), 2023-01-02→2026-07-31.
- `docs/coverage-asymmetry-within-series.csv` — comparația within-series
  (punctul 1b).
- `docs/coverage-asymmetry-pair-dominance.csv` — toate observațiile
  (pereche, categorie, dată) cu ambele picioare prezente (punctul 2).
- `docs/coverage-asymmetry-shrinkage-cells.csv` /
  `-shrinkage-flips.csv` — rezultatul simulării de shrinkage (punctul 4).
- `scripts/measure/` — instrumentar (reconstruct.py, build_history.py,
  validate.py, analyze_n_amplitude.py, analyze_pair_dominance.py,
  shrinkage_today.py). Investigație, nu cod de producție.
