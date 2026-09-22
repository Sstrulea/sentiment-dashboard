"""The schedule of the external trigger (phase 4): data/cb/trigger_schedule.json, generated from data/cb/meetings.yaml + config/central_banks.yaml (the official local time
of each decision and conference, DST-aware through the bank's zone) + config/cb_trigger.yaml. Pure functions over those three; the Cloudflare Worker in infra/cb-trigger reads
the file through the GitHub API and dispatches the workflows - it knows no time zone and no bank, only UTC instants.

Events (each with a unique `id` = the dedupe key of the Worker):
    decision:{ccy}:{date}    the statement of a rate decision: due 1 minute after the official time (the job itself watches for it up to 15 minutes)
    decision:JPY:{date}      the BoJ has no fixed time: kind `boj_window` = the UTC window (11:30-13:30 JST) in which the Worker reads the BoJ RSS
    conference:{ccy}:{date}  the press conference: due 2 hours after its start (transcript and video)
plus `regular` (the every-2-hours cb-refresh, the hourly econ-refresh: minute, hours and weekdays in UTC)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "cb_trigger.yaml"
FILE = "trigger_schedule.json"
VERSION = 1


def load_config(path=None) -> dict:
    return yaml.safe_load(Path(path or CONFIG).read_text())


def instant(day: date, hhmm: str, tz: str) -> datetime:
    """The UTC instant of a local wall-clock time on `day` in the zone `tz` (DST decided by the zone's rules on that day)."""
    h, m = hhmm.split(":")
    return datetime.combine(day, time(int(h), int(m)), ZoneInfo(tz)).astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dispatch(workflow: str, event: str, ccy: str, day: date) -> dict:
    return {"workflow": workflow, "inputs": {"event": event, "bank": ccy, "date": day.isoformat()}}


def decision_event(ccy: str, bank: dict, day: date, cfg: dict) -> Optional[dict]:
    """The statement of the decision of `day`: None when the bank announces at no known time and has no window."""
    dt = bank.get("decision_time") or {}
    d = cfg["decision"]
    work = _dispatch(cfg["workflows"]["decision"], "decision", ccy, day)
    if dt.get("local") and not dt.get("variable"):
        at = instant(day, dt["local"], bank["tz"])
        return {"id": f"decision:{ccy}:{day.isoformat()}", "kind": "decision", "bank": ccy, "date": day.isoformat(), "at": iso(at),
                "fire_at": iso(at + timedelta(minutes=d["fire_after_minutes"])), "lead_minutes": d["lead_minutes"], "grace_minutes": d["grace_minutes"], "dispatch": work}
    if dt.get("variable") and dt.get("window_local"):
        start, end = (instant(day, t, bank["tz"]) for t in dt["window_local"])
        return {"id": f"decision:{ccy}:{day.isoformat()}", "kind": "boj_window", "bank": ccy, "date": day.isoformat(), "window_start": iso(start), "window_end": iso(end),
                "fallback_at_window_end": bool(cfg["boj"].get("fallback_at_window_end")), "grace_minutes": cfg["boj"]["grace_minutes"], "dispatch": work}
    return None


def conference_event(ccy: str, bank: dict, day: date, cfg: dict) -> Optional[dict]:
    """The press conference of the decision of `day`. A bank whose config gives no conference time (the BoE) is taken from the decision time: the dispatch is two hours later anyway."""
    conf, dt = bank.get("conference") or {}, bank.get("decision_time") or {}
    local, basis = conf.get("local"), "conference"
    if not local:
        local, basis = (dt.get("local") if not dt.get("variable") else (dt.get("window_local") or [None, None])[1]), "decision"
    if not local:
        return None
    c = cfg["conference"]
    at = instant(day, local, bank["tz"])
    return {"id": f"conference:{ccy}:{day.isoformat()}", "kind": "conference", "bank": ccy, "date": day.isoformat(), "at": iso(at), "at_basis": basis,
            "fire_at": iso(at + timedelta(minutes=c["fire_after_minutes"])), "lead_minutes": c["lead_minutes"], "grace_minutes": c["grace_minutes"],
            "dispatch": _dispatch(cfg["workflows"]["conference"], "conference", ccy, day)}


def regular_entries(cfg: dict) -> list:
    return [{"workflow": r["workflow"], "minute": r["minute"], "hours": list(range(0, 24, r["every_hours"])), "dow": list(r["dow"])} for r in cfg["regular"]]


def build(meetings: dict, banks: dict, cfg: dict, today: date) -> dict:
    """The schedule for the meetings from `back_days` before `today` to `ahead_days` after it. Deterministic: the same inputs give the same bytes."""
    lo, hi = today - timedelta(days=cfg["horizon"]["back_days"]), today + timedelta(days=cfg["horizon"]["ahead_days"])
    events = []
    for ccy in sorted(meetings):
        if ccy in (cfg.get("skip_banks") or ()) or ccy not in banks:
            continue
        for m in meetings[ccy]:
            day = m["date"]
            if not lo <= day <= hi:
                continue
            ev = decision_event(ccy, banks[ccy], day, cfg)
            if ev:
                events.append(ev)
            if m.get("has_presser"):
                ev = conference_event(ccy, banks[ccy], day, cfg)
                if ev:
                    events.append(ev)
    events.sort(key=lambda e: (e.get("fire_at") or e["window_start"], e["id"]))
    return {"version": VERSION, "horizon": {"from": lo.isoformat(), "to": hi.isoformat()}, "tick_minutes": cfg["tick_minutes"],
            "boj": {"rss": cfg["boj"]["rss"], "item_match": cfg["boj"]["item_match"]}, "events": events, "regular": regular_entries(cfg)}


def dump(obj: dict) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass
class ScheduleReport:
    events: int = 0
    by_kind: dict = field(default_factory=dict)
    written: bool = False
    next_events: list = field(default_factory=list)              # [(id, when)] the next few events from `today`


def run_schedule(paths, today: date, *, cfg: Optional[dict] = None, banks: Optional[dict] = None, meetings: Optional[dict] = None) -> ScheduleReport:
    """Stage `schedule` of cb_collect: writes data/cb/trigger_schedule.json when its bytes change (an idle run leaves no diff)."""
    from .. import cb_datasets as ds
    cfg = cfg or load_config()
    sched = build(meetings if meetings is not None else ds.load_meetings(paths.meetings), banks if banks is not None else ds.load_banks(), cfg, today)
    text = dump(sched)
    target = Path(paths.dir) / FILE
    rep = ScheduleReport(events=len(sched["events"]))
    for e in sched["events"]:
        rep.by_kind[e["kind"]] = rep.by_kind.get(e["kind"], 0) + 1
    now = iso(datetime.combine(today, time(0, 0), timezone.utc))
    rep.next_events = [(e["id"], e.get("fire_at") or e["window_start"]) for e in sched["events"] if (e.get("fire_at") or e["window_end"]) >= now][:6]
    if not target.exists() or target.read_text() != text:
        target.write_text(text)
        rep.written = True
    return rep


def report(rep: ScheduleReport) -> tuple:
    kinds = ", ".join(f"{n} {k}" for k, n in sorted(rep.by_kind.items())) or "none"
    text = f"trigger schedule: {rep.events} events ({kinds}); {'written' if rep.written else 'unchanged'}"
    if rep.next_events:
        text += "\n  next: " + "; ".join(f"{i} at {w}" for i, w in rep.next_events[:3])
    return text, "### trigger schedule\n\n" + text.replace("\n  ", "\n\n")
