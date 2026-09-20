"""Official rate series for the Central Banks module (phase 1B-1): policy rates, overnight benchmarks, BIS policy
rates. One adapter per provider (FRED, ECB, BoE, BoJ, BoC, RBA, BIS, SNB); the series come from
config/cb_official.yaml. Same contract as the market adapters: HTTP via BaseSource, never raises - `fetch` returns an
`OfficialResult` or None with `last_status` / `last_note`; a provider that fails for one series still returns the
others (the failure is listed in `failed`). Values are stored as published, in percent, dated by their own date.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from ..rate_sources import _safe_float
from .base import HttpSource, NON_BROWSER_UA

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_YAML = ROOT / "config" / "cb_official.yaml"


@dataclass(frozen=True)
class Obs:
    series_id: str
    currency: str
    date: date
    value: float
    unit: str
    fetched_at: datetime


@dataclass
class OfficialResult:
    status: str                                    # ok | not_modified
    obs: list = field(default_factory=list)
    state: dict = field(default_factory=dict)      # validators (RBA)
    note: str = ""
    failed: dict = field(default_factory=dict)     # series_id -> "STATUS note"


def load_official(path: Path | str | None = None) -> dict:
    return yaml.safe_load(Path(path or OFFICIAL_YAML).read_text())


def obs_order(o: Obs):
    return (o.series_id, o.date)


class OfficialSource(HttpSource):
    """Base: `provider` selects the series of config/cb_official.yaml handled by this adapter."""
    provider = ""

    def __init__(self, cfg: dict | None = None, series: dict | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        super().__init__(now)
        full = None
        if cfg is None or series is None:
            full = load_official()
        self.cfg = cfg if cfg is not None else full["providers"][self.provider]
        self.series = series if series is not None else {
            sid: s for sid, s in full["series"].items() if s["provider"] == self.provider}
        self.name = f"official:{self.provider}"
        if self.cfg.get("ua") == "non_browser":
            self.ua = NON_BROWSER_UA

    # ---- contract -------------------------------------------------------------------------
    def fetch(self, since: date | None = None, state: dict | None = None) -> Optional[OfficialResult]:
        self.last_status = ""
        self.last_note = ""
        try:
            res = self._fetch(since or (self._now().date() - timedelta(days=10)), state or {})
        except Exception as e:                       # never propagate
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None
        if res is not None:
            res.obs.sort(key=obs_order)
        return res

    def _fetch(self, since: date, state: dict) -> Optional[OfficialResult]:      # overridden
        return None

    # ---- helpers --------------------------------------------------------------------------
    def obs(self, sid: str, d: date, v: float) -> Obs:
        s = self.series[sid]
        return Obs(sid, s["currency"], d, float(v), s.get("unit", "percent"), self._now())

    def _finish(self, out: list, failed: dict, state: dict | None = None) -> Optional[OfficialResult]:
        if not out and failed:
            self.last_status = "UNREACHABLE"
            self.last_note = "; ".join(f"{k}: {v}" for k, v in failed.items())
            return None
        note = ("failed: " + "; ".join(f"{k} ({v})" for k, v in failed.items())) if failed else ""
        return OfficialResult("ok", out, state or {}, note, failed)

    def _csv_get(self, url: str, validators: dict | None = None):
        r, nm, v = self.get_url(url, validators)
        if r is None or self._check_botwall(r):
            return None, nm, v
        return r, nm, v

    def _err(self) -> str:
        return f"{self.last_status} {self.last_note}".strip()


# ---------------------------------------------------------------------------
# Pure parsers (fixtures -> deterministic rows)
# ---------------------------------------------------------------------------

def _num(x) -> Optional[float]:
    return _safe_float(x)


def parse_fred(text: str, since: date) -> list:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    out = []
    for r in rows[1:]:
        if len(r) < 2 or not re.match(r"\d{4}-\d{2}-\d{2}$", r[0]):
            continue
        d, v = date.fromisoformat(r[0]), _num(r[1])            # "." (no observation) -> None
        if v is not None and d >= since:
            out.append((d, v))
    return out


def parse_ecb(text: str, since: date) -> list:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        t, v = r.get("TIME_PERIOD", ""), _num(r.get("OBS_VALUE"))
        if re.match(r"\d{4}-\d{2}-\d{2}$", t) and v is not None and date.fromisoformat(t) >= since:
            out.append((date.fromisoformat(t), v))
    return out


def parse_boe(text: str, since: date) -> dict:
    """{code: [(date, value)]} from the IADB CSV (`DATE,IUDBEDR,IUDSOIA`, dates like '16 Sep 2026')."""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    out: dict = {c: [] for c in (rd.fieldnames or []) if c and c != "DATE"}
    for r in rd:
        try:
            d = datetime.strptime(r["DATE"].strip(), "%d %b %Y").date()
        except (KeyError, ValueError):
            continue
        for c in out:
            v = _num(r.get(c))
            if v is not None and d >= since:
                out[c].append((d, v))
    return out


def parse_boj(payload: dict, since: date) -> list:
    rs = payload.get("RESULTSET") or []
    if not rs:
        return []
    vals = rs[0]["VALUES"]
    out = []
    for sd, v in zip(vals["SURVEY_DATES"], vals["VALUES"]):
        d, x = datetime.strptime(str(sd), "%Y%m%d").date(), _num(v)
        if x is not None and d >= since:
            out.append((d, x))
    return out


def parse_valet(payload: dict, keys: list, since: date) -> dict:
    out: dict = {k: [] for k in keys}
    for o in payload.get("observations") or []:
        d = date.fromisoformat(o["d"])
        for k in keys:
            v = _num((o.get(k) or {}).get("v"))
            if v is not None and d >= since:
                out[k].append((d, v))
    return out


def parse_rba_f1(text: str, keys: list, since: date) -> dict:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    sid_i = next((i for i, x in enumerate(rows) if x and x[0] == "Series ID"), None)
    if sid_i is None:
        raise ValueError("no 'Series ID' row in F1")
    col = {sid: j for j, sid in enumerate(rows[sid_i]) if sid}
    miss = [k for k in keys if k not in col]
    if miss:
        raise ValueError(f"F1 ids missing: {miss}")
    out: dict = {k: [] for k in keys}
    for x in rows[sid_i + 1:]:
        if not x or not re.match(r"\d{2}-[A-Za-z]{3}-\d{4}$", x[0]):
            continue
        d = datetime.strptime(x[0], "%d-%b-%Y").date()
        if d < since:
            continue
        for k in keys:
            v = _num(x[col[k]]) if len(x) > col[k] else None
            if v is not None:
                out[k].append((d, v))
    return out


def parse_bis(text: str, since: date) -> dict:
    """{area: [(date, value)]} from WS_CBPOL csv."""
    out: dict = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        t, v = r.get("TIME_PERIOD", ""), _num(r.get("OBS_VALUE"))
        if re.match(r"\d{4}-\d{2}-\d{2}$", t) and v is not None and date.fromisoformat(t) >= since:
            out.setdefault(r["REF_AREA"], []).append((date.fromisoformat(t), v))
    return out


def parse_snb(text: str, keys: list, since: date) -> dict:
    """{D0 key: [(date, value)]} from the cube csv (`;` separated, metadata lines before the `"Date"` header)."""
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip()]
    hi = next((i for i, ln in enumerate(lines) if ln.startswith('"Date"')), None)
    if hi is None:
        raise ValueError("no Date header in the SNB cube")
    out: dict = {k: [] for k in keys}
    for r in csv.DictReader(io.StringIO("\n".join(lines[hi:])), delimiter=";"):
        k, v = r.get("D0"), _num(r.get("Value"))                # "0" is a value: SNB policy rate 0.00
        if k in out and v is not None and re.match(r"\d{4}-\d{2}-\d{2}$", r["Date"]) and date.fromisoformat(r["Date"]) >= since:
            out[k].append((date.fromisoformat(r["Date"]), v))
    return out


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

class _PerSeries(OfficialSource):
    """One request per series."""

    def url_for(self, s: dict, since: date) -> str:
        raise NotImplementedError

    def parse(self, r, s: dict, since: date) -> list:
        raise NotImplementedError

    def _fetch(self, since, state):
        out, failed = [], {}
        for sid, s in self.series.items():
            r, _, _ = self._csv_get(self.url_for(s, since))
            if r is None:
                failed[sid] = self._err()
                continue
            try:
                out += [self.obs(sid, d, v) for d, v in self.parse(r, s, since)]
            except Exception as e:                    # a bad series must not take the provider down
                failed[sid] = f"PARSE-FAIL {type(e).__name__}: {e}"
        return self._finish(out, failed)


class FredSeries(_PerSeries):
    provider = "fred"

    def url_for(self, s, since):
        return self.cfg["url"].format(key=s["key"], since=since.isoformat())

    def parse(self, r, s, since):
        return parse_fred(r.text, since)


class EcbSeries(_PerSeries):
    provider = "ecb"

    def url_for(self, s, since):
        return self.cfg["url"].format(key=s["key"], since=since.isoformat())

    def parse(self, r, s, since):
        return parse_ecb(r.text, since)


class BojSeries(_PerSeries):
    provider = "boj"

    def url_for(self, s, since):
        return self.cfg["url"].format(db=s["db"], code=s["code"], since_ym=since.strftime("%Y%m"))

    def parse(self, r, s, since):
        return parse_boj(r.json(), since)

    def _csv_get(self, url, validators=None):                # JSON, not CSV: no bot-wall sniffing
        r, nm, v = self.get_url(url, validators)
        return r, nm, v


class BoeSeries(OfficialSource):
    provider = "boe"

    def _fetch(self, since, state):
        codes = {s["key"]: sid for sid, s in self.series.items()}
        url = self.cfg["url"].format(key=",".join(codes), since_dmy=since.strftime("%d/%b/%Y"))
        r, _, _ = self._csv_get(url)
        if r is None:
            return self._finish([], {sid: self._err() for sid in self.series})
        got = parse_boe(r.text, since)
        out, failed = [], {}
        for code, sid in codes.items():
            if code not in got:
                failed[sid] = "column missing"
            out += [self.obs(sid, d, v) for d, v in got.get(code, [])]
        return self._finish(out, failed)


class BocSeries(OfficialSource):
    provider = "boc"

    def _fetch(self, since, state):
        keys = {s["key"]: sid for sid, s in self.series.items()}
        r, _, _ = self.get_url(self.cfg["url"].format(key=",".join(keys), since=since.isoformat()))
        if r is None:
            return self._finish([], {sid: self._err() for sid in self.series})
        got = parse_valet(r.json(), list(keys), since)
        return self._finish([self.obs(sid, d, v) for k, sid in keys.items() for d, v in got[k]], {})


class RbaSeries(OfficialSource):
    provider = "rba"

    def _fetch(self, since, state):
        keys = {s["key"]: sid for sid, s in self.series.items()}
        r, nm, v = self._csv_get(self.cfg["url"], state)
        if nm:
            return OfficialResult("not_modified", [], v, "HTTP 304")
        if r is None:
            return self._finish([], {sid: self._err() for sid in self.series})
        got = parse_rba_f1(r.text, list(keys), since)
        return self._finish([self.obs(sid, d, x) for k, sid in keys.items() for d, x in got[k]], {}, v)


class BisSeries(OfficialSource):
    provider = "bis"

    def _fetch(self, since, state):
        areas = {s["key"]: sid for sid, s in self.series.items()}
        r, _, _ = self._csv_get(self.cfg["url"].format(areas="+".join(areas), since=since.isoformat()))
        if r is None:
            return self._finish([], {sid: self._err() for sid in self.series})
        got = parse_bis(r.text, since)
        out, failed = [], {}
        for area, sid in areas.items():
            if area not in got:
                failed[sid] = f"no rows for {area} since {since}"
            out += [self.obs(sid, d, v) for d, v in got.get(area, [])]
        return self._finish(out, {} if out else failed)          # a BIS lag can leave a series empty for a few days


class SnbSeries(OfficialSource):
    provider = "snb"

    def _fetch(self, since, state):
        keys = {s["key"]: sid for sid, s in self.series.items()}
        r, _, _ = self._csv_get(self.cfg["url"].format(since=since.isoformat()))
        if r is None:
            return self._finish([], {sid: self._err() for sid in self.series})
        got = parse_snb(r.text, list(keys), since)
        return self._finish([self.obs(sid, d, v) for k, sid in keys.items() for d, v in got[k]], {})


PROVIDERS: dict = {c.provider: c for c in (FredSeries, EcbSeries, BoeSeries, BojSeries, BocSeries, RbaSeries, BisSeries, SnbSeries)}
