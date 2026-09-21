"""The output contract as a strict JSON schema (structured outputs). Every object has `additionalProperties: false` and lists all its properties as required;
only the keywords every strict-mode implementation supports are used (type, properties, required, additionalProperties, items, integer, string). The lengths,
counts, the word counts and the paragraph range are NOT in the schema: the verifier checks them, and it is the only gate that writes."""
from __future__ import annotations

SCHEMA_NAME = "factual_summary"

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "quotes", "coverage"],
    "properties": {
        "summary": {                                   # each point carries its evidence: the paragraph(s) that state it and one verbatim fragment of one of them
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidence"],
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["paragraphs", "fragment"],
                        "properties": {"paragraphs": {"type": "array", "items": {"type": "integer"}}, "fragment": {"type": "string"}},
                    },
                },
            },
        },
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["paragraph", "text"],
                "properties": {"paragraph": {"type": "integer"}, "text": {"type": "string"}},
            },
        },
        "coverage": {"type": "array", "items": {"type": "integer"}},
    },
}
