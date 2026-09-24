"""Audit 7A — one flash/final rule, in one place."""
from pathlib import Path

import pandas as pd

from src.economic_compute import _dedup_flash_final
from src.ff_scoring import build_matcher, scoring_view, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"


def test_telemetry_variants_are_tagged_and_never_scored():
    sc = to_scoring_frame(pd.read_parquet(FROZEN_FF), build_matcher())
    tel = sc[sc["publication"] == "telemetry"]
    assert {("EUR", "Final Manufacturing PMI"), ("EUR", "Final Services PMI"),
            ("JPY", "Final Manufacturing PMI")} <= set(zip(tel["currency"], tel["name_raw"]))
    view = scoring_view(sc)
    assert (view["publication"] != "telemetry").all()
    # GBP: the config scores the Final PMI
    gbp = view[(view.currency == "GBP") & (view.indicator_key == "manufacturing_pmi")]
    assert (gbp["name_raw"] == "Final Manufacturing PMI").all() and len(gbp)


def test_dedup_keeps_the_configured_publication_not_the_final():
    df = pd.DataFrame([
        {"release_dt": pd.Timestamp("2026-09-23 08:00"), "actual": 51.0, "consensus": 51.4,
         "name_raw": "Flash Services PMI", "publication": "scored"},
        {"release_dt": pd.Timestamp("2026-10-03 08:00"), "actual": 51.2, "consensus": 51.0,
         "name_raw": "Final Services PMI", "publication": "telemetry"},
    ])
    out = _dedup_flash_final(df, 18)
    assert out["name_raw"].tolist() == ["Flash Services PMI"]
