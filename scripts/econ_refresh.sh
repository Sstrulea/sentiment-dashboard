#!/bin/zsh
set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export MT5_FILES_DIR="/Users/sebastian/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
REPO="$HOME/projects/macro-data-analysis"
cd "$REPO" || exit 1
git pull --rebase --autostash >/dev/null 2>&1
cal_before=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
rates_before=$(md5 -q data/rates.parquet 2>/dev/null)
ry_before=$(md5 -q data/real_yields.parquet 2>/dev/null)
# Refresh the calendar (surprises) and the 2y rates (Monetary Policy) before render.
./.venv/bin/python -m src.economic_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.rate_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.realyield_fetch >> /tmp/econ.log 2>&1
cal_after=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
rates_after=$(md5 -q data/rates.parquet 2>/dev/null)
ry_after=$(md5 -q data/real_yields.parquet 2>/dev/null)
if [ "$cal_before" != "$cal_after" ] || [ "$rates_before" != "$rates_after" ] || [ "$ry_before" != "$ry_after" ]; then
  ./.venv/bin/python -m src.main --mode economic >> /tmp/econ.log 2>&1
  git add data/economic_calendar.parquet data/rates.parquet data/real_yields.parquet \
          public/economic.html public/data/economic.json
  git commit -m "econ: refresh $(date -u +%FT%TZ)" >/dev/null 2>&1 && git push >/dev/null 2>&1
fi
