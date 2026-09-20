"""Word list for the spacing repair of the BoJ PDFs (src/cb_docs/extract.py): every word of >= 4 letters that occurs UNFRAGMENTED in the
given BoJ statement PDFs, plus the words that join two fragments the PDFs split ("Corporat e"). Needs the system dictionary
(/usr/share/dict/words) only to tell a real join from a coincidence; the output is committed, so runtime needs nothing.

    python scripts/cb_gen_pdf_vocab.py FILE.pdf [...]  > src/cb_docs/pdf_vocab.txt
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pypdf import PdfReader                                # noqa: E402

DICT = Path("/usr/share/dict/words")


def main(files: list) -> int:
    words = {w.strip().lower() for w in DICT.read_text().split()} if DICT.exists() else set()
    vocab: set = set()
    for f in files:
        raw = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(Path(f).read_bytes())).pages)
        toks = re.findall(r"[A-Za-z]+", raw)
        low = [t.lower() for t in toks]
        for t in low:
            if len(t) >= 4 and (not words or t in words):
                vocab.add(t)
        for a, b in zip(low, low[1:]):
            j = a + b
            if len(j) >= 5 and (len(a) <= 3 or len(b) <= 3) and (not words or (j in words and a not in words and b not in words)):
                vocab.add(j)
    sys.stdout.write("\n".join(sorted(vocab)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
