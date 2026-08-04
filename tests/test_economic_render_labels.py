"""feat/aud-inflation-monthly-promotion — CHANGE 4 (cosmetic, independent of
the scoring change, included because it touches the same rendering path).

CATEGORY_LABEL_FALLBACK's "(display-only)" parenthetical duplicated
static/economic-chart.js's legHtml own "display-only" badge (derived from
data: absence of the key in card.categories) into "X (DISPLAY-ONLY)
DISPLAY-ONLY" in the drawer. Dropped the parenthetical, kept the badge.
"""
from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from src.economic_render import _build_meta, CATEGORY_LABEL_FALLBACK, CROSSASSET_TABLE_LAYOUT

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def indicators_cfg():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def instruments_cfg():
    with open(ROOT / "data" / "economic_instruments.yaml") as f:
        return yaml.safe_load(f) or {}


@pytest.mark.parametrize("key,expected", [
    ("rates", "Rates"),
    ("inflation_display", "Inflation"),
    ("growth_display", "Growth"),
])
def test_display_only_labels_no_longer_carry_the_parenthetical(key, expected):
    assert CATEGORY_LABEL_FALLBACK[key] == expected
    assert "display-only" not in CATEGORY_LABEL_FALLBACK[key].lower()


@pytest.mark.parametrize("key,expected", [
    ("rates", "Rates"),
    ("inflation_display", "Inflation"),
    ("growth_display", "Growth"),
])
def test_built_meta_categories_carry_the_fixed_label(indicators_cfg, instruments_cfg, key, expected):
    meta = _build_meta(indicators_cfg, instruments_cfg)
    assert meta["categories"][key]["label"] == expected


def test_scored_category_labels_are_untouched():
    # Sanity: this change only touches the three display-only labels.
    assert CATEGORY_LABEL_FALLBACK["growth"] == "Growth"
    assert CATEGORY_LABEL_FALLBACK["inflation"] == "Inflation"
    assert CATEGORY_LABEL_FALLBACK["labour"] == "Labour Market"
    assert CATEGORY_LABEL_FALLBACK["monetary"] == "Monetary Policy"


def test_crossasset_table_layout_rates_label_is_a_separate_unaffected_path():
    rates_group = next(g for g in CROSSASSET_TABLE_LAYOUT if g["key"] == "rates")
    assert rates_group["label"] == "Rates & Liquidity"
