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
- AI: **doar pe text** (rezumate, citate), **fără niciun verdict de direcție**. Provider: **OpenAI**, model **`gpt-5.6-terra`** (decizia lui George, 2026-09-21;
  inițial `claude-sonnet-5`), fixat în `config/cb_summaries.yaml`; cheia = secretul GitHub `OPENAI_API_KEY`.
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
| **3a** | UI pentru partea numerică: `/central-banks`, `/central-banks/<ccy>`, `/central-banks/pair/<pair>`, JSON în `public/data/cb/`, render în `cb-refresh` (livrat) |
| **2a** | texte oficiale fără AI: comunicate, voturi, redline, discursuri, conferințe (URL + video), rata din comunicat; UI pentru blocurile de text |
| **2b** | rezumate AI peste textele din 2a (sloturile „summary pending”) |
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
src/cb_sources/{base,market,official,fed_sep,calendar,holidays,meetings}.py
src/cb_collect.py                    python -m src.cb_collect [--stage S] [--backfill] [--status] ...
scripts/cb_gen_calendars.py          generatorul lui cb_calendars.yaml (UK și JP live, restul din tabele cu sursă)
scripts/cb_gen_meetings.py           generatorul lui meetings.yaml (paginile oficiale + FF pentru ECB trecut + RBNZ manual)
scripts/cb_check_effective_dates.py  dovada regulilor de dată efectivă (serii oficiale / BIS, de la 2025-09)
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
- **Data efectivă — un singur mecanism**: regula băncii din config (`effective_rule`), cu calendarul băncii (BoJ: 18 sep →
  24 sep; Fed: miercuri → joi; ECB: joi → miercurea următoare; RBNZ: următoarea zi lucrătoare NZ, ex. 2 sep → 3 sep,
  *derivată din BIS, site-ul RBNZ e blocat*). **BIS WS_CBPOL e datat pe data efectivă**, la fel ca seriile oficiale, deci se
  citește la data efectivă, fără offset. Regulile sunt validate contra fiecărei schimbări din serii de la 2025-09
  (`scripts/cb_check_effective_dates.py`, `src/cb_compute/effective_check.py`): Fed 4/4, ECB 2/2, BoE 1/1, BoJ 2/2, BoC 2/2,
  RBA 3/3, RBNZ 4/4 (derivată), SNB 0 schimbări în fereastră; BIS vs seria oficială: aceeași zi în 10/10 comparații
  acoperite de BIS. Regulile potrivite sunt `verified` cu sursa = seria (`config/central_banks.yaml`).
- **Conflict** oficial (sau BIS) vs FF → `status = conflict`; sursa cu precedență câștigă; apare în `--status`.
- Acoperire: ultimele 4 ședințe / bancă + cele care trec; rândurile mai vechi nu se șterg.

**Proiecții.** Fed SEP: parser pe `fomcprojtabl{YYYYMMDD}.htm` (ultimele 4 SEP; se descarcă doar cele lipsă); medianele
publicate (rotunjite la 0.1) și medianele din dot-uri (ex. 4.125 / 4.125 / 3.875 / 3.625 / 3.25 la 16 sep 2026).
RBNZ: `data/cb/manual/rbnz.yaml` (schemă validată; o intrare `filled` cere sursă + pagină, nimic din memorie).

**Blackout.** Ferestrele se calculează din regula băncii (`blackout_rule`) + `first_day` + calendarul băncii, în
`src/cb_calendar.py`. Reguli neverificate rămân marcate (`verified: false`: BoJ; `precision: approximate`: BoE).
RBNZ și SNB: nu s-a găsit regulă → fără fereastră.

**Verificarea săptămânală.** (1) `meetings.yaml` (rândurile oficiale) vs paginile oficiale (Fed, ECB, BoE, BoJ, BoC, RBA,
SNB). (2) **Calendarele de sărbători** vs sursele oficiale (UK gov.uk JSON, JP CSV Cabinet Office, US Fed K.8, EU ECB T2, CA
BoC, AU NSW, NZ employment.govt.nz; CH: ETag / lungimea PDF-ului SIC, fără bibliotecă PDF). Avertizează când pagina oficială
are date care lipsesc din YAML, când o dată `verified` a dispărut, sau când un an marcat `verified: false` devine
verificabil (apare pagina oficială / se schimbă fișierul). **Doar avertizează** (în `--status` și în `$GITHUB_STEP_SUMMARY`),
nu rescrie nimic; zilele bancare de sfârșit de an din JP (statut) nu pot fi verificate pe CSV și rămân în afara controlului.

**`meetings.yaml`** se generează cu `scripts/cb_gen_meetings.py` (`--check` arată diferențele față de paginile oficiale de
azi); regulile stau în `src/cb_sources/meetings.py`; calendarul RBNZ publicat stă în `data/cb/manual/rbnz.yaml`. Testul
reface fișierul octet cu octet din tăieturi reale ale paginilor.

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
| 11 | AI (faza 2) | **actualizată 2026-09-21** — provider **OpenAI**, model **`gpt-5.6-terra`** (mijlocul familiei curente: gpt-6-astra / gpt-5.6-sol frontieră, gpt-5.6-luna mic), fixat în `config/cb_summaries.yaml`; cheia = secretul GitHub `OPENAI_API_KEY`; prețuri de listă 2 / 12 USD per MTok (standard, context scurt), sursa și data în config (https://developers.openai.com/api/docs/pricing, citit 2026-09-21). Inițial `claude-sonnet-5`; clientul Anthropic rămâne alternativă configurată și testată |
| 12 | trigger extern (faza 4) | corectat: Vercel Cron pe Hobby rulează o dată pe zi; scheduler extern Cloudflare Worker (cron `*/5`, cod în repo) → `workflow_dispatch`, 3 reîncercări cu backoff, alertă la eșec |
| 13 | minute GitHub Actions | de acord (se măsoară după prima săptămână) |
| 14 | slug-uri URL | de acord: `/central-banks/usd` etc.; perechea `/central-banks/pair/eurusd` |
| 15 | cross-link RATE EXP | ajustat: rândurile de pereche de pe `/economic` → vederea de pereche, DXY → `/central-banks/usd`; în faza 3, fără să strice click-ul pe rând |

## 11. Întrebările noi din 1B-1 — decizii

| # | subiect | decizie |
|---|---|---|
| 1 | spread-ul ECB | **aprobat:** se citește la data efectivă (singura citire care reproduce DFR-ul oficial peste schimbarea din 2024-09-18) |
| 2 | offset-ul BIS pentru NZD | **înlocuită** de mecanismul unic de dată efectivă (§8): BIS e pe data efectivă la toate băncile, RBNZ are regula „+1 zi lucrătoare NZ”, derivată din BIS; `bis_offset_days` a dispărut |
| 3 | calendare neverificate | **acoperită** de verificarea săptămânală, care include acum calendarele de sărbători (§8) |
| 4 | OCR track RBNZ | de la RBNZ trebuie **doar MPS sep 2026** (`data/cb/manual/rbnz.yaml`); PDF-ul nu se comite |
| 5 | `first_day` ECB trecut | **aprobat:** rămâne derivat (miercurea dinaintea deciziei), `verified: false` prin `source: ff` |
| 6 | blackout BoJ / BoE | **aprobat:** UI le afișează cu marcaj „approximate / unverified” |
| 7 | decizii `ff_pending` | **aprobat:** fără intervenție manuală; devin `bis` când BIS ajunge la data efectivă |

## 12. FAZA 1B-2 — motorul de calcul (pur, fără UI)

```
src/cb_compute/methods.py    numerică pură: lanțul EXACT pe media lunară, Curve / TenorCurve, pick_window, probabilități
src/cb_compute/spread.py     spread-ul benchmark overnight − politică (mediană 20 zile lucrătoare, cu excluderi)
src/cb_compute/engine.py     Context, baza, traiectoria pe ședință (4 metode), următoarea ședință, cumulat la ultima ședință
src/cb_compute/analysis.py   cross-check, repricing 1s/1l, GAP, surprize + reacție, perechi
src/cb_compute/report.py     randare text (tabel per bancă + tabelul perechilor)
src/cb_compute/__main__.py   python -m src.cb_compute --report [--asof DATA] [--data-dir DIR] [--currency C] [--points N]
src/cb_loader.py             TOATE citirile de fișiere (market_quotes, serii oficiale, decizii, ședințe, proiecții, RBNZ, perechi)
scripts/cb_freeze_engine_fixture.py   îngheață data/cb la un as-of în tests/fixtures/cb_engine/ (testele de acceptanță)
```

`src/cb_compute/*` nu citește fișiere și nu apelează URL-uri (un test o verifică); primește un `Context` construit de loader.
Fără dependențe noi. Nimic în `public/`, nimic în UI.

### Convenții

- **Normalizare.** Futures pe preț: rată = 100 − preț. MPT (Atlanta Fed): bp → %. Curbe și randamente: deja în %. Tot ce iese
  e în % (rate) sau bp (diferențe).
- **Baza la data d** = ultima rată DECISĂ până la d (`meeting_date ≤ d`), inclusiv o decizie anunțată dar neintrată în vigoare
  (BoJ: decizia din 18 sep, în vigoare 24 sep → baza 1.25 între 18 și 24 sep; `pending` în ieșire). Fed: midpoint. Fără nicio
  decizie înainte de d: `rate_before` al primei următoare. „Rata în vigoare” (`rate_in_force`) o folosește doar lanțul EXACT.
- **Spread benchmark − politică** = mediana `benchmark − rata politică în vigoare` pe ultimele 20 zile lucrătoare din calendarul
  seriei benchmark, capătul = ultima observație ≤ as-of; se exclud ±2 zile lucrătoare în jurul datelor EFECTIVE ale schimbărilor
  de rată și ultima zi lucrătoare a fiecărei luni (deci și a trimestrului). Ieșire: `value`, fereastra, `n` (zile rămase),
  lista excluderilor. Perechi (în `central_banks.yaml`, `spread:`): USD SOFR − midpoint(DFEDTARL, DFEDTARU); GBP SONIA − Bank Rate;
  JPY call rate − BIS JP; CAD CORRA − V39079; AUD interbank O/N − FIRMMCRTD. EUR, NZD, CHF: fără spread.
  Excluderea în jurul datelor efective se aplică la schimbări (`delta_bp ≠ 0`), nu și la menținere.
- **Rata implicită a politicii** = rata implicită a benchmark-ului − spread (metodele EXACT / CURVE / WINDOW); PROXY scade în schimb o
  bază proprie.

### Metode (flag pe fiecare valoare)

| flag | metodă | surse (config: `role`, `method` pe instrument) |
|---|---|---|
| **EXACT** | futures 1M pe media lunii: `r_post = (N·X − d_pre·r_prev)/(N − d_pre)`, zile calendaristice, rata se schimbă doar la data efectivă; pașii deciși dar neintrați în vigoare sunt cunoscuți; lunile fără ședință = verificare de consistență (abaterea în bp, raportată) | CAD COA (MX 1M), AUD ASX IB |
| **CURVE** | curbă OIS forward, media pe `[eff_m, eff_{m+1})` (interpolare liniară pe grila lunară; ultima ședință: 56 de zile) − spread | GBP BoE OIS |
| **UPPER_BOUND** | (metoda WINDOW) contracte 3M: o valoare pe fereastra de referință; `pick_window` = fereastra cu start în `[eff − 7 zile calendaristice, ∞)` cea mai apropiată de data efectivă (egalitate: cea mai timpurie); ieșire cu numărul de ședințe în interior și zilele ferestrei dinainte de data efectivă | USD MPT, JPY JPX TONA-3M, CAD MX CRA (dincolo de COA), NZD ASX BB (nivel BKBM) |
| **PROXY** | curbe de stat / bills: `bază = media curbei, construită DOAR din tenorii observați înainte de prima dată efectivă, pe [as-of, prima dată efectivă) − rata curentă` (bills: randamentul celui mai lung tenor care se încheie înainte de ea); implicit = medie − bază; lunar (per ședință) sau pe tenor. **Fără un astfel de tenor baza e n/a** („proxy without short end”) și tot ce cere baza devine n/a — vezi mai jos | EUR ECB AAA (per ședință); UST, BoC bills, RBA bank bills (doar cross-check, pe tenor) |
| n/a | CHF (fără sursă); NZD față de OCR (fără spread BKBM–OCR: se arată nivelul BKBM, nu bp) | — |

Instrumentul principal per valută: USD MPT (cross-check Treasury), EUR ECB AAA, GBP BoE OIS, JPY JPX, CAD COA apoi CRA dincolo de
orizontul COA (cross-check bills BoC), AUD ASX IB (cross-check RBA bills), NZD ASX BB, CHF n/a. Metodele exacte au prioritate;
ferestrele 3M completează doar ședințele pe care EXACT nu le acoperă.

**Regula PROXY „fără capăt scurt” (generală, se aplică oricărui PROXY).** Baza se măsoară doar pe tenori observați înainte de prima dată
efectivă; ECB AAA începe la 3M, iar prima ședință ECB e la ~1.6 luni, deci baza EUR = n/a. Atunci sunt n/a, cu motivul „proxy without short
end”: rata implicită (politică-echivalentă), bp cumulat față de rata curentă, pasul și probabilitatea la următoarea ședință, surpriza vs piață
T−1, diferențialul de nivel al perechilor. Rămân: nivelurile brute ale curbei (`level`, `level_kind: sovereign_proxy`, „sovereign proxy, not
policy-equivalent”, media curbei pe intervalul dintre datele efective, cu nota „shortest tenor extended flat” când intervalul începe înainte de
primul tenor), repricing-ul și reacția (Δ de nivel al aceluiași instrument, nu au nevoie de bază).

**Acoperire (regulă generală, 3a).** Orice nivel raportat (PROXY, CURVE, cross-check pe bills) trebuie să aibă intervalul INTEGRAL în interiorul
tenorilor observați: `outside curve coverage (<3M)` când începe sub primul tenor, `(>36M)` când se termină peste ultimul; fără extrapolare plată la
niciun capăt. Ex.: nivelul ECB pentru ședința din 29 oct (interval 4 nov → 23 dec, curba începe la 3M) e n/a; end-2026 / end-2027 rămân. Consecință: cu
prima maturitate BoE la 1M, o ședință aflată la <1 lună (surpriza vs piață T−1, pasul repricing-ului când ședința dinaintea ei era foarte aproape)
devine n/a pentru GBP.

Reguli ale lanțului EXACT (toate cu test + mutație):
- fereastra de coadă: `N − d_pre < 5` (ședință în ultimele 5 zile ale lunii) și luna următoare fără ședință → `r_post` = media lunii
  următoare (formula ar amplifica zgomotul de N/(N−d_pre) ori); cu ședință în luna următoare → neidentificat, cu motiv;
- două ședințe în aceeași lună → neidentificat;
- ședința care urmează unui neidentificat nu are „pas” (ar acoperi două ședințe): `step_bp` n/a cu motiv, rata și cumulatul rămân.

### Ieșiri per bancă (la un as-of)

1. **Traiectorie**: punct pe ședință (data efectivă sau fereastra, rata implicită, cumulat în bp față de bază, metodă, sursă + as-of,
   `stale`), motiv când e n/a. Fiecare punct are și `level` + `level_kind` (`policy` = rata implicită; `bkbm` = nivelul ASX BB, nu OCR;
   `sovereign_proxy` = curba brută): valoarea pe care o folosesc metricile de Δ (repricing, reacție). `rate` (politică-echivalent) și
   `cum_bp` sunt n/a când există doar un nivel (NZD BKBM, PROXY fără capăt scurt).
2. **Următoarea ședință**: pas implicit (EXACT / CURVE / PROXY lunar) și probabilitate doar pentru EXACT / CURVE: `n = ⌊|pas|/25⌋`,
   `p = (|pas| − 25n)/25`; P(n+1 mișcări) = p, P(n) = 1 − p, în direcția pasului. Altfel n/a + motiv.
3. **Cumulat la ultima ședință din 2026 și din 2027** (WINDOW: fereastra `pick_window`, UPPER_BOUND; dacă ședința e deja decisă e parte
   din bază → 0, `DECIDED`).
4. **stale** = lag (zile lucrătoare în calendarul sursei) > `stale_after_bd` (implicit 2; se poate suprascrie pe sursă).
5. **GAP (bp) = piață − bancă.** Fed: mediana dot-urilor (ultimul SEP) față de piață la ultima ședință din 2026 / 2027 / 2028, plus
   distribuția dot-urilor. Pentru 2028 nu există calendar: se presupune ultima ședință = a doua miercuri din decembrie (tiparul
   Fed), marcat în notă. RBNZ: GAP doar pe aceeași bază — proiecția MPS „90-day bank bill” față de ASX BB. Tabelele din PDF-ul MPS sep 2026
   nu conțin proiecția 90-day (doar OCR track), deci **GAP NZD = n/a** (OCR și BKBM sunt baze diferite); traiectoria băncii (OCR track,
   medii trimestriale rotunjite la 0.1, transcriere manuală din Appendix 1, Table 6.1, p. 50, proiecții finalizate 2026-08-26) se afișează
   **separat** (`bank_path`). Dacă un MPS viitor are proiecția 90-day, GAP-ul folosește ASX BB al cărui interval de 90 de zile începe în
   trimestru (cel mai apropiat de mijloc). Celelalte: n/a („nu publică o cale proprie”).
6. **Repricing 1s / 1l** = Δ peste 5 / 21 zile lucrătoare (calendarul sursei), recalculând traiectoria la as-of-ul anterior. **Valoarea
   principală = Δ al NIVELULUI implicit la orizont fix** (ultima ședință din 2026 / 2027): nu depinde de deciziile dintre cele două date
   (o hike de 25 bp nu apare ca −25 bp). Δ al bp cumulat rămâne câmp secundar (față de baza fiecărei date; `base_change_bp` marchează o
   decizie între date). **Pasul la următoarea ședință: aceeași ședință la ambele date** (pasul ei la data veche = nivelul ei − nivelul
   ședinței dinaintea ei la acea dată), nu „următoarea ședință de atunci”. n/a cu motiv când istoricul nu ajunge (JPX, MX, ASX sunt
   snapshot: istoric doar din 2026-09-18) sau când nivelul / pasul nu există (EUR: pasul cere baza; NZD are Δ pe BKBM).
7. **Surprize / reacție** (ultimele 4 decizii + următoarele): față de consens (din `decisions.parquet`); față de piață T−1 = pasul decis −
   pasul implicit la T−1 (doar EXACT / CURVE / PROXY lunar cu bază; PROXY fără capăt scurt = n/a); reacția = Δ(EOD T − EOD T−1) al `level` la următoarea ședință și la
   ultima ședință a anului.
8. **Cross-check-uri**: USD MPT vs Treasury (baza din bill-ul de 1M, singurul care se încheie înainte de 29 oct), CAD lanțul COA vs CRA pe
   aceeași perioadă, AUD lanțul IB vs RBA bank bills: **n/a** — cel mai scurt bill RBA (1M) se încheie după prima dată efectivă (30 sep),
   deci „proxy without short end”.

### Perechi (cele 28 din `data/economic_instruments.yaml`, `type: fx`) — n/a pe metrică, nu pe pereche

`bază − cotată`, metrică cu metrică (fiecare e n/a separat, cu motivul fiecărui picior):
- **rata curentă (carry)**: cere ambele baze — există pentru toate cele 28 (și CHF, și NZD);
- **diferențialul implicit / cumulat la ultima ședință din 2026 / 2027**: cere ambele niveluri politică-echivalente (deci n/a pentru EUR pe
  proxy, NZD pe BKBM, CHF);
- **repricing-ul diferențialului** (5 / 21 zile): cere Δ de nivel la ambele picioare — EUR (proxy) și NZD (BKBM) au Δ; CHF nu.
Flag = cel mai slab dintre cele două picioare (`EXACT < CURVE < UPPER_BOUND < PROXY`).

### Decizii de execuție

- **Confirmate:** excluderea din spread în jurul datei efective se face pentru schimbări de rată, nu pentru menținere; ultima ședință Fed din
  2028 e presupusă (a doua miercuri din decembrie) și marcată.
- **Închise de ajustările 1B-2:** întrebarea EUR (extrapolare sub primul tenor) → regula PROXY „fără capăt scurt”; întrebarea repricing (cum vs
  nivel) → nivelul e valoarea principală; întrebarea GAP RBNZ → înlocuită de OCR track (bank path separat) + GAP n/a fără proiecția 90-day.
- **E3 (regula ferestrei)** din 0A („prima fereastră cu start ≥ eff − 3 zile”) e înlocuită de regula de mai sus (7 zile, cea mai apropiată);
  `cb_probe.pick_window` a fost aliniată, `docs/spikes/cb_sources_numeric.md` are nota.

### Față de 0A / 0B (as-of 2026-09-18, date înghețate în `tests/fixtures/cb_engine/`)

Valori BRUTE (înainte de spread), aceeași metodă: USD MPT 4.310 / 4.638, JPY 1.475 / 2.1075, CAD CRA 2.775 / 3.595, COA 9 dec 2.6014, AUD
14 dec 2027 4.885 — toate în ±0.5 bp de 0A/0B; funcția `implied_1m_chain` din 0A reproduce CAD 28 oct (+11.6 bp ≈ +12) și AUD 8 dec (4.7259 ≈ 4.726).
Metoda schimbată (nou / 0A / diferență): spread USD +2.0 bp (mediană) vs −2.5 bp (o zi) → USD 2026 4.290 vs 4.335 (−4.5 bp); CAD 28 oct
+14.0 vs +12 (+2.0; BoC efectiv +1, coadă → media lui noiembrie); AUD 8 dec 4.7343 vs 4.726 (+0.8; coada 29 sep); GBP 2026 4.160 vs 4.051 (+10.9;
media pe interval față de valoarea punctuală, curba crește); GBP 2027 4.854 vs 4.855; EUR 2026 2.611 vs 2.821 (−21.0), 2027 3.078 vs 3.397
(−31.9) în prima versiune; cu regula „fără capăt scurt” EUR nu mai are rată politică-echivalentă: nivelul brut e 2.872 / 3.339 față de 0A 2.821 /
3.397 (+5.1 / −5.8 bp: media pe interval față de valoarea punctuală).

## 13. FAZA 3a — UI pentru partea numerică

Aceleași unelte ca restul site-ului: Jinja + JS static + `style.css` cu variabilele de temă; grafice Chart.js 4 (+ plugin annotation) deja folosite de
celelalte pagini. UI în engleză; **nimic hardcodat în HTML**: scheletul poartă doar tipul paginii, tot conținutul vine din JSON.

```
src/cb_compute/payload.py      pur: overview / pagină de bancă / perechi -> dict-uri JSON-ready (rotunjite, deterministe, fără ceas)
src/cb_render.py               python -m src.cb_render [--asof D]: JSON + 37 pagini dintr-UN singur template (templates/central_banks.html.j2)
static/cb.js  (+ copie public/) overview, pagina băncii, pagina perechii; static/style.css (+ copie) blocul `.cb-*`
public/central-banks.html            overview (tab Banks / Pairs)
public/central-banks/<ccy>.html      8 pagini de bancă
public/central-banks/pair/<pair>.html  28 de pagini de pereche (cele din /economic)
public/data/cb/overview.json, <ccy>.json, pairs.json   încărcate la cerere de pagini
```

- **Legături** interne cu `.html` (merg și local cu `python -m http.server` din `public/`, și pe Vercel, unde `cleanUrls` le redirecționează);
  navbar-ul are „Central Banks” lângă Carry (`/central-banks`), ca restul intrărilor. Paginile existente primesc navbar-ul nou la următorul
  render (după merge: un render-all).
- **cb-refresh**: `collect → render → commit` doar `data/cb/` + `public/central-banks.html`, `public/central-banks/`, `public/data/cb/`. Randarea e
  deterministă (payload-ul depinde doar de date și de as-of; fișierul se rescrie doar dacă i se schimbă octeții), deci fără date noi nu se comite nimic.
- **Convenții JSON**: fiecare valoare care poate fi n/a e `{v, flag, stale, na, ...}` (`na` = motivul exact când `v` e null, altfel null; testat pe
  toate metricile). `flag` ∈ EXACT / CURVE / UPPER_BOUND / PROXY (+ DECIDED); `stale` din engine. Semnul: pozitiv = hawkish. `level` + `level_kind`
  (`policy` / `bkbm` / `sovereign_proxy`) există și când nu există bp.
- **Culori și forma**: albastru = hawkish (pozitiv pentru valută), roșu = dovish (aceeași paletă ca /economic, `ScorePalette`); semnul (+/−) e mereu scris.
  Stale = gri + tooltip; n/a = „—” cu motivul în tooltip (pe pagina perechii, și în text). Cifre tabulare; rată `%.2f` (3 zecimale doar când 2 ar
  deforma un midpoint, ex. 3.875), bp cu semn, Fed ca interval. Desktop-first; tabelele se derulează orizontal pe mobil.
- **Etichetarea bazei.** Orice celulă cu valoare poartă badge-ul de metodă; un nivel care nu e policy-equivalent are și eticheta bazei („BKBM base” la
  NZD, „sovereign proxy” la EUR) și tooltip cu motivul pentru care nu există bp (la NZD și motivul pentru care GAP e n/a). Overview-ul e compact: toate
  coloanele, inclusiv LAST DECISION (dată · Δ bp · surpriza vs consens) și REACTION (Δ la următoarea ședință / la sfârșit de an), încap pe 1440 px.
- **Overview**: rând = valută · bancă (click → pagina băncii): rata (o decizie neintrată în vigoare = „1.25 (from 24 Sep)”), următoarea ședință cu
  countdown (BoJ: „time TBD”, fereastra din config `decision_time.window_local`), pas + probabilitate (EXACT / CURVE) sau bp cumulat până la prima
  fereastră + „N mtgs” + UPPER BOUND, bp cumulat end-2026 / end-2027, GAP (doar Fed), repricing 1w (Δ nivel end-2026; 1m, end-2027 și pasul în tooltip),
  ultima decizie (Δ, surpriza vs consens), reacția (următoarea ședință / sfârșit de an). Tab „Pairs”: cele 28, cu diferențialul curent, cel implicit
  la end-2026 / end-2027 și repricing-ul lui.
- **Pagina băncii**: header (rata, următoarea ședință, badge „in blackout” calculat în browser din ferestrele UTC, marcaje „approximate / unverified”
  etichetate pe regulă), graficul traiectoriei (istoricul ratei în trepte; piața: EXACT / CURVE în trepte pe datele efective, WINDOW ca bare marcate ≤,
  PROXY punctat cu eticheta lui; banca: mediana dot-urilor + distribuția lor, respectiv OCR track pe trimestre; „today” și ședințele viitoare),
  cardul următoarei ședințe (pas + bare de probabilitate, altfel explicația metodei), orizonturi end-2026 / end-2027, GAP / traiectoria băncii, traiectoria
  pe ședințe, ultimele 4 decizii (voturi / comunicat / conferință = sloturi goale pentru faza 2), calendar + blackout, surse cu licența din `cb_sources.yaml`,
  as-of, stale și panoul „How is this calculated?”. **Ziua deciziei** (data locală a băncii): cardul următoarei ședințe urcă în capul paginii.
- **Pagina perechii**: cele două traiectorii suprapuse (market-implied întrerupt) + diferențialul; tabel: acum (carry), end-2026, end-2027, repricing,
  n/a pe metrică cu motivul fiecărui picior; flag = cel mai slab.
- **Cross-link din /economic**: celula RATE EXP (2Y) a rândurilor de pereche → pagina perechii, a rândului DXY → `/central-banks/usd`; link separat cu
  `stopPropagation`, click-ul pe rând (modalul) rămâne neschimbat.
- **Decizii de execuție**: fereastra BoJ „time TBD” vine din config (11:30–13:30 JST), nu din „~12:00–13:30” din cerere; NZD are „approximate /
  unverified” pe două reguli (data efectivă derivată din BIS, ora deciziei) — fiecare cu eticheta ei.


## 14. FAZA 2a — texte oficiale (fără AI)

Comunicate, voturi, redline, procese-verbale, discursuri și conferințe de presă, luate **doar** de la sursele oficiale din raportul 0B
(`docs/spikes/cb_sources_text.md`). Nicio cerere către un model. Rezumatele AI sunt faza 2b; declanșatorul extern + verificările finale, faza 4.

```
src/cb_docs/http.py      Fetcher politicos: UA identificabil, robots.txt, GET condițional, 2 s / host, buget 400 cereri, HEAD
src/cb_docs/sources.py   unde stă fiecare text (URL-uri derivate din data ședinței, feed-uri RSS, liste HTML) + lagurile 0B
src/cb_docs/extract.py   HTML (container fix per bancă, fără boilerplate) și PDF (pypdf 6.19.0, normalizarea artefactelor de spațiere BoJ)
src/cb_docs/parse.py     rata din comunicat, voturi, redline pe cuvinte, relevanță monetară, dedup față de BIS, potrivire video
src/cb_docs/store.py     documente în partiții lunare (`data/cb/documents/`), `votes.parquet`, `redlines.parquet`
src/cb_docs/collect.py   orchestrarea (etapa `documents` din `python -m src.cb_collect`), așteptările de publicare pentru `--status`
config/cb_roster.yaml    membrii comitetelor (nume, rol, votant FOMC 2026 / 2027, președinte), generat de `scripts/cb_gen_roster.py` din paginile oficiale
data/cb/manual/documents.yaml   ce nu se poate colecta automat (RBNZ, videoclipuri) — șablon comentat
```

- **Model.** Un document = `doc_id` (`USD:statement:2026-09-16`), bancă, tip (statement, minutes, account, summary_of_opinions, deliberations,
  presser_transcript, opening_statement, speech, testimony), titlu, vorbitor + rol, URL, `published_at`, `first_seen_at`, `meeting_date`, limbă, format,
  **sha256 al textului extras**, metoda de extragere, nota de licență. **Textul integral se comite doar la comunicate și la declarațiile introductive**
  (BoC, SNB); minutele / deliberările / transcrierile / discursurile se hașează și se leagă (se descarcă la cerere în 2b). Idempotență: același sha ⇒
  nicio rescriere (`Dataset` scrie o partiție doar dacă i se schimbă octeții); `first_seen_at` nu se mută niciodată.
- **Politețe.** robots.txt respectat pe fiecare gazdă (o gazdă cu robots ilizibil e sărită); un bloc (Cloudflare, 403/429) e raportat, nu ocolit;
  validatorii ETag / Last-Modified stau în `data/cb/state.json` și avansează **doar după ce datele au fost stocate** (`remember`) **și doar dacă s-a schimbat
  conținutul stocat** (hash-ul textului extras, nu al octeților: o pagină poartă un nonce): aceleași date sub validatori noi lasă `state.json` neatins, deci nu
  se comite nimic doar pentru validatori (costul: un GET întreg în loc de 304, pe acea pagină). Sursele de piață și seriile oficiale procedează la fel (validatorii
  avansează doar cu rânduri noi / schimbate). **ETag-ul ECB se ignoră** (serverele de margine trimit alt ETag pentru aceiași octeți): nu se stochează și nu se trimite;
  rămâne `Last-Modified`. Feed-urile se citesc
  necondiționat (un 304 nu are elemente). O ședință de comunicat mai veche de 14 zile nu se mai cere deloc.
- **Rata din comunicat** = sursă intermediară. Precedență nouă: **serie oficială > comunicat > BIS > FF validat > manual**; status nou `statement`.
  Fiecare bancă are propriul regex; rata e validată (interval −1…15 %, pas ≤ 100 bp, direcția coerentă cu verbul, la Fed intervalul de 25 bp). Ce nu se
  parsează sau nu trece validarea nu se inventează (rând de eșec în raport). Conflictul rămâne conflict, cu câștigătorul din sursa mai sus în precedență.
  SNB primește rata în ziua deciziei; JPY iese din `ff_pending` (comunicatul BoJ e publicat în aceeași zi).
- **Voturi** (`votes.parquet`: pentru / contra, nume, direcția fiecărui disident): Fed din comunicat (antet + nume), BoE din rezumat + istoricul
  `mpcvoting.xlsx` (nume și preferințe), BoJ din PDF (propunerea disidentului comparată cu rata decisă dă direcția), RBA din minute (+14 zile).
  ECB, BoC, SNB = „not published” (decizie prin consens fără număr), RBNZ = „consensus”. O sursă mai săracă nu înlocuiește una mai bogată (`VOTE_RANK`).
- **Redline** (`redlines.parquet`): diferență pe cuvinte față de comunicatul anterior al aceleiași bănci, stocat compact (adăugat / șters / neschimbat)
  cu ancore pe paragrafe; `apply_redline` reface textul curent exact (testat pe perechi reale consecutive).
- **Discursuri și mărturii**: feed-ul RSS oficial al fiecărei bănci (Fed, ECB `ecb.sp*` / `ecb.in*`, BoE, BoC, SNB; listele HTML BoJ și RBA); BIS doar
  pentru backfill și dedup — cheia e vorbitor + similaritate de titlu ≥ 0.6, **data BIS nu intră în cheie** (postarea BIS e la 13–19 zile după discurs).
  Filtrul de relevanță monetară pe titlu și primul paragraf **marchează** (`monetary` / `other`), nu șterge; un anunț care nu e text (BoC „Media availability”: oră, loc,
  temă) se marchează `non_document` — rămâne în store, nu apare ca discurs și nu se rezumă niciodată. La fel paginile de **webcast** ale BoC (`/multimedia/…`) listate printre discursuri:
  cea a unei conferințe de presă (titlu / URL cu „press conference”) se **atașează ședinței** potrivite (decizie ±1 zi) ca videoclipul ei (`meta.attached_to`; dacă pagina de comunicat
  nu a dat deja un video, feed-ul îl dă), orice altă înregistrare, sau o conferință fără ședință potrivită, doar `non_document`; niciuna nu e discurs și niciuna nu se rezumă. Greutatea: Chair > votanți > restul.
- **Conferința de presă**: transcrieri oficiale doar ca URL + metadate (Fed PDF, ECB HTML cu Q&A unde hash-ul a fost publicat, RBA HTML; BoC și SNB au
  declarația introductivă în text). **Videoclipul vine din paginile oficiale ale băncilor, nu din feed-urile YouTube**: pagina FOMC (player Brightcove),
  pagina `/multimedia/` a BoC (legată din comunicat), pagina de transcript a RBA („Watch video: Media conference …”), pagina Monetary Policy Report a BoE
  (doar la ședințele cu conferință MPR), pagina-index a conferințelor ECB (arată doar ultima conferință: se prinde în ziua în care e curentă). O pagină
  fără video se reține în `state.json` (`presser_checked`) și se reia doar 3 zile după decizie. BoJ, SNB, RBNZ: „—” cu motivul pe fiecare (linkează doar
  canalul YouTube; RBNZ e blocat). `data/cb/manual/documents.yaml` rămâne pentru ce nu se poate lua automat.
- **Așteptări de publicare**: lagurile din 0B (`expect.py`) + 2 zile de grație; `python -m src.cb_collect --status` avertizează când un document a
  depășit termenul și nu e stocat. Aceleași termene alimentează „overdue” din payload-ul paginii băncii.
- **cb-refresh**: etapa `documents` rulează după `decisions` (rata din comunicat intră în decizii; dacă s-a schimbat un rând, deciziile se recalculează
  în aceeași rulare); se comit tot doar `data/cb/` și fișierele CB din `public/`. Timeout 10 → 15 min (pornire la rece ≈ 3 min).
- **UI** (bancă): blocul „Latest decision” (comunicatul pliabil, redline vs precedentul, voturi, linkuri spre comunicat / minute / video / transcriere),
  „Documents” (ultimele 4 ședințe + discursuri recente cu vorbitor, rol, dată, titlu, link, marcaj de relevanță), tabelul deciziilor cu Votes / Statement /
  Press conf populate; sloturile de rezumat sunt etichetate „summary pending” (2b). **Ziua deciziei**: decizia și blocul conferinței urcă în capul paginii.
- **Teste**: fixturi tăiate din paginile reale (`tests/fixtures/cb_docs/`, ~1.3 MB, `manifest.json`; regenerate cu `scripts/cb_freeze_docs_fixture.py`),
  sesiune HTTP falsă peste ele; parametrizat pe cele 4 comunicate reale ale fiecărei bănci (rata, voturile Fed 8–4 / 12–0 / 9–3 / 12–0, BoE 8–1 / 7–2 / 6–3 /
  6–3, BoJ 6–3 / 7–1 / 8–1 / 7–2, RBA 5–4 / 8–1 / unanim / unanim), redline pe două comunicate consecutive, dedup, relevanță, video, conflict, eșec de
  parsare, idempotență; ~45 de mutații pe precedență, voturi, redline, robots / blocuri, politica textului integral, așteptări — toate omorâte.

### Limitări cunoscute (raportate, nu ascunse)

- **YouTube.** `youtube.com/robots.txt` interzice `/feeds/videos.xml`; respectăm robots.txt, deci feed-urile canalelor **nu se citesc** (o notă în raport,
  nu un eșec la fiecare rulare). Videoclipurile vin din paginile oficiale ale băncilor (vezi mai sus); potrivirea ±1 zi pe feed rămâne în cod și se aplică
  doar dacă YouTube ar permite feed-ul.
- **RBNZ** rămâne BOTWALL (Cloudflare): fără comunicate automate; doar fișierul manual. Pagina lui afișează „n/a” cu motivul.
- **Nedescoperibile**: conturile ECB (`ecb.mg*~hash`) — URL-ul cu hash nu se poate deriva, iar paginile-index ECB nu îl expun fără JavaScript; se
  salvează doar cele care apar în feed-ul `press.html` (`--status` le listează pe restul ca „overdue”). Transcrierile conferințelor ECB, la fel: doar pentru
  ședințele cu URL-ul cu hash publicat.
- **URL-uri derivate din data deciziei** (verificate cu HEAD, fără feed): BoJ Summary of Opinions `mpmsche_minu/opinion_{YYYY}/opi{yymmdd}.pdf` (+14 z) și
  Minutes `minu_{YYYY}/g{yymmdd}.pdf` (~+50 z); Fed minutes `fomcminutes{YYYYMMDD}.htm` (+21 z); RBA minutes; SNB deliberations (+28 z); **BoE minutes = aceeași
  pagină ca rezumatul** (partea de după „Minutes of the Monetary Policy Committee meeting”, hașată; comunicatul se oprește la acel titlu).
- **Roster** (`config/cb_roster.yaml`, generat din paginile oficiale de comitet): Fed (rotația 2026 / 2027, Chair), ECB (cele 6 membri ai Executive Board votează
  mereu; guvernatorii băncilor naționale au vot prin rotație, pagina orarului nu se citește: `voter: null`), BoE (9 membri, cu cei 4 externi), BoJ, BoC (6, cu
  Deputy Governor extern), RBA (9), SNB (Governing Board, 3). **RBNZ** (Cloudflare) e tastat de mână în `config/cb_roster_manual.yaml`, din lista de participanți a „Summary record of meeting” din MPS sep 2026: Anna Breman
  (Governor, Chairperson) și membrii MPC Carl Hansen, Hayley Gourley, Karen Silk, Paul Conway, Prasanna Gai — doar rolurile pe care sursa le dă; generatorul îl
  îmbină în `cb_roster.yaml`, cu nota „voter = MPC member; the decision is taken by consensus, votes are not published” (`voter` e o convenție, nu un rol din sursă). Căutarea unui vorbitor se face doar printre membrii băncii lui.
- **Interviul colectiv BoE** (pooled interview) nu se colectează.


## 15. FAZA 2b — rezumate AI, strict factuale

Peste textele din 2a: comunicatul deciziei, textele conferinței, minute / accounts / summary of opinions / deliberations, discursurile trecute de filtrul de
relevanță — ultimele 4 ședințe per bancă. **Modelul doar reformulează ce scrie în document**: fără direcție, hawkish / dovish, prognoze sau evaluări. Nu mai există
nicio „interpretare” în cod: ce nu trece verificarea automată nu se scrie.

```
config/cb_summaries.yaml        provider + model (openai / gpt-5.6-terra; anthropic păstrat), limitele unui apel, vocabularul, plafoanele per rulare, prețurile cu sursă și dată
src/cb_summarize/client.py      o interfață (`complete(system, messages, schema) -> Response`), un client HTTP per provider (OpenAI Responses API, Anthropic Messages), `make_client`, RecordedClient pentru teste; fără SDK / dependență nouă; cheia nu se loghează
src/cb_summarize/schema.py      contractul de ieșire ca JSON schema strictă (structured outputs)
src/cb_summarize/prompts/       system.md + statement / transcript / minutes / speech .md, fiecare cu `prompt_version:`; versions.json fixează sha256 al fiecărui prompt
src/cb_summarize/source.py      textul unui document: comunicatele vin din store; restul se descarcă la nevoie (extragerea din 2a, sha identic) și NU se comit
src/cb_summarize/verify.py      verificarea (pură): JSON, forma, lungimi, citate verbatim, numere, cuvinte blocate / atribuite (pe familii de forme, vorbitori numiți, pronume), grounding pe fiecare punct (fragmente, sprijin, date / nume proprii, negație, replica vorbitorului), coverage
src/cb_summarize/turns.py       replicile unui transcript de conferință (etichete Fed în text, paragrafe-nume RBA, întrebările ECB în bold): cine spune fiecare paragraf; nimic altceva din document nu se schimbă
src/cb_summarize/changes.py     changes_vs_previous din redline-ul existent (cuvintele scoase / adăugate, per paragraf) — fără model, fără interpretare
src/cb_summarize/run.py         selecția, plafoanele, apelul, reîncercarea unică, marcajul validation_failed, idempotența; `estimate()` = dry run
src/cb_summarize/store.py       data/cb/summaries/summaries_YYYY-MM.json (partiții lunare, sortate, octeți deterministe) + failures.json
```

- **Contract** (un rând per `doc_id`): `doc_id`, `model`, `prompt_version`, `generated_at`, `input_sha256`, `summary` (3–6 puncte, engleză; fiecare punct = `text` + `evidence`: `paragraphs` 1–3, `fragment` verbatim 5–40 cuvinte, `paragraph` în care e, `start` / `end`, `coverage` = partea din cuvintele punctului găsită în paragrafele citate), `quotes` (1–5, fiecare
  `text` + `paragraph` + `start` / `end` = offset în textul sursă), `changes_vs_previous` (doar comunicate), `numbers` (fiecare număr din rezumat cu offset-ul lui în
  sursă), `coverage` (paragrafele folosite + dacă documentul a fost tăiat), `usage` (tokeni). Nu se stochează textul sursei.
- **Verificarea (obligatorie, înainte de scriere).** Citat: verbatim în paragraful pe care îl numește (modelul declară paragraful; offset-ul absolut îl derivă
  verificatorul și e re-verificabil: `verify_stored` — un offset greșit se detectează); ghilimele curbe, diacritice, majuscule: exact, fără normalizare
  Unicode. Număr: fiecare număr din puncte trebuie să existe în sursă, comparat pe valoare + unitate compatibilă, cu normalizare de separatori (`1,234`, spațiu
  fără întrerupere / subțire), procente (`%`, `percent`, `per cent`), puncte de bază (`25 bp` = `25bp` = `25 basis points`), fracții (`3-3/4`, `1/4`, `2¼`), semne
  minus; **fără conversie** (`1/4 percentage point` ≠ `25 basis points`) și fără număr pe care documentul nu îl scrie. Vocabular, în două trepte (`config/cb_summaries.yaml`): **mereu interzise** în puncte — hawkish, dovish, bullish, bearish, paves the way (+ paved / paving the way), chiar dacă
  documentul le folosește; **permise doar atribuite** — likely, expect(s) / expecting, signal(s) / signalled, suggest(s) / suggested (lista din config) — dacă (1) documentul folosește *același
  cuvânt, în orice formă flexionată* („I expect” într-un discurs permite „Waller expects”; „suggests” permite „suggested”; „like” NU permite „likely”) și (2) punctul îl atribuie
  băncii sau unui vorbitor numit: un subiect din `attribution_subjects` (Committee, Board, Bank, SNB, minutes …) **sau numele de familie al unui vorbitor din document** — al
  vorbitorului discursului, al membrilor băncii din roster, ori orice nume dat în text după un titlu („Chair Warsh”, „Governor Waller’s”), cu sau fără titlu — apare mai devreme în
  aceeași propoziție („The Committee expects …”, „Waller expects real GDP to grow”), niciodată în vocea rezumatului. Motivul: comunicatele reale spun „likely”, „expects”, „suggests”
  aproape în fiecare rând, iar prima rulare reală a arătat două fals-negative ale regulii (vezi mai jos). Potrivirea pe formă folosește reguli de sufix fără dicționar (`forms()`: -s / -es /
  -ed / -ing / -ies, literă dublată, -e final), aplicate ambelor părți; „-ly” nu se taie la cuvintele din vocabular. Verificarea se face doar în puncte, nu în citate (un citat e
  cuvântul băncii). **`statement-v4` / `transcript-v4` / `minutes-v4` / `speech-v4`** (`versions.json` păstrează v1–v3). Lungimi: 3–6 puncte de 20–400 caractere, ≤ 1800 în total, citate 15–500 caractere.
  Coverage: paragrafe existente.
- **Grounding pe fiecare punct** (v4, întărit în v5). Fiecare punct poartă `evidence`: 1–3 paragrafe citate + **1–3 fragmente** verbatim de 5–40 cuvinte (câte unul per afirmație; promptul cere o
  afirmație principală per punct). Verificatorul cere: (a) fiecare fragment e verbatim (caractere exacte) într-un paragraf citat — dacă e într-unul necitat, mesajul spune care — și are cel puțin
  2 cuvinte-conținut comune cu punctul (fără fragmente de umplutură; `grounding.fragment_shared_words`); (b) **≥ 85 % din cuvintele-conținut ale punctului se găsesc în paragrafele citate**
  (`grounding.min_support`). Cuvânt-conținut = alfabetic, ≥ 3 litere, nu stopword (listă fixă în `verify.py`), nu vocabular de atribuire (`grounding.attribution_words`: states, said, noted,
  reported … + subiectele băncii + numele băncii + numele vorbitorilor). Cuvintele se potrivesc pe forme (stem minimal, fără dependențe), se numără distinct, iar ce lipsește se listează în feedback
  („not found: 'bonds', 'mortgage'”). Asta prinde afirmația nesusținută **fără numere și fără cuvinte interzise**. Peste pragul de 85 %, cu aceeași reîncercare unică și același `validation_failed`:
  - **Clase stricte, tratate ca numerele** (v5): **datele, lunile, zilele săptămânii, zilele ordinale (16th), acronimele și cuvintele cu majusculă care nu încep o propoziție (persoane, instituții, locuri)**
    dintr-un punct trebuie să apară, așa cum sunt scrise, în paragrafele citate — fără date derivate („since July” din „seven weeks ago” ⇒ respins). Se exceptează numele băncii proprii, subiectele
    de atribuire și vorbitorii (sunt atribuirea, nu o afirmație); „U.S.” = „US”; „May” e lună doar în context. Anii și celelalte numere: verificați ca numere, **și în paragrafele citate** (nu oriunde în document).
  - **Negația** (v5): dacă punctul are o negație (not, no, never, n't, without, neither / nor, cannot; „not only” nu e) și propoziția cea mai apropiată din paragrafele citate (cele mai multe
    cuvinte-conținut comune; la egalitate se ia cea care se potrivește) nu are, sau invers ⇒ respins („never turn a statement into its opposite”).
  - **Replica vorbitorului, în transcripturi** (v5): transcriptul e segmentat pe replici după etichetele din sursă (`turns.py`; Fed: etichete cu majuscule în text, tăiate la fiecare etichetă, antetul
    de pagină scos; RBA: paragraf-nume; ECB: întrebările sunt paragrafele în bold, fără etichete). Un punct care numește un vorbitor (prima persoană numită; prenumele ajunge, numele de familie trebuie să
    fie pe etichetă) se sprijină doar pe paragrafe din replicile lui; un punct care nu numește pe nimeni se sprijină doar pe replicile băncii — niciodată pe întrebarea unui jurnalist sau pe replica altcuiva.
    Modelul primește paragrafele-întrebare marcate „(question)”. Hash-ul documentului rămâne cel al extragerii din 2a (doar paragrafele trimise sunt replicile).
  - **Pronumele** (v5): he / she / they atribuie un cuvânt din vocabular („Waller says X; he expects Y”) dacă banca sau vorbitorul e numit mai devreme în același punct și pronumele e în aceeași propoziție
    cu cuvântul; „it” nu e pe listă.
  **Limite cunoscute:** e o verificare lexicală, nu semantică — o schimbare de subiect cu aceleași cuvinte trece; la 85 % un punct de ~10 cuvinte-conținut poate purta un cuvânt nesuținut care nu e dată / nume;
  numele propriu la începutul propoziției nu e verificat ca nume (rămâne la pragul de 85 %); un transcript fără etichete detectabile nu are regula vorbitorului (ECB: se folosește bold-ul);
  negația se compară pe propoziția cea mai apropiată, nu pe fiecare clauză. Se completează cu citatele verbatim și cu numerele.
- **Reîncercare.** O verificare picată ⇒ **un singur** apel nou, cu ieșirea anterioară și erorile ca feedback; a doua picare ⇒ nu se scrie nimic, `failures.json` primește
  `validation_failed` (motivele), iar documentul nu se mai plătește încă o dată până la un `prompt_version` nou sau un `input_sha256` nou. Textul modelului nu e
  reparat niciodată de cod (singura atingere: un singur gard ```` ```json ```` în jurul JSON-ului se scoate — e formatare, nu conținut).
- **Idempotență și cost.** Cheia: `(doc_id, input_sha256, prompt_version)`. Un document deja rezumat nu se rezumă a doua oară și, dacă hash-ul lui e cunoscut din 2a
  (comunicate, minute, opinions), nici nu se mai descarcă; unul fără hash stocat (transcripturi, discursuri) se consideră imuabil după primul rezumat. Plafoane per
  rulare în config: `max_documents` (12) și `max_input_tokens` (250 000, numărate pe consumul real din răspuns); la depășire rularea se oprește curat și raportează.
  O eroare de API (auth, 400, rate limit / overload / server după reîncercările de transport) oprește rularea fără să scrie nimic. Ordinea: comunicat → conferință →
  minute / accounts / opinions / deliberations → discursuri (relevanță „monetary”, ultimele 60 de zile); în fiecare rang, cele mai noi întâi.
- **Prompturi.** `system.md` (regulile factuale, JSON-ul cerut) + un fișier per tip; `prompt_version` (`statement-v1`, `transcript-v1`, `minutes-v1`, `speech-v1`). sha256 al fiecărui
  prompt asamblat e în `versions.json`: **modificarea unui prompt fără versiune nouă pică suita**; o versiune nouă = documentele acelui tip se rezumă din nou.
- **Fără cheie** (`OPENAI_API_KEY`, sau cea a providerului din config): etapa se sare, nu e eroare; `--status` avertizează („WARN … the summaries stage is skipped (N candidate documents are waiting)”).
  `--status` mai arată: rezumate stocate / candidate / în așteptare / `validation_failed` (cu motiv), ultima rulare (noi, sărite, picate, tokeni, cost estimat) și totalul.
  `python -m src.cb_collect --stage summaries --summaries-dry-run [--summaries-bank USD --summaries-type statement]` măsoară ce a rămas și estimează costul fără apel și fără cheie.
- **cb-refresh**: `summaries` e ultima etapă (`--stage summaries`, nu face parte dintr-o rulare simplă), într-un pas separat: `Check for the summaries key` → `Summaries`
  (`if` pe existența secretului `OPENAI_API_KEY`, `continue-on-error: true`, `timeout-minutes: 8`) → render → commit. Secretul e vizibil doar pasului de verificare, pasului
  `Summaries` și `--status` (care doar avertizează), nu întregului job; un eșec sau un timeout al pasului nu oprește celelalte etape. Un `workflow_dispatch` are input-urile
  `stage` (all / market / … / summaries), `bank` și `type` (intră în shell doar prin `env`). **Commit după ref-ul rulat**: pe `main` — `data/cb/` + fișierele CB din `public/`,
  push pe main (condiție + gardă explicită în script); pe orice alt branch — **doar `data/cb/summaries/`**, push pe *acel* branch, niciodată pe main (gardă explicită), fără
  pagini randate și fără restul din `data/cb/` (`state.json` rămâne în runner).
- **UI.** Sloturile „summary pending” se umplu: puncte (hover = fragmentul-sursă și paragraful, în tooltip; click / Enter = caseta cu fragmentul, linkul „open the source ↗” cu ancoră
  text-fragment către pagina băncii și, pentru comunicate — al căror text e oricum comis —, paragrafele citate cu fragmentul evidențiat; pentru transcripturi / discursuri, care nu se comit, doar
  numărul paragrafului, fragmentul și linkul), `changes vs previous` pliabile (cuvintele scoase / adăugate), citate cu link către pagina băncii și **ancoră text-fragment**
  (`#:~:text=…`, doar pentru surse HTML; PDF: linkul fără ancoră), iar sub fiecare rezumat: „Factual summary, no interpretation · model · prompt_version · data”. Un rezumat
  picat la validare nu se afișează: slotul rămâne „summary pending”, motivul în tooltip. Locuri: cardul „Latest decision”, sub fiecare document din „Documents”, rândurile de
  discursuri, sub tabelul deciziilor (un rezumat pliabil per decizie).

### Decizii de execuție (abateri mici, motivate)

- **Offset-urile citatelor.** Un model nu numără fiabil caractere într-un text lung; de aceea modelul declară *paragraful* citatului (numerotat în input), iar `start` / `end`
  absolute le derivă verificatorul din acel paragraf (`find` exact) și le stochează. Verbatim-ul și offset-ul rămân verificate; `verify_stored` re-verifică orice rezumat stocat.
- **`changes_vs_previous`** nu e cerut modelului: se citește din redline-ul deja stocat (deterministă, fără risc de halucinație); paragraf dispărut = `paragraph: null`.
- **`numbers`**: lista o produce verificatorul (numerele din puncte, cu locul lor în sursă), nu modelul.
- **Sursa rezumată** e textul extras în 2a: pentru documente foarte lungi (peste `max_source_chars` = 120 000) se taie la limită de paragraf și înregistrarea spune `truncated`.
- **Providerul și modelul** (decizia lui George: OpenAI). Alese de pe paginile oficiale, citite la 2026-09-21: modele (https://developers.openai.com/api/docs/models) — gpt-6-astra
  „most capable model”, gpt-5.6-sol „complex professional work”, **gpt-5.6-terra „balances intelligence and cost”** (mijloc), gpt-5.6-luna „optimized for cost-sensitive workloads”;
  prețuri (https://developers.openai.com/api/docs/pricing) pentru terra, standard, context scurt: **2,00 USD / MTok intrare, 0,20 cached, 12,00 ieșire** (context lung 4 / 18; Batch = 50%,
  nefolosit); pagina modelului: context 1 050 000, ieșire max 128 000, structured outputs, Chat Completions și Responses. Rândurile citate sunt în config, cu sursa și data.
  API: **Responses** (`POST /v1/responses`), promptul ca mesaj `developer`, `text.format = {type: json_schema, strict: true}` cu schema din `schema.py`, `store: false`.
  **Temperatura: nu se trimite** — gpt-5.6-terra e model cu raționament („Temperature parameter: not applicable”, `reasoning.effort` în loc); determinismul vine din
  `reasoning_effort: low`, din schema strictă și din verificator, singura poartă de scriere (un model care o acceptă: `temperature: 0` în config). Tokenii de raționament sunt tokeni
  de ieșire (și se plătesc ca atare): `max_output_tokens` = 8000 îi include. Anthropic (`claude-sonnet-5`, 2 / 10 USD) rămâne configurat și testat (`provider: anthropic`), fără schemă.
- **Schema strictă.** Contractul (`summary`, `quotes` cu paragraful declarat, `coverage`) e o JSON schema cu `additionalProperties: false` și toate câmpurile obligatorii, doar cu
  cuvinte-cheie suportate de strict mode (fără `minItems` / `maxLength`): numărul, lungimile și paragraful le verifică verificatorul, care rămâne singura poartă de scriere.
  Un refuz, o ieșire tăiată la limită (`status: incomplete`, `max_output_tokens`), un JSON invalid sau lipsa `usage` sunt tratate explicit: primele trei sunt încercări picate (aceeași
  reîncercare unică, cu motivul ca feedback), a patra se estimează din text și înregistrarea spune `estimated`.
- **Cheia de idempotență**: `(doc_id, input_sha256, prompt_version, provider, model)`; înregistrarea poartă `provider`. Un alt provider sau model rezumă din nou; un eșec de validare e ținut
  și pe provider + model. `prompt_version` a devenit `*-v3` (vocabularul potrivit pe cuvântul exact) și apoi `*-v4` (grounding + vorbitori numiți) pentru toate tipurile (v1–v3 rămân în `versions.json`).
- **Costul.** Dry run pe textele reale (92 de documente încă nerezumate: ~506 000 tokeni de intrare la 4 caractere / token) + prima utilizare reală (rularea CI din 2026-09-21 pe
  branch: 8 apeluri, 26 289 tokeni de intrare, 2 999 de ieșire din care 181 de raționament, **0,0886 USD**; un comunicat Fed ≈ 1 000 intrare / 335 ieșire ≈ 0,006 USD, cu raționament 0
  la `low`). Cu ieșirea măsurată (~400 tokeni / document): backlog ≈ **1,45 USD** (≈ 2,9 dacă fiecare document ar cheltui și reîncercarea), 8 rulări de câte 12 documente ≈ 0,2 USD fiecare;
  lunar în regim stabil ≈ **0,4 USD** (până la ~0,6 cu reîncercări). Fără caching, fără Batch, fără regiune. Estimări din tokenii raportați, nu facturi.
- **Prima rulare reală (2026-09-21, pe branch).** Cele 4 comunicate Fed (ultimele 4 ședințe) au trecut validarea din prima. Transcriptul conferinței din 16 sep și discursul Waller din
  3 sep au picat de două ori (`validation_failed`, nimic scris): „likely” fără atribuire (transcript), „expects” nefolosit de document în acea formă (discursul spune „I expect”). Sunt
  fals-negative ale regulii de vocabular, nu ale numerelor sau citatelor — rezolvate în v4 (potrivire pe formă flexionată, atribuire către un vorbitor numit).
- **A doua rulare reală (v4, 2026-09-21, pe branch, CI).** 4 comunicate Fed: 4 / 4 la prima încercare (4 905 intrare / 2 061 ieșire, ≈ 0,0345 USD). Transcriptul din 16 sep: trecut după reîncercare
  (14 809 / 1 390, din care 211 raționament, ≈ 0,0463 USD; motivul primei picări nu a fost înregistrat — jurnalul nu îl avea încă). Discursul Waller: prima rulare a picat de două ori („expects” neatribuit în
  punctul 1), rularea repetată (după ce jurnalul a început să spună de ce) a trecut după reîncercare, cu aceeași cauză a primei încercări (8 877 / 1 385 și 8 851 / 1 191). Total ≈ 0,147 USD pentru 6 rezumate
  (37 442 intrare, 6 027 ieșire, 488 raționament). Jurnalul Summaries are acum `ATTEMPTS n passed at the first attempt, m after the retry, k failed twice`, `RETRIED doc: …` (motivele primei încercări)
  și `REJECTED OUTPUT doc: …` (ultima ieșire refuzată, o linie de cel mult 3 000 de caractere); înregistrarea stocată nu s-a schimbat.
- **Ce a arătat verificarea pe texte reale.** Un punct cu două afirmații are un singur fragment: în comunicatul din 16 sep, punctul „… and that it will deliver price stability” are fragmentul
  „Today's policy action will support a timelier return to the Committee's 2 percent goal.” (¶4), iar a doua jumătate e susținută de propoziția „The Committee will deliver price stability.” din
  **același ¶4** (numărată la acoperire) și e și citat. Pragul de 85 % lasă să treacă un cuvânt fără suport în punct: în transcriptul din 16 sep punctul 5 spune „since July”, iar ¶22 citat spune
  „seven weeks ago” (data e derivată de model, nu scrisă în text); în discursul Waller punctul 5 (acoperire 0,867) spune „current policy position”, cuvinte care nu sunt în ¶23 / 25 / 27 citate.
- **`no_text`**: un document a cărui pagină s-a descărcat dar nu are text extractibil (fără container cunoscut, prea puțin text, PDF scanat) primește o singură dată marcajul
  `no_text` (`data/cb/summaries/no_text.json`: url, motiv, data) și nu se mai reîncearcă (o eroare de descărcare, în schimb, e tranzitorie și se reia; un link schimbat se reia).
  `--status` le listează, slotul din pagină spune „no extractable text”.
- **`state.json` și rezumatele**: ultima rulare se scrie doar dacă rularea a făcut ceva (apeluri, rezumate noi, eșecuri, oprire, `no_text`): o rulare goală nu schimbă fișierul.

### Limitări

- Trei surse din dry run nu au conținut extractibil (două pagini „media availability” BoC și un discurs ECB fără container cunoscut): se raportează la fiecare rulare
  (`SOURCE …`), nu se marchează și nu costă nimic.
- Documentele fără hash stocat (transcripturi, discursuri) nu se re-descarcă după primul rezumat: o corectură ulterioară a paginii nu se vede.
- Prima rulare cu cheie acoperă doar 12 documente (plafonul); backlog-ul actual (~90 de documente) se termină în ~8 rulări (16 ore la 2 ore între rulări).
