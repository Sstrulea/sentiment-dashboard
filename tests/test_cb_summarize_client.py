"""Phase 2b: the HTTP clients (a fake session: no request leaves the machine) - OpenAI Responses API with structured outputs (the provider in use) and Anthropic
Messages (the tested alternative) - the factory, and the recorded client the other tests use. Responses are in each provider's own wire format."""
from __future__ import annotations

import json

import pytest
import requests

from src.cb_summarize.client import APIError, AnthropicClient, OpenAIClient, RecordedClient, Response, make_client
from src.cb_summarize.config import load as load_cfg
from src.cb_summarize.schema import OUTPUT_SCHEMA, SCHEMA_NAME

OAI = load_cfg()                                   # openai (the configured provider)
ANT = load_cfg(provider="anthropic")
KEY = "sk-test-0123456789abcdef"


class R:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Session:
    def __init__(self, *answers):
        self.answers, self.posts = list(answers), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def make(cls, cfg, *answers, sleeps=None):
    s = Session(*answers)
    return cls(cfg, KEY, session=s, sleep=(sleeps.append if sleeps is not None else (lambda x: None))), s


JSON_TEXT = '{"summary": ["a"], "quotes": [], "coverage": [1]}'


def oai_ok(text=JSON_TEXT, usage=None, status="completed", incomplete=None, extra_output=None, refusal=None):
    content = [{"type": "refusal", "refusal": refusal}] if refusal is not None else [{"type": "output_text", "text": text, "annotations": []}]
    body = {"id": "resp_1", "object": "response", "status": status, "incomplete_details": incomplete, "error": None,
            "output": (extra_output or []) + [{"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": content}]}
    if usage is not False:
        body["usage"] = usage or {"input_tokens": 1234, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 456, "output_tokens_details": {"reasoning_tokens": 120}, "total_tokens": 1690}
    return R(200, body)


# --- OpenAI: the request ---------------------------------------------------------------------------------------------------------------------

def test_the_openai_request_is_a_responses_call_with_a_strict_schema_and_no_temperature():
    c, s = make(OpenAIClient, OAI, oai_ok())
    c.complete("SYSTEM", [{"role": "user", "content": "hello"}], OUTPUT_SCHEMA)
    p = s.posts[0]
    assert p["url"] == "https://api.openai.com/v1/responses" and p["timeout"] == OAI.timeout_s
    assert p["headers"] == {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    assert p["json"] == {"model": "gpt-5.6-terra", "input": [{"role": "developer", "content": "SYSTEM"}, {"role": "user", "content": "hello"}],
                         "max_output_tokens": OAI.max_output_tokens, "store": False, "reasoning": {"effort": "medium"},
                         "text": {"format": {"type": "json_schema", "name": SCHEMA_NAME, "strict": True, "schema": OUTPUT_SCHEMA}}}
    assert "temperature" not in p["json"]                                                                              # a reasoning model: not sent (see the config)


def test_temperature_is_sent_when_the_config_sets_one_and_a_call_without_schema_has_no_format():
    import dataclasses
    cfg = dataclasses.replace(OAI, temperature=0.0, reasoning_effort=None)
    c, s = make(OpenAIClient, cfg, oai_ok())
    c.complete("S", [{"role": "user", "content": "q"}])
    assert s.posts[0]["json"]["temperature"] == 0.0 and "reasoning" not in s.posts[0]["json"] and "text" not in s.posts[0]["json"]


def test_the_retry_conversation_is_sent_whole():
    c, s = make(OpenAIClient, OAI, oai_ok())
    msgs = [{"role": "user", "content": "doc"}, {"role": "assistant", "content": "bad output"}, {"role": "user", "content": "fix it"}]
    c.complete("S", msgs, OUTPUT_SCHEMA)
    assert [m["role"] for m in s.posts[0]["json"]["input"]] == ["developer", "user", "assistant", "user"]


# --- OpenAI: the response --------------------------------------------------------------------------------------------------------------------

def test_a_completed_response_gives_the_text_and_the_usage_with_reasoning_tokens():
    c, _ = make(OpenAIClient, OAI, oai_ok())
    assert c.complete("s", [], OUTPUT_SCHEMA) == Response(JSON_TEXT, 1234, 456, "stop", refusal="", usage_reported=True, reasoning_tokens=120)


def test_reasoning_items_are_ignored_and_the_text_blocks_are_joined():
    body = oai_ok(extra_output=[{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "xyz"}]).json()
    body["output"][1]["content"] = [{"type": "output_text", "text": '{"a":'}, {"type": "output_text", "text": " 1}"}]
    c, _ = make(OpenAIClient, OAI, R(200, body))
    assert c.complete("s", []).text == '{"a": 1}'


def test_a_refusal_is_a_refusal_not_text():
    c, _ = make(OpenAIClient, OAI, oai_ok(refusal="I'm sorry, I can't help with that."))
    r = c.complete("s", [], OUTPUT_SCHEMA)
    assert r.text == "" and r.refusal == "I'm sorry, I can't help with that." and r.stop_reason == "refusal"


def test_an_output_cut_at_the_token_limit_is_stop_reason_length():
    c, _ = make(OpenAIClient, OAI, oai_ok(text='{"summary": ["a', status="incomplete", incomplete={"reason": "max_output_tokens"}))
    r = c.complete("s", [], OUTPUT_SCHEMA)
    assert r.stop_reason == "length" and r.text == '{"summary": ["a'


def test_a_content_filter_stop_and_another_unfinished_status_are_not_a_normal_stop():
    c, _ = make(OpenAIClient, OAI, oai_ok(status="incomplete", incomplete={"reason": "content_filter"}), oai_ok(status="in_progress"))
    assert c.complete("s", []).stop_reason == "content_filter" and c.complete("s", []).stop_reason == "other"


def test_a_response_without_usage_says_so():
    c, _ = make(OpenAIClient, OAI, oai_ok(usage=False), oai_ok(usage={"total_tokens": 5}), oai_ok(usage={"input_tokens": 7, "output_tokens": 3}))
    a, b, d = c.complete("s", []), c.complete("s", []), c.complete("s", [])
    assert (a.usage_reported, a.input_tokens, a.output_tokens) == (False, 0, 0) and b.usage_reported is False
    assert (d.usage_reported, d.input_tokens, d.output_tokens, d.reasoning_tokens) == (True, 7, 3, 0)


def test_a_failed_response_object_is_a_server_error_and_an_unreadable_one_too():
    body = {"id": "r", "status": "failed", "error": {"message": "the model crashed"}, "output": []}
    c, _ = make(OpenAIClient, OAI, R(200, body), R(200, {"unexpected": True, "output": "not a list"}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "server" and "the model crashed" in e.value.message
    with pytest.raises(APIError) as e2:
        c.complete("s", [])
    assert e2.value.kind == "server" and "unreadable" in e2.value.message


# --- OpenAI: errors ------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, kind", [(429, "rate_limit"), (500, "server"), (503, "server")])
def test_openai_transient_errors_are_retried_and_then_succeed(status, kind):
    sleeps = []
    c, s = make(OpenAIClient, OAI, R(status, {"error": {"message": "slow down", "type": "x", "code": "rate_limit_exceeded"}}), oai_ok(), sleeps=sleeps)
    assert c.complete("s", []).text == JSON_TEXT
    assert len(s.posts) == 2 and c.calls == 2 and len(sleeps) == 1 and sleeps[0] > 0


def test_running_out_of_credit_is_not_a_rate_limit_and_is_not_retried():
    c, s = make(OpenAIClient, OAI, R(429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "quota" and e.value.status == 429 and len(s.posts) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_openai_auth_errors_are_not_retried(status):
    c, s = make(OpenAIClient, OAI, R(status, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "auth" and len(s.posts) == 1


@pytest.mark.parametrize("status", [400, 404, 422])
def test_a_bad_request_or_an_unknown_model_is_not_retried_and_carries_the_message(status):
    c, s = make(OpenAIClient, OAI, R(status, {"error": {"message": "Unsupported parameter: 'temperature'", "type": "invalid_request_error", "param": "temperature"}}))
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "bad_request" and "Unsupported parameter: 'temperature'" in e.value.message and len(s.posts) == 1


def test_a_persistent_transient_error_and_timeouts_give_up_after_the_configured_retries():
    n = OAI.transport_retries + 1
    c, s = make(OpenAIClient, OAI, *[R(503, {"error": {"message": "unavailable"}})] * n)
    with pytest.raises(APIError) as e:
        c.complete("s", [])
    assert e.value.kind == "server" and len(s.posts) == n
    c2, s2 = make(OpenAIClient, OAI, requests.Timeout("t"), requests.ConnectionError("c"), requests.Timeout("t"))
    with pytest.raises(APIError) as e2:
        c2.complete("s", [])
    assert e2.value.kind == "transport" and len(s2.posts) == 3


def test_the_key_never_appears_in_an_error_its_repr_or_the_config():
    for cls, cfg in ((OpenAIClient, OAI), (AnthropicClient, ANT)):
        c, _ = make(cls, cfg, R(401, {"error": {"message": "invalid key"}}))
        with pytest.raises(APIError) as e:
            c.complete("s", [])
        assert KEY not in str(e.value) and KEY not in repr(e.value) and KEY not in repr(cfg)


# --- Anthropic (kept, tested) --------------------------------------------------------------------------------------------------------------

ANT_OK = {"content": [{"type": "text", "text": '{"summary": []}'}], "usage": {"input_tokens": 1234, "output_tokens": 56}, "stop_reason": "end_turn"}


def test_the_anthropic_request_and_response_still_work_and_are_normalised():
    c, s = make(AnthropicClient, ANT, R(200, ANT_OK))
    r = c.complete("SYSTEM", [{"role": "user", "content": "hello"}], OUTPUT_SCHEMA)                                       # the schema is ignored by this provider
    assert r == Response('{"summary": []}', 1234, 56, "stop")
    p = s.posts[0]
    assert p["url"] == "https://api.anthropic.com/v1/messages" and p["headers"]["x-api-key"] == KEY and p["headers"]["anthropic-version"] == "2023-06-01"
    assert p["json"] == {"model": "claude-sonnet-5", "max_tokens": ANT.max_output_tokens, "system": "SYSTEM", "messages": [{"role": "user", "content": "hello"}], "temperature": 0.0}
    body = dict(ANT_OK, stop_reason="max_tokens", usage={})
    c2, _ = make(AnthropicClient, ANT, R(200, body))
    got = c2.complete("s", [])
    assert got.stop_reason == "length" and got.usage_reported is False


def test_anthropic_errors_are_classified_like_openais():
    c, s = make(AnthropicClient, ANT, R(529, {"error": {"message": "overloaded_error"}}), R(200, ANT_OK))
    assert c.complete("s", []).text == '{"summary": []}' and len(s.posts) == 2
    c2, s2 = make(AnthropicClient, ANT, R(401, {"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(APIError) as e:
        c2.complete("s", [])
    assert e.value.kind == "auth" and len(s2.posts) == 1


# --- the factory and the recorded client ---------------------------------------------------------------------------------------------------

def test_the_factory_builds_the_configured_providers_client():
    assert type(make_client(OAI, KEY)) is OpenAIClient and type(make_client(ANT, KEY)) is AnthropicClient
    assert make_client(OAI, KEY).provider == "openai" and make_client(ANT, KEY).provider == "anthropic"


def test_the_recorded_client_answers_in_order_records_calls_and_schemas_and_raises_what_it_is_told():
    c = RecordedClient(["one", Response("two", 5, 6, "length"), APIError("server", 500, "x")], tokens=(7, 8))
    assert c.complete("sys", [{"role": "user", "content": "q1"}], OUTPUT_SCHEMA) == Response("one", 7, 8)
    assert c.complete("sys", [{"role": "user", "content": "q2"}]) == Response("two", 5, 6, "length")
    with pytest.raises(APIError):
        c.complete("sys", [])
    assert [m[1][0]["content"] for m in c.calls[:2]] == ["q1", "q2"] and len(c.calls) == 3 and c.schemas[0] == OUTPUT_SCHEMA and c.schemas[1] is None
    with pytest.raises(AssertionError, match="no answer recorded"):
        c.complete("sys", [])
