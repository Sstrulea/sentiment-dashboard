"""The model clients: plain HTTP (no SDK, no new dependency), one class per provider behind one interface, and a recorded client for tests.

    client.complete(system, messages, schema=None) -> Response(text, input_tokens, output_tokens, stop_reason, ...)

`messages` are [{"role": "user" | "assistant", "content": str}]; `schema` is the strict JSON schema of the output (structured outputs) - a provider that cannot
enforce one ignores it (the verifier is the gate either way). The provider and the model come from config/cb_summaries.yaml; switching provider is that file and
`make_client`. The key is read from the environment by the caller and never logged."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from .config import Config
from .schema import SCHEMA_NAME


@dataclass
class Response:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "stop"              # normalised: stop | length (cut at the output limit) | refusal | content_filter | other
    refusal: str = ""                      # the model's own words when it declined to answer
    usage_reported: bool = True            # False: the API sent no usage - the caller must estimate
    reasoning_tokens: int = 0              # part of output_tokens (billed as output)


class APIError(Exception):
    """kind: auth (401 / 403) | quota (the account has no credit left) | bad_request (400 / 404 / 422: the request is wrong) - none of these is retried and the whole
    stage stops; rate_limit / overloaded / server / transport are retried `transport_retries` times, then the stage stops cleanly."""

    def __init__(self, kind: str, status: Optional[int], message: str) -> None:
        super().__init__(f"{kind}{f' {status}' if status else ''}: {message}")
        self.kind, self.status, self.message = kind, status, message


RETRYABLE = {429: "rate_limit", 500: "server", 502: "server", 503: "server", 504: "server", 529: "overloaded"}


def _body(r) -> dict:
    try:
        b = r.json()
        return b if isinstance(b, dict) else {}
    except ValueError:
        return {}


def _error_message(r) -> str:
    err = _body(r).get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return (getattr(r, "text", "") or "")[:200]


class HttpClient:
    """The retry loop and the error classes shared by the HTTP providers; a subclass builds the request and reads the response."""

    provider = ""

    def __init__(self, cfg: Config, api_key: str, session=None, sleep: Callable = time.sleep) -> None:
        self.cfg, self._key, self.session, self._sleep = cfg, api_key, session or requests.Session(), sleep
        self.calls = 0

    # -- per provider ------------------------------------------------------------------------------------------------------
    def build(self, system: str, messages: list, schema: Optional[dict]) -> tuple:               # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, r) -> Response:                                                                # pragma: no cover - abstract
        raise NotImplementedError

    def classify(self, r) -> Optional[APIError]:
        """None when the response is a success; else the error (raised at once, or retried when its kind is retryable)."""
        if r.status_code == 200:
            return None
        msg = _error_message(r)
        if r.status_code in (401, 403):
            return APIError("auth", r.status_code, msg)
        if r.status_code in RETRYABLE:
            return APIError(RETRYABLE[r.status_code], r.status_code, msg)
        return APIError("bad_request", r.status_code, msg)

    # -- the call ----------------------------------------------------------------------------------------------------------
    def complete(self, system: str, messages: list, schema: Optional[dict] = None) -> Response:
        body, headers = self.build(system, messages, schema)
        last: Optional[APIError] = None
        for attempt in range(self.cfg.transport_retries + 1):
            if attempt:
                self._sleep(min(2 ** attempt * 2, 30))
            self.calls += 1
            try:
                r = self.session.post(self.cfg.api_url, json=body, headers=headers, timeout=self.cfg.timeout_s)
            except requests.RequestException as e:
                last = APIError("transport", None, type(e).__name__)
                continue
            err = self.classify(r)
            if err is None:
                return self.parse(r)
            if err.kind not in ("rate_limit", "overloaded", "server"):
                raise err
            last = err
        raise last or APIError("transport", None, "no response")


class AnthropicClient(HttpClient):
    provider = "anthropic"

    def build(self, system: str, messages: list, schema: Optional[dict]) -> tuple:
        body = {"model": self.cfg.model, "max_tokens": self.cfg.max_output_tokens, "system": system, "messages": messages}
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        return body, {"x-api-key": self._key, "anthropic-version": self.cfg.api_version or "2023-06-01", "content-type": "application/json"}

    def parse(self, r) -> Response:
        try:
            data = r.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage") or {}
            stop = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "refusal": "refusal"}.get(str(data.get("stop_reason") or ""), "other")
            reported = isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int)
            return Response(text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), stop, usage_reported=reported)
        except (ValueError, KeyError, TypeError) as e:
            raise APIError("server", r.status_code, f"unreadable response ({type(e).__name__})")


class OpenAIClient(HttpClient):
    """The Responses API (POST /v1/responses) with Structured Outputs: `text.format = {type: json_schema, strict: true}`. The system prompt goes in as a
    `developer` message; `store` is off; `temperature` is sent only when the config sets one (a reasoning model does not take it); the reasoning effort comes
    from the config. Reasoning tokens count in `output_tokens` (and in the price)."""

    provider = "openai"

    def build(self, system: str, messages: list, schema: Optional[dict]) -> tuple:
        body: dict = {"model": self.cfg.model, "input": [{"role": "developer", "content": system}, *messages], "max_output_tokens": self.cfg.max_output_tokens, "store": False}
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        if self.cfg.reasoning_effort:
            body["reasoning"] = {"effort": self.cfg.reasoning_effort}
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": SCHEMA_NAME, "strict": True, "schema": schema}}
        return body, {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def classify(self, r) -> Optional[APIError]:
        err = super().classify(r)
        if err is not None and r.status_code == 429 and str((_body(r).get("error") or {}).get("code")) == "insufficient_quota":
            return APIError("quota", 429, err.message)                                             # no credit left: not a transient rate limit
        return err

    def parse(self, r) -> Response:
        try:
            data = r.json()
            texts, refusals = [], []
            for item in data.get("output") or []:
                if item.get("type") != "message":
                    continue                                                                       # reasoning items carry no text of ours
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        texts.append(c.get("text") or "")
                    elif c.get("type") == "refusal":
                        refusals.append(c.get("refusal") or "")
            status = data.get("status")
            if status == "failed":
                raise APIError("server", r.status_code, "the response failed: " + str((data.get("error") or {}).get("message") or "no detail")[:160])
            reason = str((data.get("incomplete_details") or {}).get("reason") or "")
            stop = "refusal" if refusals else "length" if status == "incomplete" and reason == "max_output_tokens" else "content_filter" if reason == "content_filter" \
                else "other" if status not in ("completed", None) else "stop"
            usage = data.get("usage")
            reported = isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int)
            u = usage if reported else {}
            reasoning = int(((u.get("output_tokens_details") or {}).get("reasoning_tokens")) or 0)
            return Response("".join(texts), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), stop, refusal=" ".join(refusals).strip(),
                            usage_reported=reported, reasoning_tokens=reasoning)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise APIError("server", r.status_code, f"unreadable response ({type(e).__name__})")


CLIENTS = {"openai": OpenAIClient, "anthropic": AnthropicClient}


def make_client(cfg: Config, api_key: str, session=None, sleep: Callable = time.sleep) -> HttpClient:
    """The client of the configured provider."""
    return CLIENTS[cfg.provider](cfg, api_key, session=session, sleep=sleep)


class RecordedClient:
    """Answers from a list (consumed in order) or from a function of (system, messages). A str is the model's text; a Response carries token counts (and a stop
    reason, a refusal, a missing usage); an exception is raised. Every call is kept in `.calls` as (system, messages), its schema in `.schemas`."""

    provider = "recorded"

    def __init__(self, answers=None, *, responder: Optional[Callable] = None, tokens: tuple = (1000, 300)) -> None:
        self.answers = list(answers or [])
        self.responder = responder
        self.tokens = tokens
        self.calls: list = []
        self.schemas: list = []

    def complete(self, system: str, messages: list, schema: Optional[dict] = None) -> Response:
        self.calls.append((system, [dict(m) for m in messages]))
        self.schemas.append(schema)
        item = self.responder(system, messages) if self.responder else (self.answers.pop(0) if self.answers else None)
        if item is None:
            raise AssertionError("RecordedClient: no answer recorded for this call")
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Response) else Response(item, *self.tokens)
