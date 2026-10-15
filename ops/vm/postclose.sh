#!/bin/bash
# Push the day s session logs + ownership state to GitHub.
cd ~/midfreq-trading-bot

# Defensive: clear any stuck rebase/merge left by a previous failed run
# (2026-07-27 incident: a stuck rebase silently detached HEAD for 5 days
# — every subsequent run kept committing onto it without ever noticing).
# Must self-heal, since nobody watches this script run every evening.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    echo "$(date) postclose: found a stuck rebase, aborting it" >> ~/preflight.log
    git rebase --abort 2>/dev/null
fi
if [ -f .git/MERGE_HEAD ]; then
    echo "$(date) postclose: found a stuck merge, aborting it" >> ~/preflight.log
    git merge --abort 2>/dev/null
fi
if [ "$(git symbolic-ref --short -q HEAD)" != "main" ]; then
    echo "$(date) postclose: HEAD was detached, reattaching to main" >> ~/preflight.log
    git checkout -q main
fi

# Defensive: never stage a state file that isn't valid JSON. A bad merge
# once left literal conflict markers inside a state_*.json file and this
# script committed it anyway, silently killing that strategy's live
# sessions for two weeks before anyone noticed (2026-07-28 incident).
STAGE_FILES=(live_sessions/*.jsonl)
for f in live_sessions/state_*.json; do
    [ -f "$f" ] || continue
    if .venv/bin/python -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null; then
        STAGE_FILES+=("$f")
    else
        echo "$(date) postclose: SKIPPING invalid JSON in $f — not staged, needs manual fix" >> ~/preflight.log
    fi
done
git add -f "${STAGE_FILES[@]}" 2>/dev/null
git commit -q -m "chore: Session logs $(date +%Y-%m-%d) [vm]" 2>/dev/null

# Merge, not rebase: a merge conflict leaves an explicit, detectable
# MERGE_HEAD; a rebase can stall silently mid-way with no clear signal.
. ~/bin/git_selfheal.sh
git_selfheal >> ~/preflight.log 2>&1
. ~/bin/deploy_lib.sh
BEFORE=$(git rev-parse HEAD)

git fetch -q origin main
# This merge deploys code for tonight's session, which no test on this VM
# sees first, so take origin/main only once GitHub Actions has passed on it
# (deploy_lib.sh). Held back, the logs stay committed here and the push
# below is refused until a later run can merge.
if has_incoming origin/main && ! ci_green origin/main >> ~/preflight.log 2>&1; then
    echo "$(date) postclose: not merging origin/main yet, tonight runs on $(git rev-parse --short HEAD)" >> ~/preflight.log
elif ! git merge --no-edit -q origin/main >/tmp/postclose_merge_err.log 2>&1; then
    echo "$(date) postclose: merge failed, aborting — local commit kept, will retry next run" >> ~/preflight.log
    # Surface the actual reason (2026-08-24 incident: an untracked stray
    # file silently blocked every merge for 8 trading days because the
    # log only ever said "merge conflict" with no detail to diagnose from).
    head -5 /tmp/postclose_merge_err.log | sed 's/^/    | /' >> ~/preflight.log
    git merge --abort 2>/dev/null
fi
sync_env "$BEFORE" >> ~/preflight.log 2>&1

if git push -q origin main; then
    echo "$(date) postclose OK: $(git log --oneline -1)" >> ~/preflight.log
else
    echo "$(date) postclose PUSH FAILED — logs committed locally, will retry next run" >> ~/preflight.log
fi
