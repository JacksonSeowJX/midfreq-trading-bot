#!/bin/bash
# Build a release bundle: everything the VM needs to run one version, and nothing else.
#
#   dist/midfreq-<version>.tar.gz          (and .sha256 beside it)
#     MANIFEST                             version, commit, build time, wheel checksums
#     wheels/midfreq_*-<version>-*.whl     the six packages
#     requirements-vm.txt                  the VM's pinned third-party versions
#     ops/run_sp100_forward_test.sh        the session launchers cron runs
#     ops/run_daily_candidates.sh
#     ops/vm/install_release.sh            installs this bundle (run by ops/deploy.sh)
#     ops/vm/crontab.txt                   the VM's schedule; maintain.sh, memlog.sh, tidy_home.sh
#     defaults/config/                     config/ as committed, for setting up a runtime folder
#
# CI runs this on a vX.Y.Z tag and attaches the bundle to the GitHub Release; run it
# locally to test a deploy. Every package must carry the same version (ops/set_version.sh).
#
# Usage: ops/build_release.sh            # needs uv; run from anywhere in the checkout

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 - <<'PYEOF'
import re, pathlib, sys
vs = {p.parent.name: re.search(r'^version = "([^"]+)"', p.read_text(), re.M).group(1)
      for p in sorted(pathlib.Path('packages').glob('*/pyproject.toml'))}
if len(set(vs.values())) != 1:
    sys.exit(f"packages disagree on the version: {vs}; run ops/set_version.sh")
print(next(iter(vs.values())))
PYEOF
)
COMMIT=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain -- packages ops)" ]; then
    echo "warning: uncommitted changes under packages/ or ops/; the bundle will not match $COMMIT" >&2
fi

NAME=midfreq-$VERSION
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
OUT="$STAGE/$NAME"
mkdir -p "$OUT/wheels" "$OUT/ops/vm" dist

# the six packages the VM runs (packages/*), not the dashboard API (apps/api)
for p in packages/*/; do uv build "$p" --wheel --quiet --out-dir "$OUT/wheels"; done
rm -f "$OUT/wheels/.gitignore"
cp ops/run_sp100_forward_test.sh ops/run_daily_candidates.sh "$OUT/ops/"
cp ops/vm/install_release.sh ops/vm/crontab.txt ops/vm/maintain.sh ops/vm/memlog.sh ops/vm/tidy_home.sh ops/vm/sync_logs.sh \
   ops/vm/broker_snapshot.sh "$OUT/ops/vm/"
cp ops/vm/requirements-vm.txt "$OUT/"
mkdir -p "$OUT/defaults"
git archive HEAD config | tar -x -C "$OUT/defaults"     # committed content, never local edits
{
    echo "version=$VERSION"
    echo "commit=$COMMIT"
    echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    (cd "$OUT" && shasum -a 256 wheels/*.whl | sed 's/^/sha256 /')
} > "$OUT/MANIFEST"

n=$(ls "$OUT"/wheels/*.whl | wc -l | tr -d ' ')
[ "$n" -eq 6 ] || { echo "expected 6 wheels, built $n" >&2; exit 1; }

tar -czf "dist/$NAME.tar.gz" -C "$STAGE" "$NAME"
(cd dist && shasum -a 256 "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "dist/$NAME.tar.gz  ($n wheels, commit $COMMIT)"
