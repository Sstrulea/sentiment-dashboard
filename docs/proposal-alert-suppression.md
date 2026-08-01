# Proposal — suprimarea alertelor cronice pentru watchdog-urile de freshness

Status: **analiză, zero cod scris**. Branch `fix/alert-noise-and-ff-archive`,
worktree `../macro-dev`. **STOP înainte de implementare, cum s-a cerut** —
recomandarea de mai jos e pentru confirmare, nu execuție.

## Problema exactă

`scripts/check_freshness.py` (per instrument) și `scripts/check_calendar_
freshness.py` (per valută) sunt corecte și testate, dar nu au niciun
mecanism de reducere a zgomotului. Rulate azi:

- **AUD core_cpi**: 276 zile, prag 110 — defect cunoscut (ABS a mutat CPI pe
  cadență lunară, `RBA Trimmed Mean CPI y/y` mort din octombrie 2025),
  documentat, nereparat azi.
- **USD core_cpi**: 52 zile, prag 45 — cauza cunoscută (JBlanked publică
  `Core CPI y/y` ca eveniment separat nemapat; rândul mapat, `Core CPI
  m/m`, vine cu `actual=0.0`, carantinat corect).

Ambele ar alerta identic **la fiecare rulare**, la nesfârșit, până se
repară — ceea ce nu se întâmplă azi. O alertă zilnică pentru ceva
neacționabil devine ignorată, exact tiparul care a lăsat FTSE100 mort 2,5
luni fără să observe nimeni.

## Constrângerea de bază

GitHub Actions nu păstrează stare între rulări — orice „am alertat deja"
cere un fișier comis în repo, citit la începutul rulării următoare, scris
(și comis) la sfârșit. Fiecare abordare bazată pe stare mutabilă are acest
cost, indiferent de eleganța ei.

## Cele trei abordări, evaluate pe aceleași patru axe

### A. Listă de excepții în config (motiv + referință + dată de revizuire)

- **Fișiere atinse**: un singur fișier static, ex. `config/alert_
  exceptions.yaml`, citit de ambele scripturi de watchdog. **Zero fișier de
  stare mutabilă.**
- **Când se pierde/diverge starea**: nu există stare mutabilă de pierdut —
  fișierul e config versionat normal. Dacă e șters/revertit accidental,
  **toate excepțiile dispar și totul alertează din nou** — eșec sigur
  (mai zgomotos, niciodată mai tăcut).
- **Frecvența commit-urilor**: **zero, automate.** Singurele commit-uri sunt
  cele făcute deliberat de un om, la adăugarea/modificarea/expirarea unei
  excepții — fiecare unul e un PR revizuibil, cu motiv vizibil în diff.
- **Defect nou apărut cât timp unul vechi e suprimat**: excepțiile sunt
  legate STRICT de (valută, indicator) — o pereche nouă, nemenționată în
  listă, nu e afectată deloc de excepțiile existente. Alertă imediată,
  neschimbată. **Nu poate masca un defect nou, prin construcție** — fiecare
  excepție e un rând explicit, nu un comutator global.
- **Riscul propriu, și cum se închide**: o excepție FĂRĂ aplicare a datei de
  revizuire e echivalentă cu problema pe care o rezolvăm — o tăcere
  acceptată, pentru totdeauna, doar prin alt mecanism. **Soluție**: data de
  revizuire trebuie VERIFICATĂ la fiecare rulare (comparație simplă, fără
  stare) — dacă a trecut, excepția e tratată ca EXPIRATĂ și alerta revine
  (cu un mesaj distinct: „excepție expirată, reevaluează").

### B. Alertă doar la tranziție (devine stale, nu cât timp rămâne)

- **Fișiere atinse**: un fișier de stare mutabilă, ex. `data/freshness_
  alert_state.json`, cu setul de (valută, indicator) stale la rularea
  anterioară — **comis la fiecare rulare unde starea se schimbă**.
- **Când se pierde/diverge starea**: dacă fișierul se pierde (revert
  accidental, migrare, conflict de merge nerezolvat corect), rularea
  următoare n-are cu ce compara — fie tratează TOT ca tranziție nouă (un
  val de alerte pentru tot ce e deja stale, inclusiv cazuri vechi de luni),
  fie tratează absența ca „nimic nou" (silențios de la prima rulare pentru
  AUD/USD, exact opusul dorit). **Ambele comportamente de bootstrap sunt
  proaste** și trebuie alese explicit — nu există o variantă „sigură
  implicit" aici.
- **Frecvența commit-urilor**: la fiecare tranziție de stare — pentru serii
  cu vârstă aproape de prag (flickering stale/fresh între rulări din motive
  de timing), poate fi frecvent, nu doar la evenimente reale.
- **Defect nou apărut cât timp unul vechi e suprimat**: tehnic, NU maschează
  un defect nou (o tranziție reală fresh→stale se detectează corect,
  presupunând starea intactă). **Dar** are o gaură structurală specifică
  cazului de față: **tranziția AUD/USD core_cpi s-a întâmplat deja, cu luni
  înainte să existe acest mecanism** — deci sub B, ele n-ar alerta
  NICIODATĂ, de la prima rulare (nu există o "primă alertă" de ratat, mereu
  au fost deja stale). Iar odată alertate o singură dată (la prima rulare
  cu bootstrap „totul e nou"), **nu se mai repetă niciodată** cât timp
  rămân continuu stale — dacă alerta aia se pierde (email ignorat, ratat
  într-o săptămână aglomerată), nu mai există a doua șansă, vreodată, fără
  nicio dată de revizuire care să reamintească.

### C. Cooldown per serie (max 1 alertă / N zile)

- **Fișiere atinse**: fișier de stare mutabilă,
  `{(valută,indicator): ultima_alertă}`, comis la fiecare alertă emisă.
- **Când se pierde/diverge starea**: cooldown-urile se resetează la zero —
  flood imediat pentru tot ce e stale azi (zgomotos, nu tăcut — la fel ca
  A). Divergență (fus orar, format de dată) poate produce fie „niciodată
  expirat" (bug, tăcere permanentă), fie „mereu expirat" (anulează scopul).
- **Frecvența commit-urilor**: mai rar decât B (doar la alertele efective,
  nu la fiecare schimbare de status), dar tot recurent — pentru AUD/USD,
  un commit la fiecare N zile, la nesfârșit, cât timp rămân stale.
- **Defect nou apărut cât timp unul vechi e suprimat**: la fel ca A, scopat
  per (valută,indicator) — o pereche nouă n-are intrare în cooldown, deci
  alertă imediată. **Nu maschează un defect nou.**
- **Riscul propriu**: fără o dată de expirare a ÎNTREGULUI mecanism de
  cooldown pentru o serie, „max 1 alertă / N zile, pentru totdeauna" e
  aceeași uitare lentă ca o excepție fără termen — doar cu pași
  intermediari (reamintiri periodice) în loc de tăcere completă.

## Tabel comparativ

| | A — excepții | B — tranziție | C — cooldown |
|---|---|---|---|
| fișier de stare mutabilă | **niciunul** | da, comis des | da, comis la alertă |
| pierdere de stare | mai zgomotos (sigur) | flood SAU tăcere (ambiguu) | mai zgomotos (sigur) |
| commit-uri automate | zero | frecvente (posibil la fiecare tick) | periodice |
| maschează un defect nou? | **nu, prin construcție** | nu, dar ratează ce era deja stale înainte de mecanism | **nu, prin construcție** |
| risc propriu de uitare | doar dacă `review_by` nu e aplicat | permanent, fără reamintire, după prima alertă | lent, fără termen final |
| potrivire cu cazul AUD/USD (deja stale de luni) | directă — excepție scrisă acum, cu motiv | proastă — n-ar fi alertat niciodată sub acest mecanism | ok, dar tot fără termen |

## Recomandare

**Opțiunea A, cu aplicarea obligatorie a datei de revizuire.** Motivele
decisive:

1. **Zero cost de stare-comisă-de-CI** — exact costul pe care l-ai
   semnalat explicit (commit-uri + conflicte cu alte job-uri). A e
   singura opțiune care nu-l are deloc.
2. **Nu poate masca un defect nou, prin construcție** — la fel ca C, dar
   fără mecanismul de stare care vine cu C.
3. **Se potrivește exact cazului de față** — AUD/USD core_cpi sunt DEJA
   cunoscute, documentate, cu propuneri proprii scrise. B ar fi trebuit să
   prindă tranziția ACUM LUNI, ca să funcționeze corect — a ratat-o deja,
   structural, nu din greșeală de implementare.
4. **Oglindește tiparul deja stabilit în acest cod** — `can_be_zero`,
   `frequency_overrides`, intrarea DXY din `price_symbols.yaml`: gol
   cunoscut, documentat inline, cu motiv. A extinde același tipar la
   watchdog, nu introduce unul nou.

**Schema propusă** (nescrisă încă — pentru confirmare):

```yaml
# config/alert_exceptions.yaml (propus)
exceptions:
  - currency: AUD
    indicator: core_cpi
    reason: "ABS a mutat CPI pe cadență lunară; RBA Trimmed Mean CPI y/y mort din 2025-10-29"
    doc: docs/faza1-watchdog-per-instrument-implementation.md
    review_by: "2026-09-01"
  - currency: USD
    indicator: core_cpi
    reason: "JBlanked publică Core CPI y/y nemapat; Core CPI m/m mapat vine cu actual=0.0"
    doc: docs/calendar-freshness-per-currency.md
    review_by: "2026-09-01"
```

Aplicare (propusă, nescrisă): la fiecare (valută,indicator) stale, caută în
listă; dacă găsit ȘI `as_of <= review_by`, exclude din poarta de `exit 1`
dar rămâne vizibil în raport ca „excepted, review by X"; dacă găsit dar
`review_by` a trecut, tratează ca NEexceptat (alertă normală + mesaj
„excepție expirată"); dacă nu e găsit, alertă normală.

**Opțiunea C rămâne o completare rezonabilă, nu necesară acum** — pentru
cazuri proaspete, netriajate (ceva devine stale azi, nimeni nu s-a uitat
încă), un cooldown ar reduce zgomotul cât timp se decide dacă merită o
excepție permanentă. Nu construiesc asta acum — A singură rezolvă complet
cazul AUD/USD, care e livrabilul cerut.

**Opțiunea B nu se recomandă** — nepotrivire structurală cu cazul exact pe
care trebuie să-l rezolve (tranziția deja ratată), nu doar cost mai mare.

## Nu implementat

Zero cod, zero fișier de config scris. Aștept confirmarea recomandării
înainte de a construi `config/alert_exceptions.yaml` și logica de aplicare
în `scripts/check_freshness.py` / `scripts/check_calendar_freshness.py`.
