# Auditul evenimentelor nemapate — ce pierdem din acoperire

Status: **investigație FAZA 1 — zero cod, zero config.** Branch
`audit/unmapped-coverage`, worktree `../macro-dev`.

Sursă: `data/archive/ff_calendar_range.json` (16.204 evenimente, 2023-01 →
2026-07, schema JBlanked `Name/Currency/Date/Actual/Forecast/Previous`).
`data/ff_raw/` (payload weekly curent) a confirmat convenția de nume identică
(`title`/`country` ↔ `Name`/`Currency`), deci arhiva e utilizabilă direct
pentru inventarul istoric. Filtrare identică cu pipeline-ul de producție
(`src/econ_calendar_ff.py`, reutilizat read-only): cele 8 valute în scop,
whitelist-ul EUR pentru agregate, `excluded_final_variants`.

Din 16.204 evenimente: 3.202 deja scorate (mapate), 185 excluse ca variante
finale/revizuite (telemetrie), 3.111 EUR non-agregat (membru UE, excludere
by design). **Rămân 9.706 evenimente nemapate, în 316 perechi distincte
(valută, nume brut).**

## O presupunere infirmată în timpul investigației

Metodologia cerută agregă pe `(valută, nume)`. **Asta nu identifică unic un
indicator în arhivă.** Exemplu concret, `USD / "Housing Starts"` (81
evenimente brute, arăta ca un candidat curat lunar):

```
12:13-12:15 UTC   n=25   valori 202-294        (scală mii — potrivire cu intervalul
                                                 tipic al Canada Housing Starts SAAR)
12:30-13:30 UTC   n=38   valori 0.0-1.63        (scală milioane — potrivire cu US
                                                 Housing Starts SAAR real)
```

Două scale de valori disjuncte, la ~15-20 minute distanță, în fiecare lună,
3.5 ani la rând — nu e aceeași serie. Cel puțin un eveniment pare etichetat
greșit sub `Currency` în arhivă, contopind doi indicatori sub o singură
cheie `(valută, nume)`.

Verificare pe scară: din cele 160 de perechi care treceau pragul brut de
volum, **111 arătau ≥2 ore de release UTC recurente** (conversie corectă
DST via `jblanked_to_utc`, nu ora brută din câmpul `Date`). Majoritatea sunt
artefact benign (un singur indicator real, două ore exact la 1h distanță —
DST-ul propriu al SUA aplicat peste un ceas fix non-DST în altă țară), dar
nu toate — `Housing Starts` de mai sus e un caz confirmat de contaminare
reală.

**Rezoluție convenită**: pragurile complete (≥N cu actual ȘI forecast
valide, fără suprapunere, cadență stabilă) au fost aplicate pe toate cele
316 perechi. Doar cele ~33 care au ajuns efectiv în găleata C au fost
verificate individual cu un detector de cluster orar (oră UTC recurentă +
comparație de scală a valorilor între clustere). Restul (zgomot, redundant,
sub prag) NU au fost verificate pentru compunere — nu contează, oricum nu
intră în C. Rezultat verificare: **0 din 33 candidați finali s-au dovedit
compuși.** Cele două perechi suspecte găsite inițial (`USD Goods Trade
Balance` — 4 clustere orare distincte confirmate; `GBP Current Account` —
cadență implauzibilă, fără cluster dominant) fuseseră deja excluse din C
pe alte criterii (nume duplicat, variantă mai curată disponibilă); detectorul
confirmă independent că excluderea a fost corectă.

## Găleata C — candidați reali (33)

Toate verificate: ≥prag (cadență-dependent, `CADENCE_THRESHOLD` din
`src/ff_scoring.py`: lunar/săptămânal=24, trimestrial=8), fără suprapunere
cu un `indicator_key` existent, cadență stabilă, verificate individual
pentru non-compunere (cluster orar unic sau shift DST de 1h cu valori care
se suprapun ca interval).

### Inflation (8)

| Valută | Nume brut | N (ambele valide) | Cadență |
|---|---|---|---|
| JPY | Tokyo Core CPI y/y | 46 | lunar |
| USD | Import Prices m/m | 41 | lunar |
| GBP | RPI y/y | 43 | lunar |
| JPY | SPPI y/y | 42 | lunar |
| USD | Prelim UoM Inflation Expectations | 42 | lunar |
| AUD | Import Prices q/q | 17 | trimestrial |
| USD | Advance GDP Price Index q/q | 16 | trimestrial |
| JPY | Prelim GDP Price Index y/y | 14 | trimestrial |

Tokyo Core CPI e indicator avansat pentru CPI național JPY (precede cu
~3 săpt.); RPI e a doua măsură oficială de inflație UK (folosită încă la
gilts index-linked), distinctă metodologic de CPI deja scorat; Import
Prices e inflație de import, un unghi diferit de PPI/CPI/PCE; SPPI e PPI
pe partea de servicii (JPY nu are un PPI-servicii scorat azi); GDP Price
Index e deflatorul PIB, o măsură de inflație economie-largă distinctă de
coșurile CPI/PPI; UoM Inflation Expectations e sondaj de așteptări (
urmărit explicit de Fed), fără echivalent în taxonomie.

### Growth (15)

| Valută | Nume brut | N | Cadență |
|---|---|---|---|
| USD | Trade Balance | 44 | lunar |
| JPY | Trade Balance | 45 | lunar |
| CAD | Trade Balance | 43 | lunar |
| NZD | Trade Balance | 43 | lunar |
| CHF | Trade Balance | 36 | lunar |
| USD | Durable Goods Orders m/m | 43 | lunar |
| USD | Industrial Production m/m | 41 | lunar |
| GBP | Industrial Production m/m | 42 | lunar |
| JPY | Prelim Industrial Production m/m | 42 | lunar |
| USD | Personal Income m/m | 41 | lunar |
| USD | Personal Spending m/m | 42 | lunar |
| JPY | Core Machinery Orders m/m | 42 | lunar |
| AUD | Private Capital Expenditure q/q | 14 | trimestrial |
| JPY | Capital Spending q/y | 13 | trimestrial |
| AUD | Company Operating Profits q/q | 12 | trimestrial |

Trade Balance acoperă 5 din cele 8 valute (gol de acoperire cross-currency
notabil — vezi și nota de asimetrie mai jos). Durable Goods/Industrial
Production/Personal Income-Spending sunt indicatori "hard data" standard,
distincți de PMI (sondaj) și GDP (agregat trimestrial) deja scorate.
Capex/Machinery Orders/Company Profits sunt semnale de investiție/profit
corporativ, fără echivalent azi.

### Labour (3)

| Valută | Nume brut | N | Cadență |
|---|---|---|---|
| USD | Prelim Nonfarm Productivity q/q | 14 | trimestrial |
| CAD | Labor Productivity q/q | 13 | trimestrial |
| USD | Prelim Unit Labor Costs q/q | 14 | trimestrial |

Productivitatea și costul unitar al muncii sunt distincte de `wage_growth`
(AHE, un nivel salarial) — combină salarii cu productivitatea, unghi
diferit. Cea mai subțire găleată — labour e categoria cu cel mai puțin
spațiu de acoperire nouă găsit.

### Monetary (7)

| Valută | Nume brut | N | Cadență |
|---|---|---|---|
| GBP | M4 Money Supply m/m | 45 | lunar |
| GBP | Net Lending to Individuals m/m | 45 | lunar |
| JPY | Monetary Base y/y | 43 | lunar |
| JPY | Bank Lending y/y | 42 | lunar |
| JPY | M2 Money Stock y/y | 42 | lunar |
| AUD | Private Sector Credit m/m | 44 | lunar |
| GBP | MPC Official Bank Rate Votes | 15 | trimestrial |

Astăzi categoria "monetary/rates" din taxonomie conține DOAR
`interest_rate_decision` (display-only, weight 0). Masă monetară/credit
pentru GBP/JPY/AUD ar fi acoperire complet nouă. MPC Official Bank Rate
Votes (distribuția voturilor comitetului BoE) e un semnal de ton
hawkish/dovish distinct de decizia de rată în sine.

## Găleata D — ambiguu, decizie de trading necesară (63)

Nu poate fi decis fără context — listate cu întrebarea concretă. Grupuri:

- **Locuințe/construcții (22 perechi)**: Housing Starts/Building Permits/
  New-Existing-Pending Home Sales/HPI (4 variante: Halifax, Nationwide,
  Rightmove-neverificat, S&P/CS, RICS)/Mortgage Approvals/Construction
  Output-Spending/NAHB Housing Market Index etc. **Nicio categorie din cele
  patru (growth/inflation/labour/monetary) nu se potrivește curat** —
  locuințele nu sunt un pilon existent. Întrebare: se adaugă un al cincilea
  pilon (housing), sau rămân neacoperite? Aceasta e o decizie mai mare decât
  "adaugă un indicator" — schimbă structura taxonomiei.
- **Fiscal (2 perechi)**: USD Federal Budget Balance, GBP Public Sector Net
  Borrowing. Același gol de categorie ca locuințele.
- **Tehnic/flux de capital/inventar (10 perechi)**: TIC Long-Term Purchases,
  Business/Wholesale Inventories, Wholesale/Manufacturing Sales, Foreign
  Securities Purchases, Consumer Credit m/m, Wards Total Vehicle Sales,
  Tertiary Industry Activity, Index of Services 3m/3m. Date reale, dar
  relevanță FX discutabilă (indicatori întârziați/de nișă istoric) —
  întrebare: merită tranzacționate sau doar zgomot pentru scopul acestui
  dashboard?
- **Suprapunere ambiguă cu un indicator deja scorat (5 perechi)**: NZD/GBP
  PPI Input, CAD RMPI (preț materii prime vs produs industrial — complementar
  sau redundant cu `ppi_yoy`?); USD Employment Cost Index (măsură de cost
  total compensație vs `wage_growth`=AHE — se suprapun sau se completează?);
  JPY Household Spending y/y (suprapune `retail_sales` deja scorat pt JPY?).
- **Posibil superior indicatorului deja scorat, nu doar redundant (2
  perechi)**: JPY Tankan Manufacturing/Non-Manufacturing Index — sondajul
  oficial BOJ, cu greutate de piață arguabil mai mare decât au Jibun Bank
  PMI deja scorat. Întrebare: se înlocuiește PMI-ul curent cu Tankan, sau
  rămân separate?
- **Cadență implauzibilă / suspiciune de contaminare (3 perechi, verificate
  cu detectorul)**: `GBP Current Account` (gap median 8 zile — UK Current
  Account e trimestrial real; fără cluster orar dominant, comportament
  neregulat), `USD Goods Trade Balance` (CONFIRMAT 4 clustere orare
  distincte — compus), `USD Housing Starts` (CONFIRMAT 2 populații de
  valori disjuncte — compus). Nu sunt candidați; documentate ca atare
  pentru cine se uită peste inventarul brut și se întreabă de ce lipsesc.
- **Sub pragul de eșantion, dar cu semnal real (restul)**: câteva perechi
  cu date/forecast valide și variație reală, dar sub pragul de cadență
  (de obicei trimestriale/anuale prea recente sau prea rar publicate în
  fereastra de 3.5 ani) — "de urmărit", nu excludere definitivă.

Detaliu complet per pereche, cu motivul exact: `docs/unmapped-coverage-audit.csv`.

## Găleata B — redundant (40)

Măsoară concepte deja acoperite, fie de un `indicator_key` existent, fie
de un alt candidat/vintage din același grup. Grupuri principale:

- **Sondaje de sentiment/încredere regionale (21 perechi)**: Flash
  Manufacturing/Services PMI, Richmond/Philly Fed/Empire State/Chicago PMI-
  like/NAHB(sentiment)/ISM Manufacturing Prices (SUA); GfK/CBI Realized
  Sales/CBI Industrial Order Expectations/Construction PMI (GBP); KOF/SECO
  (CHF); Ivey PMI (CAD); Economy Watchers/BSI Manufacturing (JPY);
  RCM/TIPP/NFIB/UoM Consumer Sentiment (SUA). Redundante cu semnalul
  PMI/growth deja scorat sau între ele (mai multe sondaje pentru același
  concept, aceeași valută).
- **Retail redundant (4 perechi)**: Core Retail Sales (SUA/CAD/NZD),
  BRC Shop Price Index/Retail Sales Monitor (GBP) — deja acoperit de
  `retail_sales`.
- **Vintage-uri ulterioare ale unei serii deja candidate/scorate (15
  perechi)**: Factory Orders/Core Durable Goods Orders (redundant cu
  Durable Goods Orders headline din C), CPI m/m (SUA) și CPI q/q (AUD) —
  **redundant DIRECT cu `cpi_yoy` existent**, aceeași publicare doar cu
  transformare diferită (m/m sau q/q vs y/y) — exemplul de manual pentru
  găleata B. Final/Revised/Prelim variante ale GDP Price Index, Nonfarm
  Productivity, Unit Labor Costs, Business Investment, Industrial
  Production (JPY) — a doua/a treia estimare a unei serii deja candidate
  din C (convenția flash-scored: prima estimare intră în C, restul e B).

## Găleata A — zgomot (180)

Fără valoare numerică reală: discursuri (Fed/BOE/BOJ/RBA — 20+ membri
diferiți), conferințe de presă, minute de ședință, licitații de obligațiuni
(GBP 10-y/30-y), sărbători bancare, Daylight Saving Time Shift, rapoarte
narative (Beige Book, BOJ Summary of Opinions, BOC Summary of
Deliberations). Verificate cu un filtru de variație reală, nu doar
parsabilitate numerică — o singură valoare "aberantă" (ex. o eroare de
etichetare) în 90+ apariții altfel constante (0.0/0.0/0.0) NU a fost
suficientă să treacă filtrul de "semnal real" (necesar: valoarea cea mai
frecventă să nu domine >50% din apariții, pe lângă ≥2 valori distincte —
un singur exemplu prins: `FOMC Member Goolsbee Speaks`, 93/94 apariții
0.0/0.0, o singură apariție -144.0/-148.0, corect exclusă din C).
Excludere corectă, nicio acțiune.

## Asimetria de acoperire pe valută — cel mai important rezultat

Pentru fiecare valută: evenimente cu consensus valid (actual ȘI forecast)
mapate (scorate azi) vs. nemapate-dar-utilizabile (grup cu semnal real,
adică nu zgomot placeholder).

| Valută | Mapate (valide) | Nemapate utilizabile | Total utilizabil | % nemapat |
|---|---:|---:|---:|---:|
| USD | 737 | 2.566 | 3.303 | **77.7%** |
| JPY | 295 | 690 | 985 | **70.1%** |
| GBP | 634 | 1.066 | 1.700 | **62.7%** |
| CAD | 543 | 450 | 993 | 45.3% |
| AUD | 285 | 167 | 452 | 36.9% |
| CHF | 214 | 116 | 330 | 35.2% |
| NZD | 207 | 90 | 297 | 30.3% |
| EUR | 287 | 0 | 287 | 0.0%* |

*EUR e structural incomparabil: whitelist-ul de agregate UE exclude
evenimentele membrilor (Germania/Franța/Italia/Spania) by design, ca să nu
se dubleze numărătoarea națională vs. agregat. Calculat FĂRĂ acest filtru
(doar pentru context, NU comparabil 1:1): EUR ar arăta 88.2% nemapat
(2.150 din 2.437) — dar asta include toate evenimentele naționale membre
care oricum nu vor fi scorate niciodată sub arhitectura actuală.

**Ipoteza confirmată, nu doar presupusă**: asimetria e reală și mare.
USD publică nu doar mai multe evenimente brute — publică disproporționat
mai multe evenimente cu consensus valid și utilizabil care rămân nemapate
(77.7%, cel mai mare dintre cele 7 valute comparabile). JPY și GBP sunt
la rândul lor semnificativ peste CAD/AUD/CHF/NZD. Scorurile de pereche
compară doi indici — dacă USD/JPY/GBP au acoperire proporțional mai slabă
decât CAD/AUD/CHF/NZD, indicii lor sunt construiți din informație relativ
mai puțină față de ce publică efectiv economia lor, în timp ce pereche
precum AUD/NZD ajunge mai aproape de "acoperire completă" a ce se publică.
Asta nu înseamnă neapărat că scorurile USD/JPY/GBP sunt greșite — dar
înseamnă că afirmația implicită "toate valutele sunt la fel de bine
acoperite" e falsă, verificat pe date, nu presupusă.

## Ce NU s-a făcut (conform scope)

- Nicio adăugare propusă în `config/ff_aliases.yaml` sau
  `data/economic_indicators.yaml`.
- Nicio decizie luată pe găleata D (housing ca pilon nou, Tankan vs PMI,
  Input vs Output PPI) — sunt prezentate ca întrebări deschise.
- Garda PMI, cele 18 perechi cu unitate greșită, cele 76 rânduri S&P
  Global, `can_be_zero`, FTSE100, workflows — neatinse.

## Livrabile

- `docs/unmapped-coverage-audit.md` — acest document.
- `docs/unmapped-coverage-audit.csv` — inventar complet, toate cele 316
  perechi, cu bucket, motiv, cadență, N, interval de date.
