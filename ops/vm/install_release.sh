#!/bin/bash
# Install a release bundle on the VM, smoke-test it, and make it the current release.
#
#   ~/midfreq/
#     releases/<version>/      one folder per release, never modified once installed:
#       .venv/                   its own environment (requirements-vm.txt + the wheels)
#       ops/                     the session launchers cron runs
#       MANIFEST, wheels/, requirements-vm.txt, defaults/config/ (config as committed)
#     current -> releases/<version>    what cron runs: ~/midfreq/current/ops/...
#     previous                         the release current pointed at before the last switch
#     runtime/                 config/, data/, live_sessions/ (MIDFREQ_ROOT); never touched here
#     logs/, archive/, backups/  operations logs, their monthly archive, hand-made backups
#
# Switching `current` takes effect at the next session; a running session keeps the code it
# started with, because its release folder is never changed. Rolling back is switching back.
#
# Usage (ops/deploy.sh runs these over ssh):
#   install_release.sh <bundle.tar.gz> [--stage]   install; switch unless --stage
#   install_release.sh --switch <version>          make an installed release current
#   install_release.sh --rollback                  switch back to `previous`
#   install_release.sh --status
# Environment: MIDFREQ_HOME (default ~/midfreq), PYTHON_BASE (default python3, the
# interpreter each release's .venv is made from).

set -euo pipefail
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
PYTHON_BASE=${PYTHON_BASE:-python3}
REL="$HOME_DIR/releases"
mkdir -p "$REL" "$HOME_DIR/logs" "$HOME_DIR/archive" "$HOME_DIR/backups"

current() { [ -L "$HOME_DIR/current" ] && basename "$(readlink "$HOME_DIR/current")" || echo none; }

switch_to() {
    local v=$1 was
    [ -x "$REL/$v/.venv/bin/python" ] || { echo "release $v is not installed" >&2; exit 1; }
    was=$(current)
    [ "$was" = "$v" ] && { echo "current is already $v"; return; }
    ln -sfn "releases/$v" "$HOME_DIR/current"
    [ "$was" != none ] && echo "$was" > "$HOME_DIR/previous"
    echo "current: $was -> $v (takes effect at the next session)"
}

status() {
    echo "current : $(current)"
    echo "previous: $(cat "$HOME_DIR/previous" 2>/dev/null || echo none)"
    echo "installed:"; ls -1 "$REL" | sed 's/^/  /'
    echo "runtime : $([ -d "$HOME_DIR/runtime" ] && echo "$HOME_DIR/runtime" || echo "not set up")"
}

smoke_test() {
    local d=$1 py="$1/.venv/bin/python"
    # Every check must pass: `set -e` does not apply inside a function called from `if`, so each
    # line returns on failure (0.1.1's import line failed and the test still passed). The
    # packages find config/ at import, so the imports get the release's own defaults as root.
    MIDFREQ_ROOT="$d/defaults" "$py" -c "import midfreq.core, midfreq.data, midfreq.strategies, midfreq.backtest, midfreq.live.run_live, midfreq.research, midfreq.trading.node, midfreq.trading.cli" || return 1
    MIDFREQ_ROOT="$d/defaults" "$d/.venv/bin/midfreq-live" --list > /dev/null || return 1
    # the trading node: its console script, and every node configuration this release ships read
    # and checked (settings valid, components importable) without connecting to anything
    local cfg checked
    for cfg in "$d"/defaults/config/node_*.yaml; do
        [ -e "$cfg" ] || continue
        checked=$(MIDFREQ_ROOT="$d/defaults" "$d/.venv/bin/midfreq-node" check-config --config "$cfg" 2>&1) \
            || { echo "$checked" >&2; return 1; }
    done
    # the launchers end to end, if there is a runtime to launch against: the original engine's
    # (kept for rollback) and the node's, exactly as the crontab starts them
    if [ -f "$HOME_DIR/runtime/config/sp100_forward_test.json" ]; then
        local out
        out=$(DRY_RUN=1 FAKE_ET=2026-11-16T09:35:01 MIDFREQ_ROOT="$HOME_DIR/runtime" PYTHON= \
            "$d/ops/run_sp100_forward_test.sh" --scheduled) || return 1
        grep -q "importable with $py" <<<"$out" || return 1
    fi
    if [ -f "$HOME_DIR/runtime/config/node_sp100.yaml" ]; then
        local node_out
        node_out=$(DRY_RUN=1 FAKE_ET=2026-11-16T09:35:01 MIDFREQ_ROOT="$HOME_DIR/runtime" PYTHON= \
            NODE_CONFIG=config/node_sp100.yaml NODE_MODE=paper "$d/ops/run_node.sh" --scheduled 2>&1) \
            || { echo "$node_out" >&2; return 1; }
        grep -q "would run: $py -u -m midfreq.trading.cli run --config config/node_sp100.yaml --mode paper" <<<"$node_out" \
            || { echo "$node_out" >&2; return 1; }
    fi
}

case "${1:-}" in
    --status) status; exit 0 ;;
    --rollback)
        prev=$(cat "$HOME_DIR/previous" 2>/dev/null) || { echo "no previous release recorded" >&2; exit 1; }
        switch_to "$prev"; exit 0 ;;
    --switch) switch_to "${2:?version}"; exit 0 ;;
    ""|-*) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

BUNDLE=$1; STAGE_ONLY=0
[ "${2:-}" = "--stage" ] && STAGE_ONLY=1
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
tar -xzf "$BUNDLE" -C "$TMP"
SRC=$(echo "$TMP"/midfreq-*)
VERSION=$(sed -n 's/^version=//p' "$SRC/MANIFEST")
DEST="$REL/$VERSION"
[ -e "$DEST" ] && { echo "release $VERSION is already installed at $DEST; releases are never overwritten" >&2; exit 1; }

# the wheels must be the ones the manifest lists
sed -n 's/^sha256 //p' "$SRC/MANIFEST" > "$TMP/sums"
[ -s "$TMP/sums" ] || { echo "MANIFEST lists no wheel checksums" >&2; exit 1; }
if sha256sum --version >/dev/null 2>&1; then    # GNU coreutils (the VM)
    (cd "$SRC" && sha256sum -c --quiet "$TMP/sums")
else                                            # macOS, for testing
    (cd "$SRC" && shasum -a 256 -c --quiet "$TMP/sums")
fi

echo "installing $VERSION ($(sed -n 's/^commit=//p' "$SRC/MANIFEST")) into $DEST"
# Built in place (a venv holds absolute paths, so it cannot be built elsewhere and moved);
# a failed install removes the folder rather than leave half a release behind.
mkdir "$DEST"
trap 'echo "install failed; removing $DEST" >&2; rm -rf "$DEST"' ERR
cp -R "$SRC"/. "$DEST/"
"$PYTHON_BASE" -m venv "$DEST/.venv"
"$DEST/.venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$DEST/requirements-vm.txt"
"$DEST/.venv/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps "$DEST"/wheels/*.whl
trap - ERR

if ! smoke_test "$DEST"; then
    echo "smoke test FAILED; $VERSION is installed but not made current (remove $DEST to retry)" >&2
    exit 1
fi
echo "smoke test passed"
if [ -d "$HOME_DIR/runtime/config" ]; then
    for f in "$DEST"/defaults/config/*; do
        [ -e "$HOME_DIR/runtime/config/$(basename "$f")" ] \
            || echo "note: runtime/config has no $(basename "$f"); the release ships one in $DEST/defaults/config/"
    done
    MIDFREQ_ROOT="$HOME_DIR/runtime" "$DEST/.venv/bin/python" -c "
from midfreq.live.order_gateway import moomoo_settings
s = moomoo_settings()
print(f'OpenD for this release: {s[\"host\"]}:{s[\"port\"]}, trading environment {s[\"trd_env\"]}')"
fi
[ "$STAGE_ONLY" = 1 ] && { echo "staged only; make it current with --switch $VERSION"; exit 0; }
switch_to "$VERSION"
