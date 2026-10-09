"""BKBM - OCR spread for the NZD estimate (pure): the 3M NZ interbank rate (OECD monthly, via FRED IR3TIB01NZM156N) minus the
monthly mean OCR (BIS WS_CBPOL, daily), median over the months in which the OCR did not change in that month nor in the next
three - so the 3M rate of the month prices no OCR move. Used to bring the ASX 90-day bank bill futures (a BKBM level) to policy
terms; the value is an estimate, not a daily observation (scripts/cb_nzd_bkbm_spread.py writes config/cb_nzd_spread.yaml)."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

QUIET_AHEAD = 3                    # months after the month that must also have no OCR change


def _ym(d: date) -> tuple:
    return (d.year, d.month)


def _add(ym: tuple, k: int) -> tuple:
    y, m = divmod(ym[0] * 12 + ym[1] - 1 + k, 12)
    return (y, m + 1)


@dataclass
class NzdSpread:
    value_bp: Optional[float]
    n_months: int = 0
    window: Optional[tuple] = None                 # (first month, last month) of the comparison, 'YYYY-MM'
    iqr_bp: Optional[tuple] = None                 # (p25, p75)
    months: list = field(default_factory=list)     # [('YYYY-MM', diff bp)] kept
    reason: str = ""


def ocr_months(ocr_daily: list) -> tuple:
    """({(y, m): mean OCR over its observations}, {(y, m) in which the OCR changed}). Missing values (NaN) are skipped."""
    by: dict = {}
    changed: set = set()
    prev = None
    for d, v in sorted(ocr_daily):
        if v is None or math.isnan(v):
            continue
        by.setdefault(_ym(d), []).append(v)
        if prev is not None and abs(v - prev) > 1e-9:
            changed.add(_ym(d))
        prev = v
    return {k: sum(v) / len(v) for k, v in by.items()}, changed


def bkbm_ocr_spread(rate3m: list, ocr_daily: list, start: tuple = (2015, 1)) -> NzdSpread:
    """rate3m = [(date, %)] monthly (dated the 1st); ocr_daily = [(date, %)]. A month is kept when the OCR did not change in it
    nor in the QUIET_AHEAD months after it, and those months are inside the OCR data."""
    ocr_mean, changed = ocr_months(ocr_daily)
    if not ocr_mean or not rate3m:
        return NzdSpread(None, reason="no data")
    last_ocr = max(ocr_mean)
    if max(d for d, _ in ocr_daily).day < 28:                      # the last OCR month is incomplete: not a full month mean
        last_ocr = _add(last_ocr, -1)
    m3 = {_ym(d): v for d, v in rate3m if v is not None and not math.isnan(v)}
    common = sorted(k for k in m3 if k in ocr_mean and start <= k <= last_ocr)
    if not common:
        return NzdSpread(None, reason="no common month")
    kept = []
    for k in common:
        ahead = [_add(k, j) for j in range(QUIET_AHEAD + 1)]
        if ahead[-1] > last_ocr or any(a in changed for a in ahead):
            continue
        kept.append((f"{k[0]}-{k[1]:02d}", (m3[k] - ocr_mean[k]) * 100))
    window = (f"{common[0][0]}-{common[0][1]:02d}", f"{common[-1][0]}-{common[-1][1]:02d}")
    if not kept:
        return NzdSpread(None, 0, window, reason="no month without an OCR change around it")
    diffs = [x for _, x in kept]
    p25, p75 = (float(x) for x in np.percentile(diffs, [25, 75]))
    return NzdSpread(statistics.median(diffs), len(kept), window, (p25, p75), kept)
