"""The measured latency of the site behind the decisions (phase 4): for each decision, `first_seen_at` of its statement (the moment the collector stored it, UTC) minus the official
time of the decision - the local time of config/central_banks.yaml through the bank's zone, so DST is the zone's; for a bank with no fixed time (the BoJ) the time its own feed
gave (`published_at`), and none when there is not one. Target: <= 15 minutes (config/cb_trigger.yaml `latency`). Used by `--status` and by the methodology panel of the bank pages."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from .schedule import instant, iso, load_config


def official_at(bank: dict, day: date, published_at: Optional[datetime] = None) -> tuple:
    """(instant, basis): the configured decision time when it is fixed ("config"), else the bank's own timestamp ("feed"), else (None, "n/a")."""
    dt = bank.get("decision_time") or {}
    if dt.get("local") and not dt.get("variable"):
        return instant(day, dt["local"], bank["tz"]), "config"
    if published_at is not None:
        return published_at.astimezone(timezone.utc), "feed"
    return None, "n/a"


def rows(documents: list, banks: dict, cfg: Optional[dict] = None, *, ccys: Optional[list] = None) -> list:
    """One row per stored statement, newest first, `rows_per_bank` per bank: {ccy, meeting, official_at, basis, first_seen_at, minutes, ok, measured}.
    `ok` = within the target (None when the official time is unknown); `measured` = the trigger was live (first seen on or after `measure_from`, when one is set)."""
    cfg = cfg or load_config()
    lat = cfg["latency"]
    since = lat.get("measure_from")
    since = date.fromisoformat(str(since)) if since else None
    out = []
    for ccy in ccys or [c for c in banks if c not in (cfg.get("skip_banks") or ())]:
        mine = sorted((d for d in documents if d["currency"] == ccy and d["type"] == "statement" and d.get("first_seen_at")), key=lambda d: d["published_date"], reverse=True)
        for d in mine[:lat["rows_per_bank"]]:
            at, basis = official_at(banks[ccy], d["published_date"], d.get("published_at"))
            seen = d["first_seen_at"].astimezone(timezone.utc)
            minutes = None if at is None else round((seen - at).total_seconds() / 60, 1)
            out.append({"ccy": ccy, "meeting": d["published_date"].isoformat(), "official_at": None if at is None else iso(at), "basis": basis, "first_seen_at": iso(seen),
                        "minutes": minutes, "ok": None if minutes is None else minutes <= lat["target_minutes"], "measured": since is None or seen.date() >= since})
    return out


def summary(rs: list, cfg: Optional[dict] = None) -> dict:
    """{target_minutes, n, within, median_minutes, max_minutes} over the measured rows that have an official time."""
    cfg = cfg or load_config()
    vals = sorted(r["minutes"] for r in rs if r["measured"] and r["minutes"] is not None)
    med = None if not vals else (vals[len(vals) // 2] if len(vals) % 2 else round((vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2, 1))
    return {"target_minutes": cfg["latency"]["target_minutes"], "n": len(vals), "within": sum(v <= cfg["latency"]["target_minutes"] for v in vals),
            "median_minutes": med, "max_minutes": vals[-1] if vals else None, "measure_from": str(cfg["latency"]["measure_from"]) if cfg["latency"].get("measure_from") else None}


def status_section(documents: list, banks: dict, cfg: Optional[dict] = None) -> tuple:
    """(heads, rows, notes) for `--status`."""
    cfg = cfg or load_config()
    rs = rows(documents, banks, cfg)
    s = summary(rs, cfg)
    table = [[r["ccy"], r["meeting"], (r["official_at"] or "n/a").replace("T", " ").rstrip("Z"), r["first_seen_at"].replace("T", " ").rstrip("Z"),
              "" if r["minutes"] is None else r["minutes"], {True: "yes", False: "NO", None: ""}[r["ok"]], "" if r["measured"] else "before the trigger"] for r in rs]
    notes = [f"target: first seen within {s['target_minutes']} min of the official time; {s['within']} of {s['n']} measured decisions within it"
             + ("" if s["median_minutes"] is None else f" (median {s['median_minutes']} min, worst {s['max_minutes']} min)")
             + ("" if not s["measure_from"] else f"; decisions before {s['measure_from']} are shown, not counted"),
             "official time = the decision time of config/central_banks.yaml in the bank's zone (the BoJ: the time of its RSS item); first seen = when the collector stored the statement"]
    return ["bank", "meeting", "official (UTC)", "first seen (UTC)", "min", f"<= {s['target_minutes']}", "note"], table, notes


def payload(documents: list, banks: dict, ccy: str, cfg: Optional[dict] = None) -> dict:
    """The methodology panel block of a bank page: every label is here, the page has none of its own."""
    cfg = cfg or load_config()
    rs = rows(documents, banks, cfg, ccys=[ccy])
    s = summary(rs, cfg)
    return {"title": "Time from decision to site", "rows": rs, "summary": s,
            "text": f"First seen = the moment the collector stored the statement; official time = the decision time in the bank's zone (the BoJ announces at a variable time: its own RSS timestamp). "
                    f"Target: within {s['target_minutes']} minutes. An external trigger starts the collector at the decision; before it existed the statements were found by the regular runs."}
