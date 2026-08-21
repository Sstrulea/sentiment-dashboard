# FAZA 1F — Merge fix GBP, rebase, verificare pagină

Worktree `../macro-dev`. `main` local e la `8776d54` (fast-forward, NEPUSH-uit —
vezi Partea 2). `feat/history-catalog` rebazat pe acel `main`, la `834693f`.

## PARTEA 1 — lanțul de propagare (verificat înainte de merge)

```
scor indicator gdp_qoq (compute_indicator_score)
    -2 (fallback, saturat)  ->  -1 (z=-1.474, z-scored)
        |
        v  (inclus în categoria growth, coverage=5 NESCHIMBAT — gdp_qoq era deja
        |   inclus în ambele cazuri: flag nu era 'no_consensus'/'direction_mismatch'
        |   nici înainte, nici după; substituție de scor ÎN interiorul aceluiași set,
        |   nu o schimbare de membru)
        v
categoria growth: score_precise = mean(2, -1(sau -2), ...) / weight
    -0.4  ->  -0.2   (exact: +1 pe 1 din 5 sloturi de weight 1.0 => +1/5 = +0.2)
        |
        v  index = mean(growth, inflation, labour, monetary) * scale(5)
        |  (toate weight=1.0; inflation/labour/monetary NESCHIMBATE: 0.3333, 0.3333, 0.0)
        v
index: mean(-0.4, 0.333, 0.333, 0.0)*5 = 0.333   ->   mean(-0.2, 0.333, 0.333, 0.0)*5 = 0.583
        |
        v  raw_pct = 50 + index * STRENGTH_PCT_K(10.5)
        v
pct: 53.5  ->  56.1   (Δ +2.6, NU +75% — +75% e mișcarea INDEXULUI brut, 0.333->0.583;
        |               procentul /strength afișat se mișcă doar 53.5->56.1)
        v  bias_label(index, thresholds={mild:1.12, very:2.41})
        v
|index| = 0.333 și 0.583, ambele << mild(1.12)  =>  "Neutral" în AMBELE cazuri
```

Verificat cu funcțiile reale (`bias_label`, `STRENGTH_PCT_K`), nu doar algebră de mână —
vezi comanda din sesiune. Coverage per categorie (growth/inflation/labour/monetary)
confirmat identic before/after (5/3/3/1) — nimic altceva nu s-a mișcat structural:
aceleași ponderi (toate 1.0), aceleași celelalte 3 categorii, aceeași scală (5),
același K (10.5), aceiași thresholds (mild 1.12/very 2.41).

**Magnitudinea e explicabilă în întregime din substituția unui singur scor (-2→-1) în
categoria growth. Nimic altceva în lanț nu s-a schimbat.**

**Eticheta**: rămâne "**Neutral**" în ambele cazuri — utilizatorul vede doar o mișcare de
NUMĂR (index 0.333→0.583 pe /economic breakdown; pct 53.5→56.1 pe /strength), nu o
schimbare de etichetă vizibilă (Bearish→Neutral etc. nu se întâmplă aici, indexul e prea
departe de pragul mild=1.12 în ambele cazuri).

## PARTEA 2 — merge fix/gbp-gdp-frequency → main

### Obstacol neanticipat + rezolvare

`main` e checked-out live în worktree-ul principal (`macro-data-analysis`, cron activ) —
`git checkout main` din `macro-dev` a fost refuzat explicit de git
(`fatal: 'main' is already used by worktree`). Am construit merge-ul + render-all pe o
ramură de staging (`merge-staging/gbp-gdp-frequency`) în `macro-dev`, apoi am mutat `main`
prin `git merge --ff-only` rulat DIN worktree-ul principal (nu prin manipulare de ref din
altă parte — asta ar fi putut corupe starea cron-ului, vezi raționamentul din sesiune).

Pași urmați (conform confirmării tale):
1. **Cron check**: `com.po.econ-refresh` (launchd, orar) — `state = not running`,
   `last exit code = 0`, ultima rulare 09:37:26Z (~16 min înainte), următoarea ~10:37Z.
   Fereastră sigură confirmată înainte de fast-forward.
2. **Curățenie**: worktree-ul principal avea 0 modificări necomise pe fișiere TRACKED
   (`git status --porcelain` — doar linii `??`). Cele 17 fișiere untracked
   (`docs/aud-inflation-independence-check.md` etc.) sunt scratch pre-existent, prezente
   încă de la începutul acestei conversații întregi (nu produse de cron acum) — raportate,
   nu atinse.
3. **Fast-forward**: `git merge --ff-only merge-staging/gbp-gdp-frequency` — reușit curat,
   `cd947dd..8776d54`.
4. **`--mode render-all`** rulat în worktree-ul principal — confirmat: GBP
   `index=0.5833, pct=56.1, bias_label=Neutral` pe `/economic` și `/strength` (aceeași
   sursă, `data/economic.json`).

Conflictul pe `public/data/economic.json` (și celelalte `public/data/*.json` +
`public/index.html` + `public/archive/2026-08-11.html`) a apărut de fapt la pasul
intermediar (merge-ul lui `main` mai proaspăt ÎN ramura de staging, înainte de
fast-forward — `main` avansase cu ~6 commit-uri de cron cât timp lucram) — rezolvat exact
conform regulii: NU am ales o parte, am regenerat via `--mode render-all` din codul
merged + parquet-ul curent, de două ori (o dată pe staging, o dată din nou în worktree-ul
principal după fast-forward, pentru cele câteva minute scurse între timp).

### Stare după fast-forward — NEPUSH-uit, cum ai cerut

```
$ git log --oneline -6
8776d54 econ: render-all against merged parquet (post cron catch-up merge)
67b334a Merge newer main (cron econ-refresh/retail-snapshot) into staging before fast-forward
f89f678 econ: render-all after GBP gdp_qoq merge
228ef15 Merge fix/gbp-gdp-frequency into main: GBP gdp_qoq frequency_override (FAZA 1E/1F)
cd947dd econ: refresh 2026-08-21T09:37:26Z [ci]
4d86b66 retail: snapshot 2026-08-21T09:34Z

$ git status
On branch main
Your branch is ahead of 'origin/main' by 5 commits.
  (use "git push" to publish your local commits)
Changes not staged for commit:
	modified:   public/archive/2026-08-11.html
	modified:   public/data/economic.json      (etc. — 6 fișiere, doar timestamp
	                                             as_of, de la re-rularea render-all
                                                     din worktree-ul principal, NECOMISE)
Untracked files: (aceleași 17 fișiere scratch pre-existente)
```

**Suita**: aceleași 7 eșecuri pre-existente, nimic nou (verificat de 2 ori — o dată pe
staging, o dată pe `main` post-fast-forward).

**Aștept confirmarea ta pentru push** (și decizia ta pe cele 6 fișiere `public/`
necomise din worktree-ul principal — diferă doar prin timestamp `as_of`, nu prin conținut
de scor; le pot comite sau le pot lăsa nefăcute/le poți discard-ui cu
`git checkout -- public/`).

## PARTEA 3 — rebase feat/history-catalog peste main

`git rebase main` (nu merge) — 11 commit-uri reaplicate, istorie liniară confirmată
(`git log --graph` — niciun commit de merge pe ramura `feat/history-catalog` însăși).
2 din cele 11 commit-uri (cele care ating `public/` — pilotul FAZA 1C și extensia
FAZA 1D) au avut conflicte identice pe `public/data/*.json` + `public/index.html` +
`public/archive/2026-08-11.html` (aceleași fișiere generate, conflict cu commit-urile de
cron de pe `main`) — rezolvate prin placeholder în timpul rebase-ului, apoi regenerate
integral cu `--mode render-all` DUPĂ ce rebase-ul s-a terminat (un singur commit final,
`834693f`), nu hand-pick per pas intermediar.

### Re-verificare pe pagină

- **GBP growth, 29/39 scored**: confirmat direct din payload-ul randat —
  `window_options.max.n=43`, 38 printate, `Counter({'scored': 29, 'insufficient_history': 9})`.
  Coincide exact cu contra-verificarea informativă din FAZA 1E.
- **Badge-ul de cadență empirică pe GBP**: `cadence_empirical: "monthly"` — acum coincide
  cu `frequency_overrides.GBP: monthly`, deci badge-ul "M" de lângă chip nu mai semnalează
  un mismatch (înainte de fix, badge-ul arăta "M" în timp ce `frequency` declarată era
  "quarterly" — mismatch-ul era vizibil dar nefolositor fără fix; acum e doar informativ,
  coerent cu configul).
- **Restul categoriilor neschimbate**: `inflation=23, labor=15, growth=10, rates=7`
  entries — identic cu numărătoarea din FAZA 1D; `growth` omite tot JPY, `rates` omite tot
  CHF, ca înainte.
- **Non-regresie /economic + /strength**: `public/economic.html` și `public/strength.html`
  randate pe `feat/history-catalog` (post-rebase) sunt **byte-cu-byte identice** cu ce a
  produs `main` (post-merge) — `diff` gol pe amândouă. `public/data/economic.json`
  confirmă aceleași valori GBP (`index=0.5833, pct=56.1, bias_label=Neutral`).

Suita: 754 passed (vs 730 pe `fix/gbp-gdp-frequency` — diferența sunt testele proprii
`feat/history-catalog`: `test_history_compute.py`, `test_data_integrity.py`,
`test_history_js.py` etc.), aceleași 7 eșecuri pre-existente.

## PARTEA 4 — checklist pre-deploy

### 4.1 — render-all + navbar

Confirmat: toate cele 7 pagini (`index`, `economic`, `strength`, `history`, `pc-ratio`,
`vix-ratio`, `retail-sentiment`) au un navbar cu EXACT același set de linkuri
(`/economic`, `/strength`, `/history`, `/`, `/pc-ratio`, `/vix-ratio`) — clasa `active`
corect aplicată doar pe linkul paginii curente (verificat explicit: `history.html` are
`active` pe History, `economic.html` NU are `active` pe History). Exact bug-ul pe care
`render-all` (re-randarea TUTUROR paginilor într-un singur pas, în loc de fiecare pipeline
re-randând doar pagina proprie) există să-l previi: fără el, un mod parțial (`--mode
economic`, de exemplu) ar re-randa economic.html cu navbar-ul nou, dar `index.html`/
`vix-ratio.html`/etc. ar rămâne cu navbar-ul VECHI (fără linkul History) până la propria
lor rulare — inconsistență vizibilă între pagini.

### 4.2 — dimensiune public/

```
ÎNAINTE (main, fără history.html/history.js):  9.096.041 bytes  (8,67 MB)
DUPĂ    (+ history.html + history.js):         9.626.407 bytes  (9,18 MB)
DELTA:                                         +530.366 bytes   (+518 KB, +5,8%)
```
`history.html` singur: 504.014 bytes necomprimat, **35.880 bytes gzip** (~35 KB —
aproximativ ce transferă efectiv Vercel, care comprimă automat conținutul text).

### 4.3 — randare fără JS

Verificat empiric (Playwright, `javaScriptEnabled: false`), nu doar citit codul: navbar-ul
și header-ul/subtitlul paginii se randează normal (sunt HTML static din
`templates/history.html.j2`), dar **toată zona interactivă rămâne goală**: dropdown-ul de
valută fără opțiuni, niciun tab de categorie, niciun chip de serie, `<canvas>` complet gol
(nimic desenat). **Nu există niciun `<noscript>`** — utilizatorul NU vede un mesaj explicit
gen "activează JavaScript", vede doar o pagină parțial goală, fără eroare vizibilă și fără
explicație. E o lipsă reală, nu am reparat-o (nu a fost cerut în această fază) — pot
adăuga un `<noscript>` minim dacă vrei.

### 4.4 — deep-link pe Vercel static

`vercel.json`: `"cleanUrls": true` — `/history` servește `history.html`;
`/history.html` primește un 301 către `/history` (comportament standard Vercel,
documentat — query string-urile sunt păstrate de acest redirect, NU sunt atinse de
rutarea de fișiere statice în niciun caz). Partea pe care AM verificat-o empiric (server
local + Playwright, `?cat=growth&ccy=CAD&range=2y`): `history.js`'s
`stateFromUrl()`/`URLSearchParams` citește corect parametrii indiferent de calea prin
care s-a ajuns la pagină — tab Growth activ, `CAD` selectat, fereastra `2Y (25)` activă,
zero erori în consolă. Partea pe care NU am putut-o verifica (ar necesita un deploy Vercel
real, în afara scope-ului "zero deploy"): comportamentul EXACT al redirect-ului 301 pentru
varianta `.html` explicită — mă bazez pe comportamentul documentat Vercel (păstrează query
string), nu pe un test live.

### 4.5 — cronul regenerează pagina nouă?

**NU. Niciunul din cele 4 workflow-uri GitHub Actions nu randează sau comite
`/history.html` azi:**

```
econ-refresh.yml (orar, 5 * * * 1-5 + 5 */4 * * 0,6):
    python -m src.main --mode economic   -> DOAR render_economic_page() + render_strength_page()
    git add: public/economic.html public/data/economic.json   (listă explicită,
             NU include history.html, NU include strength.html, NU un glob public/)

daily.yml:    --mode daily    -> VIX/PC/retail, fără history
retail.yml:   --mode retail   -> doar retail sentiment
weekly.yml:   python -m src.main (implicit --mode weekly) -> render_dashboard() (COT)
              git add public/ (glob larg, dar nimic scrie history.html în acest mod,
              deci n-are ce să prindă nou)
```

Cron-ul local (launchd, `com.po.econ-refresh.plist`, orar) face DOAR `git pull
--rebase --autostash` — nu randează, nu comite nimic (partea MT5/price e dezactivată).

**Concluzie**: odată mers pe `main`, `/history.html` NU se va actualiza NICIODATĂ automat
— rămâne exact la conținutul din ultimul commit manual (`git ... --mode render-all`),
divergând tot mai mult de `/economic` (care SE actualizează orar) pe măsură ce trece
timpul. E o gaură operațională reală, nu ipotetică — merită o decizie explicită a ta
înainte de deploy (adaug `render_history_page()` la pasul `--mode economic`? un workflow
nou, mai rar? las manual?) — nu am modificat niciun workflow, doar raportez.

### 4.6 — comportament la stale / cheie de catalog dispărută

**Stale**: `history_compute.py` apelează `compute_indicator_score` mereu cu
`allow_stale=True` și **nu există niciun câmp `stale` în payload-ul per-punct** (spre
deosebire de `/economic`, unde `stale=True` afectează atât afișarea cât și coverage-ul).
O serie care n-a mai printat de luni de zile arată identic — aceeași culoare, același
`score_status` — ca una proaspătă. Combinat cu 4.5 (nimic nu re-randează automat), riscul
compus e real: pagina poate afișa date vechi de săptămâni, TĂCUT, fără niciun semnal
vizual.

**Cheie de catalog dispărută**: verificat empiric (nu doar citit codul) — am injectat un
`indicator_key` inventat, inexistent, direct în catalog și am rulat pipeline-ul complet:
**zero crash**. Rezultatul: `cadence_empirical: "unknown"`, `window_options: {}` —
randează exact ca mesajul explicit "No data" deja construit pentru CHF/rates și
JPY/growth, INDISTINCTIBIL de un caz legitim de serie omisă. Nimic nu se loghează, nicio
eroare. Deci: **eșec tăcut, nu zgomotos** — nu sparge pagina, dar nici nu te avertizează
că e o greșeală de config (typo) și nu o serie reală lipsă. `load_catalog()` nu are nicio
validare de schemă împotriva `economic_indicators.yaml`.

## Ce a rămas incert / decizii care îți revin

1. **4.5**: nimic nu re-randează `/history.html` automat — decizie explicită necesară
   înainte de deploy.
2. **4.6**: nicio validare de schemă pe catalog, niciun semnal de staleness pe `/history`
   — aș putea adăuga ambele (test de validare la nivel de catalog, câmp `stale`/badge de
   vârstă per serie) dar sunt schimbări de cod, nu de config, și nu au fost cerute în
   această fază.
3. **4.3**: pagină fără `<noscript>` — gap real, ușor de completat dacă vrei.
4. **4.4**: comportamentul exact al redirect-ului Vercel pe URL-ul `.html` explicit nu a
   fost testat live (ar necesita deploy).
5. Cele 6 fișiere `public/*` necomise din worktree-ul principal (Partea 2) — aștept
   decizia ta (comit / discard).

STOP înainte de merge-ul paginii `/history` în `main`. Fixul GBP e gata de push (aștept
confirmarea ta). Pagina rămâne pe `feat/history-catalog`, nemerge-uită, pentru revizuirea
ta vizuală.
