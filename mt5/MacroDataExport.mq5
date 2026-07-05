//+------------------------------------------------------------------+
//|  MacroDataExport.mq5  v1.2                                       |
//|  Single EA — exports BOTH:                                       |
//|    1) MQL5 economic calendar (8 majors) -> economic_calendar.csv |
//|    2) Daily OHLC for a symbol list      -> price_history.csv     |
//|                                                                  |
//|  v1.1: ANTI-DEGRADATION GUARD — if an export produces 0 rows     |
//|  (e.g. CalendarValueHistory returns 0 during a calendar blip),   |
//|  the previous CSV is KEPT (not overwritten with a header-only    |
//|  file). Plus per-currency calendar diagnostics.                  |
//|  v1.2: SYMBOL DISCOVERY for indices whose broker name varies     |
//|  (DAX/FTSE100/Nikkei). Each is a '|'-separated candidate group;  |
//|  the first candidate that resolves (SymbolSelect + a valid quote |
//|  + a daily bar) is exported and logged. No candidate → err +     |
//|  skip (the existing rows==0/err export guard applies naturally). |
//|  Export logic is UNCHANGED — only the symbol list is extended.   |
//|                                                                  |
//|  Run on ONE chart. Both files land in MQL5/Files/. After editing |
//|  RECOMPILE (F7) and RE-ATTACH.                                    |
//+------------------------------------------------------------------+
#property version   "1.20"
#property strict

// --- shared timer ---
input int    InpRefreshMinutes    = 15;   // calendar export interval (minutes)

// --- calendar inputs ---
input int    InpLookbackDays      = 540;  // history window back
input int    InpForwardDays       = 14;   // upcoming window forward
input string InpCalFileName       = "economic_calendar.csv";

// --- price inputs ---
input int    InpPriceEveryNTicks  = 4;    // run price export every N timer ticks (4 * 15min = hourly)
input int    InpBars              = 400;  // daily bars per symbol (>=300 for SMA200 + buffer)
input string InpPriceFileName     = "price_history.csv";
input string InpSymbols =
   "EURUSD,GBPUSD,USDJPY,USDCHF,USDCAD,AUDUSD,NZDUSD,"
   "EURGBP,EURJPY,EURCHF,EURAUD,EURNZD,EURCAD,"
   "GBPJPY,GBPCHF,GBPAUD,GBPNZD,GBPCAD,"
   "AUDJPY,NZDJPY,CADJPY,CHFJPY,"
   "AUDNZD,AUDCAD,AUDCHF,NZDCAD,NZDCHF,CADCHF,"
   "XAUUSD,XAGUSD,"
   "US500,NAS100,US30";   // fixed symbols (DXY omitted); indices below are discovered
// v1.2 discovery: candidate groups (';'-separated), candidates within a group
// '|'-separated in preference order. The first that resolves is exported under its
// broker name (the Python side maps every candidate → board key in price_symbols.yaml).
// DE40 is confirmed on MetaQuotes-Demo; the rest are alternates.
input string InpDiscoverSymbols =
   "DE40|GER40|DE30|GER30;"      // DAX
   "UK100|FTSE100|UK100Cash;"    // FTSE 100
   "JP225|JPN225|NIK225|NI225";  // Nikkei 225

string Currencies[] = {"USD","EUR","GBP","JPY","AUD","NZD","CAD","CHF"};
int    g_tick = 0;   // timer tick counter, for price throttling

//+------------------------------------------------------------------+
int OnInit()
{
   ExportCalendar();      // calendar immediately
   ExportPrices();        // price immediately
   EventSetTimer(InpRefreshMinutes*60);
   return(INIT_SUCCEEDED);
}
void OnDeinit(const int reason){ EventKillTimer(); }

void OnTimer()
{
   ExportCalendar();                       // every tick
   g_tick++;
   if(g_tick % InpPriceEveryNTicks == 0)   // throttled
      ExportPrices();
}

//=================== CALENDAR ======================================
string Clean(string s){ StringReplace(s,"\t"," "); StringReplace(s,"\n"," "); StringReplace(s,"\r"," "); return s; }
string Val(long raw){ return (raw==LONG_MIN) ? "" : DoubleToString(raw/1000000.0, 6); }

void ExportCalendar()
{
   datetime now  = TimeTradeServer();
   datetime from = now - (datetime)InpLookbackDays*86400;
   datetime to   = now + (datetime)InpForwardDays*86400;
   int gmt_offset = (int)(TimeTradeServer() - TimeGMT());

   string tmp = InpCalFileName + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_UNICODE);
   if(h==INVALID_HANDLE){ Print("EconExport FileOpen err ", GetLastError()); return; }

   FileWriteString(h, "event_id\tserver_time\tgmt_offset_sec\tcountry\tcurrency\timportance\tevent\tactual\tforecast\tprevious\trevised\tunit\timpact\tperiod\n");
   int rows=0;
   for(int c=0; c<ArraySize(Currencies); c++)
   {
      MqlCalendarValue values[];
      ResetLastError();
      int n = CalendarValueHistory(values, from, to, NULL, Currencies[c]);
      // Per-currency diagnostic (window + count + error) — surfaces calendar blips.
      PrintFormat("EconExport cal: ccy=%s from=%s to=%s n=%d err=%d",
                  Currencies[c], TimeToString(from), TimeToString(to), n, GetLastError());
      for(int i=0; i<n; i++)
      {
         if(values[i].time<=0) continue;
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev)) continue;
         MqlCalendarCountry co; CalendarCountryById(ev.country_id, co);
         string period = (values[i].period>0) ? TimeToString((datetime)values[i].period, TIME_DATE) : "";
         string row =
            (string)values[i].event_id + "\t" +
            TimeToString(values[i].time, TIME_DATE|TIME_SECONDS) + "\t" +
            IntegerToString(gmt_offset) + "\t" +
            Clean(co.name) + "\t" +
            co.currency + "\t" +
            IntegerToString((int)ev.importance) + "\t" +
            Clean(ev.name) + "\t" +
            Val(values[i].actual_value) + "\t" +
            Val(values[i].forecast_value) + "\t" +
            Val(values[i].prev_value) + "\t" +
            Val(values[i].revised_prev_value) + "\t" +
            IntegerToString((int)ev.unit) + "\t" +
            IntegerToString((int)values[i].impact_type) + "\t" +
            period + "\n";
         FileWriteString(h, row);
         rows++;
      }
   }
   FileClose(h);

   // ANTI-DEGRADATION: never replace a good CSV with an empty one. If the calendar
   // returned nothing (blip / reconnect), keep the last good file untouched.
   if(rows==0)
   {
      if(FileIsExist(tmp)) FileDelete(tmp);
      Print("EconExport: 0 rows, KEEPING previous CSV (calendar unavailable)");
      return;
   }
   if(FileIsExist(InpCalFileName)) FileDelete(InpCalFileName);
   FileMove(tmp, 0, InpCalFileName, 0);
   Print("EconExport: wrote ", rows, " rows -> MQL5/Files/", InpCalFileName);
}

//=================== PRICE =========================================
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

// v1.2 — resolve a '|'-separated candidate group to the first broker symbol with a
// valid quote and a daily bar. Returns "" (and logs) if none is available.
string ResolveSymbol(const string group)
{
   string cands[];
   int k = StringSplit(group, '|', cands);
   for(int i=0; i<k; i++)
   {
      string s = cands[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s)==0) continue;
      if(!SymbolSelect(s, true)) continue;
      MqlTick tk;
      if(!SymbolInfoTick(s, tk)) continue;
      if(tk.bid<=0 && tk.ask<=0 && tk.last<=0) continue;   // no valid quote
      MqlRates rr[];
      if(CopyRates(s, PERIOD_D1, 0, 1, rr) <= 0) continue;  // no daily bar
      PrintFormat("PriceExport discovery: [%s] -> %s (resolved)", group, s);
      return s;
   }
   PrintFormat("PriceExport discovery: [%s] -> NONE (no candidate available) err=%d",
               group, GetLastError());
   return "";
}

// Append each discovered index symbol (one per candidate group) to the export list.
void AppendDiscovered(string &out[], int &n)
{
   string groups[];
   int g = StringSplit(InpDiscoverSymbols, ';', groups);
   for(int i=0; i<g; i++)
   {
      string grp = groups[i];
      StringTrimLeft(grp); StringTrimRight(grp);
      if(StringLen(grp)==0) continue;
      string sym = ResolveSymbol(grp);
      if(StringLen(sym)==0) continue;
      ArrayResize(out, n+1);
      out[n++] = sym;
   }
}

void ExportPrices()
{
   string symbols[];
   int ns = ParseSymbols(InpSymbols, symbols);
   AppendDiscovered(symbols, ns);   // v1.2 — extend the list with resolved indices

   string tmp = InpPriceFileName + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_UNICODE);
   if(h==INVALID_HANDLE){ Print("PriceExport FileOpen err ", GetLastError()); return; }

   FileWriteString(h, "symbol\tdate\topen\thigh\tlow\tclose\n");

   int rows=0, ok_syms=0, missing=0;
   for(int s=0; s<ns; s++)
   {
      string sym = symbols[s];
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

   // ANTI-DEGRADATION: same guard as calendar — keep the last good price file if
   // this run produced nothing (all symbols unavailable / data not ready).
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
