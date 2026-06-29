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
nl_before=$(md5 -q data/net_liquidity.parquet 2>/dev/null)
ph_before=$(md5 -q data/price_history.parquet 2>/dev/null)
# Refresh the calendar (surprises), the 2y rates (Monetary Policy), real yields,
# and Fed net liquidity (WALCL−TGA−RRP) before render. liquidity_fetch is safe to
# auto-run: the ×1000 RRP units fix is verified, the anti-degradation guard keeps
# the existing parquet when RRP is missing, and _print_report tolerates a
# net_liquidity-only parquet (no crash). Self-heals — when FRED serves RRPONTSYD
# again it rebuilds the 6-col parquet and regenerates public/.
./.venv/bin/python -m src.economic_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.rate_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.realyield_fetch >> /tmp/econ.log 2>&1
./.venv/bin/python -m src.liquidity_fetch >> /tmp/econ.log 2>&1
# Daily OHLC ingest (MT5 price_history.csv → data/price_history.parquet). Graceful:
# missing CSV → log + parquet unchanged (never blocks the refresh). The parquet
# changes ~once/day when the EA writes a new daily bar → md5 differs → one render/
# day; the other hours md5 is identical → skip (idempotent, no empty commit). The
# render scores trend on the last CLOSED bar (trend_score excludes the forming bar).
./.venv/bin/python -m src.price_fetch >> /tmp/econ.log 2>&1
cal_after=$(md5 -q data/economic_calendar.parquet 2>/dev/null)
rates_after=$(md5 -q data/rates.parquet 2>/dev/null)
ry_after=$(md5 -q data/real_yields.parquet 2>/dev/null)
nl_after=$(md5 -q data/net_liquidity.parquet 2>/dev/null)
ph_after=$(md5 -q data/price_history.parquet 2>/dev/null)
if [ "$cal_before" != "$cal_after" ] || [ "$rates_before" != "$rates_after" ] || [ "$ry_before" != "$ry_after" ] || [ "$nl_before" != "$nl_after" ] || [ "$ph_before" != "$ph_after" ]; then
  ./.venv/bin/python -m src.main --mode economic >> /tmp/econ.log 2>&1
  git add data/economic_calendar.parquet data/rates.parquet data/real_yields.parquet \
          data/net_liquidity.parquet data/price_history.parquet \
          public/economic.html public/data/economic.json
  git commit -m "econ: refresh $(date -u +%FT%TZ)" >/dev/null 2>&1 && git push >/dev/null 2>&1
fi
