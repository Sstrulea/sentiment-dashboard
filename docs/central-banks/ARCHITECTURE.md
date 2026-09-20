# /central-banks — arhitectură (contractul fazelor 1A → 4)

Stare: **FAZA 1B-1** (date oficiale: rate, benchmark-uri overnight, decizii, proiecții, calendare). Documentul conține
doar deciziile date pentru proiect; ce lipsește este în [Întrebări deschise](#11-întrebări-deschise-noi-1b-1) — nu s-a
inventat nicio decizie. Dovezile din spike: `docs/spikes/cb_sources_numeric.md` (0A), `docs/spikes/cb_sources_text.md` (0B).

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
- AI: **doar pe text** (rezumate, citate), **fără niciun verdict de direcție**. Model: **`claude-sonnet-5`**, fixat în
  config; cheia devine secret GitHub în faza 2.
- Orice citat există **verbatim** în sursă, iar rezumatul **nu conține numere absente din sursă**. Ambele se verifică
  automat.

## 4. Direcția și afișarea

- Direcția = **reacția pieței**. **Reacția la decizie = Δ(EOD T − EOD T−1), în bp, a traiectoriei implicite**, măsurată în
  două puncte: (a) la **următoarea ședință**; (b) la **ultima ședință a anului curent** (a anului următor, dacă decizia a
  fost ultima din an). T = ziua deciziei; toate deciziile au loc înainte de închiderea pieței instrumentului; T−1 = ziua
  lucrătoare precedentă în calendarul sursei. Flag-urile metodei se moștenesc (EXACT / UPPER_BOUND / PROXY). CHF = n/a.
- UI în **engleză**. Navigație „Central Banks” lângă Carry. USD: **etichetă de interval** în UI, **midpoint doar în calcul**.
- `/central-banks`: tablou cu cele 8 bănci — rata, următoarea ședință + countdown, Δ implicit la următoarea ședință,
  bps la ultima ședință din 2026 / 2027, GAP, repricing, reacția la ultima decizie.
- `/central-banks/<bank>` (`/central-banks/usd` etc.): sus traiectoria (istoric + bancă + piață); în ziua ședinței urcă
  decizia + conferința. Perechea: `/central-banks/pair/eurusd`.
- Cross-link: rândurile de pereche de pe `/economic` duc la vederea de pereche, rândul DXY la `/central-banks/usd`;
  se implementează în faza 3, fără să strice click-ul pe rând.
- ECB = DFR. „Anul” = cumulat la ultima ședință din an.
- **Display-only.** Orice intrare în scor = experiment separat în `measure/`.

## 5. Reguli de date

1. Fiecare valoare are **sursă + as-of + stale**.
2. **Zero real ≠ lipsă** (CHF 0.00 este o valoare).
3. **FF este doar fallback**, validat pe ședința oficială; potrivirea FF se face pe **(valută, nume)**.
4. Spread-ul overnight–politică este **specific benchmark-ului instrumentului** (nu unul global). **Spread = mediana
   (benchmark − rata de politică) pe ultimele 20 de zile lucrătoare, excluzând ±2 zile în jurul datelor efective și
   capetele de lună / trimestru.** Valoarea și fereastra apar în metodologie.
5. Proxy-urile sunt marcate **PROXY**. **RBNZ: decizia e automată** (FF validat + BIS); manuale rămân doar OCR track-ul (la
   fiecare MPS) și calendarul 2027, în `data/cb/manual/rbnz.yaml`; `--status` avertizează când apare un MPS nou fără track.
6. Rata de politică: **modulul CB devine sursa unică**. Comparația cu `data/policy_rates.yaml` (Carry) apare în `--status`
   din 1B-1; **migrarea Carry e un pas separat**, după ≥ 1 ședință cu diff 0.
7. **Conversii:** rata implicită dintr-un futures = **100 − preț**; MPT rămâne în **bp**.
8. **AUD primar = ASX IB** (1M, lanț pe ședință); RBA bank bills = doar cross-check / PROXY. Seriile OIS din RBA F1 sunt
   oprite din 2022-12 și **nu se colectează**.
9. Plătit: respins. La final: repo privat + poarta `middleware.js`. Tot afișajul rămâne în spatele porții; nimic public
   fără permisiune scrisă a furnizorilor (Atlanta Fed / CME, BoE, ASX, MX, JPX); UI afișează atribuirea din câmpul
   `license` al fiecărei surse.

## 6. Structură și scriitor unic

- `src/cb_sources/` (I/O), `src/cb_compute/` (pur), `src/cb_render.py`.
- Date în `data/cb/`; JSON în `public/data/cb/` (overview + un fișier per bancă, încărcat la cerere).
- Pipeline separat `cb-refresh.yml`; **nu atinge `/economic`**.
- **Scriitor unic**: după merge, `data/cb/` este scris doar de CI (excepție: `data/cb/manual/`, editat de mână, și
  `meetings.yaml`, generat la cerere). Pe branch nu se mai comit modificări în `data/cb/market_quotes/`; seed-urile
  dataset-urilor noi se comit separat, ca backfill-ul din 1A.

## 7. Faze

| fază | conținut |
|---|---|
| **1A** | fundație + colector în CI (config, adaptoare de piață, `cb_collect`, `cb-refresh.yml`) |
| **1B-1** | date oficiale: calendare, serii oficiale, ședințe (first_day, blackout), decizii, proiecții Fed, RBNZ manual, verificare săptămânală a calendarului |
| **1B-2** | calcul: traiectorie pe ședință, cumulat, GAP, repricing, surprize față de piață, pereche |
| **2** | texte + rezumate |
| **3** | UI |
| **4** | trigger extern + verificări finale: **scheduler extern Cloudflare Worker (cron `*/5`, cod în repo) → `workflow_dispatch`**, 3 reîncercări cu backoff, alertă la eșec (Vercel Cron pe Hobby rulează o dată pe zi) |

## 8. Ce există după 1B-1

```
config/central_banks.yaml            8 bănci: ora deciziei, conferința, rata, benchmark overnight, regula datei efective,
                                     proiecții, blackout (text + `blackout_rule` calculabil), calendar_id, serii oficiale, ff_name
config/cb_sources.yaml               10 surse de piață (+ calendar_id)
config/cb_official.yaml              18 serii oficiale (FRED, ECB, BoE, BoJ, BoC, RBA, BIS, SNB)
config/cb_calendars.yaml             calendare de sărbători US, UK, EU (TARGET), JP, CA, AU, NZ, CH, 2026-2027, sursă pe fiecare dată
data/cb/meetings.yaml                ședințe 2026-2027 (+ SNB din 2025-09): dată, first_day, has_projections, has_presser, source, verified
data/cb/market_quotes/               cotații de piață, partiții lunare (1A)
data/cb/official_series/             serii oficiale, partiții lunare `official_series_YYYY-MM.parquet`, cheie (series_id, date)
data/cb/decisions.parquet            o decizie pe (currency, meeting_date); se schimbă doar la ședințe
data/cb/projections.parquet          Fed SEP: mediane (Tabelul 1), toate dot-urile, mediana recalculată din dot-uri
data/cb/manual/rbnz.yaml             OCR track pe MPS + calendarul 2027 (manual)
data/cb/state.json                   validatori ETag / Last-Modified, zile „fără valori noi” (MX), rezultatul verificării săptămânale
data/cb/raw/{source}/{asof}.csv.gz   raw minim pentru sursele snapshot (JPX, MX, ASX)
src/cb_calendar.py                   (pur) zile lucrătoare, data efectivă, ferestre de blackout
src/cb_compute/decisions.py          (pur) deciziile: precedență, validare FF, consens, surpriză
src/cb_store.py                      stocare deterministă: partiții lunare + fișiere unice
src/cb_datasets.py                   lipiciul I/O: decizii, proiecții, RBNZ, verificare calendar, secțiunile --status
src/cb_sources/{base,market,official,fed_sep,calendar}.py
src/cb_collect.py                    python -m src.cb_collect [--stage S] [--backfill] [--status] ...
scripts/cb_gen_calendars.py          generatorul lui cb_calendars.yaml (UK și JP live, restul din tabele cu sursă)
.github/workflows/cb-refresh.yml     workflow_dispatch + cron `37 */2 * * *`
```

**`cb_collect`** rulează, în ordine, etapele `market`, `official`, `decisions`, `projections`, `calendar` (`--stage` alege).
Doar eșecul tuturor surselor de descărcat (piață + oficiale) dă exit ≠ 0; deciziile, proiecțiile și verificarea
calendarului avertizează, nu blochează. `--backfill` folosește data proprie fiecărui set (piață 2026-03-01; oficiale
2025-09-01, ca să acopere ultimele 4 ședințe SNB).

**Stocare** (piață și serii oficiale): partiții lunare pe luna as-of-ului / datei; cheie unică global; `load_store` citește
toate partițiile sau un interval; se rescriu doar partițiile schimbate, determinist (rânduri sortate pe cheie, setări fixe
de writer, `pyarrow` fixat): același conținut ⇒ aceiași octeți. Un rând neschimbat păstrează `fetched_at`.

**Contractul adaptorului** (RateSource-like, HTTP din `src.rate_sources.BaseSource`): niciodată excepții — la eșec `None` +
`last_status` / `last_note` (`BOT-WALL`, `UNREACHABLE`, `PARSE-FAIL`). Piață: valoarea publicată + unitatea; conversia
preț→rată se face în compute; fereastra de referință e explicită (`ref_start`, `ref_end` exclusiv) sau tenorul;
orizont ≤ 36 luni; convenția bursei se rezolvă în adaptor (JPX / MX = luna de start, ASX BB = luna de decontare).
Serii oficiale: valoarea publicată, în procente, cu data ei; un furnizor cu o serie căzută le întoarce pe celelalte.

**Decizii** (`decisions.parquet`; coloane: bank, currency, meeting_date, decision_time_utc, rate_before, rate_after,
lower, upper (Fed), delta_bp, effective_date, consensus, surprise_consensus_bp, rate_source, status, notes).
- Precedență pentru `rate_after`: **serie oficială (nivelul la data efectivă) > BIS > FF validat > manual**.
- `status`: `official` | `bis` | `ff_pending` (FF câștigă, așteaptă seria oficială / BIS) | `manual` | `conflict`.
- **FF validat**: potrivire pe (valută, nume) și pe ședința oficială (prima zi − 1 … decizie + 1); la rânduri multiple,
  cel mai apropiat de ora deciziei; **0.0 cu previous > 0 respins** dacă oficialul nu confirmă 0.0; **NaN respins**.
- **Consens** = forecast FF. Fed: limita superioară → midpoint (lățime 25 bp presupusă, notată). ECB: MRO → DFR cu
  spread-ul seriilor ECB (0.50 până la 2024-09-17, 0.15 după). SNB: n/a (FF nu are rânduri numerice din 2025-06).
- **Data efectivă**: regula băncii din config, cu calendarul băncii (BoJ: 18 sep → 24 sep; Fed: miercuri → joi; ECB:
  joi → miercurea următoare).
- **Conflict** oficial (sau BIS) vs FF → `status = conflict`; sursa cu precedență câștigă; apare în `--status`.
- Acoperire: ultimele 4 ședințe / bancă + cele care trec; rândurile mai vechi nu se șterg.

**Proiecții.** Fed SEP: parser pe `fomcprojtabl{YYYYMMDD}.htm` (ultimele 4 SEP; se descarcă doar cele lipsă); medianele
publicate (rotunjite la 0.1) și medianele din dot-uri (ex. 4.125 / 4.125 / 3.875 / 3.625 / 3.25 la 16 sep 2026).
RBNZ: `data/cb/manual/rbnz.yaml` (schemă validată; o intrare `filled` cere sursă + pagină, nimic din memorie).

**Blackout.** Ferestrele se calculează din regula băncii (`blackout_rule`) + `first_day` + calendarul băncii, în
`src/cb_calendar.py`. Reguli neverificate rămân marcate (`verified: false`: BoJ; `precision: approximate`: BoE).
RBNZ și SNB: nu s-a găsit regulă → fără fereastră.

**Verificarea săptămânală a calendarului.** Compară `meetings.yaml` (rândurile oficiale) cu paginile oficiale (Fed, ECB,
BoE, BoJ, BoC, RBA, SNB); **doar avertizează** (în `--status` și în `$GITHUB_STEP_SUMMARY`), nu rescrie fișierul.

**`--status`**: piața (ultima scriere, as-of, lag, zile lipsă, validatori, „fără valori noi”), seriile oficiale, ultima
decizie pe valută (inclusiv conflicte și `ff_pending`), proiecții, avertizările RBNZ, verificarea calendarului și
diferența `policy_rates.yaml` (Carry) vs modulul CB pe valută. **Lag** = zile lucrătoare între as-of și ultima zi
lucrătoare ≤ azi, în calendarul sursei; zilele lipsă exclud sărbătorile acelui calendar.

**Calendare.** Fiecare sursă (`cb_sources.yaml`) și fiecare bancă (`central_banks.yaml`) are `calendar_id`. Generat de
`scripts/cb_gen_calendars.py`: UK (gov.uk `bank-holidays.json`) și JP (CSV Cabinet Office) live; US (Fed K.8), EU
(ECB T2), CA (BoC), AU (RBA / NSW), NZ (employment.govt.nz), CH (SIX SIC) din paginile oficiale, cu URL pe fiecare dată.

### Note de implementare (alegeri de execuție, nu decizii de produs)

- **Spread-ul ECB se citește la data efectivă, nu la ziua ședinței.** Tăierea din 12 sep 2024 a intrat în vigoare pe 18,
  când MRO − DFR trecuse deja la 0.15: FF dă MRO 3.65, iar DFR = 3.65 − 0.15 = 3.50 (= seria oficială); cu spread-ul zilei
  ședinței (0.50) ar ieși 3.15. Testul folosește rândul FF real din 2024-09-12.
- **BIS trece schimbarea OCR cu o zi mai târziu** (2 sep 2026 → 2.75 pe 3 sep). `policy_rate.bis_offset_days` (NZD 1, JPY 0)
  citește nivelul BIS la data efectivă + offset.
- Fed: `rate_before` / `rate_after` sunt midpoint-uri (convenția de calcul), `lower` / `upper` = intervalul de după decizie,
  `consensus` în aceeași convenție ca `rate_after`.
- `decision_time_utc` = ora fixă a băncii pe ziua ședinței; BoJ (fereastră 11:30–13:30 JST): gol.
- `state.json` are trei roluri: validatori ETag / Last-Modified, zilele „fără valori noi” ale MX, rezultatul verificării
  săptămânale (se schimbă o dată pe săptămână).
- Din MX se colectează doar CRA + COA; CORRA oficial vine din Valet (`boc:AVG.INTWO`).
- Erată A5 (față de raportul 0A): BoE 2027-12-16 este corectat de mână la fără MPR / fără conferință; `cb_probe.py` rămâne
  neatins.
- Rândurile snapshot din 2026-09-18 (JPX, MX, ASX) provin din rulările locale făcute înainte de merge.

## 9. Ce nu face 1B-1

Fără calculul traiectoriilor (vine în 1B-2), fără texte / rezumate, fără UI, fără trigger extern, fără modificări la
`/economic` sau `/carry`.

## 10. Cele 15 întrebări din 1A — decizii

| # | subiect | decizie |
|---|---|---|
| 1 | fișierul de cotații | **închisă:** stocare lunară (implementată în 1A) |
| 2 | reacția la decizie | Δ(EOD T − EOD T−1) în bp, în două puncte (următoarea ședință; ultima ședință a anului), flag-uri EXACT / UPPER_BOUND / PROXY, CHF n/a (§4) |
| 3 | afișarea USD | etichetă de interval în UI, midpoint doar în calcul |
| 4 | licențe | de acord (§5, punctul 9) |
| 5 | sărbători | de acord, extins: calendare US, UK, EU (TARGET), JP, CA, AU, NZ, CH, 2026–2027 (`config/cb_calendars.yaml`) |
| 6 | `meetings.yaml` | verificare săptămânală, doar avertizare; `first_day` adăugat în 1B-1 |
| 7 | RBNZ manual | parțial: decizia automată (FF + BIS); manual doar OCR track și calendarul 2027 (`manual/rbnz.yaml`) |
| 8 | surse snapshot | de acord |
| 9 | migrarea Carry | de acord; comparația apare în `--status` din 1B-1; migrarea e un pas separat după ≥ 1 ședință cu diff 0 |
| 10 | convenții de calcul | 100 − preț și MPT în bp; spread = mediana pe 20 de zile lucrătoare (§5.4); OIS din RBA F1 respins; AUD primar = ASX IB |
| 11 | AI (faza 2) | `claude-sonnet-5`, fixat în config; cheia = secret GitHub în faza 2 |
| 12 | trigger extern (faza 4) | corectat: Vercel Cron pe Hobby rulează o dată pe zi; scheduler extern Cloudflare Worker (cron `*/5`, cod în repo) → `workflow_dispatch`, 3 reîncercări cu backoff, alertă la eșec |
| 13 | minute GitHub Actions | de acord (se măsoară după prima săptămână) |
| 14 | slug-uri URL | de acord: `/central-banks/usd` etc.; perechea `/central-banks/pair/eurusd` |
| 15 | cross-link RATE EXP | ajustat: rândurile de pereche de pe `/economic` → vederea de pereche, DXY → `/central-banks/usd`; în faza 3, fără să strice click-ul pe rând |

## 11. Întrebări deschise noi (1B-1)

Cu recomandarea într-un rând (recomandare, nu decizie).

1. **Spread-ul ECB la data efectivă** (nota de mai sus) diferă de formularea „la data ședinței”. *Recomandare:* rămâne la
   data efectivă — singura citire care reproduce DFR-ul oficial peste schimbarea din 2024-09-18.
2. **Offset-ul BIS pentru NZD (+1 zi)** e observat pe patru decizii din 2026. *Recomandare:* se păstrează; dacă BIS își
   schimbă convenția, decizia devine `conflict` cu FF și se vede în `--status`.
3. **Calendare neverificate:** CA 2027 (BoC nu l-a publicat), CH după 2 ian 2027 (lista SIC se oprește acolo), sărbătorile
   bancare de sfârșit de an din JP (statut, nefetch-uit). *Recomandare:* re-rulare `scripts/cb_gen_calendars.py` când
   apar paginile, iar verificarea săptămânală se extinde ulterior la calendarele de sărbători.
4. **OCR track RBNZ: trei MPS 2026 rămân placeholder** (nu există niciun PDF MPS în `~/projects/macro-cb`; site-ul e blocat).
   *Recomandare:* pui PDF-urile MPS (feb, mai, sep 2026) în directorul proiectului și le extrag; PDF-urile nu se comit.
5. **`first_day` pentru ședințele ECB deja trecute** e derivat (miercurea dinaintea deciziei), fiindcă pagina ECB listează
   doar viitorul. *Recomandare:* rămâne derivat, marcat `verified: false` prin `source: ff`.
6. **Blackout BoJ** rămâne neverificat (formularea e dintr-un anunț vechi), iar BoE e aproximativ („de regulă 8–9 zile”).
   *Recomandare:* UI le afișează cu marcaj „approximate / unverified” până apare o sursă curentă.
7. **Deciziile `ff_pending`** (acum doar BoJ 18 sep) devin `bis` când BIS ajunge la data efectivă (24 sep). *Recomandare:*
   nu se cere nimic manual; `--status` le listează cât timp așteaptă.
