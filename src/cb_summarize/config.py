"""config/cb_summaries.yaml, validated once. Pure: no I/O beyond reading the file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "cb_summaries.yaml"


@dataclass(frozen=True)
class Config:
    model: str
    max_tokens: int
    temperature: float
    api_url: str
    api_version: str
    env_key: str
    timeout_s: int
    transport_retries: int
    backfill_meetings: int
    speech_window_days: int
    max_source_chars: int
    chars_per_token: int
    estimate_output_tokens: int
    max_documents: int
    max_input_tokens: int
    priority: dict
    summary_points: tuple
    point_chars: tuple
    quotes: tuple
    quote_chars: tuple
    summary_total_chars: int
    blocked_words: tuple
    attributed_words: tuple
    attribution_subjects: tuple
    price_input: float
    price_output: float
    price_source: str
    price_checked: str

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return round(input_tokens / 1e6 * self.price_input + output_tokens / 1e6 * self.price_output, 4)


def load(path=None) -> Config:
    raw = yaml.safe_load(Path(path or CONFIG_PATH).read_text())
    api, caps, lim, price = raw["api"], raw["run_caps"], raw["limits"], raw["pricing_usd_per_mtok"]
    cfg = Config(
        model=str(raw["model"]), max_tokens=int(raw["max_tokens"]), temperature=float(raw["temperature"]), api_url=str(api["url"]), api_version=str(api["version"]),
        env_key=str(api["env_key"]), timeout_s=int(api["timeout_s"]), transport_retries=int(api["transport_retries"]), backfill_meetings=int(raw["backfill_meetings"]),
        speech_window_days=int(raw["speech_window_days"]), max_source_chars=int(raw["max_source_chars"]), chars_per_token=int(raw["chars_per_token"]), estimate_output_tokens=int(raw["estimate_output_tokens"]),
        max_documents=int(caps["max_documents"]), max_input_tokens=int(caps["max_input_tokens"]), priority=dict(raw["priority"]),
        summary_points=tuple(lim["summary_points"]), point_chars=tuple(lim["point_chars"]), quotes=tuple(lim["quotes"]), quote_chars=tuple(lim["quote_chars"]),
        summary_total_chars=int(lim["summary_total_chars"]), blocked_words=tuple(str(w) for w in raw["blocked_words"]), attributed_words=tuple(str(w) for w in raw["attributed_words"]),
        attribution_subjects=tuple(str(w) for w in raw["attribution_subjects"]),
        price_input=float(price["input"]), price_output=float(price["output"]), price_source=str(raw["pricing_source"]),
        price_checked=str(raw["pricing_checked"]))
    if cfg.max_tokens <= 0 or cfg.max_documents <= 0 or cfg.max_input_tokens <= 0:
        raise ValueError("cb_summaries.yaml: max_tokens / run_caps must be positive")
    return cfg
