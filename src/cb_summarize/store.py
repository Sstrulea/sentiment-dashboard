"""Where the summaries live: data/cb/summaries/summaries_YYYY-MM.json (monthly partitions of the document's publication month, one record per doc_id,
sorted, deterministic bytes) and failures.json (the summaries that failed validation twice, keyed by doc_id | input_sha256 | prompt_version)."""
from __future__ import annotations

import json
from pathlib import Path

VERSION = 1
FAILURES = "failures.json"


def dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def partition_name(published: str) -> str:
    return f"summaries_{published[:7]}.json"


def load(directory: Path) -> dict:
    """doc_id -> record."""
    out: dict = {}
    for p in sorted(Path(directory).glob("summaries_*.json")):
        for rec in json.loads(p.read_text()).get("summaries", []):
            out[rec["doc_id"]] = rec
    return out


def write(directory: Path, records: dict) -> list:
    """Writes the partitions whose bytes changed; returns their names. A partition with no record is removed."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    by_part: dict = {}
    for rec in records.values():
        by_part.setdefault(partition_name(rec["published"]), []).append(rec)
    changed = []
    for name, recs in sorted(by_part.items()):
        text = dump({"version": VERSION, "summaries": sorted(recs, key=lambda r: r["doc_id"])})
        p = directory / name
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
            changed.append(name)
    for p in directory.glob("summaries_*.json"):
        if p.name not in by_part:
            p.unlink()
            changed.append(p.name)
    return changed


def failure_key(doc_id: str, input_sha256: str, prompt_version: str) -> str:
    return f"{doc_id}|{input_sha256}|{prompt_version}"


def load_failures(directory: Path) -> dict:
    p = Path(directory) / FAILURES
    return json.loads(p.read_text()).get("failures", {}) if p.exists() else {}


def write_failures(directory: Path, failures: dict) -> bool:
    directory = Path(directory)
    p = directory / FAILURES
    if not failures:
        if p.exists():
            p.unlink()
            return True
        return False
    directory.mkdir(parents=True, exist_ok=True)
    text = dump({"version": VERSION, "failures": failures})
    if p.exists() and p.read_text() == text:
        return False
    p.write_text(text)
    return True
