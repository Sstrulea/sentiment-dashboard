"""The summaries stage: which documents, in which order, under which caps; one call, one automatic check, at most one retry with the errors as feedback;
a summary is written only after it passed, a summary that fails twice is never written (a `validation_failed` marker is).

Idempotence: a document is summarised once per (doc_id, input_sha256, prompt_version). Statements carry their text and hash in the store; the hash of a
downloaded document (minutes, transcript, speech) is known once it has been downloaded, so a stored record of the same prompt version stops any new call.
Everything the run needs from outside (HTTP, the model) is passed in: tests give it a recorded client and a fake session."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .. import cb_datasets as ds
from ..cb_docs import sources as S
from ..cb_docs import store as dst
from ..cb_docs.http import Fetcher
from . import changes as CH
from . import prompts as PR
from . import source as SRC
from . import store as SS
from . import verify as V
from .client import APIError, AnthropicClient, RecordedClient
from .config import Config, load as load_config

STATE_KEY = "summaries"


@dataclass
class SummariesReport:
    enabled: bool = True
    skipped_reason: Optional[str] = None
    considered: int = 0
    new: int = 0
    unchanged: int = 0                                     # already summarised under this prompt version (or a known validation failure): no call
    failed_validation: list = field(default_factory=list)  # (doc_id, [errors]) - written as markers, never as summaries
    source_errors: list = field(default_factory=list)      # (doc_id, why) - the document could not be downloaded / extracted; tried again next run
    stopped: Optional[str] = None                          # why the run stopped before the end (caps, API)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    pending: int = 0                                       # documents still without a summary when the run ended
    notes: list = field(default_factory=list)


def candidates(docs: list, meetings: dict, today: date, cfg: Config) -> list:
    """The documents that may be summarised, most important first: the decision statement, then the press-conference texts, then minutes / accounts /
    opinions / deliberations, then speeches and testimony that passed the monetary-relevance filter; the last N decided meetings per bank; newest first."""
    last = {ccy: set(SRC.meeting_dates(meetings, ccy, today, cfg.backfill_meetings)) for ccy in meetings}
    out = []
    for d in docs:
        t = d["type"]
        if t not in cfg.priority:
            continue
        if t in ("speech", "testimony"):
            if d.get("relevance") != "monetary" or (today - d["published_date"]).days > cfg.speech_window_days:
                continue
        elif d["meeting_date"] not in last.get(d["currency"], ()):
            continue
        out.append(d)
    return sorted(out, key=lambda d: (cfg.priority[d["type"]], -d["published_date"].toordinal(), d["currency"], d["doc_id"]))


def is_done(rec: Optional[dict], doc: dict, prompt: PR.Prompt) -> bool:
    """Known to be summarised without downloading anything: same prompt version, and the hash the store has for the document (statements, and the long
    documents phase 2a hashed) equals the one summarised. A document without a stored hash is taken as immutable once summarised."""
    if rec is None or rec["prompt_version"] != prompt.version:
        return False
    expected = doc.get("text_sha256")
    return expected is None or rec["input_sha256"] == expected


def failed_before(failures: dict, doc: dict, prompt: PR.Prompt, sha: Optional[str]) -> bool:
    f = failures.get(f"{doc['doc_id']}|{prompt.version}")
    return f is not None and (sha is None or f["input_sha256"] == sha)


def _record(doc: dict, prompt: PR.Prompt, src: SRC.Source, ok: V.Verified, changes: Optional[dict], usage: dict, cfg: Config, now: datetime) -> dict:
    return {
        "doc_id": doc["doc_id"], "currency": doc["currency"], "type": doc["type"], "title": doc["title"], "url": doc["url"], "format": doc["format"],
        "meeting_date": doc["meeting_date"].isoformat() if doc["meeting_date"] else None, "published": doc["published_date"].isoformat(),
        "model": cfg.model, "prompt_version": prompt.version, "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input_sha256": src.sha256,
        "source": {"method": src.method, "chars": src.chars_total, "chars_sent": src.chars_sent, "paragraphs_sent": len(src.paragraphs), "truncated": src.truncated},
        "summary": ok.summary, "quotes": ok.quotes, "changes_vs_previous": changes, "numbers": ok.numbers,
        "coverage": {"paragraphs": ok.coverage, "paragraphs_sent": len(src.paragraphs), "truncated": src.truncated},
        "usage": usage,
    }


def summarise(client, cfg: Config, prompt: PR.Prompt, doc: dict, src: SRC.Source) -> tuple:
    """One document: (Verified, usage, errors_of_the_last_attempt). At most two model calls: the second only with the errors of the first as feedback."""
    user = PR.user_message(doc["type"], S.BANK_NAME[doc["currency"]], doc["url"], src.paragraphs)
    messages = [{"role": "user", "content": user}]
    usage = {"input_tokens": 0, "output_tokens": 0, "attempts": 0}
    errors: list = []
    for attempt in (1, 2):
        resp = client.complete(prompt.system, messages)
        usage["input_tokens"] += resp.input_tokens
        usage["output_tokens"] += resp.output_tokens
        usage["attempts"] = attempt
        obj, errors = V.parse_output(resp.text)
        if obj is not None:
            res = V.verify(obj, src.paragraphs, points=cfg.summary_points, point_chars=cfg.point_chars, quotes=cfg.quotes, quote_chars=cfg.quote_chars,
                           total_chars=cfg.summary_total_chars, blocked=cfg.blocked_words)
            if res.ok:
                return res, usage, []
            errors = res.errors
        if attempt == 1:
            messages = [messages[0], {"role": "assistant", "content": resp.text or "(empty)"}, {"role": "user", "content": PR.feedback_message(errors)}]
    return None, usage, errors


def run_summaries(paths, today: date, *, client=None, fetcher: Optional[Fetcher] = None, env=None, now: Optional[datetime] = None, cfg: Optional[Config] = None,
                  state: Optional[dict] = None, banks: Optional[set] = None, types: Optional[set] = None, only: Optional[set] = None) -> SummariesReport:
    from ..cb_collect import load_state, save_state
    cfg = cfg or load_config()
    env = os.environ if env is None else env
    rep = SummariesReport()
    if client is None:
        key = env.get(cfg.env_key)
        if not key:
            rep.enabled, rep.skipped_reason = False, f"{cfg.env_key} is not set: the stage is skipped (no summary is generated or changed)"
            return rep
        client = AnthropicClient(cfg, key)
    now = now or datetime.now(timezone.utc)
    docs = sorted(dst.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    meetings = ds.load_meetings(paths.meetings)
    redlines = {(r["currency"], r["meeting_date"]): r for r in dst.load_redlines(paths)}
    records = SS.load(paths.summaries)
    current = {PR.load(k).version for k in PR.KINDS}
    failures = {k: f for k, f in SS.load_failures(paths.summaries).items() if f["prompt_version"] in current}     # a failure of an older prompt version is history, not a status
    fetcher = fetcher or Fetcher()
    todo = [d for d in candidates(docs, meetings, today, cfg)                # optional narrowing (--summaries-bank / --summaries-type / a doc_id list): never widens
            if (not banks or d["currency"] in banks) and (not types or d["type"] in types) and (not only or d["doc_id"] in only)]
    rep.considered = len(todo)
    attempted = 0

    def save() -> None:
        SS.write(paths.summaries, records)
        SS.write_failures(paths.summaries, failures)

    for doc in todo:
        prompt = PR.for_type(doc["type"])
        rec = records.get(doc["doc_id"])
        if is_done(rec, doc, prompt) or failed_before(failures, doc, prompt, doc.get("text_sha256")):
            rep.unchanged += 1
            continue
        if attempted >= cfg.max_documents:
            rep.stopped = f"document cap reached ({cfg.max_documents} per run)"
            break
        try:
            src = SRC.load(doc, fetcher, cfg.max_source_chars)
        except SRC.SourceError as e:
            rep.source_errors.append((doc["doc_id"], str(e)))
            continue
        if (rec is not None and rec["prompt_version"] == prompt.version and rec["input_sha256"] == src.sha256) or failed_before(failures, doc, prompt, src.sha256):
            rep.unchanged += 1                                             # the downloaded text is the one already summarised (or already failed twice): no call
            continue
        est = (len(prompt.system) + sum(len(p) + 8 for p in src.paragraphs)) // cfg.chars_per_token
        if rep.input_tokens + est > cfg.max_input_tokens:
            rep.stopped = f"input token budget reached ({rep.input_tokens} used, {cfg.max_input_tokens} allowed per run)"
            break
        attempted += 1
        try:
            ok, usage, errors = summarise(client, cfg, prompt, doc, src)
        except APIError as e:
            rep.stopped = f"the API stopped the run ({e.kind}): {e.message[:160]}"
            break
        rep.calls += usage["attempts"]
        rep.input_tokens += usage["input_tokens"]
        rep.output_tokens += usage["output_tokens"]
        if ok is None:
            failures[f"{doc['doc_id']}|{prompt.version}"] = {"doc_id": doc["doc_id"], "prompt_version": prompt.version, "input_sha256": src.sha256, "errors": errors[:6],
                                                             "at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z")}
            rep.failed_validation.append((doc["doc_id"], errors))
            save()
            continue
        for k in [k for k in failures if k.startswith(doc["doc_id"] + "|")]:
            del failures[k]                                                # it has a valid summary now
        chg = CH.from_redline(redlines[(doc["currency"], doc["meeting_date"])]) if doc["type"] == "statement" and (doc["currency"], doc["meeting_date"]) in redlines else None
        records[doc["doc_id"]] = _record(doc, prompt, src, ok, chg, usage, cfg, now)
        rep.new += 1
        save()
    rep.cost_usd = cfg.cost_usd(rep.input_tokens, rep.output_tokens)
    rep.pending = sum(1 for d in todo if not is_done(records.get(d["doc_id"]), d, PR.for_type(d["type"])) and not failed_before(failures, d, PR.for_type(d["type"]), d.get("text_sha256")))
    save()
    state = state if state is not None else load_state(paths)
    s = state.setdefault(STATE_KEY, {"totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "summaries": 0}})
    s["last_run"] = {"at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "new": rep.new, "unchanged": rep.unchanged, "failed_validation": len(rep.failed_validation),
                     "source_errors": len(rep.source_errors), "calls": rep.calls, "input_tokens": rep.input_tokens, "output_tokens": rep.output_tokens,
                     "cost_usd": rep.cost_usd, "stopped": rep.stopped, "pending": rep.pending}
    for k, v in (("calls", rep.calls), ("input_tokens", rep.input_tokens), ("output_tokens", rep.output_tokens), ("summaries", rep.new)):
        s["totals"][k] = s["totals"].get(k, 0) + v
    save_state(paths, state)
    return rep


@dataclass
class Estimate:
    documents: list = field(default_factory=list)           # (doc_id, type, chars_sent, input_tokens) of every document still to summarise
    source_errors: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    runs: int = 0                                           # how many capped runs the backlog needs


def estimate(paths, today: date, *, fetcher: Optional[Fetcher] = None, cfg: Optional[Config] = None, banks: Optional[set] = None, types: Optional[set] = None) -> Estimate:
    """What summarising everything still pending would cost, WITHOUT calling the model: the pending documents are downloaded (politely) and measured;
    input = system prompt + the numbered paragraphs at `chars_per_token`, output = `estimate_output_tokens` per document; prices from the config (assumptions)."""
    cfg = cfg or load_config()
    docs = sorted(dst.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    records = SS.load(paths.summaries)
    current = {PR.load(k).version for k in PR.KINDS}
    failures = {k: f for k, f in SS.load_failures(paths.summaries).items() if f["prompt_version"] in current}
    fetcher = fetcher or Fetcher()
    est = Estimate()
    for doc in candidates(docs, ds.load_meetings(paths.meetings), today, cfg):
        if (banks and doc["currency"] not in banks) or (types and doc["type"] not in types):
            continue
        prompt = PR.for_type(doc["type"])
        if is_done(records.get(doc["doc_id"]), doc, prompt) or failed_before(failures, doc, prompt, doc.get("text_sha256")):
            continue
        try:
            src = SRC.load(doc, fetcher, cfg.max_source_chars)
        except SRC.SourceError as e:
            est.source_errors.append((doc["doc_id"], str(e)))
            continue
        tokens = (len(prompt.system) + sum(len(p) + 8 for p in src.paragraphs)) // cfg.chars_per_token
        est.documents.append((doc["doc_id"], doc["type"], src.chars_sent, tokens))
        est.input_tokens += tokens
        est.output_tokens += cfg.estimate_output_tokens
    est.cost_usd = cfg.cost_usd(est.input_tokens, est.output_tokens)
    est.runs = -(-len(est.documents) // cfg.max_documents) if est.documents else 0
    return est


def estimate_report(est: Estimate, cfg: Optional[Config] = None) -> str:
    cfg = cfg or load_config()
    by: dict = {}
    for _id, typ, chars, tok in est.documents:
        b = by.setdefault(typ, [0, 0, 0])
        b[0] += 1
        b[1] += chars
        b[2] += tok
    lines = [f"summaries dry run (no call): {len(est.documents)} documents still to summarise, {est.runs} runs at {cfg.max_documents} documents per run"]
    lines += [f"  {t:22s} {n:3d} documents  {chars:9d} chars  ~{tok:8d} input tokens" for t, (n, chars, tok) in sorted(by.items())]
    lines.append(f"  total input ~{est.input_tokens} tokens, output ~{est.output_tokens} tokens ({cfg.estimate_output_tokens} each): about ${est.cost_usd:.2f} at "
                 f"${cfg.price_input}/${cfg.price_output} per million tokens (assumed prices)")
    lines += [f"  SOURCE {d}: {why}" for d, why in est.source_errors]
    return "\n".join(lines)
