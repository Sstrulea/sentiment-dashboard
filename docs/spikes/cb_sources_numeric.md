# FAZA 0A — Spike surse numerice + calendar pentru /central-banks

**Rulat:** 2026-09-19 (sâmbătă), de pe un IP rezidențial local · **Branch:** `feat/central-banks` (worktree `../macro-cb`, din `origin/main` c9c035f1) ·
**Livrabile:** `src/cb_probe.py` (probe pe contract `RateSource`, HTTP reutilizat din `src/rate_sources.BaseSource`) + acest raport. Nimic altceva modificat.
**Reproducere:** `python -m src.cb_probe [--bank USD] [--section a1..a5] [--json-out F]` (~1 min; nu scrie nimic fără `--json-out`).
**Dependențe noi:** zero (`openpyxl` e deja în `requirements.txt`; fără lxml/bs4 → parsere regex). **Teste:** 13 failed / 774 passed, aceleași 13 ID-uri ca pe `origin/main` (eșecurile sunt preexistente).
**Convenție lag:** zile lucrătoare între ultima observație și ultima zi lucrătoare (sâmbăta, date din vineri = 0, din joi = 1). Criteriul pre-înregistrat „lag ≤ 1” se aplică așa.

---

## 0. Concluzii

1. **A1 (rata de politică) e rezolvat oficial pentru 5 din 8 bănci** (Fed, ECB, BoE, BoC, RBA) cu serii machine-readable, lag ≤1. Restul: **BoJ** = PARTIAL (nivelul-țintă nu e în nicio serie oficială machine-readable; hike-ul din 18 sep e doar în PDF), **SNB** = PARTIAL (cub cu lag 5 zile), **RBNZ** = FAIL/BOTWALL (Cloudflare challenge; și în CI, unde nu a aterizat niciodată un rând NZD).
2. **A3 (traiectoria implicită din piață): nicio sursă nu trece toate cele 6 criterii pe nicio valută.** Singurele care le trec literal sunt proxy-uri grosiere (Treasury bills pentru USD, curba forward AAA-govt ECB pentru EUR) — fără rezoluție pe ședință și nu OIS. Ce merge tehnic (keyless, EOD, parse determinist) este blocat de **LICENȚĂ** (Atlanta Fed: „personal and educational purposes only"; MX/TMX; ASX; JPX; BoE/Bloomberg) și/sau **NO_HISTORY** (JPX, MX, ASX-JSON sunt snapshot-uri). **CHF nu are nicio sursă gratuită** (Eurex = JS_ONLY).
3. **Descoperiri care afectează cod existent (nu le-am modificat):**
   - **RBA nu e „bot-wall" pentru UA non-browser**: `curl/8.4` și UA-ul proiectului → 200; UA Chrome → 403 (testat pe `a2-data.csv`, `a02hist.xlsx`, pagina cash-rate, pagina deciziilor 2026 și SMP; F1 și calendarul le-am accesat doar cu UA non-browser). Totuși `data/rates.parquet` arată că `rba` aterizează în CI până la 2026-09-16 → comportamentul depinde de IP; recomand UA non-browser + fallback.
   - **FF are rânduri `actual=0.0` false la BoJ** care trec de filtru pentru că `interest_rate_decision` are `can_be_zero: true` global: decizia din **2026-04-28 apare cu actual=0.0** (hold la 0.75 → „surpriză" −75bp), iar 2026-07-31 are `actual=NaN`. Pe CHF problema e inversă: FF **nu mai emite niciun rând numeric SNB din 2025-06-19** (doar evenimente-marker 0/0/0), deci `can_be_zero` nu are ce să protejeze — dar un 0.00 real și un 0.0 de întârziere ar fi indistinguibile dacă FF reia rândul.
   - `data/policy_rates.yaml` diferă de oficial la 4 bănci (USD −25bp, EUR −25bp, NZD −25bp, JPY −25bp): toate sunt **hike-uri după data `verified` (2026-08-27)**, nu neconcordanțe de date (§2.A1).
4. **Riscuri IP de datacenter (GitHub Actions):** dovedit în CI: FRED, ECB, BoE IADB, BoC Valet, RBA. Dovedit blocat: RBNZ. Netestat din CI (host-uri noi): BIS, BoJ API, ASX (`asx.api.markitdigital.com`, nedocumentat), JPX (CloudFront), MX (CloudFront), Atlanta Fed, Treasury, federalreserve.gov (Cloudflare), ICE (Cloudflare), CME (403 și local).

---

## 1. Matrice 8 bănci × A1–A5

Verdictul e cel al probe-ului, ajustat de mine acolo unde regula mecanică induce în eroare (marcat †). Codurile: BOTWALL / STALE / JS_ONLY / NO_HISTORY / SHORT_HORIZON / LICENSE / NONE (+ `PROXY` = trece criteriile dar nu dă rezoluție pe ședință).

| Bancă | A1 rată + 4 decizii | A2 traiectorie proprie | A3 implicită din piață | A4 consens FF | A5 calendar 2026–27 |
|---|---|---|---|---|---|
| Fed/USD | **OK** | **OK** (SEP, HTML) | PARTIAL · LICENSE† (Atlanta MPT) · PROXY (Treasury) | **OK** | **OK** |
| ECB/EUR | **OK** | N/A (fără traiectorie) | PARTIAL · PROXY† (AAA-govt fwd); futures JS_ONLY | **OK** (cere mapare MRO→DFR) | PARTIAL (2026 trecut nu e pe pagina oficială) |
| BoE/GBP | **OK** | N/A | PARTIAL · LICENSE† (curbă OIS, input Bloomberg) | **OK** | **OK** (2027 provizoriu) |
| BoJ/JPY | PARTIAL · NONE | N/A* (neverificat) | PARTIAL · NO_HISTORY + LICENSE | PARTIAL (0.0 fals + NaN) | **OK** (presser derivat; regulă blackout dintr-un document vechi) |
| BoC/CAD | **OK** | N/A* (neverificat) | PARTIAL · NO_HISTORY + LICENSE (+ fără as-of) | **OK** | **OK** |
| RBA/AUD | **OK** | N/A | PARTIAL · NO_HISTORY + LICENSE | **OK** | **OK** |
| RBNZ/NZD | **FAIL · BOTWALL** | **FAIL · BOTWALL** → manual | PARTIAL · NO_HISTORY + LICENSE (BKBM≠OCR) | **OK** | PARTIAL (manual; fără regulă quiet) |
| SNB/CHF | PARTIAL · STALE | N/A | **FAIL · JS_ONLY / NONE** | **FAIL · NONE** | PARTIAL (fără regulă quiet) |

`N/A` = banca nu publică traiectorie de rată (confirmat programatic pe pagini oficiale pentru ECB, BoE, RBA, SNB; `*` = confirmat doar documentar, neverificat în sesiune).

---

## 2. Detaliu pe celule OK/PARTIAL

### A1 — rata de politică + ultimele 4 decizii

Nivelul e din serie (data efectivă), datele deciziilor din calendarul oficial (A5). Serii „efective": ECB schimbă la +6 zile față de decizie → join pe data ședinței, nu pe data valorii.

| Bancă | Endpoint / format | Lag | Istoric | Ultimele 4 decizii (dată → nivel, Δ) | Note |
|---|---|---|---|---|---|
| USD | `fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU` (sus) + `DFEDTARL` (jos), CSV; **UA non-browser necesar** (Chrome = tarpit/timeout) | 0 | 2008-12 → | 04-29 3.75, 06-17 3.75, 07-29 3.75 (hold); **09-16 4.00 (+25)** ⇒ interval 3.75–4.00 | FF „Federal Funds Rate" = limita **superioară** (4.00 = DFEDTARU) ✔. SOFR 3.85 = −15bp vs sus / −2.5bp vs midpoint; EFFR 3.88 (+0.5bp vs mid). CI-dovedit |
| EUR | `data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata` (CSV); MRO `…KR.MRR_FR.LEV`; €STR `EST/B.EU000A2X2A25.WT` | 0 (ffill zilnic) | ≥2024 (probă) | 04-30 2.00, 06-11 **2.25 (+25)**, 07-23 2.25, **09-10 2.50 (+25)** | DFR curent 2.50, MRO 2.65. €STR 2.44 = −6bp vs DFR. CI-dovedit |
| GBP | `bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp?…SeriesCodes=IUDBEDR&CSVF=TN` (302 → `/boeapps/database/…`; `requests` urmează), CSV | 1 | ≥2024 (probă) | 04-30, 06-18, 07-30, 09-17: hold 3.75 | SONIA `IUDSOIA` 3.7303 = −2.0bp vs Bank Rate. CI-dovedit (pe alt cod) |
| JPY | BoJ Time-Series API `stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=en&db=FM01&code=STRDCLUCON` (call rate O/N mediu, JSON); `db=IR01&code=MADR1Z@D` (Basic Loan Rate) | 2 (09-16) | 1998 → (metadata API) | din BIS: 04-28 0.75, 06-16 **1.00 (+25)**, 07-31 1.00, **09-18 în AȘTEPTARE** (seria e la 09-15) | **PARTIAL:** call rate 0.977 (−2.2bp vs țintă 1.00). Basic Loan Rate = politică + 25bp (1.25 din 06-17; se va muta la 1.50). Ținta însăși există doar în PDF („Change in the Guideline…", 18 sep 2026, listat pe pagina BoJ). **Hike-ul din 18 sep la 1.25 apare doar în FF** (actual 1.25). BIS `WS_CBPOL` (JP) are ținta dar lag 3–4 zile |
| CAD | `bankofcanada.ca/valet/observations/V39079/json` (țintă, business-daily) + `AVG.INTWO` (CORRA), JSON | 1 | ≥2024 (probă) | 04-29, 06-10, 07-15, 09-02: hold 2.25 | CORRA 2.29 = +4bp vs țintă. CI-dovedit |
| AUD | `rba.gov.au/statistics/tables/csv/f1-data.csv` (F1 zilnic: `FIRMMCRTD` țintă, `FIRMMCRID` overnight); `…/a2-data.csv` (schimbări) — **UA non-browser local** (Chrome = 403) | 1 | 2011 → | 03-17 **4.10 (+25)**, 05-05 **4.35 (+25)**, 06-16 4.35, 08-11 4.35 | Overnight 4.35 = +0.0bp vs țintă. Coloanele OIS 1M/3M/6M din F1 sunt **oprite din 2022-12-01**. Decizie efectivă la +1 zi |
| NZD | RBNZ B2 `hb2-daily.xlsx` / pagina OCR → **403 Cloudflare `cf-mitigated: challenge`** (UA: Chrome, proiect, curl, python-requests; și prin WebFetch) | — | — | **fallback FF/BIS:** 04-08 2.25, 05-27 2.25, 07-08 **2.50 (+25)**, 09-02 **2.75 (+25)** | BIS `WS_CBPOL` (NZ) 2.75, lag 5 zile. În `rates.parquet` nu există niciun rând NZD ⇒ blocat și în CI |
| CHF | `data.snb.ch/api/cube/snbgwdzid/data/csv/en?fromDate=…` (CSV `;`, `D0=LZ` politică, `SARON`) | **5** (09-11; cub publicat 09-14 10:00) | 2004 → | 2025-09-25, 2025-12-11, 2026-03-19, 2026-06-18: 0.00 (Δ0) | SARON −0.04 = −4bp vs 0.00. Nu am putut verifica dacă cubul se actualizează în ziua deciziei (următoarea: 24 sep) ⇒ STALE. Serie `snboffzisa` (lunară) neutilizată |

**Cross-check `data/policy_rates.yaml`:** GBP 3.75 ✔, CAD 2.25 ✔, AUD 4.35 ✔, CHF 0.00 ✔. Diferențe: **USD** yaml 3.625 (midpoint 3.50–3.75) vs oficial **3.875** (3.75–4.00); **EUR** 2.25 vs **2.50**; **NZD** 2.50 vs **2.75**; **JPY** 1.00 vs **1.25** (doar FF). Toate = decizii **după** `verified: 2026-08-27` (NZ 09-02, ECB 09-10, Fed 09-16, BoJ 09-18). Observație de semantică: la AUD/CHF/GBP/CAD câmpul `effective` din yaml urmărește ultima **ședință**, nu ultima **schimbare** (ex. CHF „2026-06-18" — ultima schimbare SNB e din iunie 2025).

### A2 — traiectoria proprie

| Bancă | Sursă | Rezultat |
|---|---|---|
| Fed | `federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm` (HTML „accessible version"; URL detectat din linkurile `fomcprojtabl\d{8}` de pe `fomccalendars.htm`; PDF în paralel). Tabel 1 = mediane; Figura 2 = număr de participanți per bucket de 25bp/an. Fără CSV/JSON — parse HTML determinist. Lag: aceeași zi (16 sep) | Mediană FFR: **2026 4.1, 2027 4.1, 2028 3.9, 2029 3.6, LR 3.2** (din puncte: 4.125 / 4.125 / 3.875 / 3.625 / 3.25 — publicat rotunjit la 0.1). Dots 2026: 4.375×4, 4.125×12, 3.875×2; 2027: 4.375×8, 4.125×6, 3.625×3, 3.125×1; 2028 (17): 4.125×4, 3.875×5, 3.625×3, 3.375×1, 3.125×4; 2029 (17): 3.875×3, 3.625×7, 3.375×2, 3.125×4, 2.875×1; LR (18): 3.875×2, 3.75×1, 3.625×2, 3.5×2, 3.375×1, 3.25×2, 3.125×1, 3.0×6, 2.875×1. Nr. de participanți variază (17–18): îl iau așa cum e publicat |
| RBNZ | MPS Sep 2026 (proiecții închise 26 aug; `…/publications/monetary-policy-statements/2026/sep-2926/mps_report_sep2026.pdf`, din căutare) | **BOTWALL** (Cloudflare) local și prin WebFetch ⇒ **manual**, 4×/an: următorul MPS 9 dec 2026 |
| SNB | comunicat 18 iun 2026: „conditional inflation forecast", legenda „SNB policy rate 0.0%" | Confirmat: prognoză condiționată pe rată constantă, fără traiectorie |
| ECB | comunicat 23 iul 2026: „data-dependent and meeting-by-meeting approach" | Confirmat: fără traiectorie |
| BoE | minutele sept 2026 raportează la „market curve" | Confirmat: condiționează pe curba pieței, fără traiectorie proprie |
| RBA | SMP aug 2026 (`…/smp/2026/aug/overview.html`): „cash rate is assumed to move in line with expectations derived from financial market pricing as per 5 August" | Confirmat (verificat manual după probe; probe-ul verifică textul acum). Tabelul SMP dă totuși traseul-ipoteză al pieței (4.3/4.4/4.5/4.5/4.4/4.4) — subprodus, nu traiectorie proprie |
| BoJ, BoC | — | **Neverificat în sesiune** (pagina MPR BoC e shell JS la fetch; URL-ul ghicit pentru Outlook BoJ = 404). Documentar: fără traiectorie |

### A4 — consens FF (`economic_calendar_ff.parquet`, rânduri unite cu ședințele oficiale, zi ±1)

| Bancă | Nume brut / canonic | Ultimele 4 decizii (actual / forecast / previous) | Observații |
|---|---|---|---|
| USD | „Federal Funds Rate" / „Fed Interest Rate Decision" | 04-29, 06-17, 07-29: 3.75/3.75/3.75; **09-16: 4.00/4.00/3.75** | **Limita superioară** confirmată (= DFEDTARU). Zile cu 2 rânduri (04-29 17:00 și 18:00 UTC) — de deduplicat |
| EUR | „Main Refinancing Rate" / „ECB Interest Rate Decision" | 04-30 2.15/2.15/2.15; 06-11 2.40/2.40/2.15; 07-23 2.40; **09-10 2.65/2.65/2.40** | **Rândul e MRO** (2.65 = `MRR_FR`). **Mapare propusă la DFR: `DFR(t) = MRO(t) − s(t)`, cu `s` luat din seriile ECB** (MRR_FR−DFR = 0.50 până la 2024-09-17, **0.15 din 2024-09-18** — offset constant ar greși istoric). Rezultat curent 2.65 → 2.50 |
| GBP | „Official Bank Rate" / „BoE Interest Rate Decision" | 04-30, 06-18, 07-30, 09-17: 3.75 (A/F/P complete) | curat |
| JPY | „BOJ Policy Rate" / „BoJ Interest Rate Decision" | 06-16 1.00/1.00/0.75 ✔; **09-18 1.25/1.25/1.00** ✔ (3 rânduri în fereastră, incl. unul `0.0` la 09-16 21:00); **04-28: actual=0.0** (forecast 0.75, previous 0.75) ✘ placeholder; **07-31: actual=NaN** ✘ | 6 rânduri `actual==0.0` în tot parquetul; în arhiva brută 9 din 28 rânduri BoJ au actual 0.0 cu previous>0. `can_be_zero: true` (global pe `interest_rate_decision`) le lasă să treacă |
| CAD | „Overnight Rate" / „BoC Interest Rate Decision" | 04-29, 06-10, 07-15, 09-02: 2.25 | 04-29 are 2 rânduri (12:45 și 13:45 UTC) |
| AUD | „Cash Rate" / „RBA Interest Rate Decision" | 03-17 4.10 (prev 3.85); 05-05 4.35; 06-16 4.35; 08-11 4.35 | curat |
| NZD | „Official Cash Rate" / „RBNZ Interest Rate Decision" | 04-08 2.25; 05-27 2.25; 07-08 2.50/2.50/2.25; 09-02 2.75/2.75/2.50 | curat |
| CHF | „SNB Policy Rate" / „SNB Interest Rate Decision" | **niciun rând numeric** pentru 2025-09-25, 2025-12-11, 2026-03-19, 2026-06-18 (există doar markerele „SNB Monetary Policy Assessment"/„Press Conference" cu 0/0/0). Ultimul rând numeric: 2025-06-19 (0.00/0.00/0.25) | **can_be_zero pe rândul SNB:** flag-ul e `true`, deci un 0.00 real trece — dar rândul lipsește. Riscul concret: markerele 0/0/0 (alt eveniment) au valoare egală cu rata reală 0.00 ⇒ coincidență periculoasă dacă un matcher le-ar rutare greșit. Consens SNB: **nu există în FF** |

Arhiva `data/archive/ff_calendar_range.json` se oprește la 2026-07-03 (ultimele rânduri de decizie ≤ iunie); pentru septembrie contează doar parquetul.

### A5 — calendar oficial 2026–2027 (P = proiecții în ziua respectivă, C = conferință de presă)

Toate paginile sunt HTML static, parse deterministic; 8 ședințe/an la fiecare, mai puțin SNB (4) și RBNZ (7/an). `page` = din pagina oficială; `derived` = practică documentată, nu marcată pe pagină.

| Bancă | URL | Date decizie 2026 → 2027 | Proiecții | Presser |
|---|---|---|---|---|
| Fed | `federalreserve.gov/monetarypolicy/fomccalendars.htm` | 01-28, 03-18*, 04-29, 06-17*, 07-29, 09-16*, 10-28, 12-09* → 2027 (tentativ): 01-27, 03-17*, 04-28, 06-09*, 07-28, 09-15*, 10-27, 12-08* | SEP la `*` (page) | link „Press Conference" pe ședințele trecute (page); viitoarele nu sunt încă anunțate |
| ECB | `ecb.europa.eu/press/calendars/mgcgc/html/index.en.html` | pagina listează **doar viitorul**: 2026: 10-29, 12-17 → 2027: 02-04, 03-18, 04-29, 06-10, 07-22, 09-09, 10-28, 12-16. **2026 trecut (02-05, 03-19, 04-30, 06-11, 07-23, 09-10) doar din FF** (neoficial; lista de comunicate ECB e încărcată JS) | Mar/Jun/Sep/Dec (derived) | „followed by press conference" (page) |
| BoE | `bankofengland.co.uk/monetary-policy/upcoming-mpc-dates` | 02-05, 03-19, 04-30, 06-18, 07-30, 09-17, 11-05, 12-17 → 2027 (provizoriu): 02-04, 03-18, 04-29, 06-17, 07-29, 09-16, 11-04, 12-16 | MPR la Feb/Apr/Jul/Nov (page) | derived: zilele MPR |
| BoJ | `boj.or.jp/en/mopo/mpmsche_minu/index.htm` | 01-23, 03-19, 04-28, 06-16, 07-31, 09-18, 10-30, 12-18 → 2027: 01-22, 03-18, 04-28, 06-11, 07-22, 09-22, 10-29, 12-17 (zi 2 a ședinței) | Outlook Report Ian/Apr/Iul/Oct (page) | nu e pe pagină |
| BoC | `bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/` | 01-28, 03-18, 04-29, 06-10, 07-15, 09-02, 10-28, 12-09 → 2027: 01-27, 03-03, 04-28, 06-02, 07-21, 09-08, 10-27, 12-08 (identic cu pagina MX) | MPR la Ian/Apr/Iul/Oct (page) | la fiecare decizie (pagina de blackout: „blackout ends … when the press conference begins") |
| RBA | `rba.gov.au/schedules-events/board-meeting-schedules.html` (UA non-browser local) | 02-03, 03-17, 05-05, 06-16, 08-11, 09-29, 11-03, 12-08 → 2027: 02-09, 03-23, 05-04, 06-22, 08-10, 09-28, 11-02, 12-14 | SMP Feb/Mai/Aug/Nov (derived) | derived |
| RBNZ | pagina oficială = 403 ⇒ **MANUAL** (din comunicatul RBNZ „Monetary policy and OCR decision dates until February 2027", regăsit prin căutare; de verificat manual) | 02-18, 04-08, 05-27, 07-08, 09-02, 10-28, 12-09 → 2027: **doar 02-17 publicat** | MPS: 02-18, 05-27, 09-02, 12-09, 02-17 (celelalte = MPR) | derived pe MPS |
| SNB | `snb.ch/en/services-events/digital-services/event-schedule` (viitor) + `…/monetary-policy/decisions` (arhivă) | 03-19, 06-18, 09-24, 12-10 → 2027: 03-18, 06-24, 09-23, 12-16 | prognoză condiționată la fiecare (derived) | „(introductory remarks, news conference)" (page, viitor) |

**Blackout / quiet period (sursă oficială):**

| Bancă | Regulă | Sursă | Stare |
|---|---|---|---|
| Fed | 00:00 ET a **2-a sâmbete înainte** de ședință → 23:59 ET a zilei **după** ședință | `federalreserve.gov/monetarypolicy/files/FOMC_ExtCommunicationParticipants.pdf` (+ calendar blackout PDF) | text din extras de căutare, PDF neparsat |
| ECB | **7 zile** înainte de fiecare ședință de politică monetară | `ecb.europa.eu/ecb-and-you/explainers/tell-me/html/what-is-the_quiet_period.en.html` | verificat pe pagină |
| BoE | de la începutul deliberărilor (de regulă **8–9 zile** înainte) → anunț | `bankofengland.co.uk/about/governance-and-funding/mpc-external-communications-code` | verificat pe pagină |
| BoJ | **2 zile lucrătoare** înainte de ziua 1 a ședinței → sfârșitul ultimei zile | `boj.or.jp/en/mopo/mpmsche_minu/m_ref/mpm0104a.htm` | **atenție:** formularea e dintr-un anunț vechi (2001); de confirmat regula curentă |
| BoC | **marți, cu 8 zile** înainte (Ian/Apr/Iul/Oct, cu MPR); altfel **miercuri, cu 7 zile**; se termină la 10:30 ET, când începe conferința | `bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/blackout-guidelines/` | verificat pe pagină |
| RBA | membrii externi ai MPB: de la **14:00 Sydney, miercurea dinaintea ședinței** → publicarea minutelor; blackout de tranzacționare până la 17:00 în ziua deciziei | `rba.gov.au/about-rba/our-policies/monetary-policy-board-external-members-comms-and-public-engagement-policy.html` | verificat pe pagină |
| RBNZ | — | — | **negăsit** (site blocat; RBNZ afirmă doar că nu „pregătește" piața) → manual |
| SNB | — | — | **negăsit** pe snb.ch → manual |

---

## 3. A3 — traiectoria implicită din piață

**Criterii pre-înregistrate:** (1) keyless · (2) EOD, lag ≤1 zi · (3) orizont ≥ ultima ședință din 2027 · (4) istoric ≥30 zile lucr. (ideal ≥250) · (5) parse determinist, fără JS · (6) licența permite afișarea pe un site personal public.
`Y/N` = (1)…(6).

| Val. | Sursă | Instrument · orizont | asof · lag | Istoric | 1 2 3 4 5 6 | Verdict |
|---|---|---|---|---|---|---|
| USD | **Atlanta Fed Market Probability Tracker** `…/cenfis/market-probability-tracker/mpt_histdata.xlsx` (6.9 MB, foaia `DATA`: `date, reference_start, target_range, field, value`) | distribuția SOFR-3M implicită din **opțiuni** CME, per trimestru IMM; 13 ferestre, ultima 2029-12-19 | 2026-09-17 · 1 | 2023-03-29 → (873 zile) | Y Y Y Y Y **N** | PARTIAL · **LICENSE** — caseta LICENSE din workbook: „Use of this data is permitted for personal and educational purposes only"; date CME folosite „under permission from CME"; termenii site-ului interzic publicarea fără autorizare |
| USD | Treasury par curve CSV `home.treasury.gov/…daily-treasury-rates.csv/{an}/all?type=daily_treasury_yield_curve` | bills 1M–6M, 1Y, 2Y (domeniu public) | 2026-09-18 · 0 | ≥2025 (probă; site-ul oferă ani anteriori) | Y Y Y Y Y Y | **PROXY** — trece criteriile, dar fără rezoluție pe ședință și nu OIS |
| USD | CME FedWatch / `CmeWS/mvc/Quotes` | FF futures | — | — | — | **BOTWALL** (403, Akamai) |
| USD | Yahoo Finance chart `ZQZ26.CBT` | FF futures | — | — | — | **BOTWALL** (429) + LICENSE (ToS neverificat) |
| EUR | ECB `YC/B.U2.EUR.4F.G_N_A.SV_C_YM.IF_*` (forward instantaneu AAA-govt, tenori lunari 3M…30Y) | curbă guvernamentală | 2026-09-17 · 1 | 2004 → | Y Y Y Y Y Y | **PROXY** — nu e OIS, nu e €STR; baza suveran–OIS variază |
| EUR | Eurex €STR (FESR) / Euribor-3M (FEU3): pagini de produs | futures | — | — | — | **JS_ONLY** („Statistics loading…"); fișierele „Product and Price Report" CB001/CB002 sunt rapoarte de **fee-uri**, nu prețuri (verificat în XML). ICE Euribor: JS_ONLY. Euribor zilnic din ECB: 404 |
| GBP | **BoE OIS curves** `…/yield-curves/latest-yield-curve-data.zip` + `oisddata.zip` (xlsx, foaia „1. fwds, short end") | forward instantaneu SONIA, grilă lunară 1–60 luni | 2026-09-17 (fișier ștampilat 09-18 13:34) · 1 | 2025-01-02 → 2026-09-17 (433 zile; arhive 2009–2024 disponibile); lipsește 2026-08-31 (bank holiday UK) | Y Y Y Y Y **N** | PARTIAL · **LICENSE** — BoE: OGL v3.0, dar seriile cu surse terțe sunt excluse; paginile OIS citează „Bloomberg Finance L.P. and Bank calculations" ⇒ cere confirmare scrisă |
| JPY | **JPX** CSV zilnic `jpx.co.jp/english/markets/derivatives/settlement-price/tvdivq00000014l6-att/rb_e<YYYYMMDD>.csv` (cp932; rândurile `FUT_TOA3M_*`) | 3M TONA futures, 20 contracte trimestriale până în 2031-09 | 2026-09-18 · 0 | **doar ultima zi** (zilele vechi → 404) | Y Y Y **N** Y **N** | PARTIAL · **NO_HISTORY** + **LICENSE** (termeni JPX neobținuți: 403/404; istoricul se vinde prin J-Quants DataCube) |
| CAD | **MX „Canadian Interest Rate Expectations"** `m-x.ca/en/trading/tools/canadian-interest-rate-expectations` (HTML server-side) | CRA (3M) 12 contracte → 2029-06; **COA (1M) doar 4 luni** (sep–dec 2026); datele ședințelor BoC pe pagină | **fără ștampilă as-of** (CORRA spot 2.29) | zero | Y **N** Y **N** Y **N** | PARTIAL · **NO_HISTORY** + lag neverificabil + **LICENSE** (© Bourse de Montréal; TMX Datalinx: „single end user, no redistribution rights"). Pagina de cotații = **JS_ONLY** (QuoteMedia) |
| AUD | **ASX** JSON `asx.api.markitdigital.com/asx-research/1.0/derivatives/interest-rate/IB/futures?days=1&height=179&width=179` (nedocumentat; îl folosește site-ul ASX „RBA Rate Tracker") | 30-day interbank cash rate futures, 18 luni → 2028-02 | 2026-09-18 · 0 | snapshot; **mirror comunitar** `raw.githubusercontent.com/bpalmer4/ASX/main/ASX-COMBINED/ASX-COMBINED.csv`: 1 039 capturi zilnice din 2022-04-21 până 2026-09-18 | Y Y Y **N**(oficial) Y **N** | PARTIAL · **NO_HISTORY** (istoric doar neoficial) + **LICENSE** (termeni ASX neobținuți; repo fără licență) |
| NZD | **ASX** același API, cod `BB` | 90-day NZ bank bill (BKBM-3M), 12 contracte trimestriale → 2029-09 | 2026-09-18 · 0 | snapshot | Y Y Y **N** Y **N** | PARTIAL · NO_HISTORY + LICENSE; **BKBM ≠ OCR** (spread necunoscut: B2 blocat) |
| CHF | Eurex SARON (FSR3): pagină de produs | 3M SARON futures | — | — | — | **FAIL · JS_ONLY** („TradingView loading…", „Statistics loading…"). Nicio altă sursă gratuită găsită |

**Verdict A3:** *0/8 valute calificate.* Cel mai bun per valută pentru un site personal public: USD = Treasury (proxy, curat juridic) sau Atlanta MPT (bun tehnic, blocat juridic); EUR = proxy; GBP = curba BoE (bună tehnic, de confirmat juridic); JPY/CAD/AUD/NZD = snapshot ASX/JPX/MX (fără istoric, juridic neclar); CHF = nimic.

### Valori curente (2026-09-18/17) și implicații

Politică-echivalent = rata overnight implicită − spread-ul curent overnight–politică; Δ față de politica actuală. „Ferestre în interior" = câte alte ședințe cad în fereastra de 3M citită ⇒ mișcarea cumulată e **margine superioară** pentru ședința respectivă.

| Val. | Politică acum · spread overnight | După ultima ședință din 2026 | După ultima din 2027 | Metodă |
|---|---|---|---|---|
| USD | 3.875 (midpoint) · SOFR −2.5bp | **4.335 (+46bp)**, fereastra 12-16 (1 ședință în interior; MPT 4.310) — *corectat în 0B, vezi E1* | **4.663 (+79bp)** (fereastra 2027-12-15) — *corectat* | MPT „Rate: mean" |
| EUR | 2.50 (DFR) · €STR −6bp | 2.821 (+32bp) | 3.397 (+90bp) | **proxy** AAA-govt fwd @3.0m / @14.9m |
| GBP | 3.75 · SONIA −2.0bp | **4.051 (+30bp)** | **4.855 (+110bp)** | curbă OIS fitată, interpolare lunară |
| JPY | 1.25 (FF; oficial încă 1.00) · call −2.2bp | **1.497 (+25bp)** (fereastra 12-16, 1 în interior) | **2.130 (+88bp)** | TONA-3M |
| CAD | 2.25 · CORRA +4bp | COA-lanț: 28 oct **+12bp** (zgomotos: doar 4 zile după data efectivă), 9 dec **+35bp** (2.600); CRA fereastra 12-16: **2.735 (+48bp)**, 2 ședințe în interior | CRA 2027-12-15: **3.555 (+131bp)** | COA 1M lanț + CRA 3M |
| AUD | 4.35 · +0.0bp | IB lanț, 8 dec: **4.726 (+38bp)** | 14 dec 2027: **4.885 (+54bp)** | 1M lanț (o ședință/lună) |
| NZD | 2.75 · **n/a** (B2 blocat) | BKBM-3M implicit **3.450** (fereastra 12-14, 1 în interior) — nivel BKBM, nu OCR | 3.800 la 17 feb 2027 (**singura** ședință 2027 cunoscută; RBNZ nu a publicat restul) | BB bank bills |
| CHF | 0.00 · SARON −4bp | — | — | fără sursă |

Alte valori brute: **BoE OIS fwd** 1m 3.748 · 3m 4.033 · 6m 4.412 · 12m 4.793 · 24m 4.728. **JPX TONA-3M** (implicit; etichetat pe luna de START a ferestrei, convenția JPX — vezi E2): sep-26 1.225 · dec-26 1.475 · mar-27 1.680 · jun-27 1.850 · dec-27 2.107. *(Inițial etichetat pe luna de expirare, decalat cu un contract; calculul „+25bp / +88bp" folosea deja contractele corecte.)* **MX CRA:** sep-26 2.385 · dec-26 2.775 · mar-27 3.130 · dec-27 3.595 · dec-28 3.645; **COA:** sep 2.288 · oct 2.305 · nov 2.430 · dec 2.580. **ASX IB (AUD):** sep 4.355 · dec-26 4.725 · dec-27 4.900. **ASX BB (NZD):** dec-26 3.450 · mar-27 3.800 · dec-27 4.210 (plat după). **MPT (USD, bp):** 2026-12 431 · 2027-03 455 · 2027-12 464 · 2029-12 446.

**Diferența în bps unde am 2 surse pe aceeași valută, la ultima ședință din 2026:**
- **USD:** MPT fereastra 12-16 = 4.310 vs forward-ul T-bill 3M→6M = 4.296 ⇒ **−1bp** (baza bills–SOFR + convenții) — concordanță foarte bună.
- **CAD:** COA-lanț post 9 dec = 2.640 vs CRA fereastra 12-16 = 2.775 ⇒ **+13bp** (ferestre diferite; CRA include și 27 ian).
- Restul valutelor nu au o a doua sursă independentă (AUD: mirror-ul e aceeași sursă ASX).

### Nota de metodologie — se poate obține rata implicită pe ședință fără model?

- **Futures 1M pe media lunară (ASX IB, MX COA, fed funds ZQ): aproape fără model.** Media lunii = amestec ponderat pe zile între rata dinainte și de după data efectivă; `r_post = (N·X − d_pre·r_prev)/(N − d_pre)`. Ipoteze: rata se schimbă doar la data efectivă (Fed +1z, RBA +1z, ECB +6z), ≤1 ședință/lună, spread overnight–politică constant (îl scad din `r0`). Limită: numitorul `N − d_pre` mic (ședință târzie în lună) amplifică zgomotul (BoC 28 oct: doar 4 zile → +12bp nesigur); erorile se propagă în lanț.
- **Futures/opțiuni 3M compuse (TONA, CRA, SOFR→MPT, BB):** ferestre IMM–IMM (a 3-a miercuri), cu 1–2 ședințe fiecare ⇒ N ferestre, ~1.4–2N necunoscute ⇒ **subdeterminat**. Fără model, citesc fereastra care începe imediat după ședință (toleranță 3 zile) și o tratez ca margine superioară; pentru valori pe ședință e nevoie de bootstrap cu ipoteze (pași doar la date de ședință, pas egal pe ședință în fereastră, sau interpolare liniară).
- **Curbă OIS fitată (BoE):** forward-ul e spline netezit; pașii de ședință sunt „întinși" (câțiva bp eroare lângă ședință); grilă lunară 1–60m. Nu e model-free, dar e cea mai curată sursă gratuită.
- **BKBM/bank bills (NZD):** include spread de credit/termen peste OCR (RBNZ îl consideră mai zgomotos decât OIS); trebuie ipoteză BKBM−OCR.
- **AAA-govt forward (EUR):** nu e OIS.

**Spread benchmark overnight – politică (date oficiale, curente):**

| Bancă | Benchmark | Valoare | Politică | Spread |
|---|---|---|---|---|
| Fed | SOFR / EFFR (FRED) | 3.85 / 3.88 (09-17) | 3.75–4.00 (mid 3.875) | −2.5bp / +0.5bp vs mid (−15/−12bp vs limita sus) |
| ECB | €STR (ECB `EST`) | 2.44 (09-17) | DFR 2.50 | **−6bp** |
| BoE | SONIA (IADB) | 3.7303 (09-16) | 3.75 | **−2.0bp** |
| BoJ | call O/N necolateralizat, medie (BoJ API) | 0.977 (09-16) | 1.00 (BIS) | **−2.2bp** (pre-hike) |
| BoC | CORRA (Valet) | 2.29 (09-17) | 2.25 | **+4bp** |
| RBA | interbank overnight (F1) | 4.35 (09-17) | 4.35 | **+0.0bp** |
| RBNZ | — (B2 blocat; OCR e chiar overnight) | n/a | 2.75 | n/a |
| SNB | SARON (cub SNB) | −0.04 (09-11) | 0.00 | **−4bp** |

### Plătit — pentru golurile rămase (fără cont; „preț nepublic" unde e cazul)

| Furnizor | Acoperire (din paginile citite) | Preț public | API | Redistribuire |
|---|---|---|---|---|
| **Databento** — CME Globex MDP 3.0 | USD (ZQ, SR3, opțiuni). Alte piețe: **neverificat** | **Standard $179/lună** (blog „Introducing new CME pricing plans"); Plus/Unlimited: preț neafișat în articol; istoric pay-as-you-go rămâne | REST/Python (istoric + live) | licențele CME se transferă la cost fără adaos, prin chestionar (afișare/non-afișare) ⇒ **necesar pentru un site public** |
| **Barchart OnDemand** | futures multi-bursă (pagina „EUREX (XEUR) historical and intraday futures price data" există; acoperirea altor burse: neverificată) — potențial EUR/CHF/USD | „de la ~$500/lună" **doar din listare terță** (saasworthy); Barchart cere contact | REST (`getQuote`, `getHistory`) | taxe de redistribuire + taxe de bursă (pagina „Exchange Fees" neparcursă) |
| **TMX Datalinx — MX Futures Trading Summary** | MX (CORRA CRA/COA — acoperire neconfirmată pe pagină) → CAD | **nepublic** (login) | CSV `;` gz, zilnic, Web/FTP/e-mail | „single end user, **no redistribution rights**" ⇒ nu pentru afișare publică fără licență distribuitor |
| **Deutsche Börse MDS / Eurex** („Eurex Market Statistics Offline": settlement, toate seriile, publicat T+1, fereastră 20 zile; Marketplace pentru istoric) | EUR (FESR, FEU3), CHF (FSR3) | **nepublic** | fișiere / Interactive Datacenter / Marketplace | necunoscut |
| **JPX J-Quants DataCube** | JPY (istoric futures, CSV) | „Price List" există; **neparcurs** ⇒ nepublic în raport | CSV | necunoscut |

Doar **Databento** și (indirect) **Barchart** au un preț public verificabil; restul e „preț nepublic". ASX: nu am investigat un canal plătit.

---

## 4. Recomandare — sursă + fallback per bancă × tip

| Bancă | Rată de politică | Traiectorie proprie | Implicită din piață | Consens decizie | Calendar |
|---|---|---|---|---|---|
| Fed/USD | **FRED DFEDTARU/L** → fallback FF (limita sus) → BIS (midpoint) | **SEP HTML** (auto) | **Treasury** bills (curat) + **MPT** dacă obții permisiune scrisă Atlanta Fed → altfel Databento ZQ/SR3 | FF | pagina FOMC |
| ECB/EUR | **ECB Data Portal DFR** (+MRO, €STR) | — | **GAP** (proxy AAA-fwd; €STR/Euribor futures = plătit) | FF **cu mapare MRO→DFR din seriile ECB** | pagina GC (viitor) + FF/manual pentru 2026 trecut |
| BoE/GBP | **IADB IUDBEDR** | — | **curbă OIS BoE** (cere confirmare licență Bloomberg) | FF | pagina MPC |
| BoJ/JPY | **BIS + FF** (hike-uri) ; țintă din PDF-ul deciziei (manual/regex) ; call rate BoJ API pentru spread | — | **JPX TONA** snapshot zilnic (acumulăm istoric de acum; licență de clarificat) | FF **cu gardă pentru 0.0 și NaN** (alinierea pe ședință oficială) | pagina BoJ |
| BoC/CAD | **Valet V39079** | — | **MX** (CRA lung + COA 4 luni), snapshot zilnic; licență de clarificat | FF | pagina BoC |
| RBA/AUD | **RBA F1** (UA non-browser) | — | **ASX IB JSON** + backfill din mirror comunitar (neoficial) | FF | pagina RBA (UA non-browser) |
| RBNZ/NZD | **FF + BIS** (lag 5z) + manual | **manual** (MPS, 4×/an) | **ASX BB** (BKBM; spread necunoscut) | FF | **manual** (RBNZ publicat doar până în feb 2027) |
| SNB/CHF | **cub SNB** (lag) + comunicat SNB | — | **GAP** | **GAP** — FF nu mai are rând numeric; de derivat din decizie oficială | event-schedule + arhivă decizii |

### Goluri explicite
1. **CHF: nicio traiectorie implicită gratuită** și **consens SNB inexistent în FF** (rândul numeric a dispărut după 2025-06-19).
2. **EUR: fără OIS/futures gratuite** (doar proxy AAA-govt).
3. **Istoric pentru surpriza vs piață T-1 la ultimele 4 decizii:** posibil pentru GBP (BoE, oficial), USD (MPT, dar licență), AUD (mirror neoficial), EUR (proxy). **Imposibil** pentru JPY, CAD, NZD, CHF (JPX/MX/ASX-JSON sunt snapshot-uri: istoricul începe din ziua în care pornim job-ul propriu).
4. **Licențe:** de rezolvat înainte de afișare — Atlanta Fed (personal/educațional), BoE (Bloomberg), ASX, JPX (neobținute), MX/TMX (fără redistribuire). Nu e sfat juridic.
5. **RBNZ:** BOTWALL pentru A1/A2/calendar; fără regulă quiet; 2027 publicat doar până la 17 feb.
6. **BoJ:** hike-ul din 18 sep nu e în nicio serie oficială machine-readable; **FF 0.0/NaN** la BoJ; regula de blackout dintr-un document din 2001.
7. **ECB:** date ale ședințelor trecute neoficiale (lista de comunicate = JS).
8. **SNB:** lag 5 zile pe cub (de verificat la decizia din 24 sep); regulă quiet negăsită.
9. **MX:** fără as-of ⇒ lag neverificabil.

### Riscuri de blocare pe IP de datacenter (GitHub Actions)

| Sursă | CDN observat | Dovadă CI | Risc |
|---|---|---|---|
| FRED, ECB Data Portal, BoE IADB, BoC Valet, RBA (statistici) | — / akamai (RBA) | **aterizează în CI** (`rates.parquet` refăcut de CI 2026-09-18: AUD→09-16, CAD→09-17, EUR→09-17, GBP→09-16, USD→09-17) | scăzut |
| RBNZ | Cloudflare challenge | **niciun rând NZD vreodată** | **blocat** |
| SNB `data.snb.ch` | — | niciun rând CHF în `rates.parquet` (seria `rendoblid` e marcată stale) | necunoscut |
| BIS, BoJ API, Treasury, ECB YC | — | netestat | scăzut–mediu |
| federalreserve.gov, ICE | Cloudflare | netestat | mediu |
| JPX, MX | CloudFront | netestat | mediu |
| ASX `markitdigital` | — (API nedocumentat) | netestat | mediu (poate fi schimbat/limitat fără avertizare) |
| CME, Yahoo | — | 403 / 429 **și local** | blocat |

---

## Errata 0A (aplicată în 0B, 2026-09-19)

**E1 — USD: spread-ul scăzut din MPT.** Confirmat: cifrele din 0A (4.305 / +43bp și 4.633 / +76bp) foloseau spread-ul **EFFR** (+0.5bp vs midpoint), deși eticheta spunea SOFR; MPT e construit pe opțiuni **SOFR** 3M, deci se scade spread-ul SOFR. Cauza: în primul run alegeam primul benchmark din listă (EFFR); am fixat SOFR ca benchmark USD, dar tabelul din raport rămăsese din run-ul vechi. Recalculat (SOFR 3.85 = −2.5bp vs midpoint 3.875): fereastra 2026-12-16..2027-03-17, medie 4.310 → **4.335 (+46bp)** (cu EFFR ar fi 4.305, +43bp); fereastra 2027-12-15..2028-03-15 → **4.663 (+79bp)**. Diferența MPT vs forward T-bill (−1bp) e neschimbată (valori brute, fără spread).

**E2 — contracte 3M: fereastră de referință și convenția de denumire.** Confirmat pentru JPY: valoarea 1.497 provine din contractul JPX cu luna de contract **202612** (start 2026-12-16, ultima tranzacționare 2027-03-16; preț 98.525 → 1.475, plus 2.2bp), adică *exact* fereastra 12-16 din tabel; eticheta „2027-03" din lista de valori brute era luna de **expirare**. Nu era decalaj de calcul, ci de etichetare (acum corectată).

| Bursă / sursă | Convenția de denumire | Fereastra de referință [start, end) | Exemple (start → end · implicit) |
|---|---|---|---|
| Atlanta Fed MPT (opțiuni CME 3M SOFR) | `reference_start` explicit | a 3-a miercuri a lunii de start → a 3-a miercuri +3 luni | 2026-12-16→2027-03-17 4.310 · 2027-03-17→06-16 4.549 · 2027-06-16→09-15 4.660 · 2027-09-15→12-15 4.671 |
| JPX 3M TONA | **luna de START** (spec JPX: „from the 3rd Wednesday of each contract month to the Tuesday preceding the 3rd Wednesday of the month 3 months later"); codul din numele emisiunii e ultima zi de tranzacționare | idem | 202609 (exp. 2026-12-15): 09-16→12-16 1.225 · 202612 (exp. 2027-03-16): 12-16→2027-03-17 1.475 · 202703: 03-17→06-16 1.680 · 202706: 06-16→09-15 1.850 |
| MX CRA (3M CORRA) | **luna de START** = „Contract Reference Month" (spec MX: „the month in which the Reference Quarter begins"); „Delivery Month" = luna de final | idem | CRAU26: 09-16→12-16 2.385 · CRAZ26: 12-16→2027-03-17 2.775 · CRAH27: 03-17→06-16 3.130 · CRAM27: 06-16→09-15 3.365 |
| ASX 90-day NZ bank bill (BB) | luna de **decontare** (spec ASX: expirare = prima miercuri după a 9-a zi a lunii de decontare; FRA/BKBM 90 zile de la expirare) | expirare → +91 zile; `dateExpiry` din API e cu 2 zile mai devreme (luni), fără efect asupra rezultatelor | BBZ2026: 2026-12-16→2027-03-17 3.450 · BBH2027: 03-10→06-09 3.800 · BBM2027: 06-16→09-15 4.050 · BBU2027: 09-15→12-15 4.150 |

**E3 — regula de selecție a ferestrei (aceeași funcție pentru USD/JPY/CAD/NZD).** Data efectivă = decizia + 1 zi (Fed, RBA, SNB), + 6 zile (ECB), + 0 (BoE, BoJ, BoC, RBNZ). Se ia **prima fereastră cu start ≥ (data efectivă − 3 zile)**; doar înainte în timp (o fereastră începută cu mai mult de 3 zile înaintea ședinței nu se folosește, fiindcă reflectă doar parțial decizia). Rezultat = media ferestrei − spread-ul curent overnight–politică; „în interior" = alte ședințe din fereastră ⇒ **margine superioară** a mișcării cumulate. În 0A cele trei locuri (raportul principal, diferența T-bill, diferența COA/CRA) foloseau reguli ușor diferite (toleranță 3 zile / fără toleranță / start ≥ data deciziei); dau aceleași ferestre pe datele curente, dar acum e o singură funcție (`pick_window` în `src/cb_probe.py`).

| Val. | Ședința | Data efectivă | Fereastra aleasă | Decalaj start−efectiv | Alte ședințe în fereastră | Implicit (politică-echivalent) | Δ vs politica acum |
|---|---|---|---|---|---|---|---|
| USD | 2026-12-09 | 12-10 | 2026-12-16→2027-03-17 | +6 z | 1 | 4.335 | +46bp (vs 3.875) |
| USD | 2027-12-08 | 12-09 | 2027-12-15→2028-03-15 | +6 z | 0 | 4.663 | +79bp |
| JPY | 2026-12-18 | 12-18 | 2026-12-16→2027-03-17 | −2 z | 1 | 1.497 | +25bp (vs 1.250) |
| JPY | 2027-12-17 | 12-17 | 2027-12-15→2028-03-15 | −2 z | 0 | 2.130 | +88bp |
| CAD | 2026-12-09 | 12-09 | 2026-12-16→2027-03-17 | +7 z | 2 | 2.735 | +48bp (vs 2.250) |
| CAD | 2027-12-08 | 12-08 | 2027-12-15→2028-03-15 | +7 z | 0 | 3.555 | +131bp |
| NZD | 2026-12-09 | 12-09 | 2026-12-16→2027-03-17 | +7 z | 1 | 3.450 (nivel BKBM, nu OCR) | +70bp (vs 2.750) |
| NZD | 2027-02-17 (singura din 2027 publicată) | 02-17 | 2027-03-10→06-09 | +21 z | 0 | 3.800 (BKBM) | +105bp |

Reproducere: `python -m src.cb_probe --section e`.

---

*STOP după raport — fără push, merge, adaptere, parquet sau UI. Commit local doar pe `feat/central-banks`.*
