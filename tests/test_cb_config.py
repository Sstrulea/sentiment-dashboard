"""Central Banks configuration + data files (phase 1A): config/central_banks.yaml, config/cb_sources.yaml,
data/cb/meetings.yaml and .github/workflows/cb-refresh.yml stay structurally valid and consistent with each
other and with the adapters."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from src.cb_sources import ADAPTERS
from src.cb_sources.market import MxCorra

ROOT = Path(__file__).resolve().parents[1]
BANKS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]


def load(rel: str):
    return yaml.safe_load((ROOT / rel).read_text())


@pytest.fixture(scope="module")
def banks():
    return load("config/central_banks.yaml")["banks"]


@pytest.fixture(scope="module")
def sources():
    return load("config/cb_sources.yaml")


@pytest.fixture(scope="module")
def meetings():
    return load("data/cb/meetings.yaml")["meetings"]


# ---------------------------------------------------------------------------
# config/central_banks.yaml
# ---------------------------------------------------------------------------

def test_eight_banks_with_the_required_fields(banks):
    assert list(banks) == BANKS
    need = {"id", "name", "tz", "calendar_url", "decision_time", "conference", "policy_rate", "overnight_benchmark",
            "effective_rule", "projections", "blackout", "youtube_channel_id", "can_be_zero"}
    for b, c in banks.items():
        assert need <= set(c), (b, need - set(c))
        ZoneInfo(c["tz"])
        assert re.fullmatch(r"UC[\w-]{22}", c["youtube_channel_id"]), b
        assert c["conference"]["held"] in {"every_meeting", "mpr_only", "quarterly"}, b
        assert c["policy_rate"]["definition"] and c["policy_rate"]["unit"], b


def test_effective_date_rules_are_sourced_or_flagged(banks):
    for b, c in banks.items():
        r = c["effective_rule"]
        assert r["kind"] in {"calendar_days", "next_business_day"}, b
        assert isinstance(r["verified"], bool) and r["note"], b
        if r["verified"]:
            assert r["source"], f"{b}: verified rule without a source"
    # the BoJ rule: next Japanese business day (18 Sep 2026 -> 24 Sep 2026, 21-23 Sep are JP holidays)
    jp = banks["JPY"]["effective_rule"]
    assert (jp["kind"], jp["calendar"], jp["verified"]) == ("next_business_day", "JP", True) and "24 Sep" in jp["note"]
    assert {b: banks[b]["effective_rule"].get("days") for b in ("USD", "EUR", "GBP", "CAD", "AUD", "CHF")} == {
        "USD": 1, "EUR": 6, "GBP": 0, "CAD": 1, "AUD": 1, "CHF": 1}
    assert banks["NZD"]["effective_rule"]["verified"] is False and banks["NZD"]["effective_rule"]["source"] is None


def test_conventions_that_the_spec_fixes(banks):
    assert banks["EUR"]["policy_rate"]["definition"].startswith("Deposit facility rate")       # ECB = DFR
    assert banks["USD"]["policy_rate"]["level_for_compute"] == "midpoint"
    assert banks["NZD"].get("manual") is True and not any(banks[b].get("manual") for b in BANKS if b != "NZD")
    assert {b for b in BANKS if banks[b]["can_be_zero"]} == {"CHF"}                             # CHF 0.00 is a value
    assert banks["NZD"]["blackout"] is None and banks["CHF"]["blackout"] is None                # not found -> null
    for b in BANKS:
        bl = banks[b]["blackout"]
        assert bl is None or (bl["text"] and bl["url"].startswith("http")), b
    assert banks["JPY"]["decision_time"]["variable"] and len(banks["JPY"]["decision_time"]["window_local"]) == 2


def test_overnight_benchmark_is_per_bank(banks):
    names = {b: (banks[b]["overnight_benchmark"] or {}).get("name") for b in BANKS}
    assert names["NZD"] is None                                     # no overnight benchmark path for NZD
    assert names["USD"] == "SOFR" and names["GBP"] == "SONIA" and names["CAD"] == "CORRA" and names["CHF"] == "SARON"


# ---------------------------------------------------------------------------
# config/cb_sources.yaml  <->  adapters
# ---------------------------------------------------------------------------

def test_every_configured_source_has_an_adapter_and_the_required_fields(sources):
    assert set(sources["sources"]) == set(ADAPTERS)
    assert sources["meta"]["horizon_months"] == 36
    for sid, c in sources["sources"].items():
        cls = ADAPTERS[sid]
        assert c["adapter"] == cls.__name__ and c["currency"] == cls.currency, sid
        assert c["history"] in {"official", "snapshot"}, sid
        assert re.fullmatch(r"\d{2}:\d{2}", c["eod_cutoff"]), sid
        ZoneInfo(c["exchange_tz"])
        assert c["instruments"] and c["naming"] and c["license"] and c["download"] is not None, sid
        assert isinstance(c["proxy"], bool) and c["horizon_months"] <= 36, sid
        assert "url" in c or "urls" in c, sid


def test_history_and_download_policy_match_what_the_spikes_measured(sources):
    s = sources["sources"]
    assert {i for i, c in s.items() if c["history"] == "snapshot"} == {"jpx_tona", "mx_corra", "asx_ib", "asx_bb"}
    cond = {i for i, c in s.items() if c["download"].get("conditional")}
    assert cond == {"atlantafed_mpt", "boe_ois", "jpx_tona", "rba_bank_bills"}
    assert "etag" in s["atlantafed_mpt"]["download"]["validators"] and s["atlantafed_mpt"]["download"]["size_mb"] == 6.9
    assert {i for i, c in s.items() if c["proxy"]} == {"ust_bills", "boc_tbills", "ecb_aaa_fwd", "rba_bank_bills"}
    assert s["mx_corra"]["intraday_utc"] == ["13:00", "22:00"] and s["mx_corra"]["asof_stamp"] == "none"
    assert date.fromisoformat(str(sources["meta"]["backfill_from"])) == date(2026, 3, 1)
    assert {i for i, c in s.items() if c["history"] == "official"} == {
        "atlantafed_mpt", "boe_ois", "ust_bills", "boc_tbills", "ecb_aaa_fwd", "rba_bank_bills"}


def test_adapter_instruments_and_units_match_the_config(sources):
    for sid, cls in ADAPTERS.items():
        cfg_instr = sources["sources"][sid]["instruments"]
        names = {name for name, _ in MxCorra.INSTR.values()} if cls is MxCorra else {cls.INSTRUMENT}
        assert names == set(cfg_instr), sid
        assert {v["unit"] for v in cfg_instr.values()} == {cls.UNIT}, sid


# ---------------------------------------------------------------------------
# data/cb/meetings.yaml
# ---------------------------------------------------------------------------

def test_meetings_cover_2026_2027_with_the_specified_fields(meetings):
    assert list(meetings) == BANKS
    counts = {b: len(m) for b, m in meetings.items()}
    assert counts == {"USD": 16, "EUR": 16, "GBP": 16, "JPY": 16, "CAD": 16, "AUD": 16, "NZD": 8, "CHF": 10}   # CHF from 2025-09 (last 4 decisions)
    for b, rows in meetings.items():
        dates = [r["date"] for r in rows]
        assert dates == sorted(set(dates)), b
        lo = date(2025, 9, 1) if b == "CHF" else date(2026, 1, 1)
        assert all(lo <= d <= date(2027, 12, 31) for d in dates), b
        for r in rows:
            assert set(r) - {"first_day"} == {"date", "has_projections", "has_presser", "source", "verified"}, (b, r)
            assert r.get("first_day") is None or (isinstance(r["first_day"], date) and r["first_day"] < r["date"]), (b, r)
            assert r["source"] in {"official", "ff", "manual"}
            assert isinstance(r["has_projections"], bool) and isinstance(r["has_presser"], bool)
            assert r["verified"] is False or isinstance(r["verified"], date)


def test_meeting_sources_and_verification_are_honest(meetings):
    assert all(r["source"] == "manual" and r["verified"] is False for r in meetings["NZD"])     # RBNZ is manual
    assert max(r["date"] for r in meetings["NZD"]) == date(2027, 2, 17)                          # until the Feb 2027 release
    assert {b for b, rows in meetings.items() if any(r["source"] == "ff" for r in rows)} == {"EUR"}
    assert all(r["date"] < date(2026, 10, 1) and r["verified"] is False for r in meetings["EUR"] if r["source"] == "ff")
    assert all(r["verified"] is False for r in meetings["GBP"] if r["date"].year == 2027)       # BoE 2027 is provisional
    for b, rows in meetings.items():
        for r in rows:
            if r["source"] == "official" and not (b == "GBP" and r["date"].year == 2027):
                assert r["verified"] == date(2026, 9, 20), (b, r["date"])


def test_meetings_agree_with_the_bank_config(meetings, banks):
    for b, rows in meetings.items():
        proj_months = sorted({r["date"].month for r in rows if r["has_projections"]})
        assert proj_months == sorted(banks[b]["projections"]["months"]), b
        held = banks[b]["conference"]["held"]
        if held == "every_meeting":
            assert all(r["has_presser"] for r in rows), b
        if held == "mpr_only":                                       # BoE: press conference on MPR days only
            assert all(r["has_presser"] == r["has_projections"] for r in rows), b


def test_known_anchor_dates(meetings):
    def d(b, y, m, dd):
        return next(r for r in meetings[b] if r["date"] == date(y, m, dd))
    assert d("USD", 2026, 9, 16)["has_projections"] and d("JPY", 2026, 9, 18)["has_presser"]
    assert not d("JPY", 2026, 9, 18)["has_projections"]                # BoJ Outlook Report: Jan/Apr/Jul/Oct
    assert d("CAD", 2026, 9, 2)["has_presser"] and not d("CAD", 2026, 9, 2)["has_projections"]
    assert d("GBP", 2026, 9, 17)["has_presser"] is False
    assert d("CHF", 2026, 9, 24)["has_projections"] and d("EUR", 2026, 9, 10)["source"] == "ff"
    assert d("NZD", 2026, 9, 2)["has_projections"] and d("AUD", 2026, 9, 29)["has_projections"] is False


# ---------------------------------------------------------------------------
# .github/workflows/cb-refresh.yml
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/cb-refresh.yml").read_text())


def test_workflow_triggers_and_guards(workflow):
    on = workflow.get("on", workflow.get(True))          # PyYAML (YAML 1.1) reads the bare key `on` as True
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["workflow_dispatch"]["inputs"]["force_calendar_check"]["type"] == "boolean"
    cron = on["schedule"][0]["cron"]
    assert cron == "37 */2 * * *" and len(cron.split()) == 5
    assert workflow["concurrency"] == {"group": "cb-refresh", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "write"}
    job = workflow["jobs"]["refresh"]
    assert job["timeout-minutes"] == 10 and job["runs-on"] == "ubuntu-latest"


CB_PUBLIC = ["public/central-banks.html", "public/central-banks/", "public/data/cb/"]          # the only public/ paths the workflow may write


def test_workflow_steps_mirror_econ_refresh_and_touch_only_the_cb_files(workflow):
    steps = workflow["jobs"]["refresh"]["steps"]
    uses = [s.get("uses") for s in steps if "uses" in s]
    assert uses == ["actions/checkout@v4", "actions/setup-python@v5"]
    assert steps[1]["with"] == {"python-version": "3.11", "cache": "pip"}
    runs = [s.get("run", "") for s in steps]
    assert "pip install -r requirements.txt" in runs
    collect = next(s for s in steps if s.get("name") == "Collect")
    assert collect["run"].startswith("python -m src.cb_collect") and "--force-calendar-check" in collect["run"]   # only via the dispatch input
    assert "python -m src.cb_collect --status" in runs
    names = [s.get("name") for s in steps]
    assert names.index("Collect") < names.index("Render Central Banks pages") < names.index("Commit + push")     # collect -> render -> commit
    assert next(s for s in steps if s.get("name") == "Render Central Banks pages")["run"] == "python -m src.cb_render"
    commit = next(s for s in steps if s.get("name") == "Commit + push")
    assert commit["if"] == "github.ref == 'refs/heads/main'"          # a dispatch from another ref never pushes
    adds = re.findall(r"git add (\S+)", commit["run"])
    add_line = re.search(r"git add (.+)", commit["run"]).group(1).split()
    assert add_line == ["data/cb/"] + CB_PUBLIC, add_line              # data/cb/ + the CB files of public/ and nothing else
    assert adds == ["data/cb/"]
    assert "git diff --cached --quiet" in commit["run"]                # no commit when nothing changed
    assert "git pull --rebase --autostash origin main && git push origin main" in commit["run"]
    assert "for i in 1 2 3" in commit["run"]


def test_workflow_paths_are_disjoint_from_econ_refresh(workflow):
    econ = (ROOT / ".github/workflows/econ-refresh.yml").read_text()
    mine = "\n".join(ln for ln in (ROOT / ".github/workflows/cb-refresh.yml").read_text().splitlines()
                     if not ln.lstrip().startswith("#"))              # comments may say what it does NOT touch
    assert "data/cb" not in econ and "central-banks" not in econ
    for path in re.findall(r"public/\S*", mine):                          # any public/ path it names is a Central Banks one
        assert path in CB_PUBLIC, path
    assert workflow["concurrency"]["group"] != "econ-refresh"


def test_multi_day_meetings_carry_first_day_and_one_day_meetings_do_not(meetings):
    multi = {"USD", "EUR", "JPY", "AUD"}
    for b, rows in meetings.items():
        for r in rows:
            if b in multi:
                assert isinstance(r.get("first_day"), date) and 0 < (r["date"] - r["first_day"]).days <= 3, (b, r["date"])
            else:
                assert r.get("first_day") is None, (b, r["date"])
    by = {r["date"]: r for r in meetings["USD"]}
    assert by[date(2026, 9, 16)]["first_day"] == date(2026, 9, 15)            # Tue-Wed FOMC
    assert {r["date"]: r["first_day"] for r in meetings["JPY"]}[date(2026, 9, 18)] == date(2026, 9, 17)
    assert {r["date"]: r["first_day"] for r in meetings["AUD"]}[date(2026, 9, 29)] == date(2026, 9, 28)
    ecb = {r["date"]: r for r in meetings["EUR"]}
    assert ecb[date(2026, 9, 10)]["first_day"] == date(2026, 9, 9) and ecb[date(2026, 9, 10)]["source"] == "ff"     # derived, unverified


def test_bank_config_carries_the_1b1_fields(banks):
    for b, c in banks.items():
        assert c["calendar_id"] and c["policy_rate"]["official"] and "blackout_rule" in c, b
        assert (c["blackout_rule"] is None) == (c["blackout"] is None), b            # a rule exists exactly where the bank states one
        assert c["policy_rate"]["ff_name"], b
    assert not any("bis_offset_days" in c["policy_rate"] for c in banks.values())          # BIS is dated by the effective date
    nz = banks["NZD"]["effective_rule"]
    assert (nz["kind"], nz["calendar"], nz["verified"], nz["derived_from"]) == ("next_business_day", "NZ", False, "bis:NZ")
    assert "derived from BIS (RBNZ site blocked)" in nz["note"]
    for b in ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"):                                 # verified rules cite the series tested
        r = banks[b]["effective_rule"]
        assert r["verified"] is True and r["source"] and ":" in r["source"], b
    assert banks["JPY"]["blackout_rule"]["verified"] is False and banks["USD"]["blackout_rule"]["verified"] is True
    assert banks["GBP"]["blackout_rule"]["precision"] == "approximate"


def test_econ_refresh_can_render_every_page_on_dispatch_only():
    """The navbar (shared template) reaches every page through a workflow_dispatch input of econ-refresh; the schedule is untouched and no
    workflow commits public/ from a local run."""
    econ = yaml.safe_load((ROOT / ".github/workflows/econ-refresh.yml").read_text())
    on = econ.get("on", econ.get(True))
    assert on["workflow_dispatch"]["inputs"]["render_all"]["type"] == "boolean" and on["workflow_dispatch"]["inputs"]["render_all"]["default"] is False
    assert [c["cron"] for c in on["schedule"]] == ["5 * * * 1-5", "5 */4 * * 0,6"]
    steps = econ["jobs"]["refresh"]["steps"]
    names = [s.get("name") for s in steps]
    step = next(s for s in steps if s.get("name") == "Render all pages (navbar sync)")
    assert step["if"] == "inputs.render_all" and step["run"] == "python -m src.main --mode render-all"
    assert names.index("Render") < names.index("Render all pages (navbar sync)") < names.index("Commit + push")
    assert "public/" in next(s for s in steps if s.get("name") == "Commit + push")["run"]
