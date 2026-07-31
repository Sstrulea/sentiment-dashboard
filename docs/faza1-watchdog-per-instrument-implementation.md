# FAZA 1 — watchdog de freshness per instrument (implementat)

Status: **implementat, testat, NEwired în workflow-uri**
(`.github/workflows/` explicit în afara scopului). Branch
`fix/watchdog-per-instrument`, worktree `../macro-dev`.

## Ce s-a construit

`src/price_freshness_guard.py` — pur, fără I/O:
- `instrument_cadence_threshold(dates, ...)` — pragul de stagnare (zile) e
  **derivat din istoricul propriu al instrumentului**, nu fix: P95 al
  intervalelor dintre bare consecutive, calculat pe fereastra de 90 de zile
  care se termină la ULTIMA bară proprie (nu istoricul complet — un artefact
  vechi de backfill, cum e cel al FTSE100 de mai jos, nu poate umfla pragul).
  `threshold = max(P95 * 1.5, 3 zile)`.
- `per_instrument_freshness(price_df, board_symbols, as_of)` — un rând per
  simbol: `status` ∈ {`stale`, `fresh`, `no_data`}.
- `freshness_report(...)` — un singur raport grupat: `{any_stale,
  stale_count, stale: [...], no_data: [...], fresh_count, total_count}` —
  nu o alertă per instrument.

`scripts/check_freshness.py` — CLI de sine stătător, gata de conectat
(oglindește exact modul în care `src/pmi_ingest_guard.py` a fost lăsat
complet dar neconectat la `src/ff_refresh.py` în faza anterioară). Cere
`exit 1` DOAR pe sursele reîmprospătate de cloud (`calendar`,
`actuals_pull`) — vezi raționamentul de mai jos pentru `price`.

## De ce artefactul vechi de la FTSE100 nu strică pragul

`price_history.parquet` are un singur salt istoric de 1183 zile pentru
FTSE100 (2026-03-06, artefact de backfill — probabil o rulare inițială a
EA-ului cu doar 400 de bare, sărind peste un interval mort al contului
demo). Verificat direct: **fereastra de 90 de zile ce termină la ultima
bară proprie exclude acest salt prin construcție** — pragul derivat pentru
FTSE100, calculat pe perioada normală dinainte de îngheț (mediana 1 zi, P95
3.65 zile), rezultă **4.5 zile** — corect, nu inflat de artefact.

## Verificare pe date reale, azi

```
calendar: age_days=0 stale=False
actuals_pull: age_days=0 stale=False
price: 1 stale / 37 instrumente, 1 no_data (DXY)
  STALE FTSE100: last=2026-05-15 age=77.0d (threshold 4.5d)
OK (exit 0 — price nu blochează niciodată)
```

**Semnalează exact FTSE100, nimic altceva.** DXY (fără mapare de broker,
"by design" per `data/price_symbols.yaml`) e categorisit `no_data`, exclus
din `stale`/`any_stale` — nu apare ca alertă zilnică, doar în lista
informativă a raportului (nu am implementat o stare persistentă de „deja
semnalat o dată" — mai simplu, categoria `no_data` însăși joacă acel rol:
un gol cunoscut, acceptat, nu se transformă niciodată în alertă, indiferent
de câte zile trec).

## Audit — celelalte surse din `_freshness()` au aceeași orbire?

| sursă | agregă peste subunități? | verificat |
|---|---|---|
| `calendar` | **DA** — `pub[dt_col].max()` peste toate cele 8 valute | **Aceeași orbire ca `price`, confirmată cu date reale**: CHF are ultimul actual publicat pe 2026-07-14, AUD pe 2026-07-31 — agregatul arată 2026-07-31 (verde), ignorând că CHF n-a mai publicat nimic de 17 zile. Nu fixat aici (în afara scopului cerut explicit) — semnalat. |
| `actuals_pull` | Nu — un singur pull JBlanked global/zi, nu per valută. Nu e o agregare peste subunități, e corect ca unitate unică. |
| `rates` | **Nu tracked deloc în `_freshness()`** — dar `src/rate_compute.py::compute_rate_scores` calculează deja `stale` PER VALUTĂ (`RateScore.stale`, prag 7 zile lucrătoare), folosit corect la excluderea din categoria `monetary`. Nicio orbire de agregare — doar absent din dict-ul watchdog-ului (gap de vizibilitate, nu de corectitudine). |
| `liquidity` | **Nu se aplică** — o singură serie globală (Fed net liquidity), nimic de agregat peste subunități. `src/liquidity_compute.py::compute_liquidity_score` are deja propriul `stale` (prag 10 zile lucrătoare). |

**Concluzie**: `calendar` are exact aceeași problemă structurală ca `price`
(un agregat `max()` peste 8 valute mascând o valută moartă) — confirmată cu
date, nu presupusă. Fix-ul de acum acoperă doar `price`, cum s-a cerut;
`calendar` rămâne un candidat viitor pentru același tratament.

## De ce `price` nu iese niciodată cu `exit 1`

`price_history.parquet` e alimentat EXCLUSIV de `scripts/econ_refresh.sh`
(Mac-ul lui George, prin MT5/Wine) — niciun workflow din
`.github/workflows/` nu rulează `price_fetch`. Dacă `check_freshness.py` ar
rula într-un job cloud și ar ieși `exit 1` pe baza vârstei lui `price`,
rezultatul ar fi un email în fiecare dimineață când laptopul a fost închis
peste noapte sau în weekend — exact falsul-pozitiv garantat descris în
cerință. Un job din cloud n-are cum să repare o problemă de pe Mac oricum,
deci nu are rost să pice pentru asta.

**Rezolvare**: `price` rămâne calculat și AFIȘAT (deci FTSE100 rămâne
vizibil în jurnal, nu dispare), dar exclus explicit din condiția de
`exit 1` — doar `calendar`/`actuals_pull` (surse pe care cloud-ul chiar le
reîmprospătează, deci o alertă acolo e acționabilă) declanșează eșecul.

## Verificări cerute — toate confirmate

| cerință | rezultat |
|---|---|
| Rulat pe starea de azi, semnalează FTSE100 și nimic altceva | ✓ (vezi mai sus) |
| Rulat pe stare simulată cu toate instrumentele proaspete, tace | ✓ `test_all_fresh_report_is_silent` |
| DXY tratat explicit, nu raportat zilnic | ✓ `no_data`, exclus din `stale` |
| Instrument stale detectat | ✓ `test_stale_instrument_detected` |
| Weekend ignorat | ✓ `test_weekend_gap_is_not_flagged` |
| Instrument absent distinct de instrument stale | ✓ `test_absent_instrument_is_no_data_not_stale` |
| `pytest` verde | ✓ 446 (437 + 9 noi) |

## Ce NU s-a făcut (explicit în afara scopului)

- Conectarea în `.github/workflows/*.yml` — scriptul e complet și testat,
  gata de adăugat printr-o schimbare separată (un singur pas nou de
  workflow).
- Fixul pentru `calendar` (aceeași orbire, confirmată mai sus) — doar
  raportat, cum a cerut task-ul explicit ("chiar dacă fix-ul de acum
  acoperă doar price").
- Adăugarea `rates`/`liquidity` în `_freshness()` — gap de vizibilitate
  notat, nu construit (ar fi extindere de scop dincolo de "watchdog per
  instrument pentru price").

## Reproducere

```bash
.venv/bin/python3 scripts/check_freshness.py
.venv/bin/python3 -m pytest tests/test_price_freshness_guard.py -v
.venv/bin/python3 -m pytest -q   # 446 verzi
```
