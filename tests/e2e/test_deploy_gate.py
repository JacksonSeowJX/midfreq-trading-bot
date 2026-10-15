"""ops/vm/deploy_lib.sh: the VM merges origin/main only when CI passed on the same code.

Each test builds a throwaway repository with an `origin`, puts a fake `curl` first on
PATH that answers the GitHub API with a chosen list of CI-passed commits (or fails),
and sources the real deploy_lib.sh.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from midfreq.core.paths import repo_root

LIB = repo_root() / "ops" / "vm" / "deploy_lib.sh"

pytestmark = pytest.mark.skipif(not (shutil.which("jq") and shutil.which("git")),
                                reason="needs git and jq, as the VM has")


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, path, text, msg):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    sh(repo, "git", "add", path)
    sh(repo, "git", "commit", "-q", "-m", msg)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    """origin (bare), dev (pushes code) and vm (deploys) clones sharing one history."""
    git_env = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", *git_env, "init", "-q", "--bare", str(origin))
    dev = tmp_path / "dev"
    sh(tmp_path, "git", *git_env, "init", "-q", str(dev))
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        sh(dev, "git", "config", k, v)
    sh(dev, "git", "checkout", "-q", "-b", "main")
    base = commit(dev, "code.py", "v1\n", "base")
    sh(dev, "git", "remote", "add", "origin", str(origin))
    sh(dev, "git", "push", "-q", "origin", "main")
    vm = tmp_path / "vm"
    sh(tmp_path, "git", "clone", "-q", str(origin), str(vm))
    for k, v in (("user.name", "vm"), ("user.email", "vm@vm")):
        sh(vm, "git", "config", k, v)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    return {"dev": dev, "vm": vm, "bin": fakebin, "base": base}


def set_ci(world, passed_shas=None, fail=False):
    curl = world["bin"] / "curl"
    if fail:
        curl.write_text("#!/bin/sh\nexit 22\n")
    else:
        runs = ",".join(f'{{"head_sha":"{s}"}}' for s in passed_shas)
        curl.write_text(f"#!/bin/sh\necho '{{\"workflow_runs\":[{runs}]}}'\n")
    curl.chmod(0o755)


def gate(world, fn="ci_green origin/main"):
    vm = world["vm"]
    sh(vm, "git", "fetch", "-q", "origin", "main")
    r = subprocess.run(["bash", "-c", f". '{LIB}'; {fn}"], cwd=vm, capture_output=True, text=True,
                       env={"PATH": f"{world['bin']}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                            "HOME": str(vm)})
    return r.returncode, r.stdout


def test_new_code_is_held_until_ci_passes_on_it(world):
    new = commit(world["dev"], "code.py", "v2\n", "change")
    sh(world["dev"], "git", "push", "-q", "origin", "main")
    set_ci(world, [world["base"]])
    rc, out = gate(world)
    assert rc == 1 and "HOLD" in out
    set_ci(world, [new, world["base"]])
    assert gate(world)[0] == 0


def test_log_only_and_merge_commits_inherit_the_tested_code(world):
    dev, vm = world["dev"], world["vm"]
    tested = commit(dev, "code.py", "v2\n", "change")
    sh(dev, "git", "push", "-q", "origin", "main")
    # the VM commits session logs and pushes; the laptop pulls (a merge) and pushes again
    sh(vm, "git", "pull", "-q", "--no-rebase", "origin", "main")
    commit(vm, "live_sessions/s.jsonl", "{}\n", "logs [vm]")
    sh(vm, "git", "push", "-q", "origin", "main")
    commit(dev, "notes.txt", "x\n", "local")
    sh(dev, "git", "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    set_ci(world, [tested])
    sh(dev, "git", "push", "-q", "origin", "main")
    assert gate(world)[0] == 1                      # notes.txt is untested code
    set_ci(world, [sh(dev, "git", "rev-parse", "HEAD")])
    sh(vm, "git", "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    commit(vm, "live_sessions/s.jsonl", "{1}\n", "more logs [vm]")
    sh(vm, "git", "push", "-q", "origin", "main")
    assert gate(world)[0] == 0                      # only logs differ from the CI-passed tree


def test_unreachable_github_holds(world):
    commit(world["dev"], "code.py", "v2\n", "change")
    sh(world["dev"], "git", "push", "-q", "origin", "main")
    set_ci(world, fail=True)
    rc, out = gate(world)
    assert rc == 1 and "no passing CI runs readable" in out


def test_has_incoming(world):
    set_ci(world, [])
    assert gate(world, "has_incoming origin/main")[0] == 1
    commit(world["dev"], "code.py", "v2\n", "change")
    sh(world["dev"], "git", "push", "-q", "origin", "main")
    assert gate(world, "has_incoming origin/main")[0] == 0
