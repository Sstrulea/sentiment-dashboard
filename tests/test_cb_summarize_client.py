"""Phase 2b: the HTTP client of the Messages API (a fake session: no request leaves the machine) and the recorded client the other tests use."""
from __future__ import annotations

import json

import pytest
import requests

from src.cb_summarize.client import APIError, AnthropicClient, RecordedClient, Response
from src.cb_summarize.config import load as load_cfg

CFG = load_cfg()
KEY = "sk-ant-test-0123456789"


class R:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


OK = {"content": [{"type": "text", "text": '{"summary": []}'}], "usage": {"input_tokens": 1234, "output_tokens": 56}, "stop_reason": "end_turn"}


class Session:
    def __init__(self, *answers):
        self.answers, self.posts = list(answers), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def client(*answers, sleeps=None):
    s = Session(*answers)
    return AnthropicClient(CFG, KEY, session=s, sleep=(sleeps.append if sleeps is not None else (lambda x: None))), s


def test_the_request_follows_the_config_and_the_response_is_read():
    c, s = client(R(200, OK))
    r = c.complete("SYSTEM", [{"role": "user", "content": "hello"}])
    assert r == Response('{"summary": []}', 1234, 56, "end_turn")
    p = s.posts[0]
    assert p["url"] == "https://api.anthropic.com/v1/messages" and p["timeout"] == CFG.timeout_s
    assert p["json"] == {"model": "claude-sonnet-5", "max_tokens": CFG.max_tokens, "temperature": 0, "system": "SYSTEM", "messages": [{"role": "user", "content": "hello"}]}
    assert p["headers"] == {"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}


def test_text_blocks_are_joined_and_other_blocks_ignored():
    body = dict(OK, content=[{"type": "text", "text": "a"}, {"type": "tool_use", "id": "x"}, {"type": "text", "text": "b"}], stop_reason="max_tokens")
    r, _ = client(R(200, body))
    got = r.complete("s", [])
    assert got.text == "ab" and got.stop_reason == "max_tokens"


@pytest.mark.parametrize("status, kind", [(429, "rate_limit"), (500, "server"), (503, "server"), (529, "overloaded")])
def test_a_transient_error_is_retried_and_then_succeeds(status, kind):
    sleeps = []
    c, s = client(R(status, {"error": {"message": "slow down"}}), R(200, OK), sleeps=sleeps)
    assert c.complete("s", []).text == '{"summary": []}'
    assert len(s.posts) == 2 and c.calls == 2 and len(sleeps) == 1 and sleeps[0] > 0                                     # waited before the second attempt


def test_a_persistent_transient_error_gives_up_after_the_configured_retries():
    n = CFG.transport_retries + 1
    c, s = client(*[R(529, {"error": {"message": "overloaded_error"}})] * n)
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "overloaded" and e.value.status == 529 and len(s.posts) == n and "overloaded_error" in str(e.value)


def test_a_timeout_is_a_transport_error_and_is_retried():
    c, s = client(requests.Timeout("t"), requests.ConnectionError("c"), requests.Timeout("t"))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "transport" and len(s.posts) == 3


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_error_is_not_retried(status):
    c, s = client(R(status, {"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "auth" and len(s.posts) == 1


def test_a_bad_request_is_not_retried_and_carries_the_message():
    c, s = client(R(400, {"error": {"type": "invalid_request_error", "message": "temperature: not supported"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "bad_request" and "temperature: not supported" in e.value.message and len(s.posts) == 1


def test_an_unreadable_success_is_a_server_error():
    c, _ = client(R(200, {"unexpected": True}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "server"


def test_the_key_never_appears_in_an_error_or_its_repr():
    c, _ = client(R(401, {"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert KEY not in str(e.value) and KEY not in repr(e.value) and KEY not in repr(c.__dict__.get("cfg"))


def test_the_recorded_client_answers_in_order_records_calls_and_raises_what_it_is_told():
    c = RecordedClient(["one", Response("two", 5, 6), APIError("server", 500, "x")], tokens=(7, 8))
    assert c.complete("sys", [{"role": "user", "content": "q1"}]) == Response("one", 7, 8)
    assert c.complete("sys", [{"role": "user", "content": "q2"}]) == Response("two", 5, 6)
    with pytest.raises(APIError):
        c.complete("sys", [])
    assert [m[1][0]["content"] for m in c.calls[:2]] == ["q1", "q2"] and len(c.calls) == 3
    with pytest.raises(AssertionError, match="no answer recorded"):
        c.complete("sys", [])
