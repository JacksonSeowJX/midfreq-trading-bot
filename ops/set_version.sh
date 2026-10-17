#!/bin/bash
# Set one version on every package (and the workspace root), and refresh uv.lock.
#
# A release is tagged with the same version: ops/set_version.sh 0.2.0, commit, then
# `git tag v0.2.0 && git push origin v0.2.0`, which makes CI build and publish the bundle.
#
# midfreq-engine (the C++ backtest core) is kept in step too, so the workspace has one version,
# although releases do not ship it: build_release.sh skips it when it builds the wheels and when
# it checks that the shipped packages agree, so an engine merged at another version (0.1.0 from
# feature/native-engine) never blocks a release build. Its version lives only in its
# pyproject.toml (CMake takes it from scikit-build-core).
#
# Usage: ops/set_version.sh X.Y.Z

set -euo pipefail
cd "$(dirname "$0")/.."
V=${1:?usage: ops/set_version.sh X.Y.Z}
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not X.Y.Z: $V" >&2; exit 1; }
for f in pyproject.toml packages/*/pyproject.toml; do
    # -i.bak works with both GNU and BSD sed; only [project] has a line starting "version ="
    sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$V\"/" "$f"
    rm -f "$f.bak"
done
uv lock --quiet
grep -H '^version = ' pyproject.toml packages/*/pyproject.toml
