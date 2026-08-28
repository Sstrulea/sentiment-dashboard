"""Update data/policy_rates.yaml from BIS — the automated replacement for
manually editing that file (see src.policy_rate_sources for the fetch and
src.policy_rate_probe for the read-only diagnostic that validated the
design).

    python -m src.policy_rate_fetch

Anti-degradation, mirroring src.liquidity_fetch / src.ff_refresh:
  - Fetch failed, payload empty, or fewer than MIN_RESOLVED currencies
    resolved -> the file is left byte-for-byte UNCHANGED, a warning is
    logged, exit 0 (never fails the job over a soft data problem).
  - A currency BIS didn't resolve this run keeps its EXISTING entry from the
    file — never deleted, never nulled — see `merge_rates`.
  - Deterministic: the file is only written if the freshly-rendered text
    actually differs from what's already on disk, so two runs against
    unchanged upstream data produce zero git diff.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from src.policy_rate_sources import CURRENCY_ORDER, PolicyRateObservation, fetch_policy_rates

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
POLICY_RATES_YAML = ROOT / "data" / "policy_rates.yaml"

BIS_SOURCE_URL = "https://data.bis.org/topics/CBPOL"
MIN_RESOLVED = 8   # of 8 — see the module docstring; a partial result never writes

_EMPTY_LEG: dict[str, Any] = {
    "rate_pct": None, "effective": None, "verified": None,
    "source_name": "", "source_url": "",
}

HEADER = """# Central bank policy rates for the Carry page (/carry) — fetched
# automatically from BIS (src.policy_rate_fetch + src.policy_rate_sources),
# once a day. A manual edit here will be OVERWRITTEN by the next automated
# run; to correct a value, fix it at the source or adjust the fetch, not
# this file. See src.policy_rate_probe for the diagnostic that validated
# this design (dataflow, conventions, change-date accuracy) before it shipped.
#
#   rate_pct    — BIS's latest valid observation, in percent.
#   effective   — date of the last VALUE CHANGE BIS shows — NOT the date of
#                 the latest observation. Those differ whenever a rate has
#                 been held since before the fetch's lookback window; when
#                 that happens this keeps whatever `effective` was already
#                 on file rather than guessing.
#   verified    — date of the latest valid BIS observation behind rate_pct.
#                 src.carry_compute flags a row `stale` once this is older
#                 than meta.stale_after_days.
#   source_name — "BIS Central bank policy rates (<central bank, BIS's own
#                 SOURCE_REF>)".
#   source_url  — https://data.bis.org/topics/CBPOL
#
# A currency BIS didn't resolve on a given run keeps its previous entry
# untouched — see merge_rates() in src/policy_rate_fetch.py.
meta:
  stale_after_days: {stale_after_days}

rates:
"""


def _fmt_rate(v: Optional[float]) -> str:
    if v is None:
        return "null"
    s = f"{v:.3f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s


def _fmt_date(d: Any) -> str:
    if d is None:
        return "null"
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d)   # defensive: an already-string value from a malformed existing file


def _yaml_dq(s: str) -> str:
    """A safe double-quoted YAML scalar for a single-line string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_leg_line(ccy: str, leg: dict) -> str:
    return (f"  {ccy}: {{rate_pct: {_fmt_rate(leg.get('rate_pct'))}, "
            f"effective: {_fmt_date(leg.get('effective'))}, "
            f"verified: {_fmt_date(leg.get('verified'))}, "
            f"source_name: {_yaml_dq(leg.get('source_name') or '')}, "
            f"source_url: {_yaml_dq(leg.get('source_url') or '')}}}")


def render_yaml(stale_after_days: int, rates: dict[str, dict]) -> str:
    """Deterministic text render: same `stale_after_days` + `rates` always
    produces the exact same string, currencies in CURRENCY_ORDER regardless
    of dict insertion order."""
    lines = [HEADER.format(stale_after_days=stale_after_days).rstrip("\n")]
    for ccy in CURRENCY_ORDER:
        lines.append(_render_leg_line(ccy, rates.get(ccy) or _EMPTY_LEG))
    return "\n".join(lines) + "\n"


def merge_rates(existing_rates: dict, fetched: dict[str, PolicyRateObservation]) -> dict[str, dict]:
    """Pure: build the new {currency: leg} mapping. A currency present in
    `fetched` gets a freshly-built leg; one absent from `fetched` (BIS
    didn't resolve it this run) keeps its EXISTING leg from
    `existing_rates` verbatim — never deleted, never nulled."""
    out: dict[str, dict] = {}
    for ccy in CURRENCY_ORDER:
        obs = fetched.get(ccy)
        if obs is None:
            out[ccy] = dict(existing_rates.get(ccy) or _EMPTY_LEG)
            continue
        existing_leg = existing_rates.get(ccy) or {}
        effective = obs.effective if obs.effective is not None else existing_leg.get("effective")
        source_name = (f"BIS Central bank policy rates ({obs.bis_source_ref})"
                       if obs.bis_source_ref else "BIS Central bank policy rates")
        out[ccy] = {
            "rate_pct": obs.rate_pct,
            "effective": effective,
            "verified": obs.verified,
            "source_name": source_name,
            "source_url": BIS_SOURCE_URL,
        }
    return out


def update_policy_rates_yaml(
    yaml_path: Path = POLICY_RATES_YAML,
    fetch_fn: Callable[[], dict[str, PolicyRateObservation]] = fetch_policy_rates,
) -> bool:
    """Fetch BIS, merge into the existing YAML, write iff the result
    actually differs from what's on disk. Returns True iff the file was
    written. Never raises — every failure path logs a warning and leaves
    the file exactly as it was.
    """
    existing_text = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
    try:
        existing_cfg = yaml.safe_load(existing_text) if existing_text else {}
    except yaml.YAMLError as e:
        log.error("data/policy_rates.yaml is not valid YAML (%s) — refusing to touch it.", e)
        return False
    existing_cfg = existing_cfg or {}
    existing_rates = existing_cfg.get("rates") or {}
    stale_after_days = int((existing_cfg.get("meta") or {}).get("stale_after_days", 45))

    fetched = fetch_fn()
    if len(fetched) < MIN_RESOLVED:
        log.warning("BIS resolved %d/%d currencies (need >= %d) — leaving %s unchanged.",
                    len(fetched), len(CURRENCY_ORDER), MIN_RESOLVED, yaml_path)
        return False

    new_rates = merge_rates(existing_rates, fetched)
    new_text = render_yaml(stale_after_days, new_rates)

    if new_text == existing_text:
        log.info("BIS rates unchanged — %s already up to date.", yaml_path)
        return False

    yaml_path.write_text(new_text, encoding="utf-8")
    log.info("Updated %s from BIS (%d/%d currencies resolved this run).",
             yaml_path, len(fetched), len(CURRENCY_ORDER))
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    changed = update_policy_rates_yaml()
    if changed:
        log.info("data/policy_rates.yaml changed — re-rendering /carry.")
        from src.carry_render import render_carry_page
        out = render_carry_page()
        log.info("Carry page re-rendered → %s", out)
    else:
        log.info("data/policy_rates.yaml unchanged — skipping /carry re-render.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
