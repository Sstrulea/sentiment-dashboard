# FAZA 0B — surse TEXT + latență + A3-bis + scenariul „privat"

**Rulat:** 2026-09-19 (sâmbătă), IP rezidențial local · **Branch:** `feat/central-banks` (worktree `../macro-cb`) ·
**Livrabile:** `src/cb_probe.py` extins (secțiuni `e`, `b1`…`b9`, `a3bis`; HTTP din `BaseSource`, extracție cu mini-DOM din stdlib, zero dependențe noi) + acest raport + errata în `docs/spikes/cb_sources_numeric.md`.
**Reproducere:** `python -m src.cb_probe --section b1 --section b2 …` (fiecare secțiune separat; `b9` rulează ~1 min). Textele PDF se extrag doar dacă o bibliotecă PDF e importabilă; proiectul nu are niciuna, deci fără ea celulele PDF apar ca `PDF_ONLY`. Pentru evidență am folosit `pypdf` 6.19.0 dintr-un venv temporar în scratchpad, pus pe `PYTHONPATH` (nu instalat în proiect).
**Teste:** 13 failed / 774 passed, aceleași 13 ID-uri ca pe `origin/main`. **Fără push/merge.**
**Ce NU am făcut:** nu am ocolit Cloudflare (RBNZ = BOTWALL); nu am apelat API-ul FactSet din widgetul Eurex (are un token de sesiune încorporat în pagină); nu am creat nicio dispatch, niciun workflow, nicio resursă.

---

## 0. Concluzii

1. **Textul oficial e disponibil și determinist în HTML la 6 din 8 bănci** (Fed, ECB, BoE, BoC, RBA, SNB). Excepții: **BoJ** (comunicatul e doar PDF) și **RBNZ** (BOTWALL Cloudflare pentru tot site-ul; doar canalul YouTube e accesibil).
2. **Latența publicării, unde se poate dovedi:** Fed **+0.2–0.3 min** față de 14:00 ET (Last-Modified, la toate cele 4), SNB **0.0 min** (RSS 07:30Z = 09:30 CEST), ECB **0.0 min** (RSS 14:15 CEST, doar pentru 10 sep). BoJ nu are oră fixă: PDF-urile au Last-Modified **12:28–13:06 JST** (ultimele 4). BoE, BoC, RBA nu expun nicio ștampilă de timp utilizabilă (BoC: `pubDate` din RSS e ora crearea paginii, 09:47Z, nu ora publicării 13:45Z).
3. **Cron-ul GitHub nu poate livra ≤15 min.** Din ultimele 200 de rulări programate ale `econ-refresh.yml` (28 de zile vs 531 de ticuri de cron): **63% din ticuri nu au rulat deloc**; la cele care au rulat, întârzierea față de tick e **p50 37 min, p90 100 min, max 239 min**; doar **27 de rulări** au pornit în ≤15 min; pauza dintre rulări consecutive e **p50 198 min, max 667 min**. Trigger-ul trebuie să fie extern, prin `workflow_dispatch`; repo-ul deja face asta din `api/manual-actual.py` (același endpoint).
4. **Voturi:** publicate la Fed (în comunicat), BoE (rezumat + minute), BoJ (comunicat PDF) și RBA (doar în minute, +14 zile). **Nepublicate:** ECB, BoC, SNB; RBNZ decide „prin consens" (comunicatul din 2 sep), numărul nu e public.
5. **Transcript oficial al conferinței:** Fed (PDF), ECB (HTML cu Q&A, dar URL cu hash), RBA (HTML integral, aceeași zi + audio). Lipsă: BoJ (doar rezumat JP în ziua următoare), BoC și SNB (doar declarația introductivă), BoE (interviu pooled transcris la ședințele fără MPR), RBNZ (blocat).
6. **BIS „central bankers' speeches" nu e sursă în timp real:** `date` din feed e data postării BIS, nu a discursului; pe 4 perechi verificate întârzierea față de site-ul băncii e **13–19 zile**. E bun doar ca backfill/dedup.
7. **Scenariul privat:** repo-ul e **PUBLIC** acum; Actions a consumat **688 min facturabile în 30 de zile** (453 min brut) ⇒ **$0** pe planul Free (2 000 min incluse; 34%), $4.13/lună la $0.006/min dacă n-ar exista minute incluse. **Poarta Vercel există deja în repo** (`middleware.js`, cookie + login, nu Basic Auth) și funcționează pe Hobby fără cost.
8. **A3-bis:** Eurex rămâne **JS_ONLY** (și backend-ul e un widget FactSet cu token încorporat); proxy-uri oficiale utile: **BoC T-bills 1/3/6M** și **RBA bank bills 1/3/6M** (ambele doar până la 6 luni), **CHF: nimic actual** (cuburile SNB sunt vechi). În scenariul privat (criteriul 6 = „uz personal permis") **doar Atlanta MPT (USD) și curba OIS BoE (GBP) devin complet calificate**; celelalte rămân blocate de NO_HISTORY.

---

## 1. Matrice 8 bănci × B1–B5

Criterii pre-înregistrate: **B1** text integral ≤10 min, extragere deterministă, ultimele 4 accesibile · **B5** lag ≤24 h, vorbitor + dată + URL, text integral · **B3/B4** doar lag. Coduri: BOTWALL / STALE / JS_ONLY / PDF_ONLY / NO_HISTORY / LICENSE / NONE.

| Bancă | B1 comunicat | B2 voturi | B3 conferință | B4 minute | B5 discursuri |
|---|---|---|---|---|---|
| Fed/USD | **OK** | **OK** | PARTIAL · PDF_ONLY (transcript) | **OK** (+21 z) | **OK** |
| ECB/EUR | PARTIAL · NONE (URL cu hash; latență dovedită pe 1) | **FAIL · NONE** (nepublicat) | PARTIAL · NONE (hash; 2/4 transcripturi derivabile) | PARTIAL · NONE (hash; +35 z) | PARTIAL · PDF_ONLY (3/11) |
| BoE/GBP | PARTIAL · NONE (fără ștampilă de timp) | **OK** | PARTIAL (doar zile MPR) | **OK** (0 z) | PARTIAL · NONE (fără câmp vorbitor) |
| BoJ/JPY | PARTIAL · PDF_ONLY | PARTIAL · PDF_ONLY | PARTIAL · NONE (fără transcript EN) | PARTIAL · PDF_ONLY (+8–14 z / +48–59 z) | PARTIAL · NONE (listă HTML, fără oră) |
| BoC/CAD | PARTIAL · NONE (fără ștampilă) | **FAIL · NONE** (nepublicat) | PARTIAL (declarație; fără Q&A) | **OK** (+14 z) | PARTIAL (feed amestecat) |
| RBA/AUD | PARTIAL · NONE (URL din listă; fără oră) | **OK** (în minute, +14 z) | **OK** (transcript integral + audio) | **OK** (+14 z) | PARTIAL (RSS trunchiat; listă HTML) |
| RBNZ/NZD | **FAIL · BOTWALL** | **FAIL · BOTWALL** (consens) | PARTIAL · BOTWALL (doar video YouTube) | **FAIL · BOTWALL** | **FAIL · BOTWALL** |
| SNB/CHF | **OK** | **FAIL · NONE** (nepublicat) | PARTIAL (4/an; fără Q&A) | **OK** (+28 z) | **OK** |

---

## 2. Detaliu pe celule

### B1 — comunicatul deciziei (ultimele 4 descărcate și extrase)

Extracție = un selector de container fix per bancă (fără euristici) + tăiere la marker; „boiler" = paragrafe de navigație/cookie în text (0 peste tot); „sim" = similaritatea cu comunicatul precedent (`difflib`), pentru fezabilitatea redline-ului.

| Bancă | Pattern URL / sursă | Format · selector | Paragrafe · caractere · sim | Latență (dovadă) |
|---|---|---|---|---|
| USD | `federalreserve.gov/newsevents/pressreleases/monetary{YYYYMMDD}a.htm`; RSS `feeds/press_monetary.xml` | HTML · `div#article`, cu text „liber" (linia „approved … by a 12–0 vote") | 7–8 · 926–2 245 · 0.08–0.88 | **+0.2–0.3 min** (Last-Modified 18:00:0x–18:00:17Z vs 14:00 ET, cele 4); RSS `pubDate` = 14:00 exact |
| EUR | `ecb.europa.eu/press/pr/date/2026/html/ecb.mp{YYMMDD}~{hash10}.en.html` — **hash nederivabil**; ultimele 4 obținute prin căutare + RSS `rss/press.html` (retenție 15 elemente ≈ 3 săptămâni) | HTML · `div.section` | 10–12 · 2 423–3 644 · 0.38–0.64 | RSS 14:15 CEST = oră oficială (**0.0 min**, doar 10 sep); pagina n-are Last-Modified, `article:published_time` are doar data |
| GBP | `bankofengland.co.uk/monetary-policy-summary-and-minutes/{YYYY}/{luna}-{YYYY}` | HTML · `div#output`, tăiat la „Minutes of the Monetary Policy Committee meeting" | 5–7 · 1 661–2 165 · 0.26–0.63 | **nedovedibilă** (nici Last-Modified, nici feed, nici ștampilă); oficial 12:00 UK |
| JPY | `boj.or.jp/en/mopo/mpmdeci/mpr_{YYYY}/k{yymmdd}a.pdf`; RSS `rss/whatsnew.xml` | **PDF only**; cu pypdf: 3–14 paragrafe, 2.2–12.6 k car; artefacte de spațiere („202 6", „Funds -Supplying") | 3–14 · 2 216–12 630 · 0.06–0.07 | Last-Modified **12:53, 13:06, 13:06, 12:28 JST** (28 apr, 16 iun, 31 iul, 18 sep); RSS 18 sep 12:40 JST |
| CAD | `bankofcanada.ca/{YYYY}/{MM}/fad-press-release-{YYYY-MM-DD}/` | HTML · `div.post-content` | 10–13 · 3 189–4 525 · 0.05–0.07 (text rescris integral; schelet identic) | **nedovedibilă**; RSS `pubDate` 09:47Z ≠ 13:45Z (pagina e creată înainte de lock-up) |
| AUD | `rba.gov.au/media-releases/{YYYY}/mr-{YY}-{NN}.html`; NN e secvențial, luat din `/monetary-policy/int-rate-decisions/{YYYY}/` | HTML · `div.rss-mr-content`; **necesită UA non-browser** | 9–10 · 3 342–4 166 · 0.08–0.28 | doar data (`dc.date`); oficial 14:30 AEST |
| NZD | `rbnz.govt.nz/…` | — | — | **BOTWALL** (Cloudflare `cf-mitigated: challenge`) |
| CHF | `snb.ch/en/publications/communication/press-releases-restricted/pre_{YYYYMMDD}`; RSS `public/rss/en/mopo` | HTML · `div.a-text` | 11–12 · 3 640–4 402 · 0.11–0.37 | RSS **07:30Z = 09:30 CEST (0.0 min)** |

Concluzie B1: criteriul „≤10 min" e **dovedit** doar la Fed, SNB (și ECB pe un caz). Pentru rest, latența trebuie **măsurată de propriul job** (prima observare a paginii vs ora oficială); nu există dovezi publice.

### B2 — voturi / dizidențe (ultimele 4)

| Bancă | Unde apar · format | Ultimele 4 |
|---|---|---|
| Fed | în comunicat (HTML): „approved … by a X–Y vote", „Voting against … were …" + motivul | 29 apr **8–4** (Miran pt. reducere; Hammack, Kashkari, Logan contra „easing bias"), 17 iun 12–0, 29 iul **9–3** (Hammack, Kashkari, Logan), 16 sep 12–0 |
| ECB | nepublicat (decizie prin consens; „account" +35 z fără numărătoare) | „nepublicat" |
| BoE | rezumatul MPC („voted by a majority of X–Y"); pe membru în minute (aceeași pagină); istoric machine-readable: `…/monetary-policy-summary-and-minutes/mpcvoting.xlsx` (link găsit, nedescărcat) | 30 apr **8–1**, 18 iun **7–2**, 30 iul **6–3**, 17 sep **6–3** (menține 3.75%) |
| BoJ | în comunicatul PDF („by a 7–2 majority vote"); numărătoare parsată cu pypdf | 28 apr **6–3**, 16 iun **7–1**, 31 iul **8–1**, 18 sep **7–2** |
| BoC | nepublicat (Summary of deliberations: consens) | „nepublicat" |
| RBA | **doar în minute** (+14 z): „Five members voted in favour; four …" | 17 mar **5–4**, 5 mai **8–1**, 16 iun unanim, 11 aug unanim |
| RBNZ | „reached consensus" (comunicat 2 sep, via căutare); Record of Meeting blocat | BOTWALL |
| SNB | nepublicat | „nepublicat" |

### B3 — conferința de presă

Canale YouTube oficiale (feed keyless `youtube.com/feeds/videos.xml?channel_id=…`, câte 15 intrări; `channel_id` rezolvat din linkul de pe site-ul băncii): Fed `UCAzhpt9DmG6PnHXjmJTvRGQ` · ECB `UCXB8fM4VyQubRu3UVGhd3wA` (`/user/ecbeuro`) · BoE `UCY70MMJ8Rj4wtwt7bVN6hIA` (`/user/bankofenglanduk`) · BoJ `UC32Yu7NyStgmKYsXvYofPvQ` (`/user/BOJchannel`) · BoC `UCY4EvEbIox0M4JuEsu5OKnQ` (`/user/bankofcanadaofficial`) · RBA `UCaLmgAMEglL-yGvuGzTx6ig` (`youtube.com/RBAInfo`) · RBNZ `UC1v0CeB83SX6Px9r5KT0NuQ` (`/user/reservebankofnz`, din căutare) · SNB `UC4vQTVEqtj2orppzBkdGmyg` (`/@swissnationalbank`).

| Bancă | Se ține? | Video oficial | Transcript oficial |
|---|---|---|---|
| Fed | la fiecare (8/8) | YouTube: 29 iul **+2.1 h**, 16 sep **+1.7 h** după 14:00 ET (VOD; celelalte 2 au ieșit din fereastra de 15 intrări) | PDF `federalreserve.gov/mediacenter/files/FOMCpresconf{YYYYMMDD}.pdf`, există pentru toate 4; Last-Modified 17 sep 14:51Z pentru 16 sep (≈ +21 h; la celelalte e o revizie ulterioară, deci prima publicare nu se poate dovedi) → **PDF_ONLY** |
| ECB | la fiecare (8/8) | YouTube „Live – ECB Governing Council Press Conference" 10 sep +1.6 h | HTML „Monetary policy statement (with Q&A)" `press_conference/monetary-policy-statement/2026/html/ecb.is{YYMMDD}~{hash}.en.html` (≈ 33 k car), **doar 2/4 URL-uri derivabile** (11 iun, 10 sep) |
| BoE | doar la ședințele cu MPR (feb/apr/iul/nov); la celelalte „pooled broadcast interview" | YouTube „Monetary Policy Report Press Conference, 30 July" **+2.4 h** | interviul pooled: transcript în news feed (17 sep 14:30 BST, 18 iun 13:30); pentru conferința MPR nu am găsit transcript |
| BoJ | la fiecare MPM, ~15:30 JST (pagina BoJ) | YouTube BOJchannel (JP): 18 sep 07:54Z (16:54 JST); 31 iul apare abia 3 aug | **niciun transcript EN**; rezumat doar în japoneză în ziua lucrătoare următoare; transcript integral după 10 ani |
| BoC | la fiecare decizie (pagina de blackout) | pagina „Press Conference: Policy Rate Announcement — {lună} 2026" (webcast); pe YouTube doar clipuri „ICYMI" | „Opening statement" HTML `…/opening-statement-{YYYY-MM-DD}/` (4/4); **fără Q&A** |
| RBA | la fiecare (15:30 AEST) | **audio** MP3 (fără video pe canalul YouTube oficial) | HTML integral `speeches/{YYYY}/mc-gov-{YYYY-MM-DD}.html` (148 par., ≈ 43 k car, `dc.date` = aceeași zi) — UA non-browser |
| RBNZ | la fiecare anunț OCR (MPS și MPR), 15:00 NZ | YouTube: 8 apr +1.8 h, 27 mai **+17.0 h**, 8 iul +3.1 h, 2 sep **+16.5 h** | pe site (blocat) → manual |
| SNB | doar trimestrial | YouTube: 19 iun **+31 h** (ziua următoare) | declarația introductivă HTML `speeches-restricted/ref_{YYYYMMDD}_mslanmargpe` (4/4, la 10:00 CEST); fără Q&A |

### B4 — minute / accounts / summaries (lag față de decizie)

| Bancă | Sursă · format | Lag (ultimele 4) |
|---|---|---|
| Fed | `fomcminutes{YYYYMMDD}.htm` (HTML; data din calendar) | 21 z (20 mai, 8 iul, 19 aug; 16 sep → 7 oct, viitor) |
| ECB | „Account of the monetary policy meeting" `press/accounts/2026/html/ecb.mg{YYMMDD}~{hash}.en.html` (hash) | 35 z pentru 23 iul (→ 27 aug); celelalte 3 URL-uri nederivabile |
| BoE | aceeași pagină ca rezumatul | 0 z (4/4 conțin „Minutes of the MPC meeting") |
| BoJ | Summary of Opinions (PDF `opinion_{YYYY}/opi{yymmdd}.pdf`) + Minutes | Opinions +14, +8, +10 z (13 oct: 1 oct, viitor); Minutes +52, +50, +59 z (18 sep → 5 nov, +48). PDF-urile apar pe server cu 1–3 zile înainte de data anunțată (Last-Modified) |
| BoC | Summary of Governing Council deliberations (HTML; RSS `summary-of-deliberations/feed/`) | 14 z (4/4) |
| RBA | `rba-board-minutes/{YYYY}/{YYYY-MM-DD}.html` | 14 z (4/4) |
| RBNZ | Record of Meeting | BOTWALL |
| SNB | `communication/summaries/zus_{data+28z}` (URL derivabil) | 28 z (4/4, toate 200) |

### B5 — discursuri și audieri (surse oficiale)

| Bancă | Sursă | Câmpuri în feed | Lag / volum 30 z | Audieri | Text integral |
|---|---|---|---|---|---|
| Fed | `feeds/speeches.xml` + `feeds/testimony.xml` | vorbitor (prefix „Bowman, …"), dată+oră, titlu, URL | Last-Modified pagină − feed **+0.2–0.3 min**; 5/30 z | da (`testimony.xml`: Warsh, Semiannual Monetary Policy Report, 14 iul) | HTML |
| ECB | `rss/press.html` (filtrat `ecb.sp*`/`ecb.in*`) | vorbitor în titlu („Nume: titlu"), dată+oră | fără Last-Modified; 11/30 z | niciuna în fereastră | HTML; **3/11 PDF-only** (ex. Lane, Schnabel) |
| BoE | `rss/speeches` (50 elem.) | dată+oră, titlu, URL; **vorbitor doar în URL** | 3/30 z | nu apar | HTML |
| BoJ | listă HTML `about/press/koen_{YYYY}/index.htm` (fără RSS dedicat; `whatsnew.xml` amestecă tot) | doar data (din URL `ko{yymmdd}a.htm`) | 3/30 z; ora nedovedibilă | — | HTML |
| BoC | `content_type/speeches/feed/` | dată+oră (a evenimentului; 2 elemente **cu dată viitoare**), titlu; fără vorbitor | 4/30 z | — | HTML |
| RBA | listă HTML `/speeches/` (RSS păstrează 1 element) | dată (din URL), titlu, cod vorbitor (`gov`/`dg`/`ag`) | 8/30 z | **da** (18 sep 09:30, House Standing Committee on Economics) | HTML |
| RBNZ | — | — | — | — | **BOTWALL** |
| SNB | `public/rss/en/speeches` + `…/interviews` | vorbitor în titlu, dată+oră (07:30Z etc.) | 2/30 z | — | HTML |

**BIS** (`bis.org/doclist/cbspeeches.rss`, 50 elem., 17 aug–16 sep): câmpuri `title`, `dc:creator` (vorbitor), `date` (doar ziua, **data postării BIS**), `description` („Speech by Mr X, Vice-President of the European Central Bank, …"). Volum 30 z: USD 3, EUR 5, GBP 1, JPY 2, CAD 1, AUD 2, NZD 0, CHF 1. Feed-ul e modificat cu ≥2 zile după cel mai nou element. **Cheie de dedup** bancă↔BIS: (prenume-nume de familie al vorbitorului + similaritate titlu ≥0.6); potriviri: Fed 2/6 cu întârziere BIS 13 și 18 z, ECB 1/11 (15 z), SNB 1/2 (19 z) — data nu se poate folosi în cheie.

**Roster oficial (pagini HTML; extragerea nume+funcție ar fi regex/DOM, nepornită aici):** Fed `monetarypolicy/fomc.htm` (membri, alternanți și **tabel de rotație a votanților 2026/2027/2028**), ECB `ecb/orga/decisions/govc/html/index.en.html` (24 nume), BoE `about/people/monetary-policy-committee`, BoJ `about/organization/policyboard/index.htm` (Policy Board, 9 nume), BoC `about/people/governing-council/`, RBA `about-rba/boards/monetary-policy-board.html`, SNB `the-snb/organisation/supervisory-management-boards`, RBNZ blocat.

---

## 3. B6–B9 și A3-bis, pe scurt

### B6 — nume de evenimente FF pentru bănci centrale (ultimele 12 săptămâni, 2026-06-27 →)

Surse: `data/ff_raw/ff_weekly_*.json` (de la 2026-08-01) + `data/archive/ff_calendar_range.json` (până la 2026-07-03); **golul 4 iul–31 iul nu e acoperit de niciuna**, iar parquetul conține doar evenimentele mapate în catalog.

| Val. | Nume distincte (tip) |
|---|---|
| USD | „Federal Funds Rate" (decizie) · „FOMC Statement" · „FOMC Press Conference" · „FOMC Economic Projections" · „FOMC Meeting Minutes" · „Fed Monetary Policy Report" · „Beige Book" · „Fed Chairman Warsh Speaks" · „FOMC Member {X} Speaks" (Barkin, Hammack, Schmid, Musalem, Goolsbee, Daly, Bowman, Waller, Cook, Barr) · zgomot non-CB: „President Trump Speaks", „Treasury Sec Bessent Speaks" |
| EUR | „Main Refinancing Rate" · „Monetary Policy Statement" · „ECB Press Conference" · „ECB Monetary Policy Meeting Accounts" · „ECB President Lagarde Speaks" · (zgomot: „German Buba President Nagel Speaks", „ECOFIN/Eurogroup Meetings") |
| GBP | „Official Bank Rate" · „MPC Official Bank Rate Votes" · „Monetary Policy Summary" · „BOE Monetary Policy Report" · „Monetary Policy Report Hearings" · „BOE Gov Bailey Speaks" · „MPC Member {Pill, Mann, Breeden} Speaks" |
| JPY | „BOJ Policy Rate" · „Monetary Policy Statement" · „BOJ Press Conference" · „BOJ Outlook Report" · „BOJ Summary of Opinions" · „Monetary Policy Meeting Minutes" |
| CAD | „Overnight Rate" · „BOC Rate Statement" · „BOC Press Conference" · „BOC Summary of Deliberations" · „BOC Gov Macklem Speaks" |
| AUD | „Cash Rate" · „RBA Rate Statement" · „RBA Monetary Policy Statement" · „RBA Press Conference" · „Monetary Policy Meeting Minutes" · „RBA Gov Bullock / Deputy Gov Hauser / Assist Gov {Hunter, Kent, Jones} Speaks" |
| NZD | „Official Cash Rate" · „RBNZ Rate Statement" · „RBNZ Monetary Policy Statement" · „RBNZ Press Conference" · „RBNZ Gov Breman Speaks" |
| CHF | în fereastră doar „SNB Chairman Schlegel Speaks", „Gov Board Member Martin Speaks", „SNB Financial Stability Report" (nicio decizie în interval; în arhivă: „SNB Policy Rate", „SNB Monetary Policy Assessment", „SNB Press Conference") |

**Consecință:** numele **nu sunt unice între valute** („Monetary Policy Statement" = ECB *și* BoJ; „Monetary Policy Meeting Minutes" = BoJ *și* RBA) ⇒ potrivirea trebuie făcută pe (valută, nume), nu pe nume.

### B7 — trigger ≤15 min

(a) **Ora oficială a deciziei:** Fed 14:00 ET · ECB 14:15 CET/CEST (conferință 14:45) · BoE 12:00 UK · **BoJ variabilă** (Last-Modified PDF 12:28–13:06 JST în ultimele 4; conferință ~15:30) · BoC 09:45 ET (conferință 10:30) · RBA 14:30 AEST (conferință 15:30) · RBNZ 14:00 NZ (conferință 15:00) · SNB 09:30 (conferință 10:00). Pentru BoJ: fereastră de polling ~11:30–13:30 JST în zilele de decizie.
(b) **Cron GitHub:** vezi §0.3 — 63% ticuri pierdute, p50 37 min / p90 100 min / max 239 min; ⇒ **nu se poate baza pe `schedule`**. Durata unei rulări `econ-refresh` (`updated_at − run_started_at`): p50 1.3 min, p90 1.6, max 2.9; deploy-ul Vercel nu l-am măsurat.
(c) **Dispatch extern:** `POST https://api.github.com/repos/Sstrulea/sentiment-dashboard/actions/workflows/econ-refresh.yml/dispatches` cu `{"ref":"main"}` → 204. Workflow-ul e `active`, declară `workflow_dispatch`, are `concurrency: econ-refresh` (`cancel-in-progress: false`). Token: fine-grained cu **Actions: Read/write** (cum documentează `api/manual-actual.py`, care deja face acest apel; în ultimele 30 de zile workflow-ul a avut 37 de rulări `workflow_dispatch`, fără a putea distinge sursa); tokenul `gh` curent are scope-urile `repo, workflow, gist, read:org`. Latența request→start nu am măsurat-o (ar cere o dispatch reală; în API `run_started_at = created_at`). Nu am creat nimic.

### B8 — PDF
Doar-PDF: comunicatul **BoJ** (4/4), Summary of Opinions și Minutes **BoJ**, transcriptul conferinței **Fed**, ~3/11 discursuri ECB (Lane, Schnabel), MPS **RBNZ** (`mps_report_sep2026.pdf`, blocat). `requirements.txt` **nu are** extractor PDF. Propunere: **`pypdf`** (pur Python, BSD, fără dependențe de sistem, pin exact la instalare). Testat (fără a-l instala în proiect): comunicatul BoJ din 18 sep → 6 pagini, 12 630 caractere, 14 paragrafe, conține „1.25 percent" și nota „September 24, 2026"; artefacte de spațiere („202 6") ⇒ necesită normalizare; extracția e deterministă (același `sha` la rulări repetate). Alternativă: `pdfminer.six` (layout mai bun, mai greu).

### B9 — scenariul „privat" (doar verificări)
- **Vizibilitate repo:** `Sstrulea/sentiment-dashboard` = **PUBLIC**.
- **Minute GitHub Actions, ultimele 30 de zile (de la 2026-08-20), toate workflow-urile:** 488 rulări, 453 min brut, **688 min facturabile** (rotunjit în sus per job). `econ-refresh` 483 (283 rulări: 246 programate + 37 dispatch), `retail` 166, `daily` 31, `cot-freshness` 4, `weekly` 4.
- **Cost dacă devine privat** (Linux 2-core $0.006/min, docs.github.com „Actions runner pricing", citit azi): planul Free include 2 000 min (Pro/Team 3 000) ⇒ **$0** (34% din cota Free; rămân 1 312 min); fără minute incluse: **$4.13/lună**. Planul contului nu l-am putut citi (tokenul nu are scope `user`; presupun Free). Regula de rotunjire per job nu apare pe pagina de docs citită (am aplicat rotunjirea în sus). Un job de trigger extern per eveniment (≈ 2 min facturabile) e neglijabil; polling la 5 min non-stop (~8 600 rulări/lună) ar depăși cota.
- **Poarta de parolă pe Vercel:** deja implementată — `middleware.js` (Routing Middleware, cookie `dash_auth` + formular `POST /login`, tokenuri din `DASH_TOKENS`, **nu HTTP Basic**), fără `config.matcher` ⇒ acoperă toate căile, inclusiv `/api/*`. **Cost: $0 pe Hobby** (Routing Middleware se taxează pe modelul Fluid compute; Hobby include 4 CPU-hrs, 1 000 000 invocări funcții, 1 000 000 Edge Requests). **Vercel „Password Protection" nu e disponibil pe Hobby**; pe Pro costă **$20/lună per proiect** (+ $20/loc de dezvoltator). Hobby e limitat la uz personal/necomercial.
- **Impact asupra `api/manual-actual.py`:** rulează după middleware; apelurile din browserul deja autentificat (cookie pe același host) trec; apelurile externe (curl, un webhook) primesc 303 → `/login`. Endpoint-ul are propriul `X-Manual-Token`, deci gate-ul e un strat suplimentar. Un trigger extern de refresh trebuie să lovească direct API-ul GitHub, nu Vercel.
- **Neverificat:** că Vercel Git integration pe Hobby funcționează cu repo privat al contului; că poarta e activă acum în producție (nu am apelat URL-ul de producție).

### A3-bis — reîncercare țintită

**(a) Eurex** (FESR €STR, FEU3 Euribor, FSR3 SARON) — **JS_ONLY.** Paginile de produs răspund 200 dar au 0 prețuri în HTML. FSR3 și FEU3 încarcă un widget cu **backend FactSet Digital Solutions** (`eurex-api.factsetdigitalsolutions.com`) și un `connectionToken` de sesiune încorporat în pagină, plus `s3.tradingview.com/tv.js`; FESR nu are nici măcar widget de cotații. Pagina „Market statistics (online)" (`…/100!onlineStats`) răspunde 200 dar doar cu scheletul (12 k car), fără tabel „Settlement" (se populează prin XHR). **Nu am apelat API-ul FactSet** (token emis widgetului, date sub licență Eurex/FactSet). Fișierele „Product and Price Report" CB001/CB002 sunt de fee-uri (deja verificat în 0A). ⇒ **JS_ONLY + LICENSE.**

**(b) Proxy-uri oficiale (nu OIS):**

| Val. | Sursă | asof · lag | Orizont | 1 2 3 4 5 6 | Verdict |
|---|---|---|---|---|---|
| CAD | BoC Valet T-bills `TB.CDN.30D/90D/180D.MID` (JSON): 1M 2.28 · 3M 2.33 · 6M 2.58 | 2026-09-17 · 1 | 6 luni (≈ 3 ședințe BoC) | Y Y **N** Y Y Y | PARTIAL · **SHORT_HORIZON** (proxy). Licență: BoC permite „freely use, copy, distribute and transmit" cu atribuire |
| AUD | RBA F1 „EOD bank bills" (sursă ASX) `FIRMMBAB30D/90D/180D`: 1M 4.41 · 3M 4.68 · 6M 5.07; coloanele OIS din F1 oprite din 2022-12 | 2026-09-17 · 1 | 6 luni | Y Y **N** Y Y ? | PARTIAL · **SHORT_HORIZON** (+ licență neclară: date ASX republicate de RBA; termeni RBA nepreluați) |
| CHF | SNB `zirepo` (SARON compus 1M/3M/6M, **retrospectiv**) și `rendoblid` (randamente Confederație) | 2026-08-14 · 25 zile lucr. / 2025-07-31 | — | Y **N** **N** Y Y ? | **FAIL · STALE** — niciun proxy prospectiv gratuit și actual |

**(c) Sondaje oficiale ale participanților** (fallback pentru scenariul public; toate **publică după ședință**, deci criteriul 2 pică prin construcție — utile ca cross-check, nu ca sursă „în timp real"):

| Bancă | Sondaj | Frecvență · publicare | Orizont | Format | Termeni |
|---|---|---|---|---|---|
| Fed | NY Fed **SME** | 8/an, înaintea fiecărui FOMC; rezultate ~3 săptămâni **după** ședință (după minute) | fed funds așteptat la următoarele ședințe + capete de trimestru/an | **PDF** (445 linkuri PDF pe pagină; XLSX nevăzut) → PDF_ONLY | Terms of Use — fraza de permisiune neextrasă; de verificat |
| ECB | **SMA** (Survey of Monetary Analysts) | 8/an; luni în săptămâna de după ședința GC | traseu median DFR pentru ședințe/orizonturi | **PDF** agregat | reutilizare cu menționarea sursei (pagina de copyright neparsată complet) |
| BoE | **Market Participants Survey** | ~8/an (2026: feb, mar, apr, iun, iul, sep); ediția din sep publicată **18 sep** (ziua *după* MPC 17 sep), sondaj 2–4 sep | Bank Rate modal după următoarele ședințe + distribuții de probabilitate | **XLSX + tabele HTML** | OGL v3.0 |
| BoC | **Market Participants Survey** | trimestrial; ~2 săptămâni după decizia din ian/apr/iul/oct (T2 2026: 27 iul) | prognoza ratei de politică pe orizonturi (Canada + SUA) | tabele **HTML** | „freely use, copy, distribute and transmit" cu atribuire |

**Scenariul privat (criteriul 6 = „uz personal permis"):** cu licența deblocată, **USD (Atlanta MPT) trece toate cele 6 criterii** (lag 1, orizont 2029-12, istoric 2023-03 → 873 zile; caseta de licență: „personal and educational purposes only") și **GBP (curba OIS BoE) le trece** (OGL v3.0, istoric 433 zile). **JPY/CAD/AUD/NZD rămân blocate de NO_HISTORY** (JPX/MX/ASX-JSON sunt snapshot-uri; istoricul începe din ziua în care pornește propriul job) — schimbarea de licență nu le ajută. EUR rămâne proxy; CHF rămâne FAIL.

---

## 4. Recomandare — sursă + fallback per bancă × tip

| Bancă | Comunicat | Voturi | Conferință (video / transcript) | Minute | Discursuri | Trigger |
|---|---|---|---|---|---|---|
| Fed | HTML `monetary{YYYYMMDD}a.htm` (RSS ca semnal) | din comunicat | YouTube feed / **PDF** transcript (extractor) | HTML, +21 z | `speeches.xml` + `testimony.xml` | dispatch la 14:00 ET |
| ECB | HTML din RSS `rss/press.html`; **salvăm URL-ul cu hash la prima apariție** | — | YouTube / `ecb.is…` (Q&A) prin RSS | account (hash) +35 z | RSS `press.html` (sp/in) | dispatch la 14:15 CET |
| BoE | HTML din pattern (fără ștampilă: măsurăm noi) | din rezumat (+ `mpcvoting.xlsx`) | YouTube (MPR) / transcript interviu în news feed | aceeași pagină | `rss/speeches` (vorbitor din URL) | dispatch la 12:00 UK |
| BoJ | **PDF** (extractor) `k{yymmdd}a.pdf`; RSS `whatsnew.xml` ca semnal | din PDF | YouTube JP / — (fără transcript EN) | PDF (opinions +8–14 z; minutes ~2 luni) | listă HTML `koen_YYYY` | polling 11:30–13:30 JST |
| BoC | HTML din pattern | — | pagina webcast + opening statement | summary +14 z (RSS) | `speeches/feed` (filtrat) | dispatch la 09:45 ET |
| RBA | HTML din lista `int-rate-decisions` (UA non-browser) | din minute (+14 z) | audio + **transcript HTML** aceeași zi | minute +14 z | listă HTML `/speeches/` | dispatch la 14:30 AEST |
| RBNZ | **manual / BOTWALL**; doar YouTube | consens | YouTube (video +2–17 h) | manual | manual | — |
| SNB | HTML din pattern `pre_YYYYMMDD` (RSS `mopo`) | — | YouTube (+31 h) / remarks HTML | summary +28 z (URL derivabil) | RSS speeches + interviews | dispatch la 09:30 CET |

**Arhitectură minimă pentru ≤15 min:** un scheduler extern (nu `schedule:` din GitHub) care lovește `workflow_dispatch` la ora oficială (+ fereastră de polling pentru BoJ), un job scurt (~1.3–3 min) care descarcă textul și comite `public/`, iar Vercel deployează la push. Nu e măsurat: latența request→start și deploy-ul Vercel.

### Goluri explicite
1. **RBNZ:** tot textul (comunicat, voturi/record, minute, discursuri, roster) e BOTWALL; rămân doar YouTube și manual.
2. **Latență nedovedibilă** pentru BoE, BoC, RBA (fără Last-Modified/feed/ștampilă): trebuie măsurată de propriul job de la prima observare.
3. **ECB:** URL-uri cu hash ⇒ descoperire doar prin RSS (retenție ≈ 3 săptămâni) sau salvare la prima apariție; istoricul dinaintea pornirii nu e derivabil.
4. **PDF-only:** BoJ (comunicat, opinions, minutes), transcript Fed ⇒ necesită `pypdf` (propus, neinstalat).
5. **Fără transcript oficial:** BoJ (EN), BoC (Q&A), SNB (Q&A), BoE la conferințele MPR (negăsit), RBNZ (blocat).
6. **Voturi nepublicate:** ECB, BoC, SNB (RBNZ: consens).
7. **BIS** nu e în timp real (lag 13–19 z): doar backfill/dedup.
8. **FF:** nume nu sunt unice între valute; **gol 4–31 iulie** în datele brute; CHF nu are evenimente numerice de decizie din 2025-06.
9. **A3:** CHF fără proxy actual; EUR doar proxy; proxy-urile CAD/AUD până la 6 luni; sondajele publică după ședință.
10. **B9:** planul GitHub al contului și activarea porții Vercel în producție — neverificate; fără măsurare a latenței dispatch.

---

*STOP după raport — fără push, merge, adaptere, parquet sau UI. Commit local doar pe `feat/central-banks`.*
