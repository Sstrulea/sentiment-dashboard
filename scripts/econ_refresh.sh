#!/bin/zsh
set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export MT5_FILES_DIR="/Users/sebastian/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
REPO="$HOME/Documents/projects/macro-data-analysis"
cd "$REPO" || exit 1
git pull --rebase --autostash >/dev/null 2>&1
before=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
./.venv/bin/python -m src.economic_fetch >> /tmp/econ.log 2>&1
after=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
if [ "$before" != "$after" ]; then
  ./.venv/bin/python -m src.main --mode economic >> /tmp/econ.log 2>&1
  git add data/economic_calendar.parquet public/economic.html public/data/economic.json
  git commit -m "econ: refresh $(date -u +%FT%TZ)" >/dev/null 2>&1 && git push >/dev/null 2>&1
fi
