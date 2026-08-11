"""Manual Actuals Panel write endpoint (feat/manual-actuals-panel, Phase B) —
api/manual-actual.py, loaded by file path since Vercel Python entrypoints
aren't a conventional importable module name (hyphenated filename).

Drives the handler over REAL HTTP against an in-process http.server instance
(the actual code path Vercel invokes), mocking only the outbound GitHub
Contents API calls — never the request/response handling itself.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import threading
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "api" / "manual-actual.py"

spec = importlib.util.spec_from_file_location("manual_actual_endpoint", MODULE_PATH)
endpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endpoint)

TOKEN = "test-token-123"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUAL_ACTUALS_TOKEN", TOKEN)
    monkeypatch.setenv("MANUAL_ACTUALS_GITHUB_TOKEN", "gh-fake")
    monkeypatch.setenv("MANUAL_ACTUALS_GITHUB_REPO", "someone/repo")

    ff_parquet = tmp_path / "ff.parquet"
    pd.DataFrame([
        {"canonical_id": "usd_cpi", "datetime_utc": pd.Timestamp("2026-07-01 12:30:00"), "actual": float("nan")},
        {"canonical_id": "usd_ppi", "datetime_utc": pd.Timestamp("2026-07-02 12:30:00"), "actual": 3.2},
        {"canonical_id": "usd_gdp", "datetime_utc": pd.Timestamp("2026-07-03 12:30:00"), "actual": 0.0},
    ]).to_parquet(ff_parquet, index=False)
    monkeypatch.setattr(endpoint, "FF_PARQUET", ff_parquet)

    httpd = HTTPServer(("127.0.0.1", 0), endpoint.handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    thread.join(timeout=5)


def _payload(**over):
    base = {"canonical_id": "usd_cpi", "currency": "USD", "indicator_key": "cpi_yoy",
            "datetime_utc": "2026-07-01T12:30:00", "actual": 3.1,
            "state_resolved": "MISSING", "entered_by": "tester", "note": "unit test"}
    base.update(over)
    return base


def _post(server, payload, token=TOKEN):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Manual-Token"] = token
    return requests.post(server + "/api/manual-actual", json=payload, headers=headers, timeout=5)


def test_missing_token_rejected(server):
    r = _post(server, _payload(), token=None)
    assert r.status_code == 401


def test_wrong_token_rejected(server):
    r = _post(server, _payload(), token="nope")
    assert r.status_code == 401


def test_missing_required_field_rejected(server):
    payload = _payload()
    del payload["actual"]
    r = _post(server, payload)
    assert r.status_code == 400
    assert "actual" in r.json()["error"]


def test_non_numeric_actual_rejected(server):
    r = _post(server, _payload(actual="not-a-number"))
    assert r.status_code == 400


def test_bad_state_resolved_rejected(server):
    r = _post(server, _payload(state_resolved="BOGUS"))
    assert r.status_code == 400


def test_row_not_found_in_parquet_rejected(server):
    r = _post(server, _payload(canonical_id="does_not_exist"))
    assert r.status_code == 404


def test_row_with_real_actual_rejected(server):
    r = _post(server, _payload(canonical_id="usd_ppi", datetime_utc="2026-07-02T12:30:00"))
    assert r.status_code == 409
    assert "already has a real actual" in r.json()["error"]


def test_zero_confirm_row_accepted_for_sanity_check(server):
    """actual==0.0 on the target row is exactly the ZERO_CONFIRM case — must
    NOT be rejected by the 'already has a real actual' guard."""
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/z"}}
        r = _post(server, _payload(canonical_id="usd_gdp", datetime_utc="2026-07-03T12:30:00",
                                   indicator_key="gdp_qoq", state_resolved="ZERO_CONFIRM", actual=0.0))
    assert r.status_code == 200


def test_missing_github_config_returns_500(server, monkeypatch):
    monkeypatch.delenv("MANUAL_ACTUALS_GITHUB_TOKEN", raising=False)
    r = _post(server, _payload())
    assert r.status_code == 500


def test_happy_path_creates_new_overrides_file(server):
    """No overrides file exists yet in the repo (GitHub 404) -> commit creates
    one with a single entry, via a PUT with no `sha` (create, not update)."""
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/abc"}}

        r = _post(server, _payload())

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["entry"]["canonical_id"] == "usd_cpi"
    assert body["entry"]["actual"] == pytest.approx(3.1)
    assert body["entry"]["entered_by"] == "tester"
    assert body["commit"] == "https://github.com/x/y/commit/abc"

    put_kwargs = mput.call_args.kwargs
    assert "sha" not in put_kwargs["json"]
    committed = json.loads(base64.b64decode(put_kwargs["json"]["content"]))
    assert len(committed) == 1 and committed[0]["canonical_id"] == "usd_cpi"


def test_resubmission_replaces_prior_entry_for_same_key(server):
    """Same (canonical_id, datetime_utc) submitted twice -> the second commit
    REPLACES the first entry for that key, not duplicates it (a correction)."""
    existing = [{"canonical_id": "usd_cpi", "datetime_utc": "2026-07-01T12:30:00",
                "actual": 1.0, "state_resolved": "MISSING", "entered_by": "someone_else",
                "entered_at": "2026-01-01T00:00:00", "note": "old"}]
    existing_content = base64.b64encode(json.dumps(existing).encode()).decode()

    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput:
        mget.return_value.status_code = 200
        mget.return_value.json.return_value = {"sha": "abc123", "content": existing_content}
        mput.return_value.status_code = 200
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/def"}}

        r = _post(server, _payload(actual=9.9))

    assert r.status_code == 200
    put_kwargs = mput.call_args.kwargs
    assert put_kwargs["json"]["sha"] == "abc123"
    committed = json.loads(base64.b64decode(put_kwargs["json"]["content"]))
    assert len(committed) == 1                     # replaced, not appended
    assert committed[0]["actual"] == pytest.approx(9.9)
    assert committed[0]["entered_by"] == "tester"   # the NEW entry, not the old one


def test_commit_message_includes_identifying_fields(server):
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        _post(server, _payload())
    msg = mput.call_args.kwargs["json"]["message"]
    assert "usd_cpi" in msg and "MISSING" in msg and "tester" in msg


def test_github_read_failure_surfaces_as_502(server):
    with patch.object(endpoint.requests, "get") as mget:
        mget.return_value.status_code = 500
        mget.return_value.text = "server error"
        r = _post(server, _payload())
    assert r.status_code == 502


def test_github_write_failure_surfaces_as_502(server):
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 403
        mput.return_value.text = "forbidden"
        r = _post(server, _payload())
    assert r.status_code == 502
