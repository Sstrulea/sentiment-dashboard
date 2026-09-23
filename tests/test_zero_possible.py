"""Audit Z1 — config/ff_zero_possible.yaml declares every mapped FF series, no default."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.econ_calendar_ff import canonical_id, load_aliases
from src.ff_scoring import CCY2COUNTRY, build_matcher, load_zero_possible

ROOT = Path(__file__).resolve().parents[1]
LEVEL_KEYS = {"manufacturing_pmi", "services_pmi", "unemployment_rate", "jobless_claims", "jolts"}


def _mapped_series() -> dict[str, str]:
    m, out = build_matcher(), {}
    for ccy, mp in load_aliases().items():
        for _raw, canon in (mp or {}).items():
            key = m.match(CCY2COUNTRY.get(ccy, ""), canon)
            if key:
                out[canonical_id(ccy, canon)] = key
    pq = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet",
                         columns=["canonical_id", "currency", "name_canonical"])
    for r in pq.drop_duplicates("canonical_id").itertuples(index=False):
        key = m.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key:
            out.setdefault(r.canonical_id, key)
    return out


def test_every_mapped_series_declares_zero_possible():
    zp = load_zero_possible()
    missing = sorted(set(_mapped_series()) - set(zp))
    assert not missing, f"declare zero_possible for: {missing}"


def test_levels_and_indices_are_false_changes_true():
    zp, series = load_zero_possible(), _mapped_series()
    wrong = {cid: (key, zp[cid]) for cid, key in series.items()
             if zp.get(cid) is not (key not in LEVEL_KEYS)}
    assert not wrong, wrong


def test_values_are_booleans():
    import yaml
    raw = yaml.safe_load((ROOT / "config" / "ff_zero_possible.yaml").read_text())["zero_possible"]
    assert all(isinstance(v, bool) for v in raw.values())
