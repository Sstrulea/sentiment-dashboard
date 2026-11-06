//+------------------------------------------------------------------+
//| CalendarHistoryExport.mq5 — one-off SCRIPT (phase 5, stage F)     |
//| Exports MT5 economic-calendar history (CalendarValueHistory) for  |
//| the 8 currencies, 2012-01-01 -> 2026-10-31, to                    |
//| MQL5/Files/calendar_history_2012_2026.csv (UTF-8, comma).          |
//| Columns: event_id, event_name, country_code, currency,            |
//|          time_server, server_minus_gmt_sec, importance, period,   |
//|          actual, forecast, previous, revised_previous, unit,       |
//|          multiplier, digits                                        |
//| Times are trade-server times; server_minus_gmt_sec is the offset   |
//| at export time — the Python side converts with the broker's DST    |
//| rule and checks it against FF on 2023-2026.                        |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input datetime InpFrom = D'2012.01.01 00:00';
input datetime InpTo   = D'2026.10.31 23:59';
input string   InpFile = "calendar_history_2012_2026.csv";

string CCYS[] = {"USD","EUR","GBP","JPY","CAD","AUD","NZD","CHF"};

string Num(const bool has, const double v)
{
   return has ? DoubleToString(v, 6) : "";
}

string Clean(string s)
{
   StringReplace(s, ",", " ");
   StringReplace(s, "\"", "'");
   return s;
}

void OnStart()
{
   int h = FileOpen(InpFile, FILE_WRITE | FILE_TXT | FILE_ANSI, 0, CP_UTF8);
   if(h == INVALID_HANDLE) { Print("cannot open ", InpFile, " err=", GetLastError()); return; }
   FileWriteString(h, "event_id,event_name,country_code,currency,time_server,server_minus_gmt_sec,importance,period,"
                      "actual,forecast,previous,revised_previous,unit,multiplier,digits\n");
   long offset = (long)(TimeTradeServer() - TimeGMT());
   int total = 0;
   for(int i = 0; i < ArraySize(CCYS); i++)
   {
      MqlCalendarValue values[];
      ResetLastError();
      int n = CalendarValueHistory(values, InpFrom, InpTo, NULL, CCYS[i]);
      if(n < 0) { Print(CCYS[i], ": CalendarValueHistory failed err=", GetLastError()); continue; }
      for(int k = 0; k < n; k++)
      {
         MqlCalendarEvent ev;
         MqlCalendarCountry co;
         if(!CalendarEventById(values[k].event_id, ev)) continue;
         if(!CalendarCountryById(ev.country_id, co)) continue;
         string line = StringFormat("%I64u,%s,%s,%s,%s,%I64d,%d,%s,%s,%s,%s,%s,%d,%d,%d\n",
            values[k].event_id, Clean(ev.name), co.code, co.currency,
            TimeToString(values[k].time, TIME_DATE | TIME_SECONDS), offset, (int)ev.importance,
            TimeToString(values[k].period, TIME_DATE),
            Num(values[k].HasActualValue(), values[k].GetActualValue()),
            Num(values[k].HasForecastValue(), values[k].GetForecastValue()),
            Num(values[k].HasPreviousValue(), values[k].GetPreviousValue()),
            Num(values[k].HasRevisedValue(), values[k].GetRevisedValue()),
            (int)ev.unit, (int)ev.multiplier, (int)ev.digits);
         FileWriteString(h, line);
         total++;
      }
      Print(CCYS[i], ": ", n, " values");
   }
   FileClose(h);
   Print("CalendarHistoryExport: ", total, " rows -> MQL5/Files/", InpFile);
}
