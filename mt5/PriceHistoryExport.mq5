//+------------------------------------------------------------------+
//|  PriceHistoryExport.mq5  v1.0                                     |
//|  Dumps daily OHLC for a configured symbol list to a TSV file.    |
//|                                                                  |
//|  Companion to EconCalendarExport.mq5 — IDENTICAL export model:   |
//|  tab-separated, UTF-16 (FILE_UNICODE), atomic .tmp -> FileMove,  |
//|  periodic via timer. This EA does NOT touch the calendar export. |
//|                                                                  |
//|  INSTALL: copy to  MQL5/Experts/  (next to EconCalendarExport),  |
//|  compile (F7), drop on ANY one chart. The output lands in        |
//|  MQL5/Files/price_history.csv — the same folder the calendar EA  |
//|  writes to, which src/price_fetch.py already knows via           |
//|  $MT5_FILES_DIR.                                                  |
//|                                                                  |
//|  SYMBOLS: edit InpSymbols with YOUR broker's exact symbol names  |
//|  (Market Watch -> right-click -> Symbols). These are the BROKER  |
//|  symbols; src/price_fetch.py maps them to board keys via         |
//|  data/price_symbols.yaml. A symbol your broker lacks (e.g. DXY)  |
//|  is simply skipped with a log line — no crash, no empty rows.    |
//+------------------------------------------------------------------+
#property version   "1.00"
#property strict

input int    InpRefreshMinutes = 60;     // export interval (minutes)
input int    InpBars           = 400;    // daily bars per symbol (>=300 for SMA200 + buffer)
input string InpFileName       = "price_history.csv"; // -> MQL5/Files/
// Comma-separated BROKER symbols. Defaults are PLACEHOLDERS — confirm/replace
// with the exact names in your terminal. Keep names aligned with
// data/price_symbols.yaml (board_key: broker_symbol).
input string InpSymbols =
   "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,"
   "EURGBP,EURJPY,EURCHF,EURAUD,EURNZD,EURCAD,"
   "GBPJPY,GBPCHF,GBPAUD,GBPNZD,GBPCAD,"
   "AUDJPY,NZDJPY,CADJPY,CHFJPY,"
   "AUDNZD,AUDCAD,AUDCHF,NZDCAD,NZDCHF,CADCHF,"
   "XAUUSD,XAGUSD,"
   "US500,NAS100,US30,GER40,JP225,UK100";   // DXY intentionally omitted (often absent)

//+------------------------------------------------------------------+
int OnInit()
{
   ExportPrices();
   EventSetTimer(InpRefreshMinutes*60);
   return(INIT_SUCCEEDED);
}
void OnDeinit(const int reason){ EventKillTimer(); }
void OnTimer(){ ExportPrices(); }

// Split "A, B ,C" -> trimmed array, dropping blanks.
int ParseSymbols(const string csv, string &out[])
{
   string parts[];
   int k = StringSplit(csv, ',', parts);
   ArrayResize(out, 0);
   int n = 0;
   for(int i=0; i<k; i++)
   {
      string s = parts[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s)==0) continue;
      ArrayResize(out, n+1);
      out[n++] = s;
   }
   return n;
}

void ExportPrices()
{
   string symbols[];
   int ns = ParseSymbols(InpSymbols, symbols);

   string tmp = InpFileName + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_UNICODE);
   if(h==INVALID_HANDLE){ Print("PriceExport FileOpen err ", GetLastError()); return; }

   FileWriteString(h, "symbol\tdate\topen\thigh\tlow\tclose\n");

   int rows=0, ok_syms=0, missing=0;
   for(int s=0; s<ns; s++)
   {
      string sym = symbols[s];
      // Make sure the symbol is resolvable / in Market Watch before copying.
      if(!SymbolSelect(sym, true)){ Print("PriceExport: symbol unavailable -> ", sym); missing++; continue; }

      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int n = CopyRates(sym, PERIOD_D1, 0, InpBars, rates);
      if(n<=0){ Print("PriceExport: no D1 data for ", sym, " (", GetLastError(), ")"); missing++; continue; }

      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(dg<=0) dg = 5;
      ok_syms++;

      for(int i=0; i<n; i++)
      {
         // YYYY.MM.DD -> YYYY-MM-DD (board/parquet convention)
         string d = TimeToString(rates[i].time, TIME_DATE);
         StringReplace(d, ".", "-");
         string row =
            sym + "\t" +
            d + "\t" +
            DoubleToString(rates[i].open,  dg) + "\t" +
            DoubleToString(rates[i].high,  dg) + "\t" +
            DoubleToString(rates[i].low,   dg) + "\t" +
            DoubleToString(rates[i].close, dg) + "\n";
         FileWriteString(h, row);
         rows++;
      }
   }
   FileClose(h);
   if(FileIsExist(InpFileName)) FileDelete(InpFileName);
   FileMove(tmp, 0, InpFileName, 0);
   Print("PriceExport: ", rows, " rows / ", ok_syms, " symbols (", missing,
         " missing) -> MQL5/Files/", InpFileName);
}
//+------------------------------------------------------------------+
