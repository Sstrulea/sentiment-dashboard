#!/bin/zsh
set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export MT5_FILES_DIR="/Users/sebastian/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
REPO="$HOME/projects/macro-data-analysis"
cd "$REPO" || exit 1
git pull --rebase --autostash >/dev/null 2>&1

# Phase 3 — economic calendar source switch (config/pipeline.yaml: ff | mt5 rollback).
# MT5 remains OHLC/trend; FRED remains rates/liquidity. Default 'ff'.
CAL_SRC=$(./.venv/bin/python -c "from src.ff_refresh import calendar_source; print(calendar_source())" 2>/dev/null)
[ -z "$CAL_SRC" ] && CAL_SRC="mt5"   # fail-safe to rollback if config unreadable

rates_before=$(md5 -q data/rates.parquet 2>/dev/null)
ry_before=$(md5 -q data/real_yields.parquet 2>/dev/null)
nl_before=$(md5 -q data/net_liquidity.parquet 2>/dev/null)
# Price TREND trigger: hash the trend OUTPUT (trend_cell values), not the raw parquet.
trend_before=$(./.venv/bin/python -m src.trend_signature 2>/dev/null)

# ---- CALENDAR ingest (source-switched) ----
if [ "$CAL_SRC" = "ff" ]; then
  cal_before=$(md5 -q data/economic_calendar_ff.parquet 2>/dev/null)
  # Fetch FF weekly JSON (keyless) → merge historical FF parquet; anti-degradation
  # keeps last-good on fetch fail / empty / thin payload; FRED cross-check quarantines
  # US actuals that disagree with FRED (retroactive, fail-open).
  ./.venv/bin/python -m src.ff_refresh >> /tmp/econ.log 2>&1
  cal_after=$(md5 -q data/economic_calendar_ff.parquet 2>/dev/null)
else
  # ROLLBACK: MT5 calendar ingest (quarantined behind the flag, not deleted).
  cal_before=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
  ./.venv/bin/python -m src.economic_fetch --days-back 130 >> /tmp/econ.log 2>&1
  ./.venv/bin/python -m src.calendar_fred_fallback >> /tmp/econ.log 2>&1
  cal_after=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
fi

# ---- non-calendar fetchers (unchanged) ----
./.venv/bin/python -m src.rate_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.realyield_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.liquidity_fetch >> /tmp/econ.log 2>&1
# Daily OHLC ingest (MT5 price_history.csv → parquet) — MT5 stays the price/trend source.
./.venv/bin/python -m src.price_fetch >> /tmp/econ.log 2>&1
# Freshness watchdog (non-fatal): warn if any source is stale.
./.venv/bin/python -c "
from src.economic_render import _freshness
import datetime
f=_freshness()
for k,v in f.items():
    if isinstance(v,dict) and v.get('stale'):
        print(f\"[{datetime.datetime.utcnow():%FT%TZ}] FRESHNESS WARNING: {k} STALE — last update {v['last_update']} ({v['age_days']}d ago)\")
" >> /tmp/econ.log 2>&1

rates_after=$(md5 -q data/rates.parquet 2>/dev/null)
ry_after=$(md5 -q data/real_yields.parquet 2>/dev/null)
nl_after=$(md5 -q data/net_liquidity.parquet 2>/dev/null)
trend_after=$(./.venv/bin/python -m src.trend_signature 2>/dev/null)

if [ "$cal_before" != "$cal_after" ] || [ "$rates_before" != "$rates_after" ] || [ "$ry_before" != "$ry_after" ] || [ "$nl_before" != "$nl_after" ] || [ "$trend_before" != "$trend_after" ]; then
  ./.venv/bin/python -m src.main --mode economic >> /tmp/econ.log 2>&1
  # commit the active calendar parquet + shared data + rendered public/.
  if [ "$CAL_SRC" = "ff" ]; then
    git add data/economic_calendar_ff.parquet 2>/dev/null
    [ -f data/ff_quarantine.parquet ] && git add data/ff_quarantine.parquet
  else
    git add data/economic_calendar.parquet
  fi
  git add data/rates.parquet data/real_yields.parquet data/net_liquidity.parquet \
          data/price_history.parquet public/economic.html public/data/economic.json
  git commit -m "econ: refresh $(date -u +%FT%TZ) [cal=$CAL_SRC]" >/dev/null 2>&1 && git push >/dev/null 2>&1
fi
