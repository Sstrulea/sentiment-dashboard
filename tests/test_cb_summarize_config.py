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


def test_the_provider_the_model_and_its_limits_are_fixed_in_the_config():
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    o = raw["providers"]["openai"]
    assert raw["provider"] == "openai" and CFG.provider == "openai" and CFG.model == "gpt-5.6-terra" == o["model"]
    assert CFG.max_output_tokens == o["max_output_tokens"] >= 4000                                                    # reasoning tokens come out of it
    assert CFG.temperature is None and CFG.reasoning_effort == "low"                                                   # a reasoning model: no temperature (noted in the file), effort instead
    assert "reasoning model" in CONFIG_PATH.read_text() and "not applicable" in CONFIG_PATH.read_text()
    assert CFG.env_key == "OPENAI_API_KEY" and CFG.api_url == "https://api.openai.com/v1/responses" and CFG.backfill_meetings == 4
    assert CFG.summary_points == (3, 6) and CFG.quotes == (1, 5) and CFG.max_documents > 0 and CFG.max_input_tokens > 0
    assert set(CFG.blocked_words) == {"hawkish", "dovish", "bullish", "bearish", "paves the way", "paved the way", "paving the way"}
    assert set(CFG.priority) == set(PR.KIND_OF_TYPE) and CFG.priority["statement"] < CFG.priority["presser_transcript"] < CFG.priority["minutes"] < CFG.priority["speech"]
    assert CFG.priority["opening_statement"] == CFG.priority["presser_transcript"]


def test_the_other_provider_is_kept_configured_and_selectable():
    a = load_cfg(provider="anthropic")
    assert (a.provider, a.model, a.temperature, a.env_key, a.api_url) == ("anthropic", "claude-sonnet-5", 0.0, "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages")
    assert (a.price_input, a.price_output) == (2.0, 10.0) and a.chars_per_token == 3
    assert a.summary_points == CFG.summary_points and a.blocked_words == CFG.blocked_words                             # everything else is provider-independent
    with pytest.raises(ValueError, match="unknown provider"):
        load_cfg(provider="gemini")


def test_the_model_ids_appear_nowhere_else_in_the_code():
    hits = [p.name for p in (ROOT / "src").rglob("*.py") if "claude-sonnet-5" in p.read_text() or "gpt-5.6" in p.read_text()]
    assert hits == []


def test_no_dependency_was_added_for_the_api():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    assert not any(x in reqs for x in ("anthropic", "openai", "httpx", "aiohttp"))


def test_prices_are_the_official_list_prices_with_their_source_and_date():
    assert (CFG.price_input, CFG.price_cached_input, CFG.price_output) == (2.0, 0.2, 12.0)                              # gpt-5.6-terra, standard tier, short context
    assert CFG.price_source == "https://developers.openai.com/api/docs/pricing" and CFG.price_model_page == "https://developers.openai.com/api/docs/models/gpt-5.6-terra"
    assert CFG.price_checked == "2026-09-21"
    for quoted in ("Short context input $2.00", "output $12.00", "balances intelligence and cost", "Reasoning tokens are output tokens", "1,050,000 context window"):
        assert quoted in CFG.price_note, quoted                                                                        # the note quotes the rows it took them from
    assert CFG.cost_usd(1_000_000, 0) == 2.0 and CFG.cost_usd(0, 1_000_000) == 12.0
    assert CFG.cost_usd(2000, 500) == round(2000 / 1e6 * 2.0 + 500 / 1e6 * 12.0, 4) and CFG.chars_per_token == 4
    a = load_cfg(provider="anthropic")
    assert a.price_source == "https://platform.claude.com/docs/en/about-claude/pricing" and a.price_checked == "2026-09-21" and "will not occur" in a.price_note


def test_the_output_schema_is_strict_and_matches_what_the_verifier_reads():
    from src.cb_summarize.schema import OUTPUT_SCHEMA

    def check(node, path="$"):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False, path                                                         # strict mode: no extra keys
            assert sorted(node["required"]) == sorted(node["properties"]), path                                        # strict mode: every property required
            for k, v in node["properties"].items():
                check(v, f"{path}.{k}")
        elif node.get("type") == "array":
            check(node["items"], path + "[]")
        else:
            assert node["type"] in ("string", "integer"), path
        assert set(node) <= {"type", "properties", "required", "additionalProperties", "items"}, path                  # only keywords every strict implementation supports
    check(OUTPUT_SCHEMA)
    assert set(OUTPUT_SCHEMA["properties"]) == {"summary", "quotes", "coverage"}
    assert set(OUTPUT_SCHEMA["properties"]["quotes"]["items"]["properties"]) == {"paragraph", "text"}


# --- prompts -----------------------------------------------------------------------------------------------------------------------------

def test_every_document_type_has_a_prompt_with_a_pinned_version():
    reg = PR.registry()
    current = {PR.load(k).version for k in PR.KINDS}
    assert current == {"statement-v3", "transcript-v3", "minutes-v3", "speech-v3"} and current <= set(reg)
    for k in PR.KINDS:
        p = PR.load(k)
        assert p.version.startswith(k) and re.fullmatch(r"[a-z]+-v\d+", p.version) and reg[p.version] == p.sha256 == hashlib.sha256(p.system.encode()).hexdigest()
    assert {PR.for_type(t).kind for t in CFG.priority} == set(PR.KINDS)


def test_the_registry_keeps_the_versions_that_were_replaced_and_they_differ_from_the_current_ones():
    reg = PR.registry()
    assert {k for k in reg if k.endswith(("-v1", "-v2"))} == {f"{k}-v{n}" for k in PR.KINDS for n in (1, 2)}              # history: a summary made under v1 / v2 names a known prompt
    for k in PR.KINDS:
        assert len({reg[f"{k}-v1"], reg[f"{k}-v2"], reg[PR.load(k).version]}) == 3                                     # every version is another prompt


def test_editing_a_prompt_without_a_new_version_is_caught(tmp_path):
    alt = tmp_path / "p"
    shutil.copytree(PR.DIR, alt)
    assert all(PR.registry(alt)[PR.load(k, alt).version] == PR.load(k, alt).sha256 for k in PR.KINDS)
    (alt / "system.md").write_text((alt / "system.md").read_text().replace("Never use these words", "Try not to use these words"))
    stale = [k for k in PR.KINDS if PR.registry(alt)[PR.load(k, alt).version] != PR.load(k, alt).sha256]
    assert stale == list(PR.KINDS)                                                                                     # the shared text is part of every prompt: every version must move


def test_the_prompt_states_the_factual_rules_and_every_listed_word_of_the_config():
    system = PR.load("statement").system
    for w in CFG.blocked_words + CFG.attributed_words:
        assert w in system, w
    for phrase in ("Factual only", "never add, subtract, round or compute", "character for character", "single JSON object", "3 to 6 summary points", "1 to 5 quotes",
                   "only when the document itself uses that same word", "attribute it to the bank", "never in your own voice", "a strict JSON schema enforces the shape"):
        assert phrase in system, phrase
    assert "hawkish" in system and "do not forecast" in system.lower() and "do not interpret" in system.lower()
    assert set(CFG.blocked_words).isdisjoint(CFG.attributed_words) and {"hawkish", "dovish", "bullish", "bearish", "paves the way"} <= set(CFG.blocked_words)
    assert {"likely", "expects", "signals", "suggests"} <= set(CFG.attributed_words) and {"Committee", "Board", "Bank", "SNB"} <= set(CFG.attribution_subjects)


def test_the_user_message_numbers_the_paragraphs():
    m = PR.user_message("presser_transcript", "European Central Bank", "https://x", ["first", "second"])
    assert m == "Document: Press conference transcript\nBank: European Central Bank\nSource: https://x\n\nParagraphs:\n[1] first\n[2] second\n"
    f = PR.feedback_message(["a", "b"])
    assert f.startswith("Your previous output failed the automatic check for these reasons:\n- a\n- b\n")


# --- the workflow ------------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/cb-refresh.yml").read_text())


def steps_of(workflow):
    return {s.get("name"): s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name")}


def test_the_summaries_step_runs_only_with_the_secret_and_never_fails_the_workflow(workflow):
    steps = workflow["jobs"]["refresh"]["steps"]
    names = [s.get("name") for s in steps]
    by = steps_of(workflow)
    step = by["Summaries"]
    assert names.index("Collect") < names.index("Check for the summaries key") < names.index("Summaries") < names.index("Render Central Banks pages") < names.index("Commit + push")
    assert step["run"].startswith("python -m src.cb_collect --stage summaries")
    assert step["if"] == "${{ steps.key.outputs.present == 'true' && (!inputs.stage || inputs.stage == 'all' || inputs.stage == 'summaries') }}"
    assert step["continue-on-error"] is True and step["timeout-minutes"] <= 10
    check = by["Check for the summaries key"]
    assert check["id"] == "key" and check["env"] == {"KEY": "${{ secrets.OPENAI_API_KEY }}"} and "GITHUB_OUTPUT" in check["run"]
    assert step["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
    assert "env" not in workflow["jobs"]["refresh"]                                                                    # the secret is not in the environment of every step
    holders = [s["name"] for s in steps if "OPENAI_API_KEY" in json.dumps(s.get("env", {}))]
    assert holders == ["Check for the summaries key", "Summaries", "Freshness status"]                                # the presence check, the paid step, the read-only status
    assert "ANTHROPIC_API_KEY" not in (ROOT / ".github/workflows/cb-refresh.yml").read_text()                          # the provider in use is OpenAI
    collect = by["Collect"]
    assert "--stage" not in collect["run"] and "env" not in collect                                                    # a plain run does not include the paid stage


def test_a_dispatch_can_choose_the_stage_the_bank_and_the_type_without_shell_injection(workflow):
    on = workflow.get("on", workflow.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["force_calendar_check", "stage", "bank", "type", "doc"]
    assert inputs["stage"]["type"] == "choice" and inputs["stage"]["default"] == "all"
    assert inputs["stage"]["options"] == ["all", "market", "official", "decisions", "documents", "projections", "calendar", "summaries"]
    assert inputs["bank"]["type"] == inputs["type"]["type"] == inputs["doc"]["type"] == "string" and inputs["bank"]["default"] == inputs["type"]["default"] == inputs["doc"]["default"] == ""
    by = steps_of(workflow)
    assert by["Collect"]["if"] == "${{ !inputs.stage || inputs.stage == 'all' }}"                                        # a plain run and the schedule: as before
    one = by["Collect one stage"]
    assert one["if"] == "${{ inputs.stage && inputs.stage != 'all' && inputs.stage != 'summaries' }}" and one["env"] == {"STAGE": "${{ inputs.stage }}"}
    assert '--stage "$STAGE"' in one["run"]
    s = by["Summaries"]
    assert s["env"]["BANK"] == "${{ inputs.bank }}" and s["env"]["TYPE"] == "${{ inputs.type }}" and s["env"]["DOC"] == "${{ inputs.doc }}"
    assert '${BANK:+--summaries-bank "$BANK"}' in s["run"] and '${TYPE:+--summaries-type "$TYPE"}' in s["run"] and '${DOC:+--summaries-doc "$DOC"}' in s["run"]
    for name, step in by.items():
        assert "${{ inputs." not in step.get("run", "").replace("${{ inputs.force_calendar_check && '--force-calendar-check' || '' }}", ""), name   # an input reaches the shell only through env


def test_main_is_touched_only_by_a_run_that_is_on_main_and_a_branch_commits_only_the_summaries(workflow):
    by = steps_of(workflow)
    main, branch = by["Commit + push"], by["Commit summaries to the branch"]
    assert main["if"] == "github.ref == 'refs/heads/main'" and '[ "$GITHUB_REF" = "refs/heads/main" ] || ' in main["run"]      # condition AND explicit guard in the script
    assert re.search(r"git add (.+)", main["run"]).group(1).split() == ["data/cb/", "public/central-banks.html", "public/central-banks/", "public/data/cb/"]
    assert "git push origin main" in main["run"] and "HEAD:" not in main["run"]
    assert branch["if"] == "${{ github.ref != 'refs/heads/main' && startsWith(github.ref, 'refs/heads/') }}"
    assert '[ "$GITHUB_REF_NAME" != "main" ] || ' in branch["run"] and "exit 1" in branch["run"]
    assert re.findall(r"git add (\S+)", branch["run"]) == ["data/cb/summaries/"]                                        # nothing but the summaries
    assert 'git push origin "HEAD:refs/heads/$GITHUB_REF_NAME"' in branch["run"] and "origin main" not in branch["run"]    # to the branch it runs on
    assert 'git pull --rebase --autostash origin "$GITHUB_REF_NAME"' in branch["run"]
    text = [ln for ln in branch["run"].splitlines() if "git push" in ln or "git pull" in ln]
    assert all("main" not in ln.replace("$GITHUB_REF_NAME", "") for ln in text)
    order = [s.get("name") for s in workflow["jobs"]["refresh"]["steps"]]
    assert order.index("Render Central Banks pages") < order.index("Commit + push") < order.index("Commit summaries to the branch")


def test_the_commit_step_still_adds_only_the_central_banks_files(workflow):
    commit = steps_of(workflow)["Commit + push"]
    assert re.search(r"git add (.+)", commit["run"]).group(1).split() == ["data/cb/", "public/central-banks.html", "public/central-banks/", "public/data/cb/"]   # data/cb/summaries/ is inside data/cb/


def test_the_workflow_job_timeout_leaves_room_for_the_summaries_step(workflow):
    assert workflow["jobs"]["refresh"]["timeout-minutes"] >= 15


def test_data_dir_layout_is_the_agreed_one():
    assert cc.Paths("/x/cb").summaries == Path("/x/cb/summaries")
