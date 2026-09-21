"""The prompts (src/cb_summarize/prompts/*.md) and their versions. A prompt is `system.md` + one kind file with a `prompt_version:` header. The sha256 of
every assembled prompt is pinned in prompts/versions.json: editing a prompt without a new version is caught by the test suite (a change of prompt = a
new prompt_version = every document summarised again under it)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DIR = Path(__file__).with_name("prompts")

KIND_OF_TYPE = {"statement": "statement", "opening_statement": "statement", "presser_transcript": "transcript", "minutes": "minutes", "account": "minutes",
                "summary_of_opinions": "minutes", "deliberations": "minutes", "speech": "speech", "testimony": "speech"}
KINDS = ("statement", "transcript", "minutes", "speech")
LABEL = {"statement": "Monetary policy decision statement", "opening_statement": "Introductory statement at the press conference",
         "presser_transcript": "Press conference transcript", "minutes": "Minutes of the monetary policy meeting", "account": "Account of the monetary policy meeting",
         "summary_of_opinions": "Summary of opinions", "deliberations": "Summary of deliberations", "speech": "Speech", "testimony": "Testimony"}


@dataclass(frozen=True)
class Prompt:
    kind: str
    version: str
    system: str
    sha256: str


def load(kind: str, directory: Optional[Path] = None) -> Prompt:
    directory = directory or DIR                                            # looked up at call time: a test (or a new version) can point DIR elsewhere
    head, _, body = (directory / f"{kind}.md").read_text().partition("\n---\n")
    version = head.split("prompt_version:", 1)[1].strip()
    system = (directory / "system.md").read_text().strip() + "\n\n" + body.strip() + "\n"
    return Prompt(kind, version, system, hashlib.sha256(system.encode("utf-8")).hexdigest())


def for_type(doc_type: str, directory: Optional[Path] = None) -> Prompt:
    return load(KIND_OF_TYPE[doc_type], directory)


def registry(directory: Optional[Path] = None) -> dict:
    return json.loads(((directory or DIR) / "versions.json").read_text())


def user_message(doc_type: str, bank: str, url: str, paragraphs: list, tags: Optional[list] = None) -> str:
    """The numbered paragraphs; `tags` (a press-conference transcript) marks a journalist's question: "[12] (question) ..."."""
    body = "\n".join(f"[{i}] " + (f"({tags[i - 1]}) " if tags and tags[i - 1] else "") + p for i, p in enumerate(paragraphs, 1))
    return f"Document: {LABEL[doc_type]}\nBank: {bank}\nSource: {url}\n\nParagraphs:\n{body}\n"


def feedback_message(errors: list) -> str:
    return ("Your previous output failed the automatic check for these reasons:\n" + "\n".join(f"- {e}" for e in errors) +
            "\n\nReturn a corrected JSON object in the same shape, with nothing before or after it. Change only what the reasons require; keep every rule.")
