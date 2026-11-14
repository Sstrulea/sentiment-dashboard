"""Calibrate the framework-watch keywords (config/cb_framework.yaml) on the existing phase-2a corpus (stance S0a).

    python scripts/cb_framework_calibrate.py            # flags per bank, with title and hits

Offline: reads data/cb/documents/ only. Scans exactly what the collector scans for a new document (title + stored text +
first paragraph) and prints every flag, so each one can be classified by hand as a true change signal or noise. The rule
(pre-registered in the S0a prompt): at most 1 false flag per bank.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import cb_collect as cc                      # noqa: E402
from src.cb_docs import store as dst                  # noqa: E402
from src.cb_framework import flag_documents, load_config   # noqa: E402


def main() -> int:
    docs = list(dst.load_documents(cc.Paths()).values())
    flags = flag_documents(docs, load_config()["keywords"])
    per = Counter(d["currency"] for d in docs)
    print(f"corpus: {len(docs)} documents ({', '.join(f'{k} {v}' for k, v in sorted(per.items()))})")
    print(f"flags: {len(flags)}")
    for k, v in sorted(flags.items(), key=lambda x: (x[1]["currency"], x[1]["published"])):
        print(f"  {v['currency']} {v['published']} {v['type']:<18} {k}  '{v['title'][:80]}'  hits={v['hits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
