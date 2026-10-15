#!/bin/bash
# The only command the GitHub deploy key can run on the VM.
#
# ~/.ssh/authorized_keys pins the key to this script:
#   command="/home/<user>/midfreq/bin/deploy_gate.sh",restrict ssh-ed25519 AAAA... github-deploy
# so whatever the Deploy workflow sends arrives as $SSH_ORIGINAL_COMMAND and this decides
# what happens. Allowed, nothing else:
#
#   status                         what is installed, current and previous
#   deploy X.Y.Z [--stage] [--force]
#                                  download the GitHub Release vX.Y.Z of this repository, check
#                                  its checksum, install it (ops/vm/install_release.sh from the
#                                  bundle) and, unless --stage, make it current
#   switch X.Y.Z                   make an installed release current
#   rollback                       make the previous release current again
#
# The key never uploads code: a deploy fetches a release CI built, tested and published, so a
# leaked key can at most install or roll back one of those. `restrict` also denies it a
# terminal and every kind of forwarding. Each call is logged to ~/midfreq/deploy.log.
#
# Installed by hand, not by a release: cp ops/vm/deploy_gate.sh ~/midfreq/bin/ (see ops/README.md).

set -euo pipefail
REPO=JacksonSeowJX/midfreq-trading-bot
HOME_DIR=${MIDFREQ_HOME:-$HOME/midfreq}
LOG="$HOME_DIR/deploy.log"
mkdir -p "$HOME_DIR"

say() { echo "$*"; echo "$(date '+%F %T') $*" >> "$LOG"; }
deny() { say "DENIED: $*"; exit 2; }

read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-}"
ACTION=${ARGS[0]:-}
say "request: ${SSH_ORIGINAL_COMMAND:-<none>}"

version_ok() { [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# the installer of the current release, or of any installed one before the first switch
installer() {
    local i="$HOME_DIR/current/ops/vm/install_release.sh"
    [ -f "$i" ] || i=$(ls -d "$HOME_DIR"/releases/*/ops/vm/install_release.sh 2>/dev/null | tail -1 || true)
    [ -n "$i" ] || { say "no release installed yet"; exit 1; }
    MIDFREQ_HOME="$HOME_DIR" bash "$i" "$@"
}

case "$ACTION" in
    status)
        [ ${#ARGS[@]} -eq 1 ] || deny "status takes no arguments"
        if ! ls -d "$HOME_DIR"/releases/*/ >/dev/null 2>&1; then
            say "no release installed yet (runtime: $([ -d "$HOME_DIR/runtime" ] && echo set up || echo not set up))"
            exit 0
        fi
        installer --status ;;
    rollback)
        [ ${#ARGS[@]} -eq 1 ] || deny "rollback takes no arguments"
        installer --rollback; installer --status ;;
    switch)
        [ ${#ARGS[@]} -eq 2 ] && version_ok "${ARGS[1]}" || deny "switch needs exactly X.Y.Z"
        installer --switch "${ARGS[1]}"; installer --status ;;
    deploy)
        version_ok "${ARGS[1]:-}" || deny "deploy needs X.Y.Z"
        V=${ARGS[1]}; STAGE=""; FORCE=0
        for a in "${ARGS[@]:2}"; do
            case "$a" in --stage) STAGE=--stage ;; --force) FORCE=1 ;; *) deny "unknown option $a" ;; esac
        done
        if [ "$FORCE" = 0 ] && pgrep -f '[r]un_live' >/dev/null; then
            say "a session is running; deploy after it ends, or use force"; exit 1
        fi
        TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
        URL="https://github.com/$REPO/releases/download/v$V"
        NAME="midfreq-$V.tar.gz"
        curl -fsSL -o "$TMP/$NAME" "$URL/$NAME" || { say "no published release v$V"; exit 1; }
        curl -fsSL -o "$TMP/$NAME.sha256" "$URL/$NAME.sha256"
        (cd "$TMP" && sha256sum -c --quiet "$NAME.sha256") || { say "checksum mismatch for v$V"; exit 1; }
        mkdir "$TMP/x" && tar -xzf "$TMP/$NAME" -C "$TMP/x"
        MIDFREQ_HOME="$HOME_DIR" bash "$TMP"/x/midfreq-*/ops/vm/install_release.sh "$TMP/$NAME" $STAGE
        say "deploy v$V${STAGE:+ (staged)} finished"
        installer --status ;;
    *)
        deny "'${ACTION}' is not one of status, deploy, switch, rollback" ;;
esac
