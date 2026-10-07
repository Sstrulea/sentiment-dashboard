"""/economic cross-links to the Central Banks module, checked on the real payload by a plain-Node script (no browser, no jsdom, no dependency; skipped when
`node` is not on PATH, like tests/test_history_js.py). 28 pairs + DXY: the pair whose RATE EXP (2Y) is n/a keeps its link on the dash."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "economic_js" / "test_cross_links.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_economic_page_links_every_pair_and_dxy_to_the_central_banks_pages():
    r = subprocess.run(["node", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "28 pair links + DXY" in r.stdout


def test_the_served_copy_of_the_script_is_in_step():
    assert (ROOT / "public" / "economic-chart.js").read_text() == (ROOT / "static" / "economic-chart.js").read_text()


PHONE = ROOT / "tests" / "economic_js" / "test_phone_bars.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_phone_bars_add_up_to_the_score():
    """Faza 11B: the phone sheet's bars add up to inst.score on all 29 rows; cross-asset bars only when exact."""
    r = subprocess.run(["node", str(PHONE)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "29 rows exact" in r.stdout


ACTUALS = ROOT / "tests" / "economic_js" / "test_actuals_badge.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_actuals_badge_reads_the_data_not_the_source():
    r = subprocess.run(["node", str(ACTUALS)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "3 states ok" in r.stdout


SIGMA = ROOT / "tests" / "economic_js" / "test_sigma_headers.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_drilldown_headers_show_sigma_and_strength_has_no_rates_group():
    r = subprocess.run(["node", str(SIGMA)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "sigma headers ok" in r.stdout


UTC_ASOF = ROOT / "tests" / "economic_js" / "test_utc_asof.cjs"
Z_COLUMN = ROOT / "tests" / "economic_js" / "test_z_column.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_as_of_is_read_as_utc_at_utc_plus_3():
    import os
    env = dict(os.environ, TZ="Europe/Bucharest")
    r = subprocess.run(["node", str(UTC_ASOF)], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "utc as_of ok" in r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_tables_show_z_and_sort_by_it():
    r = subprocess.run(["node", str(Z_COLUMN)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "z column ok" in r.stdout


CA_NO_COT = ROOT / "tests" / "economic_js" / "test_ca_no_cot.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_crossasset_table_has_no_cot_column_in_v3():
    r = subprocess.run(["node", str(CA_NO_COT)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "cross-asset no cot ok" in r.stdout
