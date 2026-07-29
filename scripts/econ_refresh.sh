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
  # Daily JBlanked actuals pull (the weekly feed above is structurally
  # actual-less). Window-gated INSIDE the module: first hourly tick at/after
  # 21:00 UTC with no success recorded in data/jb_last_pull.json pulls; later
  # ticks retry until success (free tier ~1 call/day; auth JBLANKED_API_KEY
  # from .env). Fail-open — a JB failure never blocks the rest of the refresh;
  # its lines carry the "JB pull:" prefix in /tmp/econ.log.
  ./.venv/bin/python -m src.jb_actuals >> /tmp/econ.log 2>&1
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
# ---- Freshness watchdog + ESCALARE (max 1 notificare / 6h per sursă) ----
./.venv/bin/python - <<'PY' >> /tmp/econ.log 2>&1
import datetime, json, pathlib, subprocess
from src.economic_render import _freshness

MARK, COOLDOWN_H = pathlib.Path("/tmp/econ_alert_state.json"), 6
now = datetime.datetime.utcnow()
seen = json.loads(MARK.read_text()) if MARK.exists() else {}
stale = {k: v for k, v in _freshness().items() if isinstance(v, dict) and v.get("stale")}

for k, v in stale.items():
    print(f"[{now:%FT%TZ}] FRESHNESS WARNING: {k} STALE — last update {v['last_update']} ({v['age_days']}d ago)")
    last = seen.get(k)
    if last and (now - datetime.datetime.fromisoformat(last)).total_seconds() < COOLDOWN_H * 3600:
        continue
    msg = f"{k} stale — {v['age_days']}d (last {v['last_update']})"
    subprocess.run(["osascript", "-e",
        f'display notification "{msg}" with title "macro-dashboard" subtitle "date invechite" sound name "Basso"'],
        check=False)
    seen[k] = now.isoformat()
    print(f"[{now:%FT%TZ}] ALERT sent: {msg}")

for k in list(seen):
    if k not in stale:
        seen.pop(k)          # sursa s-a însănătoșit → resetăm cooldown-ul
MARK.write_text(json.dumps(seen))
PY

rates_after=$(md5 -q data/rates.parquet 2>/dev/null)
ry_after=$(md5 -q data/real_yields.parquet 2>/dev/null)
nl_after=$(md5 -q data/net_liquidity.parquet 2>/dev/null)
trend_after=$(./.venv/bin/python -m src.trend_signature 2>/dev/null)

if [ "$cal_before" != "$cal_after" ] || [ "$rates_before" != "$rates_after" ] || [ "$ry_before" != "$ry_after" ] || [ "$nl_before" != "$nl_after" ] || [ "$trend_before" != "$trend_after" ]; then
  ./.venv/bin/python -m src.main --mode economic >> /tmp/econ.log 2>&1
  # commit the active calendar parquet + shared data + rendered public/.
  if [ "${ECON_REFRESH_NO_PUBLISH:-0}" = "1" ]; then
    echo "[$(date -u +%FT%TZ)] NO_PUBLISH=1 — sar peste git add/commit/push." >> /tmp/econ.log
  else
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
fi
