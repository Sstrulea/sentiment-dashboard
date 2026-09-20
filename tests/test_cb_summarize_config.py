"""Phase 2b: what is fixed in configuration - the model and its limits, the prompts and their versions, the workflow step - and the UI contract."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from src import cb_collect as cc
from src.cb_summarize import prompts as PR
from src.cb_summarize.config import CONFIG_PATH, load as load_cfg

ROOT = Path(__file__).resolve().parents[1]
CFG = load_cfg()


def test_the_model_and_its_limits_are_fixed_in_the_config():
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    assert (CFG.model, CFG.temperature, CFG.max_tokens) == ("claude-sonnet-5", 0.0, raw["max_tokens"]) and CFG.max_tokens >= 2000
    assert CFG.env_key == "ANTHROPIC_API_KEY" and CFG.api_url.startswith("https://api.anthropic.com/") and CFG.backfill_meetings == 4
    assert CFG.summary_points == (3, 6) and CFG.quotes == (1, 5) and CFG.max_documents > 0 and CFG.max_input_tokens > 0
    assert set(CFG.blocked_words) >= {"hawkish", "dovish", "bullish", "bearish", "likely", "expects to", "signals", "suggests", "paves the way"}
    assert set(CFG.priority) == set(PR.KIND_OF_TYPE) and CFG.priority["statement"] < CFG.priority["presser_transcript"] < CFG.priority["minutes"] < CFG.priority["speech"]
    assert CFG.priority["opening_statement"] == CFG.priority["presser_transcript"]


def test_the_model_id_appears_nowhere_else_in_the_code():
    hits = [p.name for p in (ROOT / "src").rglob("*.py") if "claude-sonnet-5" in p.read_text()]
    assert hits == []


def test_no_dependency_was_added_for_the_api():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    assert not any(x in reqs for x in ("anthropic", "openai", "httpx", "aiohttp"))


def test_cost_estimate_uses_the_configured_prices():
    assert CFG.cost_usd(1_000_000, 0) == CFG.price_input and CFG.cost_usd(0, 1_000_000) == CFG.price_output
    assert CFG.cost_usd(2000, 500) == round(2000 / 1e6 * CFG.price_input + 500 / 1e6 * CFG.price_output, 4)
    assert "ASSUMPTION" in CONFIG_PATH.read_text()                                                                     # the prices are declared an assumption, in the file


# --- prompts -----------------------------------------------------------------------------------------------------------------------------

def test_every_document_type_has_a_prompt_with_a_pinned_version():
    reg = PR.registry()
    assert set(reg) == {PR.load(k).version for k in PR.KINDS} == {"statement-v1", "transcript-v1", "minutes-v1", "speech-v1"}
    for k in PR.KINDS:
        p = PR.load(k)
        assert p.version.startswith(k) and re.fullmatch(r"[a-z]+-v\d+", p.version) and reg[p.version] == p.sha256 == hashlib.sha256(p.system.encode()).hexdigest()
    assert {PR.for_type(t).kind for t in CFG.priority} == set(PR.KINDS)


def test_editing_a_prompt_without_a_new_version_is_caught(tmp_path):
    alt = tmp_path / "p"
    shutil.copytree(PR.DIR, alt)
    assert all(PR.registry(alt)[PR.load(k, alt).version] == PR.load(k, alt).sha256 for k in PR.KINDS)
    (alt / "system.md").write_text((alt / "system.md").read_text().replace("Never use these words", "Try not to use these words"))
    stale = [k for k in PR.KINDS if PR.registry(alt)[PR.load(k, alt).version] != PR.load(k, alt).sha256]
    assert stale == list(PR.KINDS)                                                                                     # the shared text is part of every prompt: every version must move


def test_the_prompt_states_the_factual_rules_and_every_blocked_word_of_the_config():
    system = PR.load("statement").system
    for w in CFG.blocked_words:
        assert w in system, w
    for phrase in ("Factual only", "never add, subtract, round or compute", "character for character", "single JSON object", "3 to 6 summary points", "1 to 5 quotes"):
        assert phrase in system
    assert "hawkish" in system and "do not forecast" in system.lower() and "do not interpret" in system.lower()


def test_the_user_message_numbers_the_paragraphs():
    m = PR.user_message("presser_transcript", "European Central Bank", "https://x", ["first", "second"])
    assert m == "Document: Press conference transcript\nBank: European Central Bank\nSource: https://x\n\nParagraphs:\n[1] first\n[2] second\n"
    f = PR.feedback_message(["a", "b"])
    assert f.startswith("Your previous output failed the automatic check for these reasons:\n- a\n- b\n")


# --- the workflow ------------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/cb-refresh.yml").read_text())


def test_the_summaries_step_runs_only_with_the_secret_and_never_fails_the_workflow(workflow):
    steps = workflow["jobs"]["refresh"]["steps"]
    names = [s.get("name") for s in steps]
    step = next(s for s in steps if s.get("name") == "Summaries")
    assert names.index("Collect") < names.index("Check for the summaries key") < names.index("Summaries") < names.index("Render Central Banks pages") < names.index("Commit + push")
    assert step["run"] == "python -m src.cb_collect --stage summaries"
    assert step["if"] == "${{ steps.key.outputs.present == 'true' }}" and step["continue-on-error"] is True and step["timeout-minutes"] <= 10
    check = next(s for s in steps if s.get("name") == "Check for the summaries key")
    assert check["id"] == "key" and check["env"] == {"KEY": "${{ secrets.ANTHROPIC_API_KEY }}"} and "GITHUB_OUTPUT" in check["run"]
    assert step["env"] == {"ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}"}
    assert "env" not in workflow["jobs"]["refresh"]                                                                    # the secret is not in the environment of every step
    holders = [s["name"] for s in steps if "ANTHROPIC_API_KEY" in json.dumps(s.get("env", {}))]
    assert holders == ["Check for the summaries key", "Summaries", "Freshness status"]                                # the presence check, the paid step, the read-only status
    collect = next(s for s in steps if s.get("name") == "Collect")
    assert "--stage" not in collect["run"] and "env" not in collect                                                    # a plain run does not include the paid stage


def test_the_commit_step_still_adds_only_the_central_banks_files(workflow):
    commit = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name") == "Commit + push")
    assert re.search(r"git add (.+)", commit["run"]).group(1).split() == ["data/cb/", "public/central-banks.html", "public/central-banks/", "public/data/cb/"]   # data/cb/summaries/ is inside data/cb/


def test_the_workflow_job_timeout_leaves_room_for_the_summaries_step(workflow):
    assert workflow["jobs"]["refresh"]["timeout-minutes"] >= 15


def test_data_dir_layout_is_the_agreed_one():
    assert cc.Paths("/x/cb").summaries == Path("/x/cb/summaries")
