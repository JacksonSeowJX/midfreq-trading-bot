#!/bin/bash
# Runs each market morning before sessions. Pulls latest code; if tests
# fail, rolls back to the last known-good commit so the day still runs.
cd ~/midfreq-trading-bot

# Same defensive self-heal as postclose.sh — belt and braces in case a
# stuck git state ever survives overnight into the next trading morning.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    echo "$(date) preflight: found a stuck rebase, aborting it" >> ~/preflight.log
    git rebase --abort 2>/dev/null
fi
if [ -f .git/MERGE_HEAD ]; then
    echo "$(date) preflight: found a stuck merge, aborting it" >> ~/preflight.log
    git merge --abort 2>/dev/null
fi
if [ "$(git symbolic-ref --short -q HEAD)" != "main" ]; then
    echo "$(date) preflight: HEAD was detached, reattaching to main" >> ~/preflight.log
    git checkout -q main
fi

GOOD=$(git rev-parse HEAD)
# 2026-08-24 -> 2026-09-03 incident: a stray untracked file blocked this
# pull every single morning for 9 trading days. The failure was logged
# correctly the whole time, but with no detail and no way to tell "new
# problem" from "same problem, day 6" at a glance — so nobody caught it.
# Now: show the actual git error (same pattern as postclose.sh), and
# persist a marker so a repeat day is impossible to mistake for routine.
. ~/bin/git_selfheal.sh
git_selfheal >> ~/preflight.log 2>&1
. ~/bin/deploy_lib.sh

git fetch -q origin main 2>/dev/null
# Deploy origin/main only once GitHub Actions has passed on its code
# (deploy_lib.sh); otherwise today runs on the code already here.
if has_incoming origin/main && ! ci_green origin/main >> ~/preflight.log 2>&1; then
    echo "$(date) preflight: not deploying origin/main yet, running on $(git rev-parse --short HEAD)" >> ~/preflight.log
# --autostash: the live state files are tracked and change every night;
# a plain pull refuses to run with them modified, and they must survive it.
elif ! git pull -q --rebase --autostash origin main > /tmp/preflight_pull_err.log 2>&1; then
    AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null)
    BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null)
    if [ -f ~/.git_sync_broken_since ]; then
        echo "$(date) preflight: STILL BROKEN (since $(cat ~/.git_sync_broken_since)) — ahead $AHEAD, behind $BEHIND, running on existing code" >> ~/preflight.log
    else
        date >> ~/.git_sync_broken_since
        echo "$(date) preflight: git pull failed — ahead $AHEAD, behind $BEHIND, running on existing code" >> ~/preflight.log
    fi
    head -5 /tmp/preflight_pull_err.log | sed 's/^/    | /' >> ~/preflight.log
else
    rm -f ~/.git_sync_broken_since
fi
sync_env "$GOOD" >> ~/preflight.log 2>&1
# Every package's tests plus tests/ (the testpaths in pyproject.toml)
if ! .venv/bin/python -m pytest -q -p no:cacheprovider > /tmp/preflight_tests.log 2>&1; then
    echo "$(date) TESTS FAILED after pull — rolling back to $GOOD" >> ~/preflight.log
    # reset --hard would also discard the live state files (rebalance
    # counter, positions), so keep them aside and put them back.
    mkdir -p /tmp/state_keep && cp live_sessions/state_*.json /tmp/state_keep/ 2>/dev/null
    git reset --hard -q "$GOOD"
    cp /tmp/state_keep/state_*.json live_sessions/ 2>/dev/null
else
    echo "$(date) preflight OK at $(git rev-parse --short HEAD)" >> ~/preflight.log
fi
# OpenD healthcheck
if ! nc -z 127.0.0.1 11111 2>/dev/null; then
    echo "$(date) WARNING: OpenD not listening on 11111" >> ~/preflight.log
fi

# Catch a repeat of the 2026-07-28 incident: two strategies crashed at
# startup on every session for two weeks (corrupt state JSON) with
# nothing surfacing it until someone happened to check by hand. Grep
# the most recent day's console logs and flag it loudly if it happened
# again, so this shows up in preflight.log instead of going silent.
LATEST_STAMP=$(ls -t live_sessions/console_*.log 2>/dev/null | head -1 | grep -oE '[0-9]{8}_[0-9]{6}')
if [ -n "$LATEST_STAMP" ]; then
    CRASHED=$(grep -l "Traceback\|CRITICAL" live_sessions/console_*_"$LATEST_STAMP".log 2>/dev/null)
    if [ -n "$CRASHED" ]; then
        echo "$(date) WARNING: previous session(s) crashed on startup: $CRASHED" >> ~/preflight.log
    fi
fi
