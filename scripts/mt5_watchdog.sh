#!/bin/zsh
# relansează MT5 dacă terminalul nu rulează (auto-close/crash)
if ! pgrep -f "terminal64.exe" >/dev/null 2>&1; then
  open -a "MetaTrader 5"
fi
