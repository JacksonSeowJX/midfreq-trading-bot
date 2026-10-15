# ── deployment gate and environment sync, sourced by preflight.sh and postclose.sh ──
#
# The VM deploys by merging origin/main twice a day (09:10 and 16:15 SGT). Until
# 2026-10-15 nothing checked what it merged at 16:15: a change pushed in the morning
# went live that night without a single test (9 Oct). Now a merge happens only if
# GitHub Actions has passed on the same code.
#
# "The same code" is compared by content, not by commit id: the VM's own commits
# only touch live_sessions/ and CI skips them, and a merge made on a laptop gets a
# new id without new code. So origin/main is deployable when some commit that CI
# passed on main has a byte-identical tree outside live_sessions/.
#
# The gate fails closed: no network, an API error or a run still in progress all
# mean "stay on the current code", and the next sync tries again.

CI_REPO="JacksonSeowJX/midfreq-trading-bot"
CI_WORKFLOW="ci.yml"

ci_green() {
    local target="${1:-origin/main}" runs sha
    runs=$(curl -fsS --max-time 20 -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$CI_REPO/actions/workflows/$CI_WORKFLOW/runs?branch=main&status=success&per_page=30" \
        | jq -r '.workflow_runs[].head_sha' 2>/dev/null)
    if [ -z "$runs" ]; then
        echo "$(date) ci_gate: no passing CI runs readable from GitHub (offline, rate-limited or none yet)"
        return 1
    fi
    for sha in $runs; do
        git cat-file -e "$sha^{commit}" 2>/dev/null || continue
        if git diff --quiet "$sha" "$target" -- . ':(exclude)live_sessions'; then
            echo "$(date) ci_gate: $(git rev-parse --short "$target") matches CI-passed $(git rev-parse --short "$sha")"
            return 0
        fi
    done
    echo "$(date) ci_gate: HOLD — no passing CI run for the code at $(git rev-parse --short "$target") (still running, failed, or not pushed through CI)"
    return 1
}

# Something to deploy? (false when origin/main is already contained in HEAD)
has_incoming() {
    ! git merge-base --is-ancestor "${1:-origin/main}" HEAD
}

# Bring .venv in line with the checked-out code. Editable installs point at the
# package sources, so code changes need nothing; only a changed requirements file
# or package metadata since $1 (the commit deployed before) triggers a reinstall.
sync_env() {
    local before="$1"
    [ -f ops/vm/requirements-vm.txt ] || return 0
    if .venv/bin/python -c "import midfreq.core, midfreq.live" 2>/dev/null \
       && git diff --quiet "$before" HEAD -- ops/vm/requirements-vm.txt 'packages/*/pyproject.toml'; then
        return 0
    fi
    echo "$(date) sync_env: installing ops/vm/requirements-vm.txt and the midfreq packages"
    .venv/bin/python -m pip install -q -r ops/vm/requirements-vm.txt \
        && .venv/bin/python -m pip install -q --no-deps $(for p in packages/*/; do printf -- '-e %s ' "$p"; done)
}
