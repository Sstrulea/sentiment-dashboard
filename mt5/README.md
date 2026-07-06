# MT5 Expert Advisor — MacroDataExport

`MacroDataExport.mq5` (v1.3) is the versioned source of truth for the EA that exports daily
OHLC (`price_history.csv`) into the MT5 `MQL5/Files/` directory the Python pipeline reads via
`$MT5_FILES_DIR`.

> **v1.3 is PRICE-ONLY** — the economic **calendar** is now sourced from Forex Factory (Python
> `ff_refresh`); the EA's calendar export was removed. MT5 is the source for **OHLC/trend** only.

## Deploy (manual — compiled by hand in MetaEditor)

The live copy runs from the Wine MT5 install, OUTSIDE the repo:

```
~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/MacroDataExport.mq5
```

To deploy a new version:
1. `cp mt5/MacroDataExport.mq5  "<…>/MQL5/Experts/MacroDataExport.mq5"` (overwrite the live copy).
2. Open MetaEditor → `MacroDataExport.mq5` → **Compile (F7)** → expect `0 errors, 0 warnings`.
3. Re-attach the EA to a chart (or remove + re-add) so the new binary runs.
4. Confirm in **Toolbox → Experts** log.

## v1.3 — inline index symbol discovery

`InpSymbols` entries are `,`-separated; an entry may hold `|`-separated candidates in preference
order for indices whose broker name varies. `ResolveSymbol` picks the first candidate with valid
D1 data and exports under that broker name; `data/price_symbols.yaml` maps every candidate →
board key, so whichever resolves ingests correctly.

```
US100|USTEC|NAS100  (Nasdaq)   DE40|GER40  (DAX)   UK100|FTSE100  (FTSE)   JPN225|JP225|NIK225 (Nikkei)
```

**Validation on MetaQuotes-Demo (2026-07-06):** 34/36 entries resolve, 400 D1 bars each.
Resolved indices: `USTEC` (Nasdaq), `DE40` (DAX), plus `US500`/`US30`. **FTSE100 and NIKKEI are
NOT offered by this broker under any candidate** → they stay "no data" (no trend cell), handled
gracefully. The EA's "0 missing" report reflects the running chart's `InpSymbols` (the two absent
index groups are not in the chart's saved params).

**Checkpoint after compile+run:** in the Experts log confirm the discovery lines, e.g.
`PriceExport discovery: [DE40|GER40] -> DE40 (resolved)` with **err=0**.
