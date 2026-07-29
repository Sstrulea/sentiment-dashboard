#!/bin/zsh
# SPLIT 2026-07-29: GitHub Actions face calendar (FF+JB), FRED si render.
# Mac-ul ramane DOAR sursa de pret — MT5 ruleaza prin Wine, imposibil in cloud.
# Fisiere disjuncte intre cele doua = zero conflicte pe parquet (binar, ne-merge-uibil).
set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export MT5_FILES_DIR="/Users/sebastian/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
REPO="$HOME/projects/macro-data-analysis"
cd "$REPO" || exit 1
git pull --rebase --autostash >/dev/null 2>&1

./.venv/bin/python -m src.price_fetch >> /tmp/econ.log 2>&1

if ! git diff --quiet data/price_history.parquet; then
  git add data/price_history.parquet
  git commit -m "price: MT5 export $(date -u +%FT%TZ)" >/dev/null 2>&1
  for i in 1 2 3; do
    git pull --rebase --autostash >/dev/null 2>&1 && git push >/dev/null 2>&1 && break
    sleep 5
  done
fi
