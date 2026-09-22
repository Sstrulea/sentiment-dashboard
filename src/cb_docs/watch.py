"""The decision watch (phase 4): the job the external trigger dispatches a minute after a rate decision. It looks for the statement of that decision - the derivable URL
(or the ECB / RBA discovery page, or the BoJ RSS) - every `poll_seconds` for at most `watch_minutes`, stores it the moment it appears (`first_seen_at` = the poll that saw it,
the start of the decision -> site latency) and returns; a statement that does not appear in time is not an error: the job exits clean and the regular runs pick it up.

Polite as the rest of the collector (http.py): one Fetcher for the whole watch (robots.txt read once, 2 s per host, conditional GET), no other request than the bank's own
statement page and the one discovery source it needs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Optional

from . import store as ST
from .collect import STATE_KEY, _Run, load_roster
from .http import Fetcher


@dataclass
class WatchReport:
    ccy: str
    day: date
    found: bool = False
    stored_before: bool = False                            # the statement was already stored when the watch began (a regular run was first)
    polls: int = 0
    started_at: Optional[datetime] = None
    first_seen_at: Optional[datetime] = None
    published_at: Optional[datetime] = None                # the time the bank's own feed gives (ECB, BoJ), when there is one
    url: str = ""
    errors: list = field(default_factory=list)            # the last poll's failures (a page that is not there yet is not news)
    new: int = 0
    rate_rows_changed: bool = False


def doc_id(ccy: str, day: date) -> str:
    return f"{ccy}:statement:{day.isoformat()}"


def watch_statement(paths, ccy: str, day: date, *, minutes: float = 15, poll_seconds: float = 60, fetcher: Optional[Fetcher] = None, roster: Optional[dict] = None,
                    state: Optional[dict] = None, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                    clock: Optional[Callable[[], datetime]] = None) -> WatchReport:
    """Polls until the statement of `ccy` for the decision of `day` is stored, or `minutes` have passed. Writes the documents partition and state.json only when it found something."""
    from ..cb_collect import load_state, save_state
    clock = clock or (lambda: datetime.now(timezone.utc))
    state = state if state is not None else load_state(paths)
    f = fetcher or Fetcher(validators=state.setdefault(STATE_KEY, {}))
    roster = roster if roster is not None else load_roster()
    rep = WatchReport(ccy, day, started_at=clock().replace(microsecond=0))
    deadline = monotonic() + minutes * 60
    key = (doc_id(ccy, day),)
    while True:
        rep.polls += 1
        now = clock()
        run = _Run(paths, now.date(), f, now, 4, (ccy,), roster, state)
        if rep.polls == 1:
            old = run.docs.get(key)
            rep.stored_before = bool(old and old["text"])
        run.statement(ccy, day, run.discover_for(ccy))
        row = run.docs.get(key)
        rep.errors = [f"{what}: {why}" for what, why in run.rep.failed]
        if row is not None and row["text"]:
            rep.found, rep.url = True, row["url"]
            rep.first_seen_at, rep.published_at = row["first_seen_at"], row["published_at"]
            rep.new, rep.rate_rows_changed = run.rep.new + run.rep.updated, run.rep.rate_rows_changed
            if run.rep.new or run.rep.updated:
                ST.write_documents(paths, run.docs)
                if f.validators is not state.get(STATE_KEY):
                    state[STATE_KEY] = f.validators
                save_state(paths, state)
            return rep
        if monotonic() + poll_seconds >= deadline:
            return rep
        sleep(poll_seconds)


def report(rep: WatchReport, minutes: float) -> tuple:
    head = f"decision watch {rep.ccy} {rep.day}: "
    if rep.found:
        lag = ""
        if rep.published_at is not None and rep.first_seen_at is not None:
            lag = f", {(rep.first_seen_at - rep.published_at).total_seconds() / 60:.1f} min after the bank's own timestamp"
        what = "already stored when the watch began" if rep.stored_before else f"statement stored (first seen {rep.first_seen_at:%H:%M:%S}Z{lag})"
        text = head + f"{what}; {rep.polls} poll{'s' if rep.polls != 1 else ''}; {rep.url}"
    else:
        text = head + f"statement not found in {minutes:g} minutes ({rep.polls} polls); exiting clean - the regular runs will pick it up"
        if rep.errors:
            text += "\n  last poll: " + "; ".join(rep.errors[:3])
    return text, "### decision watch\n\n" + text.replace("\n  ", "\n\n")
