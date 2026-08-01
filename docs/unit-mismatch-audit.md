# Auditul celor 18 perechi cu unitate greșită

Status: **investigație încheiată — zero cod, zero config.** Branch
`fix/guard-units-visibility`, worktree `../macro-dev`.

## Verdict

**Grupa A (reparabile prin promovare) e practic goală.** Din cele 18
perechi marcate `# xf`, una e deja moartă/orfană (CAD Core CPI, promovată
în altă parte pe 2026-07-30, nu mai alimentează niciun `indicator_key` —
nu cere nicio acțiune). Din celelalte 17 vii, **16 nu au NICIO
alternativă** în `data/archive/ff_calendar_range.json`, `data/jb_raw/`,
sau `data/ff_raw/` — sursa pur și simplu nu publică unitatea corectă,
niciodată. **O singură pereche (USD Core CPI y/y) are o alternativă vie,
dar cu istoric prea subțire de judecat azi** — deschis cu asta mai jos,
pentru că e singurul lucru din tot auditul care ar putea deveni acționabil.

**Nicio pereche nu trece pragul de ≥24 observații cu consensus valid
pentru grupa A azi.** Restul de 16 intră în grupa B (relabelare posibilă,
fără cost) sau C, cu o distincție mai subtilă decât părea din task —
detaliată mai jos.

## Candidatul care contează: USD Core CPI y/y

Excepția `alert_exceptions.yaml` de la o sesiune anterioară deja nota că
JBlanked livrează un eveniment nativ "Core CPI y/y" pentru USD, separat de
"Core CPI m/m" (cel care alimentează azi `core_cpi`, cu transformare xf).
Verificat aici cu date, nu presupus:

- **`data/archive/ff_calendar_range.json`**: doar **2 apariții**
  (2026-05-12, 2026-06-10), ambele marcate `Quality: "Bad Data"` de
  JBlanked (dar cu valori numeric plauzibile — 2.7-2.9%, în intervalul
  real al US Core CPI, nu un placeholder 0.0). **Sub pragul de 24, cu
  mult.**
- **`data/jb_raw/`** (pull-uri recente, 2026-07-19→08-01): aceeași serie
  apare, cu un print MULT mai recent (**2026-07-14, Actual=2.6,
  Quality="Good Data"**) — deci e **vie în feed-ul curent**, nu moartă.
  Arhiva înghețată doar nu a prins-o des — probabil o problemă de
  colectare a arhivei, nu o proprietate a seriei.

**Concluzie**: nu califică pentru promovare azi (istoric insuficient
verificabil — arhiva arată doar 2, iar `jb_raw` acoperă doar ~2
săptămâni, insuficient să confirme un istoric de 24+). Dar **nu-i
Grupa C** (nereparabilă) — e o serie vie, cu Impact FF **High**, care
merită monitorizată în timp (mecanism similar cu `review_by` din
`alert_exceptions.yaml`), nu respinsă definitiv azi.

## Toate cele 18, catalogate

| Valută | Nume brut (azi) | Canonic (azi) | `indicator_key` | Mediana `actual` | Cadență reală | Alternativă în arhivă/jb_raw/ff_raw |
|---|---|---|---|---:|---|---|
| CAD | Core CPI m/m | Core CPI y/y | **orfan** (nu mai alimentează nimic din 2026-07-30) | 0.100 | m/m | — nu se aplică, deja rezolvat altundeva |
| USD | Core CPI m/m | Core CPI y/y | `core_cpi` | 0.300 | m/m | vezi mai sus — n=2 arhivă, vie în jb_raw |
| USD | PPI m/m | PPI y/y | `ppi_yoy` | 0.200 | m/m | zero |
| USD | Core PCE Price Index m/m | Core PCE Price Index y/y | `core_pce` | 0.200 | m/m | zero |
| USD | Average Hourly Earnings m/m | Average Hourly Earnings y/y | `wage_growth` | 0.300 | m/m | zero |
| EUR | PPI m/m | PPI y/y | `ppi_yoy` | -0.200 | m/m | zero (doar "German PPI m/m", membru, în afara agregatului EUR) |
| GBP | PPI Output m/m | PPI Output y/y | `ppi_yoy` | 0.000 | m/m | zero |
| JPY | Retail Sales y/y | Retail Sales m/m | `retail_sales` | 2.350 | **y/y** (invers!) | zero — nu există "Retail Sales m/m" nativ pentru JPY |
| AUD | Trimmed Mean CPI q/q | RBA Trimmed Mean CPI y/y | `core_cpi` | 0.850 | q/q | zero — "y/y" e etichetă inventată, sursa e mereu q/q |
| AUD | PPI q/q | PPI y/y | `ppi_yoy` | 0.900 | q/q | zero |
| AUD | Wage Price Index q/q | Wage Price Index y/y | `wage_growth` | 0.800 | q/q | zero |
| NZD | CPI q/q | CPI y/y | `cpi_yoy` | 0.900 | q/q | zero — NZ CPI e structural trimestrial, fără variantă y/y |
| NZD | Labor Cost Index q/q | Labor Cost Index y/y | `wage_growth` | 0.700 | q/q | zero |
| CAD | CPI m/m | CPI y/y | `cpi_yoy` | 0.200 | m/m | zero |
| CAD | IPPI m/m | IPPI y/y | `ppi_yoy` | 0.400 | m/m | zero |
| CAD | GDP m/m | GDP q/q | `gdp_qoq` | 0.100 | m/m | zero |
| CHF | CPI m/m | CPI y/y | `cpi_yoy` | 0.000 | m/m | zero |
| CHF | PPI m/m | PPI y/y | `ppi_yoy` | -0.100 | m/m | zero |

Detaliu (n, mediană, valabilitate forecast) în consola de lucru — toate
medianele confirmă unitatea reală (aproape de 0, consistent cu m/m sau
q/q, nu cu magnitudinea tipică y/y de 1-5% pentru CPI/PPI/salarii).

## Grupele, cu o nuanță față de definiția din task

Definițiile B ("nu există alternativă, dar numele poate deveni onest")
și C ("sursa publică doar unitatea greșită") se suprapun aproape complet
odată verificate datele — pentru toate cele 16 fără alternativă,
condiția din C ("sursa publică doar unitatea greșită") e ADEVĂRATĂ, dar
asta NU împiedică o relabelare (B): fiecare alias e cheiat per valută
(`aliases: {USD: {...}, EUR: {...}}`), deci redenumirea canonicului unei
valute nu poate coliza cu nimic din altă valută. **Relabelarea e
posibilă, fără risc de coliziune, pentru toate cele 17 vii** — dar asta
NU rezolvă problema de fond semnalată în context ("compari surprize cu
proprietăți statistice diferite") — un `indicator_key` ca `ppi_yoy` tot
ar amesteca m/m (USD/EUR/GBP/AUD/CHF) cu y/y (oriunde e cazul) în
comparații de pereche, indiferent cum se numește eticheta. Relabelarea
rezolvă onestitatea afișajului, nu comparabilitatea statistică — exact
avertismentul "nu-l supraevalua" din task.

- **JPY Retail Sales — singurul caz cu beneficiu clar de relabelare.**
  Aici defectul e INVERSAT față de restul (canonic "m/m" alimentat de
  date "y/y" reale) — cel mai indus-în-eroare caz din toate 18, pentru că
  cititorul ar interpreta greșit magnitudinea (2.35 citit ca "m/m" pare
  o schimbare uriașă; ca "y/y" e normal). Un precedent direct există deja
  în același fișier: CHF's `"Retail Sales y/y": "Retail Sales y/y"` (fără
  tag xf) — JPY ar deveni consistent cu asta, fără coliziune.
- **Restul de 16** — relabelare posibilă tehnic, dar valoarea ei e
  cosmetică (onestitate de afișaj), nu corectivă. Nu recomand nimic activ
  aici fără o decizie separată despre cât contează claritatea etichetei.

## Ce NU s-a făcut

- Nicio modificare în `config/ff_aliases.yaml`, `data/economic_
  indicators.yaml`, sau oriunde altundeva.
- Nicio promovare — grupa A e goală, oricum ar fi fost în afara scopului.
- Cazul CAD Core CPI (orfan) — confirmat deja rezolvat de o sesiune
  anterioară, nicio acțiune necesară.

## Livrabile

- `docs/unit-mismatch-audit.md` — acest document.
