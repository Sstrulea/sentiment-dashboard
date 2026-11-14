"""Official rate series for the Central Banks module (phase 1B-1): policy rates, overnight benchmarks, BIS policy
rates. One adapter per provider (FRED, ECB, BoE, BoJ, BoC, RBA, BIS, SNB); the series come from
config/cb_official.yaml. The same adapters (plus ABS, ONS, the Statistics Bureau of Japan and Stats NZ) also fetch the
monthly / quarterly inflation and unemployment series of the stance phase (section `macro:` of the same file, stage
`macro` in src/cb_macro.py): an observation is then dated by the first day of its period. Same contract as the market adapters: HTTP via BaseSource, never raises - `fetch` returns an
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
    released: dict = field(default_factory=dict)   # series_id -> publication date of the file, when the source prints one
    urls: dict = field(default_factory=dict)       # series_id -> the URL the values came from


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


_PERIOD = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


def period_date(t: str) -> Optional[date]:
    """'2026-09-16' -> that day; '2026-09' (a monthly period) -> 2026-09-01; anything else -> None."""
    m = _PERIOD.match(t or "")
    if not m:
        return None
    return date(int(m[1]), int(m[2]), int(m[3] or 1))


def parse_ecb(text: str, since: date) -> list:
    """[(date, value)]; daily rows by their day, monthly rows ('2026-09', HICP) by the first day of the month."""
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        d, v = period_date(r.get("TIME_PERIOD", "")), _num(r.get("OBS_VALUE"))
        if d is not None and v is not None and d >= since:
            out.append((d, v))
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


_RBA_DATES = ((re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}$"), "%d-%b-%Y"),     # F1 (daily)
              (re.compile(r"\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y"))           # G1 / H5 (quarter / month end)


def _rba_date(x: str) -> Optional[date]:
    for rx, fmt in _RBA_DATES:
        if rx.match(x):
            return datetime.strptime(x, fmt).date()
    return None


def parse_rba_table(text: str, keys: list, since: date, *, table: str = "F1") -> dict:
    """{series id: [(date, value)]} from an RBA statistical-table CSV (F1, G1, H5...): the header block ends with the
    'Series ID' row; dates are '16-Sep-2026' (F1) or '30/09/2026' (G1, H5: the END of the period)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    sid_i = next((i for i, x in enumerate(rows) if x and x[0] == "Series ID"), None)
    if sid_i is None:
        raise ValueError(f"no 'Series ID' row in {table}")
    col = {sid: j for j, sid in enumerate(rows[sid_i]) if sid}
    miss = [k for k in keys if k not in col]
    if miss:
        raise ValueError(f"{table} ids missing: {miss}")
    out: dict = {k: [] for k in keys}
    for x in rows[sid_i + 1:]:
        d = _rba_date(x[0]) if x else None
        if d is None or d < since:
            continue
        for k in keys:
            v = _num(x[col[k]]) if len(x) > col[k] else None
            if v is not None:
                out[k].append((d, v))
    return out


def parse_rba_f1(text: str, keys: list, since: date) -> dict:
    return parse_rba_table(text, keys, since, table="F1")


def rba_publication_dates(text: str) -> dict:
    """{series id: date} from the 'Publication date' row of an RBA table (absent row -> {})."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    pub = next((x for x in rows if x and x[0] == "Publication date"), None)
    sids = next((x for x in rows if x and x[0] == "Series ID"), None)
    if not pub or not sids:
        return {}
    out = {}
    for sid, p in zip(sids[1:], pub[1:]):
        try:
            out[sid] = datetime.strptime(p.strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
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
    """{D0 key: [(date, value)]} from the cube csv (`;` separated, metadata lines before the `"Date"` header). Daily
    cubes are dated by their day, monthly ones ('2026-09', CPI) by the first day of the month."""
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip()]
    hi = next((i for i, ln in enumerate(lines) if ln.startswith('"Date"')), None)
    if hi is None:
        raise ValueError("no Date header in the SNB cube")
    out: dict = {k: [] for k in keys}
    for r in csv.DictReader(io.StringIO("\n".join(lines[hi:])), delimiter=";"):
        k, v, d = r.get("D0"), _num(r.get("Value")), period_date(r.get("Date", ""))   # "0" is a value: SNB policy rate 0.00
        if k in out and v is not None and d is not None and d >= since:
            out[k].append((d, v))
    return out


def snb_publishing_date(text: str) -> Optional[date]:
    """The cube's '"PublishingDate";"2026-10-21 09:00"' header line, as a date."""
    m = re.search(r'"PublishingDate";"(\d{4}-\d{2}-\d{2})', text)
    return date.fromisoformat(m[1]) if m else None


def parse_abs(text: str, since: date) -> list:
    """[(date, value)] from an ABS Data API SDMX-CSV response for ONE series key (TIME_PERIOD '2026-09' monthly or
    '2026-Q3' quarterly -> first day of the period)."""
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        t, v = r.get("TIME_PERIOD", ""), _num(r.get("OBS_VALUE"))
        q = re.match(r"^(\d{4})-Q([1-4])$", t)
        d = date(int(q[1]), 3 * int(q[2]) - 2, 1) if q else period_date(t)
        if d is not None and v is not None and d >= since:
            out.append((d, v))
    return sorted(out)


_ONS_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def parse_ons(payload: dict, since: date) -> tuple:
    """([(date, value)], release date) from an ONS time-series JSON (`months`: date '2026 SEP', value '3.5';
    description.releaseDate = the publication the latest value came with)."""
    out = []
    for m in payload.get("months") or []:
        y, mon = (m.get("date") or " ").split(" ", 1)
        v = _num(m.get("value"))
        if mon.upper() in _ONS_MONTHS and v is not None:
            d = date(int(y), _ONS_MONTHS[mon.upper()], 1)
            if d >= since:
                out.append((d, v))
    rel = (payload.get("description") or {}).get("releaseDate")
    rd = None
    if rel:
        rd = (datetime.fromisoformat(rel.replace("Z", "+00:00")) + timedelta(hours=1)).date()   # 23:00Z = 07:00 UK the next day
    return sorted(out), rd


def parse_stat_jp(text: str, code: str, since: date) -> list:
    """[(date, value)] for one item code of a Statistics Bureau of Japan CPI CSV (zmi<base>aa.csv: header rows, then
    'Group/Item' names, the '類・品目符号(Group/Item code)' row, weights; data rows 'YYYYMM,value,...')."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    hdr = next((r for r in rows if r and "Group/Item code" in r[0]), None)
    if hdr is None:
        raise ValueError("no Group/Item code row in the CPI CSV")
    if code not in hdr:
        raise ValueError(f"item code {code} missing from the CPI CSV")
    j = hdr.index(code)
    out = []
    for r in rows:
        if r and re.match(r"^\d{6}$", r[0]) and len(r) > j:
            v = _num(r[j])
            d = date(int(r[0][:4]), int(r[0][4:]), 1)
            if v is not None and d >= since:
                out.append((d, v))
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


class FredSeries(OfficialSource):
    """FRED through the official API with a key (src/fred_api.py) - never the CSV download links of the FRED website (FRED terms). One request per
    series; "." (missing) is skipped, never stored as zero. No key -> every series fails with NO-KEY, nothing is written."""
    provider = "fred"

    def _fetch(self, since, state):
        from ..fred_api import FredApiError, FredKeyMissing, observations, redact
        out, failed = [], {}
        for sid, s in self.series.items():
            try:
                obs = observations(s["key"], since)
            except FredKeyMissing as e:
                failed[sid] = f"NO-KEY {redact(e)}"
                continue
            except FredApiError as e:
                failed[sid] = f"UNREACHABLE {redact(e)}"
                continue
            out += [self.obs(sid, d, v) for d, v in obs if v is not None and d >= since]
        return self._finish(out, failed)


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
    """RBA statistical tables; the table is a per-series parameter (`table`, default F1: the policy and overnight rates).
    One request per table; conditional GET (Last-Modified) when the provider has a single table."""
    provider = "rba"

    def _fetch(self, since, state):
        tables: dict = {}
        for sid, s in self.series.items():
            tables.setdefault(str(s.get("table", "f1")).lower(), {})[s["key"]] = sid
        out, failed, released, urls, new_state = [], {}, {}, {}, {}
        single = len(tables) == 1
        for table, keys in tables.items():
            url = self.cfg["url"].format(table=table)
            r, nm, v = self._csv_get(url, state if single else {})
            if nm:
                return OfficialResult("not_modified", [], v, "HTTP 304")
            if r is None:
                failed.update({sid: self._err() for sid in keys.values()})
                continue
            try:
                got = parse_rba_table(r.text, list(keys), since, table=table.upper())
            except Exception as e:
                failed.update({sid: f"PARSE-FAIL {type(e).__name__}: {e}" for sid in keys.values()})
                continue
            pub = rba_publication_dates(r.text)
            for k, sid in keys.items():
                out += [self.obs(sid, self._period_start(sid, d), x) for d, x in got[k]]
                urls[sid] = url
                if pub.get(k):
                    released[sid] = pub[k]
            if single:
                new_state = v
        res = self._finish(out, failed, new_state)
        if res is not None:
            res.released, res.urls = released, urls
        return res

    def _period_start(self, sid: str, d: date) -> date:
        """G1 / H5 rows are dated by the END of the period; macro series store the first day of it (F1 stays daily)."""
        freq = self.series[sid].get("freq")
        if freq == "Q":
            return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
        if freq == "M":
            return date(d.year, d.month, 1)
        return d


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
    """SNB data portal cubes; the cube is a per-series parameter (`cube`, default snbgwdzid: policy rate and SARON)."""
    provider = "snb"

    def _fetch(self, since, state):
        cubes: dict = {}
        for sid, s in self.series.items():
            cubes.setdefault(s.get("cube", "snbgwdzid"), {})[s["key"]] = sid
        out, failed, released, urls = [], {}, {}, {}
        for cube, keys in cubes.items():
            url = self.cfg["url"].format(cube=cube, since=since.isoformat())
            r, _, _ = self._csv_get(url)
            if r is None:
                failed.update({sid: self._err() for sid in keys.values()})
                continue
            got = parse_snb(r.text, list(keys), since)
            pub = snb_publishing_date(r.text)
            for k, sid in keys.items():
                out += [self.obs(sid, d, v) for d, v in got[k]]
                urls[sid] = url
                if pub:
                    released[sid] = pub
        res = self._finish(out, failed)
        if res is not None:
            res.released, res.urls = released, urls
        return res


class AbsSeries(_PerSeries):
    """ABS Data API (SDMX-CSV), one series key per request: monthly CPI (dataflow CPI) and the Labour Force."""
    provider = "abs"

    def url_for(self, s, since):
        return self.cfg["url"].format(flow=s["flow"], key=s["key"], since=since.strftime("%Y-%m"))

    def parse(self, r, s, since):
        return parse_abs(r.text, since)

    def _csv_get(self, url, validators=None):
        r, nm, v = self.get_url(url, validators)
        return r, nm, v


class OnsSeries(_PerSeries):
    """ONS time-series JSON (`/timeseries/<cdid>/<dataset>/data`), one request per series; the JSON carries the date
    of the release the latest value came with."""
    provider = "ons"

    def url_for(self, s, since):
        return self.cfg["url"].format(cdid=s["key"].lower(), dataset=s["dataset"].lower())

    def _fetch(self, since, state):
        out, failed, released, urls = [], {}, {}, {}
        for sid, s in self.series.items():
            url = self.url_for(s, since)
            r, _, _ = self.get_url(url)
            if r is None:
                failed[sid] = self._err()
                continue
            try:
                rows, rd = parse_ons(r.json(), since)
            except Exception as e:
                failed[sid] = f"PARSE-FAIL {type(e).__name__}: {e}"
                continue
            out += [self.obs(sid, d, v) for d, v in rows]
            urls[sid] = url
            if rd:
                released[sid] = rd
        res = self._finish(out, failed)
        if res is not None:
            res.released, res.urls = released, urls
        return res


class StatJpSeries(OfficialSource):
    """Statistics Bureau of Japan CPI CSVs (one file per index base, e.g. zmi2025aa.csv = 2025-base national monthly
    item indices); series of the same file share one request. The 3-decimal indices are the Bureau's reference values."""
    provider = "statjp"

    def _fetch(self, since, state):
        files: dict = {}
        for sid, s in self.series.items():
            files.setdefault(s["file"], {})[s["key"]] = sid
        out, failed, urls = [], {}, {}
        for fname, codes in files.items():
            url = self.cfg["url"].format(file=fname)
            r, _, _ = self.get_url(url)
            if r is None:
                failed.update({sid: self._err() for sid in codes.values()})
                continue
            text = r.content.decode("cp932")
            for code, sid in codes.items():
                try:
                    out += [self.obs(sid, d, v) for d, v in parse_stat_jp(text, code, since)]
                    urls[sid] = url
                except Exception as e:
                    failed[sid] = f"PARSE-FAIL {type(e).__name__}: {e}"
        res = self._finish(out, failed)
        if res is not None:
            res.urls = urls
        return res


class StatsNzSeries(OfficialSource):
    """Stats NZ CPI release files, through the release-page discovery of src/official_sources.py (StatsNzCpi with the
    series reference and the release file as parameters). The `index-numbers` file carries the whole history; the
    `infoshare-data` file only the release's own quarter, so that series needs one release per quarter (backfill walks
    back release by release while the file exists)."""
    provider = "statsnz"

    def _fetch(self, since, state):
        from ..official_sources import StatsNzCpi           # lazy: keeps the rate collector free of pandas-heavy imports
        out, failed, urls = [], {}, {}
        today = self._now().date()
        for sid, s in self.series.items():
            src = StatsNzCpi(series_ref=s["key"], file_kind=s.get("file", "index-numbers"))
            get = lambda u: self._nz_get(u)                    # noqa: E731 - the polite HttpSource session
            quarters = self._quarters(since, today) if s.get("file") == "infoshare-data" else [self._last_quarter(today)]
            got: dict = {}
            for q in quarters:
                res = src.fetch(q, get=get)
                if res.status == "ok":
                    got.update(res.index)
                    urls[sid] = res.source_url
                elif res.status == "error" and q == quarters[-1] and not got:
                    failed[sid] = res.detail
            if s.get("file", "index-numbers") == "index-numbers" and not got and quarters:
                res = src.fetch(_prev_quarter(quarters[-1]), get=get)          # the newest release may not be out yet
                if res.status == "ok":
                    got.update(res.index)
                    urls[sid] = res.source_url
            for q, v in got.items():
                d = date(int(q[:4]), 3 * int(q[-1]) - 2, 1)
                if d >= since:
                    out.append(self.obs(sid, d, v))
            if not got and sid not in failed:
                failed[sid] = "no release file found"
        res = self._finish(out, failed)
        if res is not None:
            res.urls = urls
        return res

    def _nz_get(self, url):
        r, _, _ = self.get_url(url)
        if r is not None:
            return r
        m = re.match(r"HTTP (\d+)", self.last_note or "")

        class _R:                                               # official_sources expects a response-like object
            status_code = int(m[1]) if m else 0
            text, content = "", b""
        return _R()

    @staticmethod
    def _last_quarter(today: date) -> str:
        return f"{today.year}Q{(today.month - 1) // 3 + 1}"

    def _quarters(self, since: date, today: date) -> list:
        q, out = f"{since.year}Q{(since.month - 1) // 3 + 1}", []
        last = self._last_quarter(today)
        while q <= last:
            out.append(q)
            q = _next_quarter(q)
        return out


def _next_quarter(q: str) -> str:
    y, n = int(q[:4]), int(q[-1])
    return f"{y + 1}Q1" if n == 4 else f"{y}Q{n + 1}"


def _prev_quarter(q: str) -> str:
    y, n = int(q[:4]), int(q[-1])
    return f"{y - 1}Q4" if n == 1 else f"{y}Q{n - 1}"


PROVIDERS: dict = {c.provider: c for c in (FredSeries, EcbSeries, BoeSeries, BojSeries, BocSeries, RbaSeries, BisSeries, SnbSeries,
                                           AbsSeries, OnsSeries, StatJpSeries, StatsNzSeries)}
