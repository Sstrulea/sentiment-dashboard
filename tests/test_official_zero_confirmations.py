"""Every official zero confirmation must land: an override entered from an
official source (entered_by "backfill-official") that confirms a real 0.0 first
print (ZERO_CONFIRM) has to produce a manual row in apply_overrides on the real
parquet — an orphan (its target row gone or re-timed) would silently turn the
confirmed zero back into a placeholder."""
from __future__ import annotations

import pandas as pd

from src.ff_refresh import FF_PARQUET
from src.history_compute import MANUAL_ACTUALS_OVERRIDES
from src.manual_actuals import apply_overrides, load_overrides

NOW = pd.Timestamp("2026-09-25 09:30")


def test_every_official_zero_confirmation_produces_a_manual_row():
    overrides = load_overrides(MANUAL_ACTUALS_OVERRIDES)
    official = [e for e in overrides
                if e.get("entered_by") == "backfill-official" and e.get("state_resolved") == "ZERO_CONFIRM"]
    assert official, "no official ZERO_CONFIRM override found"
    ff = pd.read_parquet(FF_PARQUET)
    manual, _ = apply_overrides(ff, official, now_utc=NOW)
    applied = set(zip(manual["currency"], manual["indicator_key"], pd.to_datetime(manual["release_dt"])))
    missing = [(e["canonical_id"], e["datetime_utc"]) for e in official
               if (e["currency"], e["indicator_key"], pd.Timestamp(e["datetime_utc"])) not in applied]
    assert not missing, f"official ZERO_CONFIRM override(s) without a manual row: {missing}"
