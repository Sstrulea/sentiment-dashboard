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
import urllib.error
import urllib.request
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


class _TestResponse:
    """Minimal requests.Response lookalike (.status_code / .json()) for the
    urllib-based test client below."""
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    def json(self):
        return json.loads(self._body)


def _post(server, payload, token=TOKEN):
    # Deliberately urllib, not `requests`: the endpoint under test and this
    # test file both `import requests`, which is the SAME cached module
    # object — patching endpoint.requests.post (for the refresh-dispatch
    # tests) would silently also mock this call to the local test server if
    # it went through requests.post too.
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Manual-Token"] = token
    req = urllib.request.Request(server + "/api/manual-actual", data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _TestResponse(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return _TestResponse(e.code, e.read())


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
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/z"}}
        mpost.return_value.status_code = 204
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
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/abc"}}
        mpost.return_value.status_code = 204

        r = _post(server, _payload())

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["entry"]["canonical_id"] == "usd_cpi"
    assert body["entry"]["actual"] == pytest.approx(3.1)
    assert body["entry"]["entered_by"] == "tester"
    assert body["commit"] == "https://github.com/x/y/commit/abc"
    assert body["refresh_triggered"] is True

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

    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 200
        mget.return_value.json.return_value = {"sha": "abc123", "content": existing_content}
        mput.return_value.status_code = 200
        mput.return_value.json.return_value = {"commit": {"html_url": "https://github.com/x/y/commit/def"}}
        mpost.return_value.status_code = 204

        r = _post(server, _payload(actual=9.9))

    assert r.status_code == 200
    put_kwargs = mput.call_args.kwargs
    assert put_kwargs["json"]["sha"] == "abc123"
    committed = json.loads(base64.b64decode(put_kwargs["json"]["content"]))
    assert len(committed) == 1                     # replaced, not appended
    assert committed[0]["actual"] == pytest.approx(9.9)
    assert committed[0]["entered_by"] == "tester"   # the NEW entry, not the old one


def test_commit_message_includes_identifying_fields(server):
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        mpost.return_value.status_code = 204
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
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 403
        mput.return_value.text = "forbidden"
        r = _post(server, _payload())
    assert r.status_code == 502
    mpost.assert_not_called()  # a failed commit must never trigger a refresh dispatch


def test_refresh_dispatch_happens_after_commit_is_confirmed(server):
    """The dispatch call must be observably sequenced AFTER the commit PUT
    returns — never fired in parallel with it — since a dispatch that races
    ahead of the commit can start a run that checks out the repo before the
    new commit lands, reproducing the exact stale-render bug this fixes."""
    order = []
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        mput.side_effect = lambda *a, **k: order.append("commit") or mput.return_value
        mpost.return_value.status_code = 204
        mpost.side_effect = lambda *a, **k: order.append("dispatch") or mpost.return_value

        r = _post(server, _payload())

    assert order == ["commit", "dispatch"]
    assert r.json()["refresh_triggered"] is True


def test_refresh_dispatch_targets_econ_refresh_workflow_on_configured_branch(server):
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        mpost.return_value.status_code = 204

        _post(server, _payload())

    assert mpost.call_args.args[0] == (
        "https://api.github.com/repos/someone/repo/actions/workflows/econ-refresh.yml/dispatches"
    )
    assert mpost.call_args.kwargs["json"] == {"ref": "main"}
    assert mpost.call_args.kwargs["headers"]["Authorization"] == "Bearer gh-fake"


def test_refresh_dispatch_failure_is_best_effort_and_does_not_fail_the_request(server):
    """A commit that already succeeded must still return 200 even if the
    dispatch call raises outright (network error, timeout, etc.) — only the
    `refresh_triggered` flag reflects the failure."""
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        mpost.side_effect = requests.ConnectionError("boom")

        r = _post(server, _payload())

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["commit"] == "https://x"
    assert body["refresh_triggered"] is False


def test_refresh_dispatch_non_204_is_reported_as_not_triggered(server):
    with patch.object(endpoint.requests, "get") as mget, patch.object(endpoint.requests, "put") as mput, \
         patch.object(endpoint.requests, "post") as mpost:
        mget.return_value.status_code = 404
        mput.return_value.status_code = 201
        mput.return_value.json.return_value = {"commit": {"html_url": "https://x"}}
        mpost.return_value.status_code = 403
        mpost.return_value.text = "forbidden: missing actions:write"

        r = _post(server, _payload())

    assert r.status_code == 200
    assert r.json()["refresh_triggered"] is False
