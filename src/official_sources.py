"""Official-series source adapters (network). Registry: series_id -> adapter.

Only Stats NZ CPI for now (series_id `statsnz_cpi` = CPIQ.SE9A, All groups for
New Zealand, base June 2017 quarter = 1000). Parsing logic is the one validated
by the P1 probe (investigate/nzd-cpi-yoy-source, src/official_probe.py: 16/16
y/y and q/q, from a Mac and from GitHub Actions, with the project UA):

  - release page  https://www.stats.govt.nz/information-releases/
                  consumers-price-index-{month}-{year}-quarter/   (404 until published)
  - the download links are NOT hrefs: they sit in an HTML-entity-encoded
    DocumentBlock JSON; html.unescape + regex on "DocumentLink"; the wanted
    file ends in -index-numbers.csv
  - CSV columns Series_reference, Period, Data_value, ...; Period "2026.09" =
    2026Q3; recent values are integers.

IPv4 is pinned process-wide by importing rate_sources (same as
src/calendar_ext_sources.py); project UA, short timeout, never raises — every
failure comes back as a FetchResult with status "not_yet" or "error".
"""
from __future__ import annotations

import html
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd
import requests

import src.rate_sources  # noqa: F401 — import pins IPv4 (allowed_gai_family) process-wide

log = logging.getLogger(__name__)

UA = {"User-Agent": "macro-data-analysis/1.0 (+github.com/Sstrulea/sentiment-dashboard)"}
HTTP_TIMEOUT = 20

Getter = Callable[[str], requests.Response]


def _default_get(url: str) -> requests.Response:
    return requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)


@dataclass
class FetchResult:
    status: str                              # "ok" | "not_yet" | "error"
    detail: str = ""
    source_url: Optional[str] = None
    index: dict[str, float] = field(default_factory=dict)   # period "2026Q3" -> index value
    raw_values: dict[str, str] = field(default_factory=dict)  # period -> value as printed in the CSV


# ------------------------------------------------------------ Stats NZ ------

class StatsNzCpi:
    series_id = "statsnz_cpi"
    series_ref = "CPIQ.SE9A"
    base = "https://www.stats.govt.nz"
    release_url_tpl = base + "/information-releases/consumers-price-index-{month}-{year}-quarter/"
    _MONTH = {1: "march", 2: "june", 3: "september", 4: "december"}

    def release_page_url(self, quarter: str) -> str:
        return self.release_url_tpl.format(month=self._MONTH[int(quarter[-1])], year=quarter[:4])

    @staticmethod
    def discover_csv_url(page_html: str, base: str = "https://www.stats.govt.nz") -> Optional[str]:
        text = html.unescape(page_html).replace("\\/", "/")
        links = re.findall(r'"DocumentLink":"(/assets/[^"]+?\.csv)"', text)
        hit = next((u for u in links if u.endswith("-index-numbers.csv")), None)
        return base + hit if hit else None

    @staticmethod
    def period_to_quarter(p: str) -> str:
        y, m = str(p).strip().split(".")
        return f"{y}Q{int(m) // 3}"

    def parse_index_csv(self, content: bytes) -> tuple[dict[str, float], dict[str, str]]:
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        cols = {c.lower(): c for c in df.columns}
        ref, per, val = cols.get("series_reference"), cols.get("period"), cols.get("data_value")
        if not (ref and per and val):
            raise ValueError(f"unexpected CSV columns: {list(df.columns)}")
        s = df[df[ref].str.strip() == self.series_ref]
        raw = {self.period_to_quarter(p): v.strip() for p, v in zip(s[per], s[val])
               if v.strip() not in ("", "NA")}
        return {q: float(v) for q, v in raw.items()}, raw

    def fetch(self, quarter: str, get: Getter = _default_get) -> FetchResult:
        """Release files for `quarter`. not_yet: page 404, no index-numbers
        DocumentLink, or `quarter` absent from the CSV. Never raises."""
        page_url = self.release_page_url(quarter)
        try:
            r = get(page_url)
            if r.status_code == 404:
                return FetchResult("not_yet", f"release page 404: {page_url}")
            if r.status_code != 200:
                return FetchResult("error", f"release page HTTP {r.status_code}: {page_url}")
            csv_url = self.discover_csv_url(r.text, self.base)
            if not csv_url:
                return FetchResult("not_yet", f"no -index-numbers.csv DocumentLink on {page_url}")
            c = get(csv_url)
            if c.status_code != 200:
                return FetchResult("error", f"CSV HTTP {c.status_code}: {csv_url}", source_url=csv_url)
            index, raw = self.parse_index_csv(c.content)
            if not index:
                return FetchResult("error", f"{self.series_ref} not found in {csv_url}", source_url=csv_url)
            if quarter not in index:
                return FetchResult("not_yet", f"{quarter} absent from {csv_url} (last {max(index)})",
                                   source_url=csv_url)
            return FetchResult("ok", f"{len(index)} periods", source_url=csv_url, index=index, raw_values=raw)
        except Exception as e:   # graceful: a fetch failure never crashes the job
            return FetchResult("error", f"{type(e).__name__}: {e}")


REGISTRY: dict[str, object] = {StatsNzCpi.series_id: StatsNzCpi()}
