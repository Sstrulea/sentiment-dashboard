# Watchdog de freshness per valută pentru `calendar` (implementat)

Status: **implementat, testat, NEwired în workflow-uri**
(`.github/workflows/` explicit în afara scopului). Branch
`fix/calendar-freshness-per-ccy`, worktree `../macro-dev`.

## Contradicția ridicată — rezolvată, nu ignorată

Faza anterioară (`docs/faza1-watchdog-per-instrument-implementation.md`,
commit `d6f4ec6`) a raportat: *"CHF are ultimul actual publicat pe
2026-07-14, AUD pe 2026-07-31 — agregatul arată 2026-07-31 (verde),
ignorând că CHF n-a mai publicat nimic de 17 zile."* Investigația din acest
branch a găsit, folosind DOUĂ metode independente, că **CHF nu e stale**:

1. **Metoda "oglindește `price_freshness_guard.py`"** (P95 din gap-urile
   istorice ale valutei, ×1.5): CHF, combinat pe cele 6 indicatoare, are
   istoric `P95=17 zile, max=33 zile` — pragul derivat ar ieși ~25 zile.
   Tăcerea de 17-18 zile **nu-l depășește**.
2. **Metoda per-indicator, deja existentă în scoring**
   (`economic_compute.effective_frequency` + `_max_age_for`): niciunul din
   cele 6 indicatoare CHF nu depășește propriul prag de frecvență (CPI
   29d/45d, PPI 17d/45d, GDP 60d/110d, PMI 30d/45d, Retail 30d/45d,
   Unemployment 25d/45d azi).

**Fapta brută ("17 zile") era corectă și reprodusă identic** — ultimul
eveniment CHF cu actual valid e, confirmat din nou, `2026-07-14 06:30:00`
(`PPI m/m`). Ce era greșit era INTERPRETAREA: acel gol reflectă 6
indicatoare cu cadențe lunare decalate, nu un defect. Amestecarea a 6
cadențe independente într-o singură cifră "ultimul actual, orice indicator"
auto-relaxează orice prag derivat din ea — exact limita pe care task-ul
inițial a cerut să fie verificată explicit ("dacă tăcerea de 17 zile e deja
în fereastra de derivare, pragul se autojustifică").

**Orbirea de agregare EXISTĂ, fără ambiguitate** — confirmată cu exemple
corecte, nu cu CHF:

| valută | indicator | ultima publicare | prag | vârstă azi | depășire |
|---|---|---|---|---|---|
| **USD** | **core_cpi** | 2026-06-10 | 45d (lunar) | **52d** | **+7d** |
| **AUD** | **core_cpi** | 2025-10-29 | 110d (trimestrial) | **276d** | **+166d** |

Ambele mascate azi de un badge agregat verde (alți indicatori ai acelorași
valute publică des). AUD `core_cpi` era deja vizibil ca `stale` în
breakdown-ul de scoring din fazele anterioare; USD `core_cpi` la 52/45 e o
descoperire nouă a acestei investigații.

## Design — de ce NU s-a oglindit `price_freshness_guard.py` literal

Prima încercare (P95 din gap-uri brute, per valută) a fost testată pe date
reale ÎNAINTE de a scrie codul final — vezi metoda 1 de mai sus — și s-a
dovedit auto-înfrângătoare pentru valute cu puține serii (CHF). Motivul:
`price`'s prag e derivat dintr-un SINGUR instrument cu o cadență proprie
consistentă (zilnic, cu weekend/sărbători) — pentru `calendar`, "instrumentul"
per valută e de fapt o ÎMPLETIRE a mai multor indicatoare independente
(CPI lunar, GDP trimestrial, PMI lunar...), iar un P95 peste gap-urile
combinate amestecă cadențe diferite într-o statistică prea permisivă.

**Design ales**: `src/calendar_freshness_guard.py` reutilizează
`economic_compute.effective_frequency` + `_max_age_for` — mecanismul deja
existent și validat în scoring (aceleași praguri pe care `compute_
indicator_score` le folosește pentru propriul flag `stale`). O valută e
`stale` dacă **oricare** din indicatorii ei scorați (weight > 0, la fel ca
în audit) depășește PROPRIUL prag de frecvență. Un singur raport grupat per
valută, nu o alertă per indicator.

## Distincția „lipsă publicări" vs „defect de ingest"

Feed-ul `jb_raw` (payload-urile JBlanked zilnice reținute) e re-parsat prin
parserul de producție (`parse_jblanked_range`) pentru fiecare (valută,
indicator) stale, verificând dacă există un rând MAI NOU decât ce e deja în
parquet. Trei rezultate posibile:

- **`actual_available_not_ingested`** — o valoare mai nouă EXISTĂ în feed,
  dar n-a ajuns în parquet-ul scorat. Defect confirmat, nu presupunere.
- **`no_newer_data`** — nimic mai nou găsit în fereastra reținută (fie
  chiar nu s-a publicat nimic nou, fie golul e mai vechi decât fereastra —
  nu se poate distinge din asta singur).

**Limită onestă, consemnată, nu ascunsă**: payload-urile `jb_raw` (endpoint-ul
"range" al JBlanked) sunt orientate pe ACTUALE, nu pe PROGRAM — task-ul a
întrebat dacă feed-ul faireconomy (săptămânal, cu programul complet) poate
oferi distincția „programat dar nepublicat". **Nu există niciun payload
faireconomy săptămânal salvat în repo** (spre deosebire de `jb_raw`, care
persistă explicit fiecare pull) — `src/ff_refresh.py` îl citește live, fără
cache. Deci: distincția „programat, nu doar ce s-a publicat" nu se poate
face cu datele disponibile azi din repo — tratez pragul derivat per
indicator ca aproximare (deja mai bună decât un prag fix), nu ca soluție
completă.

## Descoperire — de ce USD `core_cpi` e stale, exact

Rulând verificarea `jb_raw` pe cazul USD: **există** un eveniment mai nou
(`2026-07-14`, `Core CPI m/m`, `actual=0.0`, marcat chiar de JBlanked
„Bad Data") — dar acel `0.0` e deja carantinat corect la scoring (zero-
placeholder, `core_cpi` nu e `can_be_zero`), deci parquet-ul arată `NaN` pe
acea zi. **Separat**, feed-ul mai conține, din mai 2026, un eveniment NATIV
`Core CPI y/y` (nealiniat pe `Core CPI m/m`) — pe `2026-07-14`,
`actual=2.6`, marcat „Good Data" — dar acesta e complet **nemapat**
(`config/ff_aliases.yaml` n-are nicio intrare pentru raw name-ul literal
`"Core CPI y/y"` la USD, doar pentru `"Core CPI m/m"` cu xf), deci e scăpat
tăcut ca „unmapped", niciodată scris în parquet.

**Verificat, nu presupus, cât de solid e acest eveniment nativ ca
alternativă**: apare de doar **3 ori** în tot (arhivă + jb_raw combinate) —
2026-05-12 (`Bad Data`), 2026-06-10 (`Bad Data`), 2026-07-14 (`Good Data`).
**2 din 3 marcate explicit „Bad Data" chiar de JBlanked.** Spre deosebire de
promovarea CAD Median CPI y/y (43/43 rânduri cu consensus valid, calitate
consistentă), acest eveniment USD **nu e o alternativă comparabilă** — n=3,
majoritar semnalat ca date proaste chiar de sursă. **Consemnat doar
informativ, zero acțiune** — nu e „la fel ca CAD", cum ar putea părea la o
privire superficială.

## Verificări cerute — toate confirmate

| cerință | rezultat |
|---|---|
| Rulat pe starea de azi: semnalează CHF și confirmă ce mai e | Nu semnalează CHF (corect — CHF nu e stale, vezi mai sus); semnalează **USD, AUD** (`core_cpi` la ambele) |
| Fiecare valută semnalată — ultimul eveniment, vârsta, prag, program în perioada tăcută | ✓ tabelul de mai sus + verificarea `jb_raw` (USD: actual disponibil dar necarantinat corect explicat; AUD: nimic nou în fereastră) |
| Rulat pe stare simulată cu toate proaspete | ✓ `test_all_fresh_is_silent` |
| Valută stale detectată | ✓ `test_stale_indicator_flags_the_currency` |
| Valută cu cadență rară (CHF, NZD) nu produce fals pozitiv | ✓ `test_chf_style_rare_cadence_does_not_false_positive`, `test_nzd_style_rare_cadence_multiple_indicators_no_false_positive` |
| Valută absentă tratată distinct de valută stale | ✓ `test_absent_indicator_is_no_data_not_stale` |
| `pytest` verde | ✓ 454 (446 + 8 noi) |

## Ce NU s-a făcut (explicit în afara scopului)

- Conectarea în `.github/workflows/*.yml` — `scripts/check_calendar_
  freshness.py` e complet și testat, gata de adăugat printr-o schimbare
  separată (spre deosebire de `price`, `calendar` chiar e reîmprospătat de
  Actions — un `exit 1` aici ar fi acționabil, nu fals-pozitiv garantat).
- Fixul pentru USD `core_cpi`/alias-ul nativ `Core CPI y/y` — descoperire
  de taxonomie, consemnată, nu reparată (decizie explicită).
- `rates`/`liquidity` în dict-ul watchdog-ului — au deja `stale` per-serie
  în modulele proprii (`RateScore.stale`, `LiquidityScore.stale`), doar
  absente din suprafața watchdog-ului — consemnat, nu construit.
- `can_be_zero` — blocat până ~4 august (fază separată).

## Reproducere

```bash
.venv/bin/python3 scripts/check_calendar_freshness.py
.venv/bin/python3 -m pytest tests/test_calendar_freshness_guard.py -v
.venv/bin/python3 -m pytest -q   # 454 verzi
```
