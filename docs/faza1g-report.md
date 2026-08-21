# FAZA 1G — Închide golurile operaționale (4.3, 4.5, 4.6)

Worktree `../macro-dev`. `main` (primary worktree, remote) e la `28630ef`, push-uit.
`feat/history-catalog` rebazat pe el, la HEAD-ul acestei faze.

## PARTEA 1 — push fix GBP

`git push origin main` a fost respins inițial (`! [rejected] ... fetch first`) —
GitHub Actions' `econ-refresh.yml` pushese un commit nou (`087d9f4`, refresh la
10:32:03Z) cât timp lucram, independent de cron-ul local (care doar face `pull`,
nu `push`). Rezolvat exact ca scriptul propriu al proiectului
(`scripts/econ_refresh.sh`): `git pull --rebase --autostash origin main`, conflict
pe `public/data/*.json` (aceleași fișiere single-line JSON, motiv identic ca de
fiecare dată) — rezolvat prin regenerare (`--mode render-all`), NU prin alegerea
unei părți. Verificat GBP încă o dată pe rezultatul final (`index=0.5833,
pct=56.1, bias_label=Neutral`), suita (aceleași 7 eșecuri), apoi push.

```
$ git push origin main
   087d9f4..28630ef  main -> main
$ git fetch origin main && git log --oneline -1 origin/main
28630ef ...
```

**Confirmat**: `main` local == `origin/main` == `28630ef`, fără divergență.
Următoarea rulare de cron (GitHub Actions, checkout proaspăt de fiecare dată —
neafectat de nimic local; cron-ul local, doar `pull --rebase --autostash`, aplicat
peste un `main` deja sincron) nu are ce conflict să întâlnească.

`feat/history-catalog` a necesitat un al doilea rebase (istoria lui `main` s-a
rescris prin `pull --rebase` înainte de push — hash-uri noi, conținut identic).

## PARTEA 2 — 4.5: cronul acum regenerează /history.html

### 2.1 — ce rula și de ce nu prindea history

`econ-refresh.yml` (orar): pasul de randare rulează `python -m src.main --mode
economic`, care apela DOAR `render_economic_page()` + `render_strength_page()`
(`src/main.py::_economic()`) — niciodată `render_history_page()`. Chiar dacă ar
fi randat-o local, pasul de commit avea o listă EXPLICITĂ de fișiere
(`public/economic.html public/data/economic.json`), fără `public/history.html` —
deci n-ar fi fost comisă/push-uită oricum.

### 2.2 — fix + timp măsurat

Măsurat izolat (nu doar estimat): `render_history_page()` singur, ~5.8s. Adăugat
în lanțul `--mode economic` (economic + strength + history secvențial): timpul
total al comenzii CLI a crescut de la ~2,2s la ~8,1s — **delta ~5,9s**, sub
pragul de 30s. Integrat direct, fără variante alternative.

```python
# src/main.py::_economic()
try:
    from .history_render import render_history_page
    history_out = render_history_page()
    log.info("History page rendered → %s", history_out)
except Exception as e:
    log.error("History render failed: %s", e)
    return 2
```

`.github/workflows/econ-refresh.yml`: `public/history.html` adăugat la lista de
`git add` (alături de `economic.html`/`economic.json`; `strength.html` rămâne
absent din listă intenționat — e un shell static fără date îmbibate, nu se
schimbă niciodată la o rulare normală, spre deosebire de `history.html` care
ÎMBIBĂ payload-ul direct).

Verificat direct: `rm public/history.html && python -m src.main --mode economic`
→ fișierul reapare, cu date curente.

### 2.3 — stamp vizibil "As of"

`templates/history.html.j2`: `<div class="econ-meta-bar" id="historyMeta">` —
aceeași clasă CSS ca bara `/economic`'s (`#econMeta`), deci identică vizual, zero
CSS nou. `static/history.js::renderMeta()` citește `payload.meta.as_of` /
`generated_at` — EXACT sursa/formatul folosit de `economic-chart.js::renderMeta()`
(`fmtAsOf`, "YYYY-MM-DD HH:MM UTC"), portat 1:1. Randează: "As of 2026-08-21 14:05
UTC · Generated 2026-08-21 14:05 UTC" — verificat vizual (Playwright).

## PARTEA 3 — 4.6: degradare tăcută

### 3.1 — log zgomotos + raport de sănătate, la generare

`history_compute.build_payload()` (nou parametru `ind_cfg`, propagat din
`history_render.build_history_payload()`) calculează, pentru fiecare intrare de
catalog (o singură dată per serie reală, nu per rol — `points_ref` moștenește
aceleași flaguri):
- `has_data` — cel puțin un print real există vreodată pentru (currency,
  indicator_key)?
- `stale` — ultimul print real e mai vechi decât `max_age` (reutilizat DIRECT din
  `economic_compute.effective_frequency`/`_max_age_for`, nu re-derivat).

Când oricare eșuează: `logging.warning(...)` imediat (vizibil în orice output de
cron/CI) + o intrare într-un `payload["health"] = {"no_data": [...], "stale":
[...]}` nou. Nu crash — pagina se randează normal, `window_options` rămâne gol
pentru o intrare fără date, exact calea grațioasă deja existentă.

Verificat end-to-end (injectat temporar o cheie inexistentă în
`data/econ_catalog.yml`, randat, restaurat):
```
WARNING [src.history_compute] history catalog: USD/totally_made_up_key_faza1g_smoketest
  (SMOKE TEST — DELETE ME, role=tertiary) has ZERO real prints — typo'd
  indicator_key, matcher regression, or a series that should have been removed
  from data/econ_catalog.yml.
WARNING [src.history_compute] history catalog health: 1 no-data + 0 stale series
  (see entries above).
```
Catalogul REAL de azi: `health = {"no_data": [], "stale": []}` — curat.

### 3.2 — marcaj explicit în UI

`templates/history.html.j2`: `<p class="history-stale-banner" id="historyStaleBanner"
hidden>` deasupra strip-ului. `static/history.js::renderStaleBanner(meta)`:
- `!meta.has_data` → "No data: this catalog entry has zero real prints — likely a
  config/matcher issue, not a legitimately quiet series."
- `meta.stale` → "Last print: `<date>` (`<N>`d ago) — this series appears to have
  stopped printing."
- altfel: ascuns.

Culoare `--warning-bg`/`--warning-fg` (deja existente în paletă, reutilizate).
Verificat vizual (Playwright, mutând un entry în `window.HISTORY_PAYLOAD` — care e
ACELAȘI obiect ca `state.payload` intern, deci mutația e vizibilă fără niciun hook
nou — și declanșând un re-render prin click pe un buton de fereastră): ambele
bannere randează corect, fără erori de consolă.

### 3.3 — integrare în verify_data.py

Extins Check 3 (STALE FLAGS) — "silent staleness made visible", exact potrivirea
conceptuală — cu un sub-check nou `[3b] HISTORY CATALOG HEALTH`, care citește
`payload["health"]` direct din `public/history.html` (regex-extrage
`window.HISTORY_PAYLOAD`, aceeași tehnică folosită peste tot în această sesiune —
nu există `public/data/history.json` separat, payload-ul e îmbibat, per design).

Diferență deliberată de severitate față de stale-urile `/economic` (informative):
- `no_data` → **FAIL** (crește `failures`, exit code 1) — un catalog referă o
  cheie fără date e o eroare de config, nu o chestiune de disponibilitate.
- `stale` → informativ, la fel ca stale-urile `/economic` existente.

Testat end-to-end, de două ori:
```
# catalog curat azi:
[3b] HISTORY CATALOG HEALTH (public/history.html — payload['health'])
  NO-DATA — none. Every catalog entry resolves to at least one real print.
  STALE   — none. Every catalog entry's latest real print is within its recency window.

# cu cheia inventată injectată temporar:
[3b] HISTORY CATALOG HEALTH (public/history.html — payload['health'])
  NO-DATA — 1 catalog entr(y/ies) with ZERO real prints ...
    USD totally_made_up_key_faza1g_smoketest (inflation) — SMOKE TEST — DELETE ME
  FAIL — a broken catalog entry is not a data-availability question, it's a config bug.
```
Confirmat: fără schimbările mele, `verify_data.py` arăta deja "❌ 1 CHECK(S)
FAILED" (Check 2, drift MT5 CSV vs pipeline FF — preexistent, neatins de mine,
nu am investigat mai departe, în afara scopului acestei faze). Cu schimbările
mele, pe catalogul curat de azi: tot "❌ 1 CHECK(S) FAILED" — checkul nou adaugă
zero eșecuri noi când catalogul e sănătos, exact cum trebuie.

## PARTEA 4 — 4.3: fără JS

`templates/history.html.j2`: `<noscript><p class="history-empty-category">This
page needs JavaScript to render the chart and controls — please enable it and
reload.</p></noscript>` — plasat imediat sub header, vizibil indiferent de restul
paginii. Verificat (Playwright, `javaScriptEnabled: false`): mesajul apare clar,
în plus față de navbar/header/subtitle (care erau deja statice). Fără fallback
server-side, cum ai cerut — doar mesajul.

## Non-regresie

- `./.venv/bin/python -m pytest -q` → **757 passed** (754 + 3 teste noi pentru
  health/stale/no_data), **aceleași 7 eșecuri pre-existente**, nimic nou.
- `test_non_regression_economic_and_strength_output_unchanged` (verifică
  `/economic`+`/strength` neschimbate) — verde.
- 3 teste noi în `tests/test_history_compute.py`: no_data flag + health,
  stale flag + health, serie proaspătă = niciun flag.
- `tests/history_js/test_resolve_entry.js` — încă verde (nicio schimbare la
  `resolveEntry`).

## Fișiere schimbate

```
src/main.py                       — history_render în --mode economic
src/history_compute.py            — has_data/stale + payload["health"], log zgomotos
src/history_render.py             — ind_cfg propagat spre build_payload
templates/history.html.j2         — meta bar, noscript, stale banner
static/history.js                 — renderMeta(), renderStaleBanner()
static/style.css                  — .history-stale-banner
.github/workflows/econ-refresh.yml — public/history.html în git add
scripts/verify_data.py            — check [3b] HISTORY CATALOG HEALTH
tests/test_history_compute.py     — 3 teste noi
```

STOP. Pagina tot nu se merge-uiește în `main` — rămâne pe `feat/history-catalog`
pentru revizuirea ta vizuală.
