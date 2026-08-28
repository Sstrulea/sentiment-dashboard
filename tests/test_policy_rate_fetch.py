"""Tests for src.policy_rate_fetch — anti-degradation, merge, determinism.
No network: `fetch_fn` is injected as a plain callable returning a fixed
dict, never the real src.policy_rate_sources.fetch_policy_rates.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from src.policy_rate_fetch import merge_rates, render_yaml, update_policy_rates_yaml
from src.policy_rate_sources import CURRENCY_ORDER, PolicyRateObservation

EXISTING_YAML = """\
meta:
  stale_after_days: 45

rates:
  USD: {rate_pct: 3.625, effective: 2025-12-11, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  EUR: {rate_pct: 2.25, effective: 2026-06-17, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  GBP: {rate_pct: 3.75, effective: 2025-12-18, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  JPY: {rate_pct: 1.00, effective: 2026-06-17, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  AUD: {rate_pct: 4.35, effective: 2026-08-12, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  NZD: {rate_pct: 2.50, effective: 2026-07-08, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  CAD: {rate_pct: 2.25, effective: 2025-10-30, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
  CHF: {rate_pct: 0.00, effective: 2025-06-20, verified: 2026-08-20, source_name: "old", source_url: "https://old.example"}
"""


def _obs(currency, ref_area, rate_pct, verified, effective=None, source_ref="Some Bank"):
    return PolicyRateObservation(currency=currency, ref_area=ref_area, rate_pct=rate_pct,
                                 verified=verified, effective=effective, bis_source_ref=source_ref)


def _full_fetch(verified=date(2026, 8, 27)) -> dict:
    """A complete 8/8 fetch result, values matching EXISTING_YAML exactly
    (so tests that shouldn't change anything can assert zero diff)."""
    data = {
        "USD": ("US", 3.625, date(2025, 12, 11)),
        "EUR": ("XM", 2.25, date(2026, 6, 17)),
        "GBP": ("GB", 3.75, date(2025, 12, 18)),
        "JPY": ("JP", 1.00, date(2026, 6, 17)),
        "AUD": ("AU", 4.35, date(2026, 5, 6)),   # BIS's real effective date, not the stale 08-12
        "NZD": ("NZ", 2.50, date(2026, 7, 9)),
        "CAD": ("CA", 2.25, date(2025, 10, 30)),
        "CHF": ("CH", 0.00, date(2025, 6, 20)),
    }
    return {ccy: _obs(ccy, ra, rate, verified, effective=eff)
            for ccy, (ra, rate, eff) in data.items()}


@pytest.fixture()
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "policy_rates.yaml"
    p.write_text(EXISTING_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# merge_rates — per-currency preserve-if-unresolved
# ---------------------------------------------------------------------------

def test_individual_unresolved_currency_keeps_existing_entry():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    fetched = _full_fetch()
    del fetched["AUD"]   # AUD unresolved this run

    merged = merge_rates(existing, fetched)

    assert merged["AUD"] == existing["AUD"]   # untouched, not deleted, not nulled
    assert merged["USD"]["rate_pct"] == 3.625  # the resolved ones still update normally


def test_merge_never_drops_a_currency():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    merged = merge_rates(existing, {})   # nothing resolved at all
    assert set(merged) == set(CURRENCY_ORDER)
    for ccy in CURRENCY_ORDER:
        assert merged[ccy] == existing[ccy]


def test_effective_none_from_bis_preserves_existing_effective():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    fetched = {"USD": _obs("USD", "US", 3.625, date(2026, 8, 27), effective=None)}
    merged = merge_rates(existing, fetched)
    assert merged["USD"]["effective"] == existing["USD"]["effective"]
    assert merged["USD"]["verified"] == date(2026, 8, 27)


# ---------------------------------------------------------------------------
# update_policy_rates_yaml — anti-degradation
# ---------------------------------------------------------------------------

def test_fetch_failed_leaves_yaml_untouched(yaml_path: Path):
    before = yaml_path.read_text(encoding="utf-8")
    changed = update_policy_rates_yaml(yaml_path=yaml_path, fetch_fn=lambda: {})
    assert changed is False
    assert yaml_path.read_text(encoding="utf-8") == before


def test_seven_of_eight_resolved_leaves_yaml_untouched(yaml_path: Path):
    before = yaml_path.read_text(encoding="utf-8")
    fetched = _full_fetch()
    del fetched["CHF"]   # only 7 resolved
    assert len(fetched) == 7

    changed = update_policy_rates_yaml(yaml_path=yaml_path, fetch_fn=lambda: fetched)

    assert changed is False
    assert yaml_path.read_text(encoding="utf-8") == before


def test_eight_of_eight_resolved_does_write(yaml_path: Path):
    fetched = _full_fetch()
    assert len(fetched) == 8
    changed = update_policy_rates_yaml(yaml_path=yaml_path, fetch_fn=lambda: fetched)
    assert changed is True

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert written["rates"]["AUD"]["effective"] == date(2026, 5, 6)   # the AUD fix
    assert written["rates"]["AUD"]["rate_pct"] == 4.35                # value itself unchanged
    assert written["meta"]["stale_after_days"] == 45                  # preserved untouched


def test_double_run_same_data_produces_zero_diff(yaml_path: Path):
    fetched = _full_fetch()

    changed1 = update_policy_rates_yaml(yaml_path=yaml_path, fetch_fn=lambda: fetched)
    text_after_first = yaml_path.read_text(encoding="utf-8")

    changed2 = update_policy_rates_yaml(yaml_path=yaml_path, fetch_fn=lambda: fetched)
    text_after_second = yaml_path.read_text(encoding="utf-8")

    assert changed1 is True     # first run: AUD's effective date actually corrects
    assert changed2 is False    # second run: identical fetch, nothing to write
    assert text_after_first == text_after_second


# ---------------------------------------------------------------------------
# render_yaml — determinism + format
# ---------------------------------------------------------------------------

def test_render_yaml_is_deterministic_across_calls():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    a = render_yaml(45, existing)
    b = render_yaml(45, existing)
    assert a == b


def test_render_yaml_currency_order_is_fixed_regardless_of_dict_order():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    shuffled = {k: existing[k] for k in reversed(list(existing))}
    assert render_yaml(45, existing) == render_yaml(45, shuffled)


def test_render_yaml_round_trips_through_yaml_safe_load():
    existing = yaml.safe_load(EXISTING_YAML)["rates"]
    text = render_yaml(45, existing)
    parsed = yaml.safe_load(text)
    assert parsed["meta"]["stale_after_days"] == 45
    assert parsed["rates"]["CHF"]["rate_pct"] == 0.0
    assert isinstance(parsed["rates"]["CHF"]["effective"], date)
