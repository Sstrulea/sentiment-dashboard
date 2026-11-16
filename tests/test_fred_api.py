"""FRED API client (src/fred_api.py): the only FRED transport of the repo. Offline: fixtures cut from real FRED API
responses (tests/fixtures/fred_api/, 2026-11-14; no key inside)."""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import date
from pathlib import Path

import pytest
import requests

from src import fred_api as F

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "fred_api"
KEY = "abcdef0123456789abcdef0123456789"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(F.ENV_KEY, KEY)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)


class Resp:
    def __init__(self, status, payload=None, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


# ------------------------------------------------------------------ parser ---

def test_parser_on_real_responses_dot_is_missing_never_zero():
    sofr = F.parse_observations(json.loads((FIX / "obs_SOFR.json").read_text()))
    assert (date(2026, 9, 7), None) in sofr                                  # Labor Day: "."
    assert all(v is None or v > 0 for _, v in sofr) and not any(v == 0 for _, v in sofr)
    assert sofr[0] == (date(2026, 9, 1), 3.66)
    cpi = F.parse_observations(json.loads((FIX / "obs_CPIAUCSL.json").read_text()))
    assert all(d.day == 1 for d, _ in cpi) and all(v is None or isinstance(v, float) for _, v in cpi)   # native monthly frequency, index units
    assert [d for d, v in cpi if v is None]                                  # the real file has a "." month: missing, not 0


def test_fixtures_carry_no_key():
    for f in FIX.glob("*.json"):
        assert "api_key" not in f.read_text()


def test_observations_paging_and_params(monkeypatch):
    seen = []

    def get(url, params=None, headers=None, timeout=None):
        seen.append((url, dict(params)))
        if params["offset"] == 0:
            return Resp(200, {"count": 3, "observations": [{"date": "2026-01-01", "value": "1"}, {"date": "2026-01-02", "value": "."}]})
        return Resp(200, {"count": 3, "observations": [{"date": "2026-01-03", "value": "2.5"}]})
    monkeypatch.setattr(F.requests, "get", get)
    assert F.observations("X", date(2026, 1, 1)) == [(date(2026, 1, 1), 1.0), (date(2026, 1, 2), None), (date(2026, 1, 3), 2.5)]
    url, p = seen[0]
    assert url == "https://api.stlouisfed.org/fred/series/observations"
    assert p["series_id"] == "X" and p["file_type"] == "json" and p["observation_start"] == "2026-01-01" and p["api_key"] == KEY


# -------------------------------------------------------------- resilience ---

def test_retry_with_backoff_on_429_then_success(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(F.time, "sleep", lambda s: sleeps.append(s))

    def get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        return Resp(429, {"error_message": "Too Many Requests"}) if len(calls) < 3 else Resp(200, {"count": 1, "observations": [{"date": "2026-01-01", "value": "4"}]})
    monkeypatch.setattr(F.requests, "get", get)
    assert F.observations("X") == [(date(2026, 1, 1), 4.0)]
    assert len(calls) == 3 and F.BACKOFF_S in sleeps and F.BACKOFF_S * 2 in sleeps


def test_no_retry_on_400_and_message_redacted(monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        return Resp(400, {"error_message": f"Bad Request. api_key={KEY} invalid"})
    monkeypatch.setattr(F.requests, "get", get)
    with pytest.raises(F.FredApiError) as e:
        F.observations("X")
    assert len(calls) == 1 and KEY not in str(e.value) and "HTTP 400" in str(e.value)


def test_missing_key_is_a_clear_error_and_no_request_is_made(monkeypatch):
    monkeypatch.delenv(F.ENV_KEY)
    monkeypatch.setattr(F, "_load_dotenv", lambda: None)
    monkeypatch.setattr(F.requests, "get", lambda *a, **k: pytest.fail("no request without a key"))
    with pytest.raises(F.FredKeyMissing, match="FRED_API_KEY is not set"):
        F.observations("SOFR")


# --------------------------------------------------------------- redaction ---

def test_a_requests_exception_with_the_key_in_its_url_leaves_no_key_in_message_log_or_report(monkeypatch, caplog):
    def get(url, params=None, headers=None, timeout=None):
        raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /fred/series/observations?series_id=SOFR&api_key={KEY}&file_type=json")
    monkeypatch.setattr(F.requests, "get", get)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(F.FredApiError) as e:
        F.observations("SOFR")
    msg = str(e.value)
    assert KEY not in msg and "api_key=REDACTED" in msg and e.value.__cause__ is None
    # the adapters put the message into their reports / logs
    from src.cb_sources.official import FredSeries
    s = FredSeries(cfg={}, series={"fred:SOFR": {"currency": "USD", "key": "SOFR", "provider": "fred"}})
    res = s.fetch(since=date(2026, 9, 1))
    note = f"{s.last_status} {s.last_note} {getattr(res, 'note', '')} {getattr(res, 'failed', '')}"
    logging.getLogger("x").warning("fred failure: %s", note)
    assert KEY not in note and KEY not in caplog.text


def test_redact_handles_url_and_bare_key():
    assert F.redact(f"x?api_key={KEY}&y=1") == "x?api_key=REDACTED&y=1"
    assert KEY not in F.redact(f"key is {KEY}")


def test_provenance_is_the_series_page():
    assert F.provenance("SOFR") == "https://fred.stlouisfed.org/series/SOFR (via FRED API)"


# ------------------------------------------------------------------ guards ---

def test_no_fred_csv_downloads_anywhere():
    r = subprocess.run(["git", "grep", "-n", "-i", "-e", "fredgraph", "-e", "fred.stlouisfed.org/graph", "--",
                        "src", "scripts", "config", "templates", ".github"], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout == "", r.stdout


def test_no_key_in_data_or_reports():
    r = subprocess.run(["git", "grep", "-n", "-i", "api_key=", "--", "data", "reports", "public"], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout == "", r.stdout


def test_every_fred_reader_delegates_to_the_client():
    for f in ("src/rate_sources/__init__.py", "src/cb_sources/official.py", "src/cb_probe.py", "src/crossasset_probe.py",
              "scripts/cb_nzd_bkbm_spread.py", "scripts/measure/factor_scales.py"):
        assert "fred_api" in (ROOT / f).read_text(), f


def test_every_page_template_includes_the_fred_notice():
    footer = (ROOT / "templates" / "_footer.html.j2").read_text()
    assert F.NOTICE in footer and F.TERMS_URL in footer and "FRED® API Terms of Use" in footer
    assert "logo" not in footer.lower().replace("no fred logo", "")
    for t in (ROOT / "templates").glob("*.html.j2"):
        if not t.name.startswith("_"):
            assert "{% include '_footer.html.j2' %}" in t.read_text(), t.name


def test_fred_provider_config_has_no_url_and_carries_the_notice():
    from src.cb_sources.official import load_official
    p = load_official()["providers"]["fred"]
    assert "url" not in p and F.NOTICE in p["license"]


def test_registry_discontinued_and_personal_only_scopes():
    import yaml
    reg = yaml.safe_load((ROOT / "config" / "fred_series.yaml").read_text())["series"]
    assert reg["AUSSARTMISMEI"]["status"] == "discontinued"
    assert "AUSSARTMISMEI" not in "".join(l for l in (ROOT / "src" / "calendar_fred_fallback.py").read_text().splitlines() if "Candidate(" in l)
    copyrighted = {k for k, v in reg.items() if v.get("copyright")}
    assert copyrighted == {"CLVMNACSCAB1GQCH", "JPNRGDPEXP", "SP500", "DJIA", "NASDAQCOM", "NIKKEI225", "VIXCLS", "VXVCLS"}
    assert all(reg[k]["scope"] == "personal_only" and reg[k]["copyright"]["owner"] and reg[k]["copyright"]["notice"] for k in copyrighted)
    assert all(v["scope"] == "general" for k, v in reg.items() if k not in copyrighted and v["status"] == "active")
