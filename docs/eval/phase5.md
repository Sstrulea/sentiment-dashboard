# Phase 5 — the single test (pre-registration d14200de, attempt #5)

## Gated (1 month, 15 currencies, 2004-01 ->)

| test | IC | t | N | 2004-09 | 2010-19 | 2020-26 | developed IC (t) | 3m IC (mean) | net L/S bp | Sharpe | pairs hit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| composite | 0.076 | 4.09 | 274 | 0.150 | 0.067 | 0.026 | 0.063 (2.83) | 0.125 | 24.5 | 0.41 | 0.527 |
| labour | -0.009 | -0.52 | 274 | 0.025 | -0.002 | -0.049 | -0.002 (-0.10) | -0.005 | -9.8 | -0.18 | 0.497 |

Gates:

- composite @1m (15): PASS
- labour @1m (15): fail (fails: IC>0 and t>=2.5, IC>0 in 2004-09, 2010-19, 2020-26, IC>0 on the 10 developed, IC>0 at 3 months, net L/S > 0)

Decision: candidate = composite

## Descriptive

| series | IC 1m | t | N | 3m IC | net L/S bp | Sharpe |
|---|---|---|---|---|---|---|
| z_excess_emp | 0.001 | 0.05 | 273 | -0.001 | -0.6 | -0.01 |
| z_unemp_trend | -0.002 | -0.09 | 273 | 0.006 | -7.1 | -0.13 |
| z_labour | -0.009 | -0.52 | 274 | -0.005 | -9.8 | -0.18 |
| z_confidence | 0.015 | 0.85 | 274 | 0.020 | 19.6 | 0.36 |
| z_external | 0.025 | 1.37 | 274 | 0.058 | 14.2 | 0.26 |
| z_tot | 0.021 | 1.10 | 274 | 0.029 | -1.6 | -0.03 |
| z_carry | 0.114 | 5.01 | 274 | 0.157 | 38.4 | 0.46 |
| carry_with_vol_rule | 0.110 | 4.39 | 221 | 0.154 | 42.6 | 0.59 |
| composite_carry_always | 0.087 | 4.61 | 274 | 0.139 | 17.4 | 0.27 |
| composite_without_carry | 0.037 | 2.05 | 274 | 0.077 | 25.4 | 0.45 |
| composite 2000-2003 (descriptive window) | 0.116 | 2.38 | 48 | 0.165 | 57.0 | 0.88 |
| composite 2000s | 0.136 | 4.72 | 120 | 0.184 | 65.1 | 1.00 |
| composite 2010s | 0.067 | 2.43 | 120 | 0.112 | 17.1 | 0.29 |
| composite 2020s | 0.026 | 0.74 | 82 | 0.078 | -5.2 | -0.10 |
| composite developed | 0.063 | 2.83 | 273 | 0.099 | 21.5 | 0.40 |
| composite emerging | 0.077 | 2.41 | 270 | 0.131 | — | — |
| composite the 8 | 0.057 | 2.25 | 274 | 0.100 | 24.2 | 0.43 |
| baseline carry (rate level) | 0.114 | 5.01 | 274 | 0.157 | 38.4 | 0.46 |
| baseline random | 0.021 | 1.25 | 274 | 0.012 | -1.4 | -0.03 |
| baseline MS-lite (8, COT + momentum) | -0.013 | -0.49 | 273 | -0.029 | 0.7 | 0.01 |
| composite on the 8 (same window as MS-lite) | 0.057 | 2.25 | 274 | 0.100 | 24.2 | 0.43 |

```
{
 "composite per currency (time series)": {
  "USD": {
   "ts_spearman": 0.066241,
   "N": 274,
   "hit_sign": 0.492701
  },
  "EUR": {
   "ts_spearman": 0.044385,
   "N": 274,
   "hit_sign": 0.521898
  },
  "GBP": {
   "ts_spearman": 0.076755,
   "N": 266,
   "hit_sign": 0.545113
  },
  "JPY": {
   "ts_spearman": -0.02457,
   "N": 274,
   "hit_sign": 0.562044
  },
  "CAD": {
   "ts_spearman": 0.014959,
   "N": 272,
   "hit_sign": 0.522059
  },
  "AUD": {
   "ts_spearman": 0.091964,
   "N": 274,
   "hit_sign": 0.536496
  },
  "NZD": {
   "ts_spearman": 0.083541,
   "N": 274,
   "hit_sign": 0.532847
  },
  "CHF": {
   "ts_spearman": -0.040801,
   "N": 274,
   "hit_sign": 0.547445
  }
 },
 "composite IC by vol": {
  "high": {
   "IC": 0.023195,
   "t": 0.589296,
   "N": 53
  },
  "low": {
   "IC": 0.089224,
   "t": 4.226594,
   "N": 221
  }
 },
 "carry IC by vol": {
  "high": {
   "IC": 0.127486,
   "t": 2.410468,
   "N": 53
  },
  "low": {
   "IC": 0.110391,
   "t": 4.385163,
   "N": 221
  }
 },
 "weekly": {
  "2": {
   "IC": 0.034006,
   "t": 2.059825,
   "N": 306,
   "window": [
    "2015-01-02",
    "2026-10-30"
   ]
  },
  "4": {
   "IC": 0.041352,
   "t": 1.717281,
   "N": 153,
   "window": [
    "2015-01-02",
    "2026-10-30"
   ]
  }
 }
}
```

## Stage E (8 currencies, weekly, H.10)

| h | score | IC | t | N |
|---|---|---|---|---|
| 2 | candidate_live | 0.033 | 0.85 | 80 |
| 2 | composite_no_news | 0.005 | 0.12 | 80 |
| 2 | labour | -0.018 | -0.43 | 80 |
| 2 | v3_A1 | 0.124 | 2.83 | 80 |
| 2 | carry | 0.103 | 2.31 | 80 |
| 2 | MS | -0.101 | -2.60 | 80 |
| 4 | candidate_live | 0.014 | 0.27 | 40 |
| 4 | composite_no_news | -0.054 | -0.93 | 40 |
| 4 | labour | 0.030 | 0.48 | 40 |
| 4 | v3_A1 | 0.117 | 1.99 | 40 |
| 4 | carry | 0.064 | 1.18 | 40 |
| 4 | MS | -0.087 | -1.59 | 40 |

Rule: {"candidate_becomes_direction": false}

