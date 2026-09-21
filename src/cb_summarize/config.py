"""config/cb_summaries.yaml, validated once. Pure: no I/O beyond reading the file. The provider block chosen by `provider:` supplies the model, the endpoint, the
key name, the limits of one call and the prices; everything else in the file is provider-independent."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "cb_summaries.yaml"
PROVIDERS = ("openai", "anthropic")


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    max_output_tokens: int
    temperature: Optional[float]           # None = not sent (a reasoning model does not take one)
    reasoning_effort: Optional[str]
    api_url: str
    api_version: Optional[str]
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
    evidence_paragraphs: tuple
    fragments: tuple
    fragment_words: tuple
    fragment_shared: int
    min_support: float
    attribution_words: tuple
    negations: tuple
    speaker_verbs: tuple
    attribution_pronouns: tuple
    price_input: float
    price_cached_input: float
    price_output: float
    price_source: str
    price_model_page: str
    price_checked: str
    price_note: str

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Uncached input + output (reasoning tokens are output tokens); no caching, batch or data-residency modifier is used."""
        return round(input_tokens / 1e6 * self.price_input + output_tokens / 1e6 * self.price_output, 4)


def load(path=None, provider: Optional[str] = None) -> Config:
    raw = yaml.safe_load(Path(path or CONFIG_PATH).read_text())
    name = provider or raw["provider"]
    if name not in PROVIDERS or name not in raw["providers"]:
        raise ValueError(f"cb_summaries.yaml: unknown provider {name!r} (known: {', '.join(PROVIDERS)})")
    p, caps, lim, price = raw["providers"][name], raw["run_caps"], raw["limits"], raw["providers"][name]["pricing_usd_per_mtok"]
    temp = p.get("temperature")
    cfg = Config(
        provider=name, model=str(p["model"]), max_output_tokens=int(p["max_output_tokens"]), temperature=None if temp is None else float(temp),
        reasoning_effort=p.get("reasoning_effort"), api_url=str(p["api_url"]), api_version=p.get("api_version"), env_key=str(p["env_key"]),
        timeout_s=int(p["timeout_s"]), transport_retries=int(p["transport_retries"]), backfill_meetings=int(raw["backfill_meetings"]),
        speech_window_days=int(raw["speech_window_days"]), max_source_chars=int(raw["max_source_chars"]), chars_per_token=int(p["chars_per_token"]),
        estimate_output_tokens=int(p["estimate_output_tokens"]), max_documents=int(caps["max_documents"]), max_input_tokens=int(caps["max_input_tokens"]),
        priority=dict(raw["priority"]), summary_points=tuple(lim["summary_points"]), point_chars=tuple(lim["point_chars"]), quotes=tuple(lim["quotes"]),
        quote_chars=tuple(lim["quote_chars"]), summary_total_chars=int(lim["summary_total_chars"]), blocked_words=tuple(str(w) for w in raw["blocked_words"]),
        attributed_words=tuple(str(w) for w in raw["attributed_words"]), attribution_subjects=tuple(str(w) for w in raw["attribution_subjects"]),
        evidence_paragraphs=tuple(raw["grounding"]["evidence_paragraphs"]), fragments=tuple(raw["grounding"]["fragments"]), fragment_words=tuple(raw["grounding"]["fragment_words"]),
        fragment_shared=int(raw["grounding"]["fragment_shared_words"]), min_support=float(raw["grounding"]["min_support"]),
        attribution_words=tuple(str(w) for w in raw["grounding"]["attribution_words"]), negations=tuple(str(w) for w in raw["grounding"]["negations"]), speaker_verbs=tuple(str(w) for w in raw["grounding"]["speaker_verbs"]),
        attribution_pronouns=tuple(str(w) for w in raw["attribution_pronouns"]),
        price_input=float(price["input"]), price_cached_input=float(price["cached_input"]), price_output=float(price["output"]), price_source=str(p["pricing_source"]),
        price_model_page=str(p["pricing_model_page"]), price_checked=str(p["pricing_checked"]), price_note=" ".join(str(p["pricing_note"]).split()))
    if cfg.max_output_tokens <= 0 or cfg.max_documents <= 0 or cfg.max_input_tokens <= 0:
        raise ValueError("cb_summaries.yaml: max_output_tokens / run_caps must be positive")
    return cfg
