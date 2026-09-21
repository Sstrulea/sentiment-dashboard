"""changes_vs_previous of a decision statement: read off the redline that phase 2a already stores (word diff against the bank's previous statement).
No model, no interpretation: the exact words removed and added, per paragraph of the current statement."""
from __future__ import annotations

import json

MAX_ITEMS = 40


def from_redline(row: dict) -> dict:
    """`row` is a data/cb/redlines.parquet row. Items: {"paragraph": n (1-based in the current statement, None for a paragraph that was dropped), "removed", "added"}."""
    items = []
    for x in json.loads(row["ops_json"]):
        kind, p = x["kind"], x["p"]
        if kind == "same":
            continue
        if kind == "removed":                                              # a paragraph of the previous statement that is gone
            items.append({"paragraph": None, "removed": x["ops"][0][1].strip(), "added": ""})
        else:                                                              # changed (and added: one "+" op): consecutive - / + words form one change
            rem, add = [], []

            def flush() -> None:
                if rem or add:
                    items.append({"paragraph": p + 1, "removed": "".join(rem).strip(), "added": "".join(add).strip()})
                    rem.clear()
                    add.clear()

            for op, text in x["ops"]:
                if op == "=":
                    flush()
                elif op == "-":
                    rem.append(text)
                else:
                    add.append(text)
            flush()
    return {"vs_meeting": row["prev_meeting_date"].isoformat(), "vs_doc_id": row["prev_doc_id"], "added_words": int(row["added_words"]),
            "removed_words": int(row["removed_words"]), "changes": items[:MAX_ITEMS], "truncated": len(items) > MAX_ITEMS}
