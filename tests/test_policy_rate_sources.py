"""Tests for src.policy_rate_sources — pure parser + aggregation, no network."""
from __future__ import annotations

from datetime import date

import pytest

from src.policy_rate_sources import build_observation, parse_obs_value


# ---------------------------------------------------------------------------
# parse_obs_value — allowlist parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["NaN", "nan", "NAN", "nAn", "+nan", "-NaN"])
def test_nan_in_any_capitalization_is_rejected_not_converted(raw):
    # float(raw) would SUCCEED here and return math.nan — the whole point of
    # this function is to catch that via math.isfinite() and reject it,
    # never let a nan float reach the caller.
    assert parse_obs_value(raw) is None


@pytest.mark.parametrize("raw", ["0", "0.0", "-0.0", "0.00"])
def test_zero_is_accepted_as_a_real_value(raw):
    # Regression guard: CHF's SNB policy rate is legitimately 0.00%. No
    # truthiness check anywhere in the parser — `0.0` must come back as
    # `0.0`, not be treated as falsy/missing.
    v = parse_obs_value(raw)
    assert v is not None
    assert v == 0.0


@pytest.mark.parametrize("raw", ["", " ", ".", "-", "null", "None", "1,25", "N/A", "--", "3..5"])
def test_unseen_forms_are_rejected_cleanly_no_exception(raw):
    # No blocklist of specific strings — parse_obs_value must reject
    # anything that isn't a clean finite float, including forms never
    # observed live by src.policy_rate_probe.
    assert parse_obs_value(raw) is None


def test_none_is_rejected():
    assert parse_obs_value(None) is None


@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "+inf"])
def test_infinity_forms_are_rejected(raw):
    # float("inf") also "succeeds" in Python — same trap as NaN.
    assert parse_obs_value(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("3.625", 3.625), ("4.35", 4.35), ("2.25", 2.25), ("-0.10", -0.10),
])
def test_clean_values_parse_exactly(raw, expected):
    assert parse_obs_value(raw) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# build_observation — effective (last CHANGE) vs verified (last OBSERVATION)
# ---------------------------------------------------------------------------

def test_empty_observation_list_returns_none():
    assert build_observation("USD", "US", []) is None


def test_effective_differs_from_verified_on_constant_tail():
    # A synthetic series: two real changes early on, then held constant for
    # a long tail right up to "today". verified must be the LATEST
    # observation's date; effective must be the date of the LAST value
    # CHANGE — nowhere near verified.
    obs = [
        (date(2026, 1, 5), 4.00),
        (date(2026, 1, 6), 4.00),
        (date(2026, 2, 10), 3.75),   # <- the last real change
        (date(2026, 2, 11), 3.75),
        (date(2026, 5, 1), 3.75),
        (date(2026, 8, 1), 3.75),
        (date(2026, 8, 25), 3.75),   # <- the latest observation
    ]
    result = build_observation("GBP", "GB", obs)
    assert result is not None
    assert result.verified == date(2026, 8, 25)
    assert result.effective == date(2026, 2, 10)
    assert result.verified != result.effective
    assert result.rate_pct == 3.75


def test_effective_is_none_when_no_change_anywhere_in_window():
    # The value is already constant at the START of the fetch window — BIS
    # genuinely doesn't tell us when it became effective (could be before
    # the window), so effective must come back None, not a guessed date.
    obs = [
        (date(2026, 6, 1), 3.625),
        (date(2026, 7, 1), 3.625),
        (date(2026, 8, 25), 3.625),
    ]
    result = build_observation("USD", "US", obs)
    assert result is not None
    assert result.verified == date(2026, 8, 25)
    assert result.effective is None


def test_unordered_input_is_sorted_before_aggregation():
    obs = [
        (date(2026, 8, 25), 0.0),
        (date(2026, 6, 20), 0.0),
        (date(2026, 3, 21), 0.25),
        (date(2026, 6, 19), 0.25),
    ]
    result = build_observation("CHF", "CH", obs)
    assert result is not None
    assert result.verified == date(2026, 8, 25)
    assert result.effective == date(2026, 6, 20)
    assert result.rate_pct == 0.0


def test_source_ref_is_carried_through():
    obs = [(date(2026, 8, 25), 2.25)]
    result = build_observation("EUR", "XM", obs, source_ref="European Central Bank")
    assert result.bis_source_ref == "European Central Bank"


def test_single_observation_has_no_effective():
    obs = [(date(2026, 8, 25), 4.35)]
    result = build_observation("AUD", "AU", obs)
    assert result.verified == date(2026, 8, 25)
    assert result.effective is None
    assert result.rate_pct == 4.35
