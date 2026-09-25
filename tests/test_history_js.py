"""FAZA 1D 4.3 — wires the plain-Node history.js test into the pytest suite.

Not Playwright: no browser, no jsdom, no new dependency — a stdlib-only
Node script (tests/history_js/test_resolve_entry.cjs) exercising resolveEntry(),
the seam behind P1.4's points_ref dedup. If `node` isn't on PATH (a Python-only
CI image), skip rather than fail — this must never block the rest of the
suite on an environment that simply doesn't have Node installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS_TEST = ROOT / "tests" / "history_js" / "test_resolve_entry.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_history_js_resolve_entry():
    result = subprocess.run(["node", str(JS_TEST)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_history_js_tooltip_lines():
    js_test = ROOT / "tests" / "history_js" / "test_tooltip_lines.cjs"
    result = subprocess.run(["node", str(js_test)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
