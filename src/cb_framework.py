"""Framework watch (stance phase S0a) - the I/O part. Stage `framework` of src/cb_collect.py.

    python -m src.cb_collect --stage framework [--force-framework]

* At most once a week (state.json `_framework.checked`): every watch target of config/cb_framework.yaml (except the
  `manual_only` RBNZ page) is fetched politely through src/cb_docs/http.Fetcher (robots.txt, 2 s per host, conditional
  GET with the validators kept in state.json and advanced only when the stored text changed), its main text is extracted
  with src/cb_docs/extract (HTML container / PDF), normalised and hashed (src/cb_compute/framework_watch.py).
* Snapshots of the normalised text: data/cb/framework_watch/<target id>/<hash[:16]>.txt (written by CI, one file per
  distinct version; the redline of an alert is computed between the confirmed and the current snapshot with the 2a redline).
* Keyword flags: every NEW official text collected by phase 2a is scanned when it is collected (cb_docs.collect calls
  `scan_documents`); hits go to data/cb/framework_watch/keyword_flags.json - a flag, never an interpretation.
* No alert store: an alert is open while the current hash differs from the hash confirmed in
  data/cb/manual/framework_reviews.yaml, or a flagged doc_id is not listed there as reviewed.
* NEVER writes data/cb/manual/ (mandates, reviews): the update stays manual.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from .cb_compute import framework_watch as FW

log = logging.getLogger("cb_framework")

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "cb_framework.yaml"
STATE_KEY = "_framework"                 # state.json: {checked, targets: {id: {hash, checked, url, error?}}, validators: {url: {...}}}
WATCH_DIR = "framework_watch"
FLAGS_FILE = "keyword_flags.json"
REVIEWS_FILE = "framework_reviews.yaml"


def load_config(path: Path | str | None = None) -> dict:
    return yaml.safe_load(Path(path or CONFIG).read_text(encoding="utf-8"))


def targets(cfg: dict) -> list:
    return [dict(t, bank=bank) for bank, b in cfg["banks"].items() for t in (b.get("watch") or [])]


def watch_dir(paths) -> Path:
    return Path(paths.dir) / WATCH_DIR


def snapshot_path(paths, target_id: str, h: str) -> Path:
    return watch_dir(paths) / target_id / f"{h[:16]}.txt"


def read_snapshot(paths, target_id: str, h: Optional[str]) -> Optional[list]:
    if not h:
        return None
    p = snapshot_path(paths, target_id, h)
    return p.read_text(encoding="utf-8").split("\n") if p.exists() else None


def write_snapshot(paths, target_id: str, h: str, paras: list) -> bool:
    p = snapshot_path(paths, target_id, h)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(paras), encoding="utf-8")
    return True


def load_reviews(paths) -> dict:
    p = Path(paths.manual) / REVIEWS_FILE
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_flags(paths) -> dict:
    p = watch_dir(paths) / FLAGS_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_flags(paths, flags: dict) -> bool:
    p = watch_dir(paths) / FLAGS_FILE
    text = json.dumps(flags, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Page fetch + normalisation
# ---------------------------------------------------------------------------

def page_paragraphs(content: bytes, text: str, ctype: str, url: str, target: dict) -> list:
    """The normalised paragraphs of a fetched page (PDF or HTML container), as the watch hashes them."""
    from .cb_docs import extract as X
    if url.lower().endswith(".pdf") or "pdf" in (ctype or "").lower():
        paras = X.pdf_paragraphs(content)
    else:
        sel = target.get("sel") or ["main", None, None]
        paras = X.html_paragraphs(text, {"sel": tuple(sel)})
    return FW.normalise(paras, select=target.get("select"), drop=target.get("drop"))


@dataclass
class FrameworkReport:
    ran: bool = False
    checked: Optional[date] = None
    rows: list = field(default_factory=list)          # [(id, status, hash12, changed_vs_confirmed)]
    failed: dict = field(default_factory=dict)
    requests: int = 0


def run_framework(paths, today: date, *, force: bool = False, fetcher=None, cfg: dict | None = None) -> FrameworkReport:
    from .cb_collect import load_state, save_state
    from .cb_docs.http import Fetcher
    cfg = cfg or load_config()
    state = load_state(paths)
    fw = dict(state.get(STATE_KEY) or {})
    rep = FrameworkReport(checked=date.fromisoformat(fw["checked"]) if fw.get("checked") else None)
    if not force and rep.checked and (today - rep.checked).days < cfg["meta"]["every_days"]:
        return rep
    rep.ran, rep.checked = True, today
    validators = dict(fw.get("validators") or {})
    f = fetcher or Fetcher(validators=validators)
    cur = dict(fw.get("targets") or {})
    reviews = (load_reviews(paths).get("targets") or {})
    for t in targets(cfg):
        if t.get("manual_only"):
            rep.rows.append((t["id"], "manual_only", "", ""))
            continue
        r = f.get(t["url"])
        if r.error:
            rep.failed[t["id"]] = f"{r.error} {t['url']}"
            rep.rows.append((t["id"], "FAILED", (cur.get(t["id"]) or {}).get("hash", "")[:12], ""))
            continue
        if r.not_modified and (cur.get(t["id"]) or {}).get("hash"):
            h = cur[t["id"]]["hash"]
            status = "not_modified"
        else:
            paras = page_paragraphs(r.content, r.text, r.headers.get("Content-Type", ""), t["url"], t)
            if not paras:
                rep.failed[t["id"]] = f"no text extracted (container {t.get('sel')}) {t['url']}"
                rep.rows.append((t["id"], "FAILED", "", ""))
                continue
            h = FW.text_hash(paras)
            write_snapshot(paths, t["id"], h, paras)
            f.remember(r, content_sha=h)
            old = (cur.get(t["id"]) or {}).get("hash")
            status = "same" if old == h else ("new" if old is None else "changed")
            if old != h:
                cur[t["id"]] = {"hash": h, "since": today.isoformat(), "url": t["url"]}
        conf = (reviews.get(t["id"]) or {}).get("confirmed_hash")
        rep.rows.append((t["id"], status, h[:12], "" if conf == h else "DIFFERS from confirmed"))
    rep.requests = f.requests_made
    fw.update(checked=today.isoformat(), targets=cur, validators=f.validators)
    state = dict(load_state(paths))
    state[STATE_KEY] = fw
    save_state(paths, state)
    return rep


def framework_report(rep: FrameworkReport) -> tuple:
    from .cb_collect import _fmt_text, _md_table
    if not rep.ran:
        line = f"framework watch: last run {rep.checked} (weekly)"
        return line, f"### framework watch\n\n{line}"
    heads = ["target", "status", "hash", "vs confirmed"]
    line = f"framework watch: ran {rep.checked}, {rep.requests} requests, {len(rep.failed)} failed"
    extra = [f"  FAILED {k}: {v}" for k, v in sorted(rep.failed.items())]
    text = line + "\n" + _fmt_text(heads, [list(r) for r in rep.rows]) + ("\n" + "\n".join(extra) if extra else "")
    md = f"### framework watch\n\n{line}\n\n" + _md_table(heads, [list(r) for r in rep.rows]) + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return text, md


# ---------------------------------------------------------------------------
# Keyword flags (called by the 2a collector for every NEW document)
# ---------------------------------------------------------------------------

def doc_scan_text(doc: dict) -> str:
    """What is scanned: title + stored text (statements, opening statements) + the first paragraph kept for speeches."""
    first = ""
    if doc.get("meta_json"):
        try:
            first = (json.loads(doc["meta_json"]) or {}).get("first", "") or ""
        except ValueError:
            first = ""
    return "\n".join(x for x in (doc.get("title") or "", doc.get("text") or "", first) if x)


def flag_documents(docs: list, keywords: list) -> dict:
    """{doc_id: flag} for the documents whose scan text contains a keyword (pure on its inputs)."""
    out = {}
    for d in docs:
        hits = FW.keyword_hits(doc_scan_text(d), keywords)
        if hits:
            out[d["doc_id"]] = {"currency": d["currency"], "type": d["type"], "title": d.get("title") or "", "url": d.get("url") or "",
                                "published": str(d.get("published_date") or ""), "hits": hits}
    return out


def scan_documents(paths, docs: list, cfg: dict | None = None) -> dict:
    """Flag the given (new) documents; returns the new flags. Never raises into the collector."""
    try:
        cfg = cfg or load_config()
        new = flag_documents(docs, cfg.get("keywords") or [])
        if new:
            flags = load_flags(paths)
            flags.update({k: v for k, v in new.items() if k not in flags})
            save_flags(paths, flags)
        return new
    except Exception:                                   # noqa: BLE001 - a flag is a convenience, the collection must go on
        log.exception("keyword scan failed")
        return {}


# ---------------------------------------------------------------------------
# Alerts for --status
# ---------------------------------------------------------------------------

def open_alerts(paths, today: date, *, cfg: dict | None = None, meetings: dict | None = None, mandates: dict | None = None) -> list:
    """Every open alert, with a redline (confirmed snapshot -> current snapshot) for page changes."""
    from .cb_collect import load_state
    from .cb_datasets import load_meetings, load_mandates
    from .cb_docs.parse import redline
    cfg = cfg or load_config()
    rv = load_reviews(paths)
    reviews = rv.get("targets") or {}
    cur = ((load_state(paths).get(STATE_KEY) or {}).get("targets") or {})
    alerts = []
    for a in FW.page_alerts(targets(cfg), cur, reviews):
        old = read_snapshot(paths, a.ref, (reviews.get(a.ref) or {}).get("confirmed_hash"))
        new = read_snapshot(paths, a.ref, cur[a.ref]["hash"])
        rl = redline(old, new) if (old is not None and new is not None) else {}
        alerts.append(FW.Alert(a.kind, a.bank, a.ref, a.url, a.detail, rl))
    alerts += FW.keyword_alerts(load_flags(paths), [x["doc_id"] for x in (rv.get("keyword_flags_reviewed") or [])])
    alerts += FW.calendar_alerts({b: v.get("calendar") or [] for b, v in cfg["banks"].items()}, today, cfg["meta"]["calendar_window_days"])
    meetings = meetings if meetings is not None else load_meetings(paths.meetings)
    mandates = mandates if mandates is not None else load_mandates(paths)
    from .cb_compute.stance_inputs import current_version
    from .cb_datasets import mandate_versions
    cur = {b: current_version(vs, today) for b, vs in mandate_versions(mandates).items()}
    lv = {b: (v.last_verified if v is not None else None) for b, v in cur.items()}
    alerts += FW.verification_alerts(lv, FW.last_projection_dates(meetings, today), today, cfg["meta"]["verification_max_days"])
    nz = reviews.get("NZD.framework") or {}
    nz_url = next((t["url"] for t in targets(cfg) if t["id"] == "NZD.framework"), "")
    from .cb_datasets import load_rbnz, rbnz_votes
    voted = rbnz_votes(load_rbnz(paths.manual / "rbnz.yaml"))
    alerts += FW.rbnz_alerts([(r["date"], r.get("has_projections")) for r in meetings.get("NZD", [])], voted, nz.get("mps_reviewed"), today, nz_url)
    return alerts


def redline_summary(rl: dict, limit: int = 3) -> str:
    if not rl:
        return "no redline (snapshot missing)"
    parts = []
    for x in rl["paras"]:
        if x["kind"] == "same":
            continue
        txt = " ".join(("+[" if o[0] == "+" else "-[") + o[1].strip()[:80] + "]" for o in x["ops"] if o[0] != "=")
        parts.append(f"{x['kind']}: {txt}")
        if len(parts) >= limit:
            break
    return f"+{rl['added_words']} / -{rl['removed_words']} words; " + " | ".join(parts)


def status_section(paths, today: date) -> tuple:
    from .cb_collect import load_state
    alerts = open_alerts(paths, today)
    rows = [[a.kind, a.bank, a.ref, a.url] for a in alerts]
    notes = [f"ALERT {a.kind} {a.bank} {a.ref}: {a.detail}" + (f" | redline: {redline_summary(a.redline)}" if a.kind == "page_changed" else "") for a in alerts]
    fw = load_state(paths).get(STATE_KEY) or {}
    notes.append(f"last weekly watch run: {fw.get('checked') or 'never'}; detection only - mandates.yaml is updated by hand after verification")
    return ("framework watch (stance S0a): open alerts", ["kind", "bank", "ref", "link"], rows, notes)
