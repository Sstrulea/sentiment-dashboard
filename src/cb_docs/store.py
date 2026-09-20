"""Storage of the official texts (phase 2a): documents in monthly partitions, votes and redlines in single files, all through the
deterministic writers of cb_store. Full text is committed only for statements and opening statements (short, needed for the redline);
long documents keep metadata + the sha256 of the extracted text."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

import pyarrow as pa

from .. import cb_store as cs

TYPES = ("statement", "minutes", "account", "summary_of_opinions", "deliberations", "presser_transcript", "presser_video", "opening_statement",
         "speech", "testimony")
FULL_TEXT_TYPES = ("statement", "opening_statement")
TS = pa.timestamp("us", tz="UTC")

DOCUMENTS_SCHEMA = pa.schema([
    ("doc_id", pa.string()), ("currency", pa.string()), ("bank", pa.string()), ("type", pa.string()), ("title", pa.string()),
    ("speaker", pa.string()), ("role", pa.string()), ("url", pa.string()), ("published_at", TS), ("published_date", pa.date32()),
    ("first_seen_at", TS), ("meeting_date", pa.date32()), ("lang", pa.string()), ("format", pa.string()), ("text_sha256", pa.string()),
    ("extraction_method", pa.string()), ("license_note", pa.string()), ("text", pa.string()), ("relevance", pa.string()),
    ("rate_after", pa.float64()), ("rate_lower", pa.float64()), ("rate_upper", pa.float64()), ("rate_note", pa.string()), ("meta_json", pa.string()),
])
DOCUMENTS_KEY = ("doc_id",)
CONTENT = tuple(c for c in DOCUMENTS_SCHEMA.names if c != "first_seen_at")
DOCUMENTS = cs.Dataset("documents", "documents", DOCUMENTS_SCHEMA, DOCUMENTS_KEY, "published_date", CONTENT)

VOTES_SCHEMA = pa.schema([
    ("currency", pa.string()), ("meeting_date", pa.date32()), ("bank", pa.string()), ("kind", pa.string()), ("n_for", pa.int32()),
    ("n_against", pa.int32()), ("for_names", pa.string()), ("against", pa.string()), ("source", pa.string()), ("source_doc_id", pa.string()),
    ("evidence", pa.string()), ("notes", pa.string()),
])
VOTES_KEY = ("currency", "meeting_date")

REDLINES_SCHEMA = pa.schema([
    ("currency", pa.string()), ("meeting_date", pa.date32()), ("prev_meeting_date", pa.date32()), ("doc_id", pa.string()), ("prev_doc_id", pa.string()),
    ("added_words", pa.int32()), ("removed_words", pa.int32()), ("unchanged_words", pa.int32()), ("similarity", pa.float64()), ("ops_json", pa.string()),
])
REDLINES_KEY = ("currency", "meeting_date")


def jdump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def doc_row(*, doc_id: str, currency: str, bank: str, type: str, title: str, url: str, published_date: date, first_seen: datetime,
            published_at: Optional[datetime] = None, speaker: str = "", role: str = "", meeting_date: Optional[date] = None, lang: str = "en",
            format: str = "html", text_sha256: Optional[str] = None, extraction_method: Optional[str] = None, license_note: str = "",
            text: Optional[str] = None, relevance: Optional[str] = None, rate_after: Optional[float] = None, rate_lower: Optional[float] = None,
            rate_upper: Optional[float] = None, rate_note: Optional[str] = None, meta: Optional[dict] = None) -> dict:
    if type not in TYPES:
        raise ValueError(f"unknown document type {type!r}")
    return {"doc_id": doc_id, "currency": currency, "bank": bank, "type": type, "title": title, "speaker": speaker, "role": role, "url": url,
            "published_at": published_at, "published_date": published_date, "first_seen_at": first_seen, "meeting_date": meeting_date, "lang": lang,
            "format": format, "text_sha256": text_sha256, "extraction_method": extraction_method, "license_note": license_note,
            "text": text if type in FULL_TEXT_TYPES else None, "relevance": relevance, "rate_after": rate_after, "rate_lower": rate_lower,
            "rate_upper": rate_upper, "rate_note": rate_note, "meta_json": jdump(meta) if meta else None}


def load_documents(paths, start=None, end=None) -> dict:
    return cs.load(DOCUMENTS, paths.dir, start, end)


def write_documents(paths, store: dict, months=None) -> list:
    return cs.write(DOCUMENTS, paths.dir, store, months)


def load_votes(paths) -> list:
    return cs.read_table_file(paths.votes)


def load_redlines(paths) -> list:
    return cs.read_table_file(paths.redlines)


def merge_table(existing: list, new: list, key: tuple) -> list:
    """Rows keyed on `key`: a new row replaces the old one only when its content differs (same rows -> same bytes)."""
    by = {tuple(r[k] for k in key): r for r in existing}
    for r in new:
        by[tuple(r[k] for k in key)] = r
    return sorted(by.values(), key=lambda r: tuple((0, "") if r[k] is None else (1, r[k]) for k in key))


def write_votes(paths, rows: list) -> bool:
    return cs.write_table_file(paths.votes, VOTES_SCHEMA, VOTES_KEY, rows) if rows else False


def write_redlines(paths, rows: list) -> bool:
    return cs.write_table_file(paths.redlines, REDLINES_SCHEMA, REDLINES_KEY, rows) if rows else False
