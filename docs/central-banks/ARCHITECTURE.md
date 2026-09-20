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
| 11 | AI (faza 2) | `claude-sonnet-5`, fixat în config; cheia = secret GitHub în faza 2 |
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
| **PROXY** | curbe de stat / bills: `bază = media curbei pe [as-of, prima dată efectivă) − rata curentă`; implicit = medie − bază; lunar (per ședință) sau pe tenor | EUR ECB AAA (per ședință); UST, BoC bills, RBA bank bills (doar cross-check, pe tenor) |
| n/a | CHF (fără sursă); NZD față de OCR (fără spread BKBM–OCR: se arată nivelul BKBM, nu bp) | — |

Instrumentul principal per valută: USD MPT (cross-check Treasury), EUR ECB AAA, GBP BoE OIS, JPY JPX, CAD COA apoi CRA dincolo de
orizontul COA (cross-check bills BoC), AUD ASX IB (cross-check RBA bills), NZD ASX BB, CHF n/a. Metodele exacte au prioritate;
ferestrele 3M completează doar ședințele pe care EXACT nu le acoperă.

Reguli ale lanțului EXACT (toate cu test + mutație):
- fereastra de coadă: `N − d_pre < 5` (ședință în ultimele 5 zile ale lunii) și luna următoare fără ședință → `r_post` = media lunii
  următoare (formula ar amplifica zgomotul de N/(N−d_pre) ori); cu ședință în luna următoare → neidentificat, cu motiv;
- două ședințe în aceeași lună → neidentificat;
- ședința care urmează unui neidentificat nu are „pas” (ar acoperi două ședințe): `step_bp` n/a cu motiv, rata și cumulatul rămân.

### Ieșiri per bancă (la un as-of)

1. **Traiectorie**: punct pe ședință (data efectivă sau fereastra, rata implicită, cumulat în bp față de bază, metodă, sursă + as-of,
   `stale`), motiv când e n/a.
2. **Următoarea ședință**: pas implicit (EXACT / CURVE / PROXY lunar) și probabilitate doar pentru EXACT / CURVE: `n = ⌊|pas|/25⌋`,
   `p = (|pas| − 25n)/25`; P(n+1 mișcări) = p, P(n) = 1 − p, în direcția pasului. Altfel n/a + motiv.
3. **Cumulat la ultima ședință din 2026 și din 2027** (WINDOW: fereastra `pick_window`, UPPER_BOUND; dacă ședința e deja decisă e parte
   din bază → 0, `DECIDED`).
4. **stale** = lag (zile lucrătoare în calendarul sursei) > `stale_after_bd` (implicit 2; se poate suprascrie pe sursă).
5. **GAP (bp) = piață − bancă.** Fed: mediana dot-urilor (ultimul SEP) față de piață la ultima ședință din 2026 / 2027 / 2028, plus
   distribuția dot-urilor. Pentru 2028 nu există calendar: se presupune ultima ședință = a doua miercuri din decembrie (tiparul
   Fed), marcat în notă. RBNZ: doar pe aceeași bază — proiecția MPS „90-day bank bill” pe trimestru față de ASX BB al cărui interval de
   90 de zile începe în acel trimestru (cel mai apropiat de mijloc); n/a cât MPS-ul e placeholder. Celelalte: n/a („nu publică o cale proprie”).
6. **Repricing 1s / 1l** = Δ peste 5 / 21 zile lucrătoare (calendarul sursei) al cumulatului la sfârșitul lui 2026 / 2027 și al pasului
   următoarei ședințe, recalculând traiectoria la as-of-ul anterior. n/a cu motiv când istoricul nu ajunge (JPX, MX, ASX sunt snapshot:
   istoric doar din 2026-09-18). **Dacă între cele două date a căzut o decizie**, cumulatul e față de altă bază: se marchează
   `base_change_bp` și se arată în plus Δ al NIVELULUI implicit (`level`).
7. **Surprize / reacție** (ultimele 4 decizii + următoarele): față de consens (din `decisions.parquet`); față de piață T−1 = pasul decis −
   pasul implicit la T−1 (doar EXACT / CURVE / PROXY lunar); reacția = Δ(EOD T − EOD T−1) al nivelului implicit la următoarea ședință și la
   ultima ședință a anului.
8. **Cross-check-uri**: USD MPT vs Treasury (fără bază → bază scoasă), CAD lanțul COA vs CRA pe aceeași perioadă, AUD lanțul IB vs RBA bank
   bills la 3M / 6M.

### Perechi (cele 28 din `data/economic_instruments.yaml`, `type: fx`)

`bază − cotată` pentru rata curentă (bp), rata implicită și cumulatul la ultima ședință din 2026 / 2027, plus repricingul diferențialului
(Δ al diferențialului implicit al nivelurilor, deci o decizie între date nu intră). Flag = cel mai slab dintre cele două picioare
(`EXACT < CURVE < UPPER_BOUND < PROXY`); n/a dacă lipsește un picior (CHF, NZD față de OCR) sau un istoric.

### Decizii de execuție (de confirmat)

- **PROXY nu extrapolează plat sub primul tenor al curbei.** Curba ECB AAA începe la 3M: o ședință al cărei interval începe cu mai mult de
  1 lună înaintea primului tenor (`EXTRAPOLATION_MONTHS`) e n/a („curba nu spune nimic despre ea”); altfel pasul ar ieși ~0 din
  extrapolare, nu din piață. Consecință: pentru EUR, următoarea ședință (la ~1.6 luni) și surprizele față de piață T−1 sunt n/a.
- **Excluderea din spread în jurul datei efective** se face pentru schimbări de rată, nu pentru menținere.
- **E3 (regula ferestrei)** din 0A („prima fereastră cu start ≥ eff − 3 zile”) e înlocuită de regula de mai sus (7 zile, cea mai apropiată);
  `cb_probe.pick_window` a fost aliniată, `docs/spikes/cb_sources_numeric.md` are nota.
- Ultima ședință Fed din 2028: presupusă (a doua miercuri din decembrie).

### Față de 0A / 0B (as-of 2026-09-18, date înghețate în `tests/fixtures/cb_engine/`)

Valori BRUTE (înainte de spread), aceeași metodă: USD MPT 4.310 / 4.638, JPY 1.475 / 2.1075, CAD CRA 2.775 / 3.595, COA 9 dec 2.6014, AUD
14 dec 2027 4.885 — toate în ±0.5 bp de 0A/0B; funcția `implied_1m_chain` din 0A reproduce CAD 28 oct (+11.6 bp ≈ +12) și AUD 8 dec (4.7259 ≈ 4.726).
Metoda schimbată (nou / 0A / diferență): spread USD +2.0 bp (mediană) vs −2.5 bp (o zi) → USD 2026 4.290 vs 4.335 (−4.5 bp); CAD 28 oct
+14.0 vs +12 (+2.0; BoC efectiv +1, coadă → media lui noiembrie); AUD 8 dec 4.7343 vs 4.726 (+0.8; coada 29 sep); GBP 2026 4.160 vs 4.051 (+10.9;
media pe interval față de valoarea punctuală, curba crește); GBP 2027 4.854 vs 4.855; EUR 2026 2.611 vs 2.821 (−21.0), 2027 3.078 vs 3.397
(−31.9): baza PROXY +26.1 bp scăzută.
