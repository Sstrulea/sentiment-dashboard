"""fix/econ-refresh-branch-guard.

`scripts/econ_refresh.sh` runs hourly (launchd, `com.po.econ-refresh.plist`)
against a hardcoded repo path, on whatever branch happens to be checked out
there. Before this change it would `git pull --rebase --autostash` and then
commit+push "price: MT5 export" unconditionally — landing on feature
branches (polluting PR diffs, causing binary parquet merge conflicts — hit
on both #8 and #9 in one session) and, via `--autostash` on a dirty tree,
stashing in-progress work with no reapply if the rebase then conflicted.

There is no existing pattern in this repo for testing shell scripts (no
bats/shunit2/`*.bats` files, nothing shell-related under `tests/`) — every
other test here is pure pytest against Python. Rather than introduce a new
shell-test framework for one function, this extracts the real
`_econ_refresh_git_state` function's TEXT directly out of the live
`scripts/econ_refresh.sh` (so there is no hand-copied, driftable duplicate
of the guard logic under test) and sources it into disposable temp git
repos via `zsh -c`, driven by ordinary pytest + `subprocess` — no new
dependency, consistent with the rest of the suite. If shell-script coverage
needs to grow past this one function, `bats-core` (the de facto standard,
itself just Bash) is the natural next step rather than hand-rolled
subprocess plumbing; not introduced here since one function doesn't
justify a new toolchain.

This does NOT invoke the script end-to-end: `REPO` inside the script is
hardcoded to `$HOME/projects/macro-data-analysis` (deliberately not made
overridable here — that would be a behavior change beyond this fix's
scope) and the script unconditionally shells out to
`./.venv/bin/python -m src.price_fetch`, which needs a real MT5 bridge.
Testing the extracted guard function in isolation is what "at minimum...
exercises the branch-detection logic" calls for; full end-to-end coverage
of the price-pull side is out of scope for a fix that touches only the
git-gating logic.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "econ_refresh.sh"

ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(ZSH is None, reason="zsh not available on this system")


def _extract_git_state_function() -> str:
    """Pull `_econ_refresh_git_state() { ... }` verbatim out of the real
    script — brace-depth counted, not hardcoded start/end line numbers, so
    this keeps working if the function moves within the file. Fails loudly
    (not silently returning an empty string) if the function is ever
    renamed or removed, so a rename doesn't quietly make every test in this
    file vacuous."""
    text = SCRIPT.read_text()
    start = text.index("_econ_refresh_git_state() {")
    depth = 0
    end = None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "unterminated _econ_refresh_git_state function body"
    return text[start:end]


GIT_STATE_FN = _extract_git_state_function()


def _run_git_state(repo_dir: Path) -> str:
    """Source the extracted function and call it with `cwd` set to `repo_dir`
    — exactly how the real script would see it after its own `cd "$REPO"`."""
    result = subprocess.run(
        [ZSH, "-c", f"{GIT_STATE_FN}\n_econ_refresh_git_state"],
        cwd=repo_dir, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    repo = path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


def test_function_extracted_and_nonempty():
    assert GIT_STATE_FN.startswith("_econ_refresh_git_state() {")
    assert GIT_STATE_FN.rstrip().endswith("}")


def test_main_branch_reaches_main(tmp_path):
    repo = _init_repo(tmp_path)
    assert _run_git_state(repo) == "MAIN"


def test_non_main_branch_reaches_non_main_with_its_own_name(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat/some-work"], check=True)
    assert _run_git_state(repo) == "NON_MAIN:feat/some-work"


def test_detached_head_reaches_detached(tmp_path):
    repo = _init_repo(tmp_path)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", sha], check=True)
    assert _run_git_state(repo) == "DETACHED"


def _diverge_and_conflict(repo: Path, branch_a: str, branch_b: str):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch_a, "main"], check=True)
    (repo / "f.txt").write_text("aaa\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-am", "a change"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch_b, "main"], check=True)
    (repo / "f.txt").write_text("bbb\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-am", "b change"], check=True)


def test_mid_rebase_reaches_mid_rebase(tmp_path):
    repo = _init_repo(tmp_path)
    _diverge_and_conflict(repo, "rb-a", "rb-b")
    subprocess.run(["git", "-C", str(repo), "rebase", "rb-a"], capture_output=True)
    try:
        assert _run_git_state(repo) == "MID_REBASE"
    finally:
        subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)


def test_mid_merge_reaches_mid_merge(tmp_path):
    repo = _init_repo(tmp_path)
    _diverge_and_conflict(repo, "mg-a", "mg-b")
    subprocess.run(["git", "-C", str(repo), "merge", "mg-a"], capture_output=True)
    try:
        assert _run_git_state(repo) == "MID_MERGE"
    finally:
        subprocess.run(["git", "-C", str(repo), "merge", "--abort"], capture_output=True)


def test_git_state_check_never_touches_a_dirty_working_tree(tmp_path):
    """The whole point of the guard: the state check itself must be
    read-only. On a non-main branch with an uncommitted, uncommittable-
    looking change, calling the function must leave the dirty file exactly
    as it was — no stash, no rebase, nothing staged."""
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat/dirty"], check=True)
    (repo / "f.txt").write_text("uncommitted change\n")

    before_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout

    state = _run_git_state(repo)

    after_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    after_content = (repo / "f.txt").read_text()

    assert state == "NON_MAIN:feat/dirty"
    assert after_status == before_status
    assert after_content == "uncommitted change\n"


def test_script_syntax_is_valid():
    result = subprocess.run([ZSH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_price_fetch_call_is_unconditional_in_source():
    """The data pull must run on every branch, not just main — confirm the
    `price_fetch` invocation sits outside any `if [[ "$GIT_STATE" == "MAIN" ]]`
    block by checking it's not indented under one in the source text (the
    real script's only conditional git-state branches are indented; the
    price_fetch line is not)."""
    text = SCRIPT.read_text()
    line = next(l for l in text.splitlines() if "price_fetch" in l)
    assert not line.startswith(" ") and not line.startswith("\t"), (
        f"price_fetch call appears indented (possibly inside a conditional): {line!r}"
    )


def test_pull_commit_push_each_appear_exactly_once_and_only_under_main_guard():
    """`git pull --rebase --autostash`, `git commit`, and `git push` must
    each occur exactly where expected: the two `--rebase --autostash` pulls
    (top-of-script sync, and the pre-push retry) plus one commit and one
    push, and every one of those lines must sit inside a block whose `if`
    tests `"$GIT_STATE" == "MAIN"` — i.e. is indented under it, not at the
    function/top level."""
    lines = SCRIPT.read_text().splitlines()
    guard_re = re.compile(r'if \[\[ "\$GIT_STATE" == "MAIN" \]\]; then')
    fi_re = re.compile(r'^\s*fi\s*$')

    def inside_main_guard(idx: int) -> bool:
        depth = 0
        for j in range(idx - 1, -1, -1):
            if fi_re.match(lines[j]):
                depth += 1
            elif guard_re.search(lines[j]):
                if depth == 0:
                    return True
                depth -= 1
        return False

    counts = {"git pull --rebase --autostash": 0, "git commit -m": 0, "git push": 0}
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        for needle in counts:
            if needle in line:
                counts[needle] += 1
                assert inside_main_guard(i), f"{needle!r} on line {i + 1} is not inside a MAIN-gated block: {line!r}"

    assert counts["git pull --rebase --autostash"] == 2
    assert counts["git commit -m"] == 1
    assert counts["git push"] == 1
