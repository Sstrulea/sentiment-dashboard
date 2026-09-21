"""Phase 2b with the OpenAI provider: the stage end to end through the REAL OpenAI client over recorded responses in the Responses API wire format (a fake session:
nothing leaves the machine). The six output cases, a refusal, an output cut at the limit, invalid JSON, a response without usage, the idempotence key and the API errors."""
from __future__ import annotations

import dataclasses
import json

import pytest

from src import cb_collect as cc
from src.cb_docs import store as dst
from src.cb_summarize import prompts as PR
from src.cb_summarize import run as R
from src.cb_summarize import store as SS
from src.cb_summarize.client import RecordedClient
from src.cb_summarize.config import load as load_cfg
from src.cb_summarize.schema import OUTPUT_SCHEMA

from .cb_docs_helpers import FakeSession
from .cb_sum_helpers import FED_KEY, GOOD_FED, NOW, STMT_V, TODAY, HttpResp, collected_dir, dumps, fresh_copy, oai_body, openai_client, responder_generic, variant
from .test_cb_docs_collect import fetcher

CFG = load_cfg()
FKEY = R.failure_key(FED_KEY, PR.load("statement"), CFG)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return collected_dir(tmp_path_factory)


@pytest.fixture()
def paths(base, tmp_path):
    return cc.Paths(fresh_copy(base, tmp_path))


def go(paths, client, cfg=CFG, **kw):
    kw.setdefault("only", {FED_KEY})
    return R.run_summaries(paths, TODAY, client=client, fetcher=fetcher(FakeSession()), env={}, now=NOW, cfg=cfg, **kw)


def good():
    return oai_body(dumps(GOOD_FED), reasoning=300)


# --- the request the stage sends ------------------------------------------------------------------------------------------------------------

def test_the_stage_sends_the_prompt_the_paragraphs_and_the_strict_schema_and_stores_provider_model_and_usage(paths):
    client, s = openai_client(CFG, good())
    rep = go(paths, client)
    assert (rep.new, rep.calls, rep.stopped, rep.failed_validation) == (1, 1, None, [])
    body = s.posts[0]["json"]
    assert s.posts[0]["url"] == "https://api.openai.com/v1/responses" and body["model"] == "gpt-5.6-terra" and "temperature" not in body
    assert body["input"][0] == {"role": "developer", "content": PR.load("statement").system}
    assert body["input"][1]["role"] == "user" and "[4] Inflation remains elevated." in body["input"][1]["content"]
    assert body["text"]["format"] == {"type": "json_schema", "name": "factual_summary", "strict": True, "schema": OUTPUT_SCHEMA}
    rec = SS.load(paths.summaries)[FED_KEY]
    assert (rec["provider"], rec["model"], rec["prompt_version"]) == ("openai", "gpt-5.6-terra", STMT_V)
    assert rec["usage"] == {"input_tokens": 1500, "output_tokens": 800, "attempts": 1, "reasoning_tokens": 300}
    assert (rep.input_tokens, rep.output_tokens, rep.reasoning_tokens) == (1500, 800, 300)
    assert rep.cost_usd == round(1500 / 1e6 * 2.0 + 800 / 1e6 * 12.0, 4) == 0.0126                                       # 2 / 12 USD per MTok, reasoning tokens inside the output


# --- the six output cases, in the OpenAI format ---------------------------------------------------------------------------------------------

BAD = {
    "invented quote": (dumps(variant(quotes=[{"paragraph": 2, "text": "The Committee decided to lower the target range for the federal funds rate"}])), "quote 1 is not verbatim in paragraph 2"),
    "invented number": (dumps(variant(summary=[GOOD_FED["summary"][0], "The Committee raised the target range to 4.5 percent.", GOOD_FED["summary"][2]])),
                        "'4.5 percent' of summary point 2 does not appear in the document"),
    "direction word": (dumps(variant(summary=GOOD_FED["summary"][:2] + ["The move is hawkish, the Committee says."])), "uses the word 'hawkish'"),
    "too long": (dumps(variant(summary=GOOD_FED["summary"][:2] + ["The Committee " + "states that inflation remains elevated " * 30])), "characters; allowed 20-400"),
    "invalid json": ('Here is the summary: {"summary": [', "the output is not valid JSON"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_a_bad_first_output_gets_one_retry_with_the_errors_and_a_good_second_is_stored(paths, case):
    bad, expected = BAD[case]
    client, s = openai_client(CFG, oai_body(bad), good())
    rep = go(paths, client)
    assert (rep.new, rep.calls, rep.failed_validation) == (1, 2, [])
    second = s.posts[1]["json"]["input"]
    assert [m["role"] for m in second] == ["developer", "user", "assistant", "user"] and second[2]["content"] == bad
    assert expected in second[3]["content"] and second[3]["content"].startswith("Your previous output failed the automatic check")
    assert SS.load(paths.summaries)[FED_KEY]["usage"] == {"input_tokens": 3000, "output_tokens": 1600, "attempts": 2, "reasoning_tokens": 300}        # both attempts are paid (the reasoning tokens of the good one)


@pytest.mark.parametrize("case", sorted(k for k in BAD if k != "invented quote"))                                                # (an invented quote alone: the summary is published without it)
def test_two_bad_outputs_write_nothing_and_mark_validation_failed(paths, case):
    bad, expected = BAD[case]
    client, s = openai_client(CFG, oai_body(bad), oai_body(bad))
    rep = go(paths, client)
    assert rep.new == 0 and rep.calls == 2 and [d for d, _ in rep.failed_validation] == [FED_KEY] and len(s.posts) == 2
    assert SS.load(paths.summaries) == {} and not list(paths.summaries.glob("summaries_*.json"))
    f = SS.load_failures(paths.summaries)[FKEY]
    assert (f["provider"], f["model"], f["prompt_version"]) == ("openai", "gpt-5.6-terra", STMT_V) and expected in " ".join(f["errors"])
    again, s2 = openai_client(CFG)                                                                                       # no answers at all: any request would raise
    assert go(paths, again).calls == 0 and not s2.posts


# --- what a structured-output API adds: refusal, cut-off, usage -----------------------------------------------------------------------------

def test_a_refusal_is_a_failed_attempt_with_its_own_feedback_and_a_good_retry_is_stored(paths):
    client, s = openai_client(CFG, oai_body(refusal="I'm sorry, I can't help with that."), good())
    rep = go(paths, client)
    assert (rep.new, rep.calls) == (1, 2)
    second = s.posts[1]["json"]["input"]
    assert second[2] == {"role": "assistant", "content": "I'm sorry, I can't help with that."}
    assert "the model declined to answer (I'm sorry, I can't help with that.)" in second[3]["content"]


def test_two_refusals_are_a_validation_failure_that_says_so(paths):
    client, _ = openai_client(CFG, oai_body(refusal="I can't."), oai_body(refusal="I can't."))
    rep = go(paths, client)
    assert rep.new == 0 and "the model declined to answer (I can't.)" in SS.load_failures(paths.summaries)[FKEY]["errors"][0]


def test_an_output_cut_at_the_token_limit_is_retried_with_a_shorter_request_and_never_stored(paths):
    cut = oai_body('{"summary": ["The Federal Open Market Committee approved the statement by a 12 –', status="incomplete", incomplete={"reason": "max_output_tokens"})
    client, s = openai_client(CFG, cut, good())
    rep = go(paths, client)
    assert (rep.new, rep.calls) == (1, 2) and "cut off at the output token limit" in s.posts[1]["json"]["input"][3]["content"]
    client2, _ = openai_client(CFG, cut, cut)
    rep2 = go(paths, client2, only={"USD:statement:2026-07-29"})
    assert rep2.new == 0 and "cut off at the output token limit" in SS.load_failures(paths.summaries)[R.failure_key("USD:statement:2026-07-29", PR.load("statement"), CFG)]["errors"][0]


def test_a_response_that_did_not_finish_normally_is_a_failed_attempt_even_if_its_text_parses(paths):
    filtered = oai_body(dumps(GOOD_FED), status="incomplete", incomplete={"reason": "content_filter"})                # complete-looking JSON, but the API says it was stopped
    client, s = openai_client(CFG, filtered, good())
    rep = go(paths, client)
    assert (rep.new, rep.calls) == (1, 2) and "did not finish normally (content_filter)" in s.posts[1]["json"]["input"][3]["content"]
    client2, s2 = openai_client(CFG, oai_body(dumps(GOOD_FED), status="in_progress"), good())
    assert go(paths, client2, only={"USD:statement:2026-07-29"}).calls == 2 and "did not finish normally (other)" in s2.posts[1]["json"]["input"][3]["content"]


def test_a_complete_but_invalid_json_body_is_the_same_failed_attempt(paths):
    client, _ = openai_client(CFG, oai_body("{'summary': 'single quotes'}"), good())
    assert go(paths, client).new == 1


def test_a_response_without_usage_is_estimated_from_the_text_and_says_so(paths):
    client, s = openai_client(CFG, oai_body(dumps(GOOD_FED), usage=False))
    rep = go(paths, client)
    sent = s.posts[0]["json"]
    est_in = (len(sent["input"][0]["content"]) + len(sent["input"][1]["content"])) // CFG.chars_per_token
    est_out = len(dumps(GOOD_FED)) // CFG.chars_per_token
    assert rep.new == 1 and (rep.input_tokens, rep.output_tokens) == (est_in, est_out) and est_in > 500 and rep.usage_estimated == 1
    rec = SS.load(paths.summaries)[FED_KEY]
    assert rec["usage"]["estimated"] is True and rec["usage"]["input_tokens"] == est_in
    assert rep.cost_usd == CFG.cost_usd(est_in, est_out) > 0                                                             # never a zero cost for a call that was made
    from src import cb_datasets as ds
    assert "usage missing in 1 call(s): tokens estimated from the text" in ds.summaries_report(rep)[0]


def test_reasoning_tokens_are_reported_inside_the_output_and_priced_as_output(paths):
    client, _ = openai_client(CFG, oai_body(dumps(GOOD_FED), usage={"input_tokens": 1000, "output_tokens": 2000, "output_tokens_details": {"reasoning_tokens": 1500}}))
    rep = go(paths, client)
    assert (rep.output_tokens, rep.reasoning_tokens) == (2000, 1500) and rep.cost_usd == round(1000 / 1e6 * 2.0 + 2000 / 1e6 * 12.0, 4) == 0.026
    from src import cb_datasets as ds
    assert "(1500 of them reasoning)" in ds.summaries_report(rep)[0]


# --- the idempotence key: input_sha256 + prompt_version + provider + model -------------------------------------------------------------------

def test_the_same_provider_and_model_never_pay_twice_and_another_model_or_provider_does(paths):
    client, s = openai_client(CFG, good())
    go(paths, client)
    same, s2 = openai_client(CFG)
    assert go(paths, same).calls == 0 and not s2.posts
    luna = dataclasses.replace(CFG, model="gpt-5.6-luna")                                                                # another model of the same provider
    c3, s3 = openai_client(luna, good())
    rep = go(paths, c3, cfg=luna)
    assert rep.new == 1 and s3.posts[0]["json"]["model"] == "gpt-5.6-luna" and SS.load(paths.summaries)[FED_KEY]["model"] == "gpt-5.6-luna"
    ant = load_cfg(provider="anthropic")                                                                                 # another provider
    rc = RecordedClient([dumps(GOOD_FED)])
    rep = go(paths, rc, cfg=ant)
    rec = SS.load(paths.summaries)[FED_KEY]
    assert rep.new == 1 and (rec["provider"], rec["model"]) == ("anthropic", "claude-sonnet-5") and len(rc.calls) == 1
    back, s4 = openai_client(CFG, good())
    assert go(paths, back).new == 1 and s4.posts and SS.load(paths.summaries)[FED_KEY]["provider"] == "openai"           # and back again: it is a different key each time


def test_a_failure_is_remembered_per_provider_and_model(paths):
    client, _ = openai_client(CFG, oai_body("nope"), oai_body("nope"))
    go(paths, client)
    assert list(SS.load_failures(paths.summaries)) == [FKEY] and FKEY.endswith("|openai|gpt-5.6-terra")
    luna = dataclasses.replace(CFG, model="gpt-5.6-luna")
    c2, s2 = openai_client(luna, good())
    rep = go(paths, c2, cfg=luna)                                                                                        # the failure of terra does not block luna
    assert rep.new == 1 and s2.posts and SS.load_failures(paths.summaries) == {}
    ant = load_cfg(provider="anthropic")
    assert R.failure_key(FED_KEY, PR.load("statement"), ant) != FKEY


def test_a_record_of_another_provider_is_not_done_and_status_counts_it_pending(paths, monkeypatch):
    from src import cb_datasets as ds
    go(paths, RecordedClient([dumps(GOOD_FED)]), cfg=load_cfg(provider="anthropic"))
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    rows = ds._summaries_status_section(paths, TODAY)[2]
    assert rows[0][0] == 1 and rows[0][2] == rows[0][1]                                                                  # stored 1 (claude), but every candidate is pending for gpt-5.6-terra


# --- API errors ---------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, body, kind", [
    (401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}, "auth"),
    (429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}, "quota"),
    (400, {"error": {"message": "Unsupported parameter: 'temperature'", "param": "temperature"}}, "bad_request"),
])
def test_an_openai_error_stops_the_run_cleanly_and_writes_nothing(paths, status, body, kind):
    client, s = openai_client(CFG, HttpResp(status, body))
    rep = go(paths, client, only=None, types={"statement"})
    assert rep.stopped.startswith(f"the API stopped the run ({kind})") and rep.new == 0 and len(s.posts) == 1
    assert not paths.summaries.exists() or not list(paths.summaries.glob("*.json"))


def test_a_transient_error_is_retried_by_the_client_and_the_stage_goes_on(paths):
    client, s = openai_client(CFG, HttpResp(503, {"error": {"message": "unavailable"}}), good())
    rep = go(paths, client)
    assert rep.new == 1 and len(s.posts) == 2 and rep.calls == 1                                                          # two HTTP attempts, one model call that counted
