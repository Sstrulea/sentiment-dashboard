# FAZA 3 — vizibilitatea N-ului

Status: **implementat.** Branch `fix/guard-units-visibility`, worktree
`../macro-dev`.

## Ce s-a verificat înainte — și de ce a fost nevoie de o discuție

`TABLE_LAYOUT` (și `CROSSASSET_TABLE_LAYOUT`, aceeași structură) nu are
NICIUN cell de "scor de categorie" în tabelul dens — doar celule
individuale per indicator (4 fixe per categorie growth/inflation/labour)
și coloanele Score/Bias per instrument. Agregatul de categorie
(`score_precise`/`coverage`) există DOAR în `card.categories` (per
valută, server-side) și e afișat DOAR în drill-down (`legHtml`).

Complicația reală: rândurile tabelului sunt **instrumente** (perechi cu
2 valute, sau single cu 1), dar N e o proprietate **per valută** — o
pereche ca EURUSD are DOUĂ N-uri diferite per categorie (EUR și USD), nu
unul. "Lângă scorul de categorie" nu avea unde să stea fără ambiguitate,
exact cum a anticipat task-ul — de-aia am întrebat înainte de a alege
o soluție.

## Soluția aleasă: tooltip extins pe celula de simbol existentă

Nicio coloană nouă, zero pixeli vizibili noi în starea normală — `title`
pe `<td class="sym">`, care există deja. La hover pe simbol, apare:

```
Growth: USD N8 · CAD N2
Inflation: USD N4 · CAD N3
Labour Market: USD N7 · CAD N2
```

Pentru instrumente single (ex. US-DOLLAR): un singur N per categorie,
fără format de pereche forțat. Pentru cross-asset (`renderCrossAsset`,
aceeași structură de problemă, verificată explicit): o singură valută
de bază, aceeași soluție simplificată (fără pereche).

**Doar growth/inflation/labour** — `monetary` exclus explicit din lista
de categorii verificate (vine din motorul de rate, nu din calendar, cum
s-a cerut).

## Verificări

- **Sintaxă**: `node --check static/economic-chart.js` — OK.
- **Valori corecte**: rulat funcțiile extrase direct în Node împotriva
  `public/data/economic.json` real (fără browser headless disponibil în
  mediu) — EURUSD, USDCAD, US-DOLLAR (single), toate cele 8 instrumente
  cross-asset. Fiecare N verificat să coincidă exact cu
  `currencies[ccy].categories[cat].coverage` din payload (ex. USDCAD →
  "Growth: USD N8 · CAD N2", coincide exact cu `USD.categories.growth.
  coverage=8` și `CAD.categories.growth.coverage=2`).
- **Lățimi mici de ecran**: nicio schimbare posibilă — modificarea adaugă
  DOAR un atribut `title` (tooltip nativ al browserului) pe o celulă deja
  existentă, zero elemente DOM noi, zero text nou vizibil în starea
  normală. Nu există ce să se rupă la nicio lățime.
- `pytest`: 485 verzi (neschimbat — modificare JS pură, fără teste Python
  care ating stratul de randare frontend).

## Ce NU s-a făcut

- Nicio coloană nouă în `TABLE_LAYOUT` sau `CROSSASSET_TABLE_LAYOUT`.
- Niciun N afișat pentru `monetary`.
- Nicio schimbare de scoring — pur prezentare (`src/economic_render.py`
  neatins, doar `static/economic-chart.js`).

## Livrabile

- `docs/faza3-n-visibility.md` — acest document.
- `static/economic-chart.js` — `categoriesNTooltip`,
  `caCategoriesNTooltip`, wiring în `renderRow`/`renderCrossAsset`.
