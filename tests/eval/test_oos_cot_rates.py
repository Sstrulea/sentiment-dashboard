"""OOS COT/rates check (eval/oos-cot-rates) — tests.

1. No look-ahead for L, F, C and R: the score at 3 random Fridays of each window is
   identical computed with all the data and with the data cut at t.
2. On the weeks in common with data/history.parquet (from 2023-03), the raw rows
   are identical, and L, F, C from the downloaded history equal those computed from
   history.parquet wherever its trailing windows are complete.

Needs the local caches (~/.cache/macro-tournament/cftc, .../yields); skipped otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "measure" / "oos"))

import signals as S  # noqa: E402

HAVE = all((S.CFTC_DIR / f"cot_full_{c}.parquet").exists() for c in S.CODES.values()) and \
    (Path.home() / ".cache" / "macro-tournament" / "yields" / "eur_2y_ecb.csv").exists()
pytestmark = pytest.mark.skipif(not HAVE, reason="OOS caches not built")


def _weeks(a, b, seed):
    ws = S.fridays(a, b)
    rng = np.random.default_rng(seed)
    return [ws[i] for i in sorted(rng.choice(len(ws), size=3, replace=False))]


def test_lfc_no_lookahead():
    raw = S.cftc_raw()
    full = S.enrich(raw)
    for t in _weeks(S.H1_START, S.H1_END, 20261016):
        cut = S.enrich(raw[raw[S.DATE] <= t])          # compute_metrics on the cut rows only
        assert S.lfc_at(full, t) == S.lfc_at(cut, t), str(t)


def test_r_no_lookahead():
    rates = S.rates_ext()
    real = pd.read_parquet(ROOT / "data" / "real_yields.parquet")
    sig = S.sigma_y()
    for t in _weeks(S.H2_START, S.H2_END, 7):
        d = pd.Timestamp(t.date())
        a = S.r_at(rates, real, t, sig)
        b = S.r_at(rates[rates["date"] <= d], real[real["date"] <= d], t, sig)
        assert a == b, str(t)
        assert any(v is not None for v in a.values())


def test_matches_history_parquet():
    r = S.hist_concordance()
    assert r["common_reports"] > 0 and r["raw_mismatches"] == 0 and r["hist_rows_not_downloaded"] == 0
    assert r["comparable_cells"] > 0 and r["mismatches"] == 0
