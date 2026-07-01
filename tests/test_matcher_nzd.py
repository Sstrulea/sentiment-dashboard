"""Anti-regression: NZ publishes growth/retail under non-standard event names;
the matcher aliases must map them to the standard indicator keys."""
from __future__ import annotations

from src.economic_fetch import CompiledMatcher, _load_indicators_cfg


def _matcher() -> CompiledMatcher:
    return CompiledMatcher(_load_indicators_cfg().get("matcher", {}))


def test_nzd_businessnz_and_electronic_card_aliases_match():
    m = _matcher()
    assert m.match("New Zealand", "BusinessNZ Manufacturing Index") == "manufacturing_pmi"
    assert m.match("New Zealand", "BusinessNZ Services Index") == "services_pmi"
    assert m.match("New Zealand", "Electronic Card Retail Sales m/m") == "retail_sales"


def test_nzd_aliases_do_not_leak_to_other_currencies():
    # The aliases are scoped to New Zealand; a same-named event under another
    # country must not accidentally match (country-keyed matcher).
    m = _matcher()
    assert m.match("Australia", "BusinessNZ Manufacturing Index") is None
    # Existing NZ standard patterns still work (no regression).
    assert m.match("New Zealand", "GDP q/q") == "gdp_qoq"
    assert m.match("New Zealand", "Retail Sales q/q") == "retail_sales"
