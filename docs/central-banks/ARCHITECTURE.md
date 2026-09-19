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
  pentru date noi, se face merge main în branch. Singura excepție: backfill-ul din 1A (și migrarea lui în partiții
  lunare, înainte de merge).

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
data/cb/market_quotes/               cotațiile, o partiție pe luna as-of-ului: market_quotes_YYYY-MM.parquet
data/cb/state.json                   validatori ETag / Last-Modified per sursă + zile „fără valori noi” (MX)
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

**`market_quotes/market_quotes_YYYY-MM.parquet`** — partiționat pe luna as-of-ului (un as-of cade într-o singură
partiție, deci cheia rămâne unică global). Coloane stabile, identice în toate partițiile: `source, currency,
instrument, contract, field, ref_start, ref_end, tenor_months, value, unit, asof, asof_inferred, fetched_at (UTC)`.
Append-only; cheie `(source, instrument, contract, field, asof)`; last-write-wins pe aceeași cheie. `load_store`
citește toate partițiile sau un interval de luni; `write_store` rescrie doar partițiile schimbate, iar scrierea e
deterministă (rânduri sortate pe cheie, setări fixe de writer, `pyarrow` fixat în `requirements.txt`): același conținut
⇒ aceiași octeți, deci o rulare obișnuită atinge doar luna as-of-urilor noi. O cotație cu as-of = ziua curentă a
bursei se înregistrează doar după `eod_cutoff`. Istoricul nu se șterge; o sursă căzută = log + skip; exit 0 la eșec
parțial, non-zero doar dacă toate sursele eșuează. Backfill de la 2026-03-01 pentru sursele cu istoric oficial (MPT,
BoE OIS, Treasury, BoC T-bills, ECB AAA, RBA bank bills), ca să existe piața T−1 la ultimele 4 ședințe.

**Gardă `asof_inferred`** (azi doar MX): dacă toate cotațiile unui as-of *nou* sunt identice cu cele ale as-of-ului
precedent înregistrat, nu se înregistrează (sărbătoare sau pagină neactualizată). Ziua apare în `state.json`
(`_no_new_values`) și în `--status` ca „fără valori noi”. Re-citirea unui as-of deja înregistrat (pagina de vineri,
citită sâmbătă) rămâne un merge obișnuit.

**`--status`** afișează per sursă: ultima scriere, as-of, lag, zilele lipsă, dacă există validatori și nota „fără valori
noi”; în CI scrie și în `$GITHUB_STEP_SUMMARY`. **Lag** = zile lucrătoare între as-of și ultima zi lucrătoare ≤ azi
(convenția din 0A: sâmbăta, datele de vineri = 0, cele de joi = 1); sărbătorile bursei nu sunt modelate.

### Note de implementare 1A (alegeri de execuție, nu decizii de produs)

- Un rând neschimbat păstrează `fetched_at`-ul original → rulările fără date noi nu produc diff (fișierele sunt
  identice bit cu bit; o partiție a cărei serializare nu s-a schimbat nici nu se rescrie). „Ultima scriere” din `--status` = cel mai nou `fetched_at`, deci ultima dată când o sursă a adus
  ceva nou sau schimbat; ultima *interogare* fără schimbări apare doar în Actions / `$GITHUB_STEP_SUMMARY`.
- Validatorii se avansează doar după ce datele lor sunt pe disc și doar dacă cutoff-ul nu a reținut rânduri din
  răspuns (altfel următoarea rulare ar primi 304 și ar pierde cotația reținută).
- Backfill = descărcare completă (fără validatori) și doar pentru sursele cu istoric oficial; sursele snapshot nu au
  ce backfill-ui.
- `cb-refresh.yml` face commit + push doar pe `main`; un dispatch de pe alt ref colectează și raportează, fără push.
- Din MX se colectează doar CRA + COA; „Actual CORRA Rate” de pe pagină nu (data valorii nu se poate verifica; CORRA
  oficial vine din Valet, `AVG.INTWO`).
- Rândurile snapshot din 2026-09-18 (JPX, MX, ASX: 58 de rânduri + raw) provin din rulările locale făcute înainte de
  merge; sursele nu expun istoric, deci ziua nu s-ar mai fi putut reface.
- Erată A5 (față de raportul 0A): în `meetings.yaml`, BoE 2027-12-16 este corectat de mână la fără MPR / fără
  conferință — proba A5 îl citise ca zi MPR din footerul paginii (pagina listează doar „December MPC Summary and
  minutes”). `cb_probe.py` rămâne neatins.

## 9. Ce nu face 1A

Fără calcul (`cb_compute`), fără texte / rezumate, fără UI, fără trigger extern, fără modificări la `/economic` sau
`/carry`.

## 10. Întrebări deschise

Nu sunt decise în cerințele primite. Fiecare are o recomandare într-un rând (recomandare, nu decizie). Numerotarea e cea
din 1A; prima s-a închis.

1. **Aspectul fișierului de cotații — ÎNCHISĂ (aprobat 2026-09-19): stocare lunară.** Simulare pe datele reale
   (838 de commituri ≈ 6 / zi × 139 zile, doar cele 6 surse cu istoric oficial): fișier unic 36,1 MiB de istoric după
   6,5 luni, creștere pătratică (≈ 120 MiB / 1 an, ≈ 1 GB / 3 ani); lunar 7,1 MiB, liniar (≈ 13 MiB / an).
2. **„Reacția la ultima decizie”** — pe ce instrument / tenor / fereastră se măsoară Δ bps EOD T vs. T−1 și ce se
   afișează unde piața are doar proxy (EUR: AAA guvernamental; USD / CAD / AUD: bills / bank bills) sau nimic (CHF;
   NZD: doar BKBM 90 zile). *Recomandare:* Δ EOD T vs. T−1 al ratei implicite pe 3 luni de la instrumentul principal al
   fiecărei bănci, marcat PROXY unde e cazul, iar CHF afișat explicit „n/a” (nu înlocuit cu altceva).
3. **Afișarea USD**: interval sau punct la mijloc. *Recomandare:* eticheta „3.75–4.00” în UI, 3.875 (midpoint) doar în
   calcul — la fel ca `/carry`.
4. **Licențe și atribuire.** MPT: „personal and educational purposes only”; BoE OIS: paginile citează Bloomberg;
   termenii JPX / MX / ASX neverificați. *Recomandare:* tot afișajul rămâne în spatele porții; nimic public fără
   permisiune scrisă (Atlanta Fed / CME, BoE, ASX, MX, JPX); UI afișează atribuirea din câmpul `license` al fiecărei surse.
5. **Calendare de sărbători.** Garda MX acoperă acum sărbătorile pe care pagina nu le reflectă, dar `--status` tot le
   numără ca „zile lipsă”, iar regula BoJ „următoarea zi lucrătoare JP” are nevoie de calendarul JP. *Recomandare:* un
   YAML static mic cu sărbătorile 2026–2027 (US, UK, JP, CA, AU) verificat de mână, folosit de `--status` și de regula
   BoJ.
6. **Cine reîmprospătează `meetings.yaml`** (ECB 2026 trecut = din FF; BoE 2027 provizoriu; fără `first_day` pentru
   ședințele de două zile, necesar la blackout). *Recomandare:* verificare lunară automată a datelor față de paginile
   oficiale, care doar alertează (fără rescriere); `first_day` se adaugă în 1B.
7. **RBNZ manual**: cine și cât de des actualizează rata (OCR) și ședințele. *Recomandare:* tu, cu o editare de o linie
   după fiecare decizie (8 / an), într-un fișier dedicat, și o avertizare în `--status` dacă ultima intrare e mai
   veche decât ultima ședință.
8. **Surse snapshot (JPX, MX, ASX)**: istoric doar de la prima rulare; între spike și merge lipsesc zilele. *Recomandare:*
   acceptat — doar 2026-09-18 a fost prins local; nu se caută oglinzi neoficiale.
9. **Migrarea Carry**: în ce fază și cine validează diff-ul 0. *Recomandare:* la finalul 1B, un test CI care compară
   `policy_rates.yaml` cu modulul CB, rulat cel puțin peste o ședință, apoi migrarea când diff = 0.
10. **Faza 1B — convenții de calcul**: preț→rată per instrument; bank bills vs. seriile OIS din RBA F1
    (`FIRMMOIS1D/3D/6D`, necolectate); MPT (bp, medie) vs. futures. *Recomandare:* rata = 100 − preț la futures, MPT
    direct în bp, spread-ul fiecărui benchmark estimat pe o fereastră recentă; se adaugă seriile OIS din F1 la colector
    la începutul lui 1B și se preferă OIS față de bank bills pentru AUD.
11. **Faza 2 — AI**: furnizor / model, buget, stocarea textelor și rezumatelor, sensul lui „ultimele 4 ședințe”.
    *Recomandare:* API Anthropic (`claude-sonnet-5`), ieșire structurată, cu verificarea automată a citatelor și
    numerelor; textele și rezumatele în `data/cb/` (sunt mici), scrise de CI.
12. **Faza 4 — trigger extern**: cine face dispatch-ul, cu ce secret, cum se reîncearcă. *Recomandare:* același model ca
    `api/manual-actual.py` (funcție Vercel + token GitHub cu drept doar pe workflow-uri, în env-ul Vercel), 3
    reîncercări cu backoff și o alertă la eșec.
13. **Minute GitHub Actions în repo privat.** *Recomandare:* măsurăm minutele reale după prima săptămână; dacă e nevoie,
    schedule-ul trece pe zilele lucrătoare (`37 */2 * * 1-5`) — lookback-ul de 10 zile recuperează weekendul.
14. **Slug-urile URL** pentru `/central-banks/<bank>`. *Recomandare:* codul valutar cu litere mici
    (`/central-banks/usd`), la fel ca la Carry.
15. **Cross-link RATE EXP (2Y)**: ce celule trimit la ce pagină. *Recomandare:* fiecare din cele 8 celule trimite la
    `/central-banks/<valută>`; pagina afișează explicit „proxy” / „n/a” unde piața nu are traiectorie comparabilă.
