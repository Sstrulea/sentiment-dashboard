# Ordinea în `_keep_latest_published` — placeholder mai nou câștigă în locul valorii reale

Status: **investigație + măsurare încheiate, zero cod.** Branch
`fix/dedup-and-zero-guards`, worktree `../macro-dev`.

## Constatarea

`_keep_latest_published` (`src/economic_compute.py:87`) alege, în interiorul
unui cluster de dedup, rândul cu `release_dt` cel mai târziu dintre cele cu
`actual` nenul — presupunând implicit "mai târziu = mai autoritar". Când un
placeholder (`actual=0.0`) e publicat DUPĂ valoarea reală (nu înainte, cum e
tiparul obișnuit CHF/GBP), regula alege greșit placeholder-ul. E același
viciu ca la garda PMI (impostorii câștigau prin oră de publicare), dar acolo
defectul era în date; aici e în logica de alegere.

## 1. Cât de răspândit e — scanat pe TOATE indicatoarele, nu doar cele 3 din FAZA 1

Metodologie: pentru fiecare (indicator, valută), replicat exact clusteringul
de proximitate al `_dedup_flash_final` (cadrul de scoring FF nu are coloană
`period`, deci ramura de proximitate rulează mereu), aplicat
`_keep_latest_published` neschimbat, și verificat pentru fiecare cluster cu
≥2 rânduri: rândul păstrat are `actual=0.0` ȘI alt rând din același cluster
are `actual` nenul și diferit de zero?

**4 instanțe confirmate, în tot setul de date** (nu doar cele 3 indicatoare
din FAZA 1):

| indicator | valută | păstrat (greșit) | scos (corect) | `can_be_zero` |
|---|---|---|---|---|
| `employment_change` | GBP | 2024-01-21, `0.0` | 2024-01-16, `11.7` | true |
| `employment_change` | USD | 2024-01-10, `0.0` | 2024-01-05, `216.0` | true |
| `interest_rate_decision` | EUR | 2024-01-30, `0.0` | 2024-01-25, `4.5` | true |
| `retail_sales` | JPY | 2026-04-29 23:50, `0.0` | 2026-04-29 22:50, `1.7` | true |

Toate 4 au aceeași amprentă: rândul-placeholder are `forecast`/`previous`
identice cu rândul real vecin (verificat direct în parquet pentru fiecare),
exact tiparul rând-fantomă găsit deja în FAZA 1 — doar că aici ordinea de
sosire e inversată (placeholder-ul vine al doilea, nu primul).

**Fereastra activă azi:**

| instanță | în fereastra `surprise_window_k=12` de azi? |
|---|---|
| GBP employment_change 2024-01-21 | Nu — 2024, mult în afara ferestrei |
| USD employment_change 2024-01-10 | Nu — 2024, mult în afara ferestrei |
| EUR interest_rate_decision 2024-01-30 | Nu — display-only oricum (vezi FAZA 1) |
| **JPY retail_sales 2026-04-29** | **Da — activ acum** |

Doar cazul JPY e activ azi; celelalte 3 sunt artefacte istorice inerte
(prea vechi ca să mai intre în fereastra de calcul a sigma curente).

## 2. Verificare: carantina de zero-placeholder neutralizează în aval?

**Confirmat, exact cum a fost presupus.** Scanarea de mai sus a acoperit
TOATE indicatoarele din `economic_indicators.yaml` (nu doar cele 3
`can_be_zero: true`), și **toate cele 4 instanțe găsite au `can_be_zero:
true`. Zero instanțe la indicatori fără `can_be_zero`.**

Motivul e mecanic: `to_scoring_frame` (`src/ff_scoring.py`) transformă
`actual==0.0` în `NaN` ÎNAINTE ca `_dedup_flash_final` să ruleze, pentru
orice indicator FĂRĂ `can_be_zero: true`. Odată ce placeholder-ul devine
`NaN`, `_keep_latest_published`'s filtru `actual.notna()` îl exclude automat
din candidați — nu mai poate câștiga prin timestamp, indiferent de ordine.
Bug-ul de ordine there for cazurile fără `can_be_zero` e deja mort la
naștere. Rămâne expus DOAR pe cele 3 (acum 4, cu EUR rate) indicatoare unde
`0.0` trece intenționat ca valoare legitimă posibilă.

## 3. Fix propus

```python
def _keep_latest_published(rows):
    published = rows[rows["actual"].notna()]
    if published.empty:
        return rows["release_dt"].idxmax()
    nonzero = published[published["actual"] != 0.0]
    pick = nonzero if not nonzero.empty else published
    return pick["release_dt"].idxmax()
```

La aceeași perioadă/cluster, un rând cu `actual` nenul are prioritate față
de unul cu `0.0`, indiferent de timestamp. Dacă TOATE rândurile publicate
din cluster sunt `0.0` (o citire reală de "fără schimbare"), comportamentul
rămâne identic cu azi (fallback la cel mai recent).

### E suficient, sau prea îngust?

**Risc teoretic identificat, verificat că nu se manifestă în datele
curente:** regula ar alege greșit dacă o revizuire ULTERIOARĂ, autentică,
în ACELAȘI cluster (până la `gap_days` distanță — 18 zile lunar, 45
trimestrial) ar corecta o valoare flash reală la un `0.0` real (o lună
chiar plată, confirmată la revizuire). Aș prefera atunci flash-ul greșit
în locul revizuirii corecte.

Verificat direct: toate cele 4 instanțe reale din setul curent au
`forecast`/`previous` IDENTICE cu rândul vecin din cluster — semnătura unui
rând-fantomă duplicat, nu a unei revizuiri autentice cu valori proprii
distincte. Nu există, în datele de azi, niciun caz unde regula nouă ar
înlocui o revizuire reală și distinctă cu un flash învechit. Riscul rămâne
teoretic, nu observat — dar merită păstrat în minte dacă apare vreodată un
al 5-lea caz cu semnătură diferită (consensus/previous NEidentice cu
vecinul).

**Concluzie: regula propusă e suficientă pentru toate cazurile observate,
cu limitarea teoretică de mai sus consemnată explicit** (nu ascunsă).

## 4. Efectul măsurat

Aplicat fix-ul (izolat, prin monkeypatch pe `_dedup_flash_final`, fără
atingere de cod real) și rulat `compute_indicator_score` +
`build_payload` complet, comparat cu starea curentă.

### Pe seria afectată activ (JPY retail_sales)

```
înainte:  actual=0.5  consensus=3.1  surprise=-2.6  z=-1.8855  score=-2
după:     actual=0.5  consensus=3.1  surprise=-2.6  z=-1.8276  score=-2
```

Sigma se schimbă (mai puțin zgomot fals în fereastra de 12), z se
schimbă vizibil (-1.8855 → -1.8276) — **bug-ul distorsionează efectiv
valoarea z afișată în drill-down chiar azi** — dar diferența nu trece
pragul de bucket (ambele rotunjesc la scor `-2`), deci **nu schimbă**
`score_precise`, coverage, sau bias-ul vreunei perechi azi.

### Pe tot payload-ul (toate valutele, toate categoriile, toate instrumentele)

**Zero schimbări** de `coverage`, `score_precise`, `index`, bias sau
scor de instrument, oriunde. Celelalte 3 instanțe (GBP/USD employment
2024, EUR rate 2024) sunt prea vechi ca să afecteze vreo fereastră activă,
iar JPY retail_sales — deși își schimbă z-ul — nu traversează o graniță de
bucket azi.

### Interpretare

Bug-ul e real și activ (z afișat pentru JPY retail_sales e azi calculat pe
date parțial greșite), dar impactul lui VIZIBIL pe scor/bias/coverage e
momentan nul, pentru că singura instanță activă cade, întâmplător, departe
de o graniță de bucket. Asta poate să nu rămână așa — pe măsură ce alte
prints intră/ies din fereastra de 12, sau dacă apare un al 5-lea caz mai
aproape de o graniță, efectul vizibil ar apărea fără avertisment. Fix-ul e
justificat ca și corectitudine a datelor folosite la scor, nu (doar) ca
reacție la un efect vizibil azi.

## Fire consemnate, fără acțiune (per instrucțiune)

- **Corroborarea prin goluri de cadență** (4 serii US labour independente,
  aceeași fereastră oct-nov 2025) — criteriul real pentru placeholderele
  de shutdown, de construit într-o fază viitoare. Vezi FAZA 1
  (`docs/proposal-zero-with-consensus.md` §2, §5).
- **BOJ sentinel `0.00`** (9/31 rânduri `interest_rate_decision` JPY) —
  display-only, fără impact de scor. Vezi FAZA 1 §7b.
- **Cele 2 rânduri AUD `employment_change`** (2025-09-18, 2025-10-16,
  consensus 21.2/20.5) — fără corroborare structurală găsită, rămân
  nerezolvate. Vezi FAZA 1 §5.

## Verificări făcute

- Scanare completă pe toate indicatoarele din `economic_indicators.yaml`
  (nu doar cele 3 `can_be_zero`), pe calea reală (`to_scoring_frame` →
  excludere carantină → clustering identic cu `_dedup_flash_final`).
- Fiecare din cele 4 instanțe verificată manual în parquet-ul brut —
  confirmată amprenta rând-fantomă (forecast/previous identice cu vecinul).
- Fix-ul testat izolat (o singură serie) și complet (`build_payload` pe tot
  setul) — niciun efect ascuns găsit dincolo de cele raportate.
- `git status` — niciun fișier de cod sau date modificat.

## Ce NU s-a făcut

- Niciun cod de producție atins (`src/economic_compute.py` neschimbat).
- FAZA 2 și FAZA 3 (triplicare CHF/GBP, USD GDP) — oprite explicit, nu
  investigate în sesiunea asta.
