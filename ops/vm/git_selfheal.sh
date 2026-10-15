# ── git self-heal, sourced by preflight.sh and postclose.sh ──────────
#
# Two causes have each silently frozen this VM's code for days:
#
#   2026-08-24 -> 09-03: an untracked file at a path the incoming commit also
#   adds; git refuses to clobber it and aborts every pull.
#
#   2026-09-23 -> 09-26: pull.ff=only set GLOBALLY, while this repo commits
#   its own session logs daily, so once ahead a fast-forward is impossible.
#
# This function must NEVER modify tracked files. The previous version
# detected blockers by running a trial merge and then `git reset --hard
# HEAD`, which discarded every uncommitted change to a tracked file — and
# the live strategy's state file is tracked and changes every night. From
# 2026-10-01 to 10-04 it reset the S&P 100 forward test's rebalance counter
# from 14 back to 7 every morning, so the counter never reached 19 and the
# strategy never traded. Detection is now read-only: compare the trees,
# never merge or reset.

git_selfheal() {
    local branch="${1:-main}"

    if [ "$(git config --get pull.ff)" = "only" ] || [ -z "$(git config --local --get pull.rebase)" ]; then
        git config --local pull.ff true
        git config --local pull.rebase true
        echo "$(date) git_selfheal: set local pull.ff=true pull.rebase=true (global pull.ff=only freezes a diverged repo)"
    fi

    git fetch -q origin "$branch" 2>/dev/null || return 0

    # Files the incoming commits ADD that exist here untracked. Remove one
    # only if it is byte-identical to the incoming version (nothing lost);
    # anything that differs is reported and left for a human.
    local f
    while IFS= read -r f; do
        [ -n "$f" ] && [ -f "$f" ] || continue
        git ls-files --error-unmatch "$f" >/dev/null 2>&1 && continue
        if [ "$(git hash-object "$f")" = "$(git rev-parse "origin/$branch:$f" 2>/dev/null)" ]; then
            rm -f "$f"
            echo "$(date) git_selfheal: removed untracked $f (identical to origin/$branch, was blocking the pull)"
        else
            echo "$(date) git_selfheal: BLOCKED — untracked $f differs from origin/$branch; a human must resolve it"
        fi
    done < <(git diff --name-only --diff-filter=A HEAD "origin/$branch" 2>/dev/null)
}
