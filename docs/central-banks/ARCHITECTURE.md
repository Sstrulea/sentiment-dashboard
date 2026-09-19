# /central-banks — arhitectură (contractul fazelor 1A → 4)

Stare: **FAZA 1A** (fundație + colector de piață în CI). Documentul conține doar deciziile date pentru proiect;
ce lipsește este în [Întrebări deschise](#10-întrebări-deschise) — nu s-a inventat nicio decizie. Dovezile din spike:
`docs/spikes/cb_sources_numeric.md` (0A), `docs/spikes/cb_sources_text.md` (0B).

Bănci: Fed/USD, ECB/EUR, BoE/GBP, BoJ/JPY, BoC/CAD, RBA/AUD, RBNZ/NZD, SNB/CHF.

## 1. Lentile

- **Traiectoria băncii** — există doar la Fed (dot plot / SEP) și RBNZ (OCR track).
- **Prețuirea pieței** — traiectoria implicită de piață.
- Între ele: **GAP** (banca vs. piață).

## 2. Ce conține v1

- redline al comunicatului;
- voturi / disidențe;
- surpriza deciziei: vs. consensul FF și vs. piața la T−1;
- repricing 1s / 1l;
- calendar: ședințe, countdown, blackout, proiecții / conferință;
- vedere pe pereche base−quote.

## 3. Text, conferință, AI

- Text: **doar surse oficiale**; ultimele **4 ședințe / bancă**; latență țintă **≤ 15 min** prin trigger extern (faza 4).
- Conferința: video + transcript oficial unde există + rezumat factual. **Fără transcriere proprie în v1.**
- AI: **doar pe text** (rezumate, citate), **fără niciun verdict de direcție**.
- Orice citat există **verbatim** în sursă, iar rezumatul **nu conține numere absente din sursă**. Ambele se verifică
  automat.

## 4. Direcția și afișarea

- Direcția = **reacția pieței**: Δ bps, EOD T vs. T−1.
- UI în **engleză**. Navigație „Central Banks” lângă Carry.
- `/central-banks`: tablou cu cele 8 bănci — rata, următoarea ședință + countdown, Δ implicit la următoarea ședință,
  bps la ultima ședință din 2026 / 2027, GAP, repricing, reacția la ultima decizie.
- `/central-banks/<bank>`: sus traiectoria (istoric + bancă + piață); în ziua ședinței urcă decizia + conferința.
- Cross-link din celula RATE EXP (2Y) de pe `/economic`.
- ECB = DFR. „Anul” = cumulat la ultima ședință din an.
- **Display-only.** Orice intrare în scor = experiment separat în `measure/`.

## 5. Reguli de date

1. Fiecare valoare are **sursă + as-of + stale**.
2. **Zero real ≠ lipsă** (CHF 0.00 este o valoare).
3. **FF este doar fallback**, validat pe ședința oficială; potrivirea FF se face pe **(valută, nume)**.
4. Spread-ul overnight–politică este **specific benchmark-ului instrumentului** (nu unul global).
5. Proxy-urile sunt marcate **PROXY**. **RBNZ este manual.**
6. Rata de politică: **modulul CB devine sursa unică**. Carry migrează după o rulare în paralel cu
   `data/policy_rates.yaml` (diff 0).
7. Plătit: respins. La final: repo privat + poarta `middleware.js`.

## 6. Structură și scriitor unic

- `src/cb_sources/` (I/O), `src/cb_compute/` (pur), `src/cb_render.py`.
- Date în `data/cb/`; JSON în `public/data/cb/` (overview + un fișier per bancă, încărcat la cerere).
- Pipeline separat `cb-refresh.yml`; **nu atinge `/economic`**.
- **Scriitor unic**: după merge, `data/cb/` este scris doar de CI. Pe branch nu se mai comit modificări în `data/cb/`;
  pentru date noi, se face merge main în branch. Singura excepție: backfill-ul din 1A.

## 7. Faze

| fază | conținut |
|---|---|
| **1A** | fundație + colector în CI (acest document; config, adaptoare de piață, `cb_collect`, `cb-refresh.yml`) |
| **1B** | rate, decizii, proiecții + calcul: traiectorie pe ședință, cumulat, GAP, repricing, surprize, pereche |
| **2** | texte + rezumate |
| **3** | UI |
| **4** | trigger extern (≤ 15 min) + verificări finale |

## 8. Ce există după 1A

```
config/central_banks.yaml            8 bănci: ora deciziei, conferința, definiția ratei, benchmark overnight,
                                     regula datei efective (+sursă / „neverificat”), proiecții, YouTube, blackout
config/cb_sources.yaml               10 surse de piață: URL, istoric official|snapshot, convenția de denumire,
                                     eod_cutoff, politica de descărcare, licență
data/cb/meetings.yaml                ședințe 2026–2027 (dată, has_projections, has_presser, source, verified)
data/cb/market_quotes.parquet        cotațiile (schema mai jos)
data/cb/state.json                   validatori ETag / Last-Modified per sursă
data/cb/raw/{source}/{asof}.csv.gz   raw minim pentru sursele snapshot (JPX, MX, ASX)
src/cb_sources/{base,market}.py      contract + 10 adaptoare
src/cb_collect.py                    colectorul  (python -m src.cb_collect [--backfill] [--source ID] [--status])
.github/workflows/cb-refresh.yml     workflow_dispatch + cron `37 */2 * * *`
```

**Contractul adaptorului** (RateSource-like, HTTP din `src.rate_sources.BaseSource`):
`fetch(since, state) → FetchResult | None`; niciodată excepții — la eșec `None` + `last_status` / `last_note`
(`BOT-WALL`, `UNREACHABLE`, `PARSE-FAIL`). Valoarea este cea publicată (preț sau rată) + unitatea; conversia
preț→rată se face în compute. Fereastra de referință e explicită (`ref_start`, `ref_end` exclusiv) sau tenorul
(`tenor_months`); orizont ≤ 36 luni. Convenția bursei se rezolvă în adaptor: JPX / MX = luna de start a perioadei de
referință, ASX BB = luna de decontare. Fără ajustări de spread (vin în 1B). MX nu are as-of: se deduce din ora
fetch-ului + cutoff, cu `asof_inferred = true`.

**`market_quotes.parquet`** — coloane stabile: `source, currency, instrument, contract, field, ref_start, ref_end,
tenor_months, value, unit, asof, asof_inferred, fetched_at (UTC)`. Append-only; cheie
`(source, instrument, contract, field, asof)`; last-write-wins pe aceeași cheie. O cotație cu as-of = ziua curentă a
bursei se înregistrează doar după `eod_cutoff`. Istoricul nu se șterge; o sursă căzută = log + skip; exit 0 la eșec
parțial, non-zero doar dacă toate sursele eșuează. Backfill de la 2026-03-01 pentru sursele cu istoric oficial (MPT,
BoE OIS, Treasury, BoC T-bills, ECB AAA, RBA bank bills), ca să existe piața T−1 la ultimele 4 ședințe.
`--status` afișează ultima scriere, as-of și lag per sursă, zilele lipsă; în CI scrie și în `$GITHUB_STEP_SUMMARY`.

### Note de implementare 1A (alegeri de execuție, nu decizii de produs)

- Un rând neschimbat păstrează `fetched_at`-ul original → rulările fără date noi nu produc diff (fișierele sunt
  identice bit cu bit). „Ultima scriere” din `--status` = cel mai nou `fetched_at`, deci ultima dată când o sursă a adus
  ceva nou sau schimbat; ultima *interogare* fără schimbări apare doar în Actions / `$GITHUB_STEP_SUMMARY`.
- Validatorii se avansează doar după ce datele lor sunt pe disc și doar dacă cutoff-ul nu a reținut rânduri din
  răspuns (altfel următoarea rulare ar primi 304 și ar pierde cotația reținută).
- Backfill = descărcare completă (fără validatori) și doar pentru sursele cu istoric oficial; sursele snapshot nu au
  ce backfill-ui.
- `cb-refresh.yml` face commit + push doar pe `main`; un dispatch de pe alt ref colectează și raportează, fără push.
- Din MX se colectează doar CRA + COA; „Actual CORRA Rate” de pe pagină nu (data valorii nu se poate verifica; CORRA
  oficial vine din Valet, `AVG.INTWO`).
- Erată A5 (față de raportul 0A): în `meetings.yaml`, BoE 2027-12-16 este corectat de mână la fără MPR / fără
  conferință — proba A5 îl citise ca zi MPR din footerul paginii (pagina listează doar „December MPC Summary and
  minutes”). `cb_probe.py` rămâne neatins.

## 9. Ce nu face 1A

Fără calcul (`cb_compute`), fără texte / rezumate, fără UI, fără trigger extern, fără modificări la `/economic` sau
`/carry`.

## 10. Întrebări deschise

Lipsesc din deciziile primite; fiecare are o recomandare doar acolo unde există evidență măsurată.

1. **Aspectul fișierului de cotații (de decis înainte de merge).** Un singur `market_quotes.parquet` se rescrie
   integral la fiecare commit CI, iar Git păstrează fiecare versiune. Simulare cu datele reale (838 de commituri =
   ~6 / zi × 139 zile de backfill, doar cele 6 surse cu istoric oficial = 98 din cele ~156 rânduri / zi; cu toate 10
   sursele cifrele cresc cu ~1,6×): **36,1 MiB** de istoric împachetat după 6,5 luni; creștere pătratică
   ⇒ ≈ 120 MiB după 1 an, ≈ 470 MiB după 2, ≈ 1 GB după 3. Partiționat lunar
   (`market_quotes_YYYY-MM.parquet`, scriitorul rescrie doar luna curentă): **7,1 MiB** pentru aceeași perioadă,
   liniar (≈ 13 MiB / an). Recomandare: partiționare lunară, aceleași coloane și aceeași cheie. Schimbarea afectează
   doar `load_store` / `write_store`. Dacă rămâne un fișier unic, trebuie hotărât un plan de retenție / compactare.
2. **„Reacția la ultima decizie”** — pe ce instrument / tenor / fereastră se măsoară Δ bps EOD T vs. T−1, și ce se
   afișează unde piața are doar proxy (EUR: AAA guvernamental, USD/CAD/AUD: bills / bank bills) sau nimic
   (CHF: fără sursă numerică; NZD: doar BKBM 90 zile, fără spread față de OCR).
3. **Afișarea USD**: interval (limita de jos / de sus) sau punct la mijloc? Configul folosește `midpoint` pentru
   calcul; UI nu e specificat.
4. **Licențe și atribuire.** Atlanta Fed MPT: „personal and educational purposes only”; BoE OIS: paginile citează
   Bloomberg; termenii JPX / MX / ASX neverificați (`license` din `cb_sources.yaml`). Nu e clar dacă „repo privat +
   poartă middleware” acoperă afișarea; formularea atribuirii în UI nu e stabilită.
5. **Calendare de sărbători.** MX nu are as-of și nu distinge zilele libere (ar înregistra prețuri vechi cu as-of de
   sărbătoare); `--status` numără sărbătorile bursei ca „zile lipsă”; regula BoJ „următoarea zi lucrătoare JP”
   are nevoie de o sursă pentru calendarul JP (în spike: 21–23 sep 2026 sărbători).
6. **Cine reîmprospătează `meetings.yaml`.** Acum e un snapshot din probele A5 (ECB 2026 trecut = din FF, `ff`,
   neverificat; BoE 2027 = provizoriu; RBNZ manual până la 17 feb 2027). Nu există `first_day` pentru ședințele de
   două zile (Fed, BoJ, RBA), necesar la calculul blackout-ului — nu era în câmpurile cerute.
7. **RBNZ manual**: cine și cât de des actualizează rata (OCR) și ședințele, cât timp site-ul e în spatele
   Cloudflare.
8. **Sursele snapshot (JPX, MX, ASX)** au istoric doar de la prima rulare; între spike și merge nu există colectare,
   deci acele zile lipsesc definitiv.
9. **Migrarea Carry**: în ce fază se face rularea în paralel cu `policy_rates.yaml` și cine o validează (diff 0).
10. **Faza 1B — convenții de calcul**: conversia preț→rată per instrument (JPX 3M TONA, MX CRA / COA, ASX IB / BB),
    alegerea între bank bills și seriile OIS din RBA F1 (`FIRMMOIS1D/3D/6D`, colectate nicăieri acum), tratamentul
    MPT (bp, medie a distribuției) vs. futures.
11. **Faza 2 — AI**: furnizor / model, buget, unde se stochează textele și rezumatele, ce înseamnă „ultimele 4
    ședințe” după ce backfill-ul acoperă doar din 2026-03-01.
12. **Faza 4 — trigger extern**: cine face dispatch-ul (repo-ul are deja `api/manual-actual.py` care dispatch-uiază
    `econ-refresh.yml`), cu ce secret, și cum se reîncearcă dacă cade.
13. **Minute GitHub Actions după trecerea repo-ului în privat.** Planul gratuit are o cotă lunară pentru repo-uri
    private; `econ-refresh` rulează deja orar, `cb-refresh` adaugă 12 rulări / zi + triggerul din faza 4. „Plătit:
    respins” impune un buget de minute care nu e calculat.
14. **Slug-urile URL** pentru `/central-banks/<bank>` (cod valutar, id bancă?) și textul navigației în afara „Central
    Banks”.
15. **Cross-link RATE EXP (2Y)**: ce celule / valute trimit la ce pagină de bancă (EUR și CHF nu au traiectorie de
    piață comparabilă).
