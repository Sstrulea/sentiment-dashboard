# TREND v2 — before/after (v1 MA×ADX → v2 Regime+Momentum)

Same ±3 range ⇒ composite weight (0.5) unchanged. ADX is display-only in v2.

## TASK 0 — empirical diagnostic verdict (both hypotheses CONFIRMED)

- **(a) NAS/SPX −1 in an uptrend pullback:** v1 `raw = short(SMA10v20) + long(SMA20v50) + slope(SMA20)`; the two SHORT terms are correlated (both read the micro-pullback) → −2, dominating the bullish SMA50/200 regime (bull_points=3 ⇒ +2).
- **(b) DAX +1 at an ATH breakout:** v1 `raw=+3` but `ADX=12.6` (lagging) → factor 0.25 → cell +1 (damped). Absolute slope threshold (eps=0) also can't scale across instruments (DAX slope/ATR=0.065 vs FTSE 0.380) — v2's ATR-normalized slope does.

v2 fixes both: SMA50/200 regime is dominant and ADX is removed from the score.

## Momentum HYSTERESIS (addendum)

Momentum uses a slope_atr **hysteresis band** (config/trend.yaml): ±1 activates at `|slope_atr| > slope_enter` (0.035) and persists until `|slope_atr| < slope_exit` (0.025); in the band it keeps the prior state (stateless — derived by walking the historical slope_atr series from price_history.parquet, no persisted state file).

## Sanity anchors
- ✅ NASDAQ >= +1  (got NASDAQ=3)
- ✅ SP500 >= +1  (got SP500=3)
- ✅ DAX == +3  (got DAX=3)

## Full before/after (trend cell)

| instrument | v1 | regime | momentum | v2 | Δ | slope/atr | ADX* |
|---|---|---|---|---|---|---|---|
| AUDCAD | -1 | +1 | +1 | +2 | +3 | 0.0388 | 14.6 |
| AUDCHF | -3 | +1 | +0 | +1 | +4 | 0.0233 | 22.9 |
| AUDJPY | -3 | +1 | +0 | +1 | +4 | 0.0042 | 23.9 |
| AUDNZD | +0 | +1 | +0 | +1 | +1 | 0.019 | 13.6 |
| AUDUSD | -3 | +1 | +0 | +1 | +4 | -0.0297 | 39.1 |
| CADCHF | -1 | -2 | +0 | -2 | -1 | -0.0192 | 19.8 |
| CADJPY | -3 | +1 | -1 | +0 | +3 | -0.0426 | 25.0 |
| CHFJPY | -2 | +1 | +0 | +1 | +3 | -0.0199 | 20.6 |
| DAX | +1 | +2 | +1 | +3 | +2 | 0.0651 | 12.6 |
| DJIA | +3 | +2 | +1 | +3 | +0 | 0.119 | 26.1 |
| EURAUD | +3 | -1 | -1 | -2 | -5 | -0.0251 | 33.8 |
| EURCAD | +2 | +1 | +0 | +1 | -1 | 0.023 | 21.4 |
| EURCHF | +0 | -1 | +0 | -1 | -1 | 0.0041 | 14.6 |
| EURGBP | -2 | -2 | -1 | -3 | -1 | -0.056 | 15.0 |
| EURJPY | -1 | +1 | +0 | +1 | +2 | -0.0202 | 14.8 |
| EURNZD | +3 | +1 | +0 | +1 | -2 | -0.0084 | 24.9 |
| EURUSD | -3 | -2 | -1 | -3 | +0 | -0.0623 | 34.6 |
| FTSE100 | -1 | +2 | +1 | +3 | +4 | 0.3802 | 16.9 |
| GBPAUD | +3 | -1 | +0 | -1 | -4 | 0.0091 | 28.7 |
| GBPCAD | +3 | +2 | +1 | +3 | +0 | 0.061 | 27.7 |
| GBPCHF | +3 | +2 | +1 | +3 | +0 | 0.0467 | 25.1 |
| GBPJPY | +0 | +2 | +0 | +2 | +2 | 0.0145 | 12.8 |
| GBPNZD | +2 | +1 | +0 | +1 | -1 | 0.0239 | 21.9 |
| GBPUSD | -3 | -1 | +0 | -1 | +2 | -0.0279 | 23.7 |
| GOLD | -3 | -2 | -1 | -3 | +0 | -0.0968 | 39.4 |
| NASDAQ | -1 | +2 | +1 | +3 | +4 | 0.1214 | 16.7 |
| NIKKEI | +2 | +2 | +1 | +3 | +1 | 0.1074 | 21.7 |
| NZDCAD | -1 | +2 | +0 | +2 | +3 | 0.0234 | 12.9 |
| NZDCHF | -3 | -1 | +0 | -1 | +2 | 0.0094 | 24.5 |
| NZDJPY | -3 | +1 | +0 | +1 | +4 | -0.0069 | 25.5 |
| NZDUSD | -3 | -1 | -1 | -2 | +1 | -0.0353 | 33.5 |
| SILVER | -3 | -1 | -1 | -2 | +1 | -0.0677 | 38.7 |
| SP500 | -1 | +2 | +1 | +3 | +4 | 0.119 | 19.0 |
| USDCAD | +3 | +2 | +1 | +3 | +0 | 0.1079 | 55.5 |
| USDCHF | +3 | +2 | +1 | +3 | +0 | 0.0553 | 33.8 |
| USDJPY | +3 | +2 | +1 | +3 | +0 | 0.0538 | 26.2 |

_*ADX display-only. 36 instruments; 28 changed cell._

## FX sign changes (v1 → v2)

| pair | v1 | v2 | regime | momentum | explanation |
|---|---|---|---|---|---|
| AUDCAD | -1 | +2 | +1 | +1 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+1 |
| AUDCHF | -3 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |
| AUDJPY | -3 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |
| AUDUSD | -3 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |
| CHFJPY | -2 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |
| EURAUD | +3 | -2 | -1 | -1 | v1 saw SMA10/20/50 momentum; v2 regime=-1 (SMA50/200 structure) dominates, momentum=-1 |
| EURJPY | -1 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |
| GBPAUD | +3 | -1 | -1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=-1 (SMA50/200 structure) dominates, momentum=+0 |
| NASDAQ | -1 | +3 | +2 | +1 | v1 saw SMA10/20/50 momentum; v2 regime=+2 (SMA50/200 structure) dominates, momentum=+1 |
| NZDCAD | -1 | +2 | +2 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+2 (SMA50/200 structure) dominates, momentum=+0 |
| NZDJPY | -3 | +1 | +1 | +0 | v1 saw SMA10/20/50 momentum; v2 regime=+1 (SMA50/200 structure) dominates, momentum=+0 |

## Composite bias/score impact (v1 trend → v2 trend)

| instrument | score v1 | bias v1 | score v2 | bias v2 |
|---|---|---|---|---|
| AUDCAD | -2.69 | Bearish | -1.03 | Neutral **← bias flip** |
| AUDCHF | -0.21 | Neutral | +2.29 | Bullish **← bias flip** |
| AUDJPY | -2.82 | Bearish | -0.60 | Neutral **← bias flip** |
| AUDNZD | -2.45 | Bearish | -1.82 | Bearish |
| AUDUSD | -2.59 | Bearish | -0.24 | Neutral **← bias flip** |
| CADCHF | +3.28 | Very Bullish | +2.72 | Bullish **← bias flip** |
| CADJPY | -0.50 | Neutral | +1.00 | Neutral |
| CHFJPY | -3.96 | Very Bearish | -2.29 | Bearish **← bias flip** |
| DAX | -2.33 | Bearish | -1.33 | Neutral **← bias flip** |
| EURAUD | +2.41 | Bullish | -0.37 | Neutral **← bias flip** |
| EURCAD | -0.42 | Neutral | -0.92 | Neutral |
| EURCHF | +2.43 | Bullish | +1.88 | Bullish |
| EURGBP | -0.54 | Neutral | -1.04 | Neutral |
| EURJPY | -0.92 | Neutral | +0.08 | Neutral |
| EURNZD | -0.08 | Neutral | -1.19 | Neutral |
| FTSE100 | -1.33 | Neutral | +0.67 | Neutral |
| GBPAUD | +1.96 | Bullish | -0.27 | Neutral **← bias flip** |
| GBPJPY | -0.88 | Neutral | +0.12 | Neutral |
| GBPNZD | -1.09 | Neutral | -1.64 | Bearish **← bias flip** |
| GBPUSD | -2.13 | Bearish | -1.07 | Neutral **← bias flip** |
| NASDAQ | -1.00 | Neutral | +1.00 | Neutral |
| NIKKEI | +0.83 | Neutral | +1.33 | Neutral |
| NZDCAD | -0.21 | Neutral | +1.46 | Bullish **← bias flip** |
| NZDCHF | +2.24 | Bullish | +3.49 | Very Bullish **← bias flip** |
| NZDJPY | -0.33 | Neutral | +1.89 | Bullish **← bias flip** |
| NZDUSD | -0.12 | Neutral | +0.47 | Neutral |
| SILVER | -2.78 | Bearish | -2.22 | Bearish |
| SP500 | -1.00 | Neutral | +1.00 | Neutral |