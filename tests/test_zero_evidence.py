"""Audit 5A — a 0.0 is real only with independent evidence (frozen snapshot 4ace910)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.ff_scoring import build_matcher, load_zero_possible, scoring_view, to_scoring_frame, zero_verdicts

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"


@pytest.fixture(scope="module")
def frozen():
    return pd.read_parquet(FROZEN_FF)


@pytest.fixture(scope="module")
def verdicts(frozen):
    return zero_verdicts(frozen, load_zero_possible(), build_matcher())


@pytest.mark.parametrize("cid,dt", [
    ("usd_nonfarm_payrolls", "2025-10-02 21:00"),          # shutdown-delayed report (out 2025-11-20)
    ("usd_average_hourly_earnings", "2025-10-02 21:00"),
    ("aud_private_capital_expenditure", "2026-02-26 00:30"),
    ("chf_retail_sales", "2025-07-01 06:30"),
])
def test_zero_without_independent_evidence_is_a_placeholder(verdicts, cid, dt):
    assert verdicts[(cid, pd.Timestamp(dt))][0] is True


def test_snb_zero_policy_rate_is_real_from_decisions(verdicts):
    """SNB 0.00% (2025-06-19): the next meeting's rate_before in decisions.parquet."""
    assert verdicts[("chf_snb_interest_rate_decision", pd.Timestamp("2025-06-19 07:30"))][0] is False


def test_shutdown_nfp_and_ahe_are_not_scored(frozen):
    sc = scoring_view(to_scoring_frame(frozen, build_matcher()))
    day = pd.to_datetime(sc["release_dt"]).dt.date == pd.Timestamp("2025-10-02").date()
    usd = sc[(sc.currency == "USD") & day & sc.indicator_key.isin(["employment_change", "wage_growth"])]
    assert len(usd) and usd["actual"].isna().all()
