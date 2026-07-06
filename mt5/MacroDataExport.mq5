//+------------------------------------------------------------------+
//|  MacroDataExport.mq5  v1.31                                      |
//|  PRICE-ONLY export -> price_history.csv                          |
//|                                                                  |
//|  v1.31: symbol list SPLIT into 3 input strings (MT5 truncates    |
//|  input strings ~250 chars in the properties dialog, which        |
//|  silently dropped UK100/JPN225 groups in v1.3).                  |
//|  v1.3: calendar export removed (FF migration); discovery groups. |
//|  Anti-degradation guard kept.                                    |
//+------------------------------------------------------------------+
#property version   "1.31"
#property strict

input int    InpRefreshMinutes = 60;    // price export interval (minutes)
input int    InpBars           = 400;   // daily bars per symbol
input string InpPriceFileName  = "price_history.csv";
// Lists are concatenated. ',' separates entries, '|' separates
// candidates within an entry (first that resolves is used).
input string InpSymbolsFX1 =
   "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,"
   "EURGBP,EURJPY,EURCHF,EURAUD,EURNZD,EURCAD";
input string InpSymbolsFX2 =
   "GBPJPY,GBPCHF,GBPAUD,GBPNZD,GBPCAD,AUDJPY,NZDJPY,CADJPY,CHFJPY,"
   "AUDNZD,AUDCAD,AUDCHF,NZDCAD,NZDCHF,CADCHF";
input string InpSymbolsOther =
   "XAUUSD,XAGUSD,US500,US30,"
   "US100|USTEC|NAS100,DE40|GER40,UK100|FTSE100,JPN225|JP225|NIK225";

//+------------------------------------------------------------------+
int OnInit()
{
   ExportPrices();
   EventSetTimer(InpRefreshMinutes*60);
   return(INIT_SUCCEEDED);
}
void OnDeinit(const int reason){ EventKillTimer(); }
void OnTimer(){ ExportPrices(); }

//=================== helpers =======================================
int SplitTrim(const string src, const ushort sep, string &out[])
{
   string parts[];
   int k = StringSplit(src, sep, parts);
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

string ResolveSymbol(const string group)
{
   string cand[];
   int k = SplitTrim(group, '|', cand);
   for(int i=0; i<k; i++)
   {
      if(!SymbolSelect(cand[i], true)) continue;
      MqlRates probe[];
      if(CopyRates(cand[i], PERIOD_D1, 0, 1, probe) > 0)
      {
         if(k>1) PrintFormat("PriceExport discovery: [%s] -> %s (resolved)", group, cand[i]);
         return cand[i];
      }
   }
   PrintFormat("PriceExport discovery: [%s] -> NONE (all candidates unavailable)", group);
   return "";
}

//=================== PRICE =========================================
void ExportPrices()
{
   string all = InpSymbolsFX1 + "," + InpSymbolsFX2 + "," + InpSymbolsOther;
   string groups[];
   int ng = SplitTrim(all, ',', groups);

   string tmp = InpPriceFileName + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_UNICODE);
   if(h==INVALID_HANDLE){ Print("PriceExport FileOpen err ", GetLastError()); return; }

   FileWriteString(h, "symbol\tdate\topen\thigh\tlow\tclose\n");

   int rows=0, ok_syms=0, missing=0;
   for(int g=0; g<ng; g++)
   {
      string sym = ResolveSymbol(groups[g]);
      if(sym==""){ missing++; continue; }

      MqlRates rates[];
      ArraySetAsSeries(rates, false);
      int n = CopyRates(sym, PERIOD_D1, 0, InpBars, rates);
      if(n<=0){ Print("PriceExport: no D1 data for ", sym, " (", GetLastError(), ")"); missing++; continue; }

      int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(dg<=0) dg = 5;
      ok_syms++;

      for(int i=0; i<n; i++)
      {
         string d = TimeToString(rates[i].time, TIME_DATE);
         StringReplace(d, ".", "-");
         string row =
            sym + "\t" + d + "\t" +
            DoubleToString(rates[i].open,  dg) + "\t" +
            DoubleToString(rates[i].high,  dg) + "\t" +
            DoubleToString(rates[i].low,   dg) + "\t" +
            DoubleToString(rates[i].close, dg) + "\n";
         FileWriteString(h, row);
         rows++;
      }
   }
   FileClose(h);

   if(rows==0)
   {
      if(FileIsExist(tmp)) FileDelete(tmp);
      Print("PriceExport: 0 rows, KEEPING previous CSV (prices unavailable)");
      return;
   }
   if(FileIsExist(InpPriceFileName)) FileDelete(InpPriceFileName);
   FileMove(tmp, 0, InpPriceFileName, 0);
   Print("PriceExport: ", rows, " rows / ", ok_syms, " symbols (", missing,
         " missing) -> MQL5/Files/", InpPriceFileName);
}
//+------------------------------------------------------------------+