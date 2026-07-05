# MT5 Expert Advisor — MacroDataExport

`MacroDataExport.mq5` is the versioned source of truth for the EA that exports BOTH the
economic calendar (`economic_calendar.csv`) and daily OHLC (`price_history.csv`) into the
MT5 `MQL5/Files/` directory the Python pipeline reads via `$MT5_FILES_DIR`.

> The economic **calendar** is now sourced from Forex Factory (Python `ff_refresh`); the EA's
> calendar export still runs but is no longer the scoring source. The EA remains the source
> for **OHLC/trend** (`price_history.csv`).

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

## v1.2 — index symbol discovery

`InpDiscoverSymbols` holds `;`-separated candidate groups, `|`-separated candidates in
preference order, for indices whose broker name varies:

```
DE40|GER40|DE30|GER30 ;  UK100|FTSE100|UK100Cash ;  JP225|JPN225|NIK225|NI225
```

At export the EA resolves each group to the first candidate with a valid quote + a daily bar
(`ResolveSymbol`) and exports under that broker name; `data/price_symbols.yaml` maps every
candidate → board key (`DAX` / `FTSE100` / `NIKKEI`), so whichever resolves ingests correctly.

**Checkpoint after compile+run:** in the Experts log confirm the discovery lines, e.g.
`PriceExport discovery: [DE40|GER40|DE30|GER30] -> DE40 (resolved)` with **err=0**, before
running the Python-side validation (parquet has the new symbols → trend cells populate).
