#!/bin/zsh
# SPLIT 2026-07-29: GitHub Actions face calendar (FF+JB), FRED si render.
# Mac-ul ramane DOAR sursa de pret — MT5 ruleaza prin Wine, imposibil in cloud.
# Fisiere disjuncte intre cele doua = zero conflicte pe parquet (binar, ne-merge-uibil).
#
# TREND DEZACTIVAT (Faza B, config/pipeline.yaml trend_enabled: false — vezi
# docs/accepted-degradations.md, dependenta MT5). data/price_history.parquet
# nu mai are niciun consumator cat timp flag-ul e false, asa ca pasul de MT5
# export de mai jos e scos (nu sters — vezi git history pentru varianta
# anterioara). Reactivare: cand trend_enabled trece pe true, readauga
# `./.venv/bin/python -m src.price_fetch` si blocul `git add
# data/price_history.parquet` de mai jos, mirroring calendar_source.
set -u
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export MT5_FILES_DIR="/Users/sebastian/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
REPO="$HOME/projects/macro-data-analysis"
cd "$REPO" || exit 1

# BRANCH GUARD (fix/econ-refresh-branch-guard) — this job runs hourly via
# com.po.econ-refresh.plist against this hardcoded path, on whatever branch a
# human (or an agent) happens to have checked out here. Without this guard it
# would `git pull --rebase --autostash` and then commit+push "price: MT5
# export" straight onto that branch — polluting PR diffs with an unrelated
# binary file, causing parquet merge conflicts (hit twice in one day, on #8
# and #9), and — via --autostash — silently stashing in-progress uncommitted
# work before rebasing, with no reapply if the rebase then conflicts.
#
# The data pull must still happen on every run regardless of branch (a
# feature-branch checkout must never mean stale price data) — only the git
# read/write side is gated. `_econ_refresh_git_state` is READ-ONLY (`git
# rev-parse --git-dir`, `git symbolic-ref`) — it never stashes, rebases, or
# otherwise touches repo state; that's what makes the skip path itself safe
# against a dirty tree.
_econ_refresh_git_state() {
  local git_dir branch
  git_dir=$(git rev-parse --git-dir 2>/dev/null) || { echo "NOT_A_REPO"; return; }
  if [[ -d "$git_dir/rebase-merge" || -d "$git_dir/rebase-apply" ]]; then
    echo "MID_REBASE"
    return
  fi
  if [[ -f "$git_dir/MERGE_HEAD" ]]; then
    echo "MID_MERGE"
    return
  fi
  branch=$(git symbolic-ref --short -q HEAD)
  if [[ -z "$branch" ]]; then
    echo "DETACHED"
    return
  fi
  if [[ "$branch" != "main" ]]; then
    echo "NON_MAIN:$branch"
    return
  fi
  echo "MAIN"
}

GIT_STATE=$(_econ_refresh_git_state)

if [[ "$GIT_STATE" == "MAIN" ]]; then
  git pull --rebase --autostash >/dev/null 2>&1
else
  {
    case "$GIT_STATE" in
      NON_MAIN:*)
        echo "econ_refresh: $(date -u +%FT%TZ) branch is '${GIT_STATE#NON_MAIN:}', not main — skipping git pull/commit/push (price data still refreshed)."
        ;;
      DETACHED)
        echo "econ_refresh: $(date -u +%FT%TZ) HEAD is detached — skipping git pull/commit/push (price data still refreshed)."
        ;;
      MID_REBASE)
        echo "econ_refresh: $(date -u +%FT%TZ) repo is mid-rebase — skipping git pull/commit/push (price data still refreshed)."
        ;;
      MID_MERGE)
        echo "econ_refresh: $(date -u +%FT%TZ) repo is mid-merge — skipping git pull/commit/push (price data still refreshed)."
        ;;
      NOT_A_REPO)
        echo "econ_refresh: $(date -u +%FT%TZ) $REPO is not a git repo — skipping git pull/commit/push (price data still refreshed)."
        ;;
    esac
  } >> /tmp/econ.log 2>&1
fi

# MT5 price export + commit — OFF while trend_enabled is false (nothing reads
# data/price_history.parquet). Re-enable together with the flag:
#   ./.venv/bin/python -m src.price_fetch >> /tmp/econ.log 2>&1
#
#   if [[ "$GIT_STATE" == "MAIN" ]]; then
#     if ! git diff --quiet data/price_history.parquet; then
#       git add data/price_history.parquet
#       git commit -m "price: MT5 export $(date -u +%FT%TZ)" >/dev/null 2>&1
#       for i in 1 2 3; do
#         git pull --rebase --autostash >/dev/null 2>&1 && git push >/dev/null 2>&1 && break
#         sleep 5
#       done
#     fi
#   fi
