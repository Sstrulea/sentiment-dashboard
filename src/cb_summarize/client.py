"""The model client: plain HTTP on the Messages API (no SDK, no new dependency) and a recorded client for tests. The key is read from the environment by
the caller and never logged."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from .config import Config


@dataclass
class Response:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"


class APIError(Exception):
    """kind: auth (401/403: the whole stage stops) | bad_request (400: the request is wrong, not retried) | rate_limit / overloaded / server / transport
    (retried `transport_retries` times, then the stage stops cleanly)."""

    def __init__(self, kind: str, status: Optional[int], message: str) -> None:
        super().__init__(f"{kind}{f' {status}' if status else ''}: {message}")
        self.kind, self.status, self.message = kind, status, message


RETRYABLE = {429: "rate_limit", 500: "server", 502: "server", 503: "server", 504: "server", 529: "overloaded"}


class AnthropicClient:
    def __init__(self, cfg: Config, api_key: str, session=None, sleep: Callable = time.sleep) -> None:
        self.cfg, self._key, self.session, self._sleep = cfg, api_key, session or requests.Session(), sleep
        self.calls = 0

    def complete(self, system: str, messages: list) -> Response:
        body = {"model": self.cfg.model, "max_tokens": self.cfg.max_tokens, "temperature": self.cfg.temperature, "system": system, "messages": messages}
        headers = {"x-api-key": self._key, "anthropic-version": self.cfg.api_version, "content-type": "application/json"}
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
            if r.status_code == 200:
                return self._parse(r)
            msg = _error_message(r)
            if r.status_code in (401, 403):
                raise APIError("auth", r.status_code, msg)
            if r.status_code in RETRYABLE:
                last = APIError(RETRYABLE[r.status_code], r.status_code, msg)
                continue
            raise APIError("bad_request", r.status_code, msg)
        raise last or APIError("transport", None, "no response")

    @staticmethod
    def _parse(r) -> Response:
        try:
            data = r.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage") or {}
            return Response(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), str(data.get("stop_reason") or ""))
        except (ValueError, KeyError, TypeError) as e:
            raise APIError("server", r.status_code, f"unreadable response ({type(e).__name__})")


def _error_message(r) -> str:
    try:
        return str((r.json().get("error") or {}).get("message") or r.text[:200])
    except ValueError:
        return r.text[:200]


class RecordedClient:
    """Answers from a list (consumed in order) or from a function of (system, messages). A str is the model's text; a Response carries token counts; an
    exception is raised. Every call is kept in `.calls` as (system, messages)."""

    def __init__(self, answers=None, *, responder: Optional[Callable] = None, tokens: tuple = (1000, 300)) -> None:
        self.answers = list(answers or [])
        self.responder = responder
        self.tokens = tokens
        self.calls: list = []

    def complete(self, system: str, messages: list) -> Response:
        self.calls.append((system, [dict(m) for m in messages]))
        item = self.responder(system, messages) if self.responder else (self.answers.pop(0) if self.answers else None)
        if item is None:
            raise AssertionError("RecordedClient: no answer recorded for this call")
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, Response) else Response(item, *self.tokens)
