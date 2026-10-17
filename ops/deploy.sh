#!/bin/bash
# Deploy a release to the VM, by hand, from a laptop.
#
# The VM never pulls code. A release is a bundle CI builds from a vX.Y.Z tag (see
# ops/build_release.sh); this copies it to the VM and runs its installer there
# (ops/vm/install_release.sh), which puts it in ~/midfreq/releases/<version>, smoke-tests it
# and makes it current. Cron starts each session from ~/midfreq/current, so the new release
# trades from the next session; a session already running keeps the code it started with.
#
# Usage:
#   ops/deploy.sh vX.Y.Z [--stage] [--force]       the bundle attached to that GitHub Release
#   ops/deploy.sh --bundle FILE [--stage] [--force] a bundle built locally (ops/build_release.sh)
#   ops/deploy.sh --status
#   ops/deploy.sh --switch X.Y.Z                     make an installed (staged) release current
#   ops/deploy.sh --rollback                         make the previous release current again
#
#   --stage   install and smoke-test, but leave `current` where it is
#   --force   deploy even while a session is running (installing costs the VM CPU and memory)
# Requires: gcloud, logged in to the project that has the VM.

set -euo pipefail
VM=${VM:-trading-bot}
ZONE=${ZONE:-asia-southeast1-b}
REPO=JacksonSeowJX/midfreq-trading-bot
# the installer of the current release, or of any installed one before the first switch
INSTALLER='i=~/midfreq/current/ops/vm/install_release.sh; [ -f $i ] || i=$(ls -d ~/midfreq/releases/*/ops/vm/install_release.sh 2>/dev/null | tail -1); [ -n "$i" ] || { echo "no release installed" >&2; exit 1; }; bash $i'

vm() { gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command "$1"; }
usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

case "${1:-}" in
    --status)   vm "$INSTALLER --status"; exit 0 ;;
    --rollback) vm "$INSTALLER --rollback && $INSTALLER --status"; exit 0 ;;
    --switch)   vm "$INSTALLER --switch ${2:?--switch needs a version} && $INSTALLER --status"; exit 0 ;;
    --bundle)   SRC=${2:?--bundle needs a file}; shift 2 ;;
    v[0-9]*)    TAG=$1; shift ;;
    *)          usage ;;
esac
STAGE=""; FORCE=0
for a in "$@"; do
    case "$a" in --stage) STAGE=--stage ;; --force) FORCE=1 ;; *) usage ;; esac
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
if [ -n "${TAG:-}" ]; then
    NAME="midfreq-${TAG#v}.tar.gz"
    URL="https://github.com/$REPO/releases/download/$TAG"
    echo "downloading $URL/$NAME"
    curl -fsSL -o "$TMP/$NAME" "$URL/$NAME"
    curl -fsSL -o "$TMP/$NAME.sha256" "$URL/$NAME.sha256"
    (cd "$TMP" && shasum -a 256 -c --quiet "$NAME.sha256")
    SRC="$TMP/$NAME"
fi
NAME=$(basename "$SRC")

# a session: the original engine (python -m midfreq.live.run_live) or the trading node
# (python -m midfreq.trading.cli run, or the midfreq-node command)
if [ "$FORCE" = 0 ] && vm "pgrep -f '[r]un_live|[m]idfreq\.trading\.cli run|[m]idfreq-node run' >/dev/null"; then
    echo "a session is running on $VM; deploy after it ends, or pass --force" >&2
    exit 1
fi

echo "copying $NAME to $VM"
gcloud compute scp --zone "$ZONE" --quiet "$SRC" "$VM:/tmp/$NAME"
# The installer comes from the bundle being installed, so a release can change it.
vm "set -e; d=\$(mktemp -d); trap 'rm -rf \$d /tmp/$NAME' EXIT
    tar -xzf /tmp/$NAME -C \$d; i=\$(echo \$d/midfreq-*/ops/vm/install_release.sh)
    bash \$i /tmp/$NAME $STAGE
    bash \$i --status"
