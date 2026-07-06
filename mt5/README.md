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

## v1.3+ — inline index symbol discovery

Symbol-list entries are `,`-separated; an entry may hold `|`-separated candidates in preference
order for indices whose broker name varies. `ResolveSymbol` picks the first candidate with valid
D1 data and exports under that broker name; `data/price_symbols.yaml` maps every candidate →
board key, so whichever resolves ingests correctly.

```
US100|USTEC|NAS100  (Nasdaq)   DE40|GER40  (DAX)   UK100|FTSE100  (FTSE)   JPN225|JP225|NIK225 (Nikkei)
```

### ⚠️ MT5 input-string truncation (v1.31 fix)

MT5 **truncates an `input string` at ~250 characters** in the properties dialog. The full 36-symbol
list exceeds that, so a single `InpSymbols` silently lost its tail — which is why UK100/JPN225 were
dropped and the EA reported "34 ok / 0 missing" (the truncated list simply never contained them).
**v1.31 splits the list into 3 concatenated inputs** (`InpSymbolsFX1 + InpSymbolsFX2 +
InpSymbolsOther`), each under the limit, joined in code. Result: all **36/36 resolve** on
MetaQuotes-Demo (UK100→FTSE, JPN225→Nikkei, DE40→DAX, USTEC→Nasdaq), 400 D1 bars each.

> **RULE — Reset Inputs on any list change:** MT5 caches an EA's inputs per chart. After editing
> any `InpSymbols*` default (or recompiling with a changed list), remove + re-add the EA (or use
> **Reset** in the Inputs tab) so the chart picks up the new defaults — otherwise the running
> instance keeps the old (possibly truncated) list and the change appears to have no effect.

**Checkpoint after compile+run:** in the Experts log confirm the discovery lines, e.g.
`PriceExport discovery: [DE40|GER40] -> DE40 (resolved)`, and **36 symbols / 0 missing**.
