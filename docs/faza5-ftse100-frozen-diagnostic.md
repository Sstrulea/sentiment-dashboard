# FAZA 5 — diagnostic FTSE100 înghețat (DIAGNOSTIC, zero cod)

Status: **diagnostic, zero cod scris, nimic MT5/EA modificat**. Branch
`fix/remaining-data-defects`, worktree `../macro-dev`.

## Fapte confirmate din repo

**FTSE100 e prezent în `price_history.parquet`, înghețat exact pe
2026-05-15**, cu **exact 400 de rânduri** — nici mai mult, nici mai puțin:

| simbol | ultima dată | rânduri |
|---|---|---|
| **FTSE100** | **2026-05-15** | **400** |
| DAX | 2026-07-31 | 419 |
| NIKKEI | 2026-07-31 | 419 |
| SP500 | 2026-07-31 | 424 |

`InpBars=400` (`mt5/MacroDataExport.mq5`) — DAX/NIKKEI/SP500 au trecut de
400 (acumulează zilnic peste fereastra inițială), FTSE100 e blocat EXACT la
valoarea inițială de backfill. Asta indică un singur export reușit complet
(400 bare), apoi zero rânduri noi de atunci — nu o degradare treptată.

## 1. E prezent FTSE100 în `InpSymbols`?

**Da, confirmat.** `mt5/MacroDataExport.mq5:27`, `InpSymbolsOther`:
```
"XAUUSD,XAGUSD,US500,US30,US100|USTEC|NAS100,DE40|GER40,UK100|FTSE100,JPN225|JP225|NIK225"
```
Grup candidate `UK100|FTSE100` (primul care rezolvă e folosit — `UK100`
încercat întâi). **Comentariul din header confirmă exact bug-ul relevant**:
"v1.31: symbol list SPLIT into 3 input strings (MT5 truncates input
strings ~250 chars..., which silently dropped UK100/JPN225 groups in
v1.3)." — grupul FTSE100 a fost VICTIMĂ a acestui bug în v1.3, dar EA-ul
curent e v1.31 (fix aplicat), deci trunchierea nu (mai) explică înghețul de
azi.

`data/price_symbols.yaml:67-72` confirmă suplimentar: *"MetaQuotes-Demo (EA
v1.31, all 36 resolve): ... UK100 (FTSE)..."* — **UK100 a fost confirmat
rezolvat cu succes pe acest cont demo**, la un moment anterior (probabil în
jurul lansării v1.31). Asta contrazice ipoteza "simbolul n-a fost niciodată
disponibil" — a funcționat cel puțin o dată.

## 2. E prezent în CSV-ul exportat azi?

**Nu se poate determina din repo** — `price_history.csv` e un fișier live pe
Mac (`MT5_FILES_DIR`), nu urmărit în git. Ce SE poate determina din codul
EA (`mt5/MacroDataExport.mq5:71-113`, `ExportPrices()`): dacă `ResolveSymbol`
nu găsește niciun candidat funcțional, scrie `PrintFormat("...-> NONE (all
candidates unavailable)")` în jurnalul Experts, incrementează `missing`, și
**sare peste simbol fără să oprească exportul** — restul simbolurilor tot se
scriu, fișierul tot se suprascrie (`rows>0` per câte simboluri rezolvă).
Garda anti-degradare (`if(rows==0) ... KEEPING previous CSV`) protejează
DOAR împotriva unui eșec TOTAL (0 rânduri) — un eșec PARȚIAL, pe un singur
simbol, e complet tăcut la nivelul fișierului: CSV-ul se actualizează normal
pentru celelalte 35, iar FTSE100 pur și simplu nu mai apare ca linie.

Partea Python (`src/price_fetch.py:148-159`, `merge()`): `pd.concat` +
`drop_duplicates(["symbol","date"], keep="last")` — **aditiv, nu șterge
niciodată** un rând pentru un simbol absent dintr-un batch nou. De-asta cele
400 de rânduri vechi ale FTSE100 rămân neatinse în parquet la nesfârșit,
în loc să dispară — comportament de „înghețare", nu de „ștergere".

## 3. Watchdog-ul de freshness — agregat sau per-instrument?

**Agregat, confirmat direct din cod.** `src/economic_render.py:558-566`:
```python
p = pd.read_parquet(PRICE_HISTORY_PARQUET)
last = pd.to_datetime(p["date"]).max()
age = _age(last)
out["price"] = {..., "stale": age > FRESHNESS_STALE_DAYS["price"]}  # prag 4 zile
```
`p["date"].max()` ia maximul peste **toate** simbolurile din
`price_history.parquet` combinate — nu per-simbol. Cât timp ORICE alt
simbol (DAX, NIKKEI, SP500 etc.) se actualizează zilnic, `age=0`,
`stale=False` — verde. **Exact de-aia n-a semnalat**: watchdog-ul n-are cum
să vadă că UN simbol specific e înghețat de 2,5 luni cât timp restul de 35
sunt la zi.

## Ce ține de MT5/EA — verificare manuală necesară (în afara repo-ului)

Nemodificat nimic; de verificat manual în jurnalul Experts (MetaTrader 5,
tab "Experts", sau fișierul de log din `MQL5/Logs/`):

1. **Caută liniile `PriceExport discovery: [UK100|FTSE100]`** din jurul
   datei de 2026-05-15 și după — dacă apare repetat `-> NONE (all candidates
   unavailable)`, simbolul a devenit indisponibil pe cont la acel moment.
2. **Caută `PriceExport: no D1 data for UK100`** (sau `FTSE100`/`UK100Cash`)
   — indică `SymbolSelect` a reușit dar `CopyRates` a întors 0 bare (simbol
   selectat dar fără istoric D1 livrat de broker).
3. **Verifică Market Watch** pe contul MetaQuotes-Demo: e UK100/FTSE100/
   UK100Cash încă listat? A fost redenumit, delistat, sau mutat într-un alt
   grup de simboluri?
4. **Verifică dacă a expirat/resetat contul demo** în jurul datei — conturile
   MetaQuotes-Demo au un ciclu de viață limitat, iar un reset ar putea
   schimba universul de simboluri disponibile fără nicio eroare explicită
   în cod, doar simbolul dispărând din listă.
5. **Verifică `GetLastError()`** raportat lângă liniile de mai sus, dacă
   există — un cod de eroare specific (ex. market closed, symbol not found)
   ar restrânge diagnosticul.

Nu am modificat `mt5/MacroDataExport.mq5`, `data/price_symbols.yaml`, nici
vreun fișier legat de EA — doar citite, per instrucțiune.
