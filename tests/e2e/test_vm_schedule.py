"""The VM's schedule and maintenance scripts (ops/vm/crontab.txt, maintain.sh, tidy_home.sh,
sync_logs.sh, broker_snapshot.sh).

Each script runs for real in a temporary home folder; nothing here touches the VM or OpenD.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from midfreq.core.paths import repo_root

OPS = repo_root() / "ops"
VM = OPS / "vm"

pytestmark = pytest.mark.skipif(not shutil.which("bash") or not shutil.which("gzip"),
                                reason="needs bash and gzip, as the VM has")


def active_lines():
    return [l for l in (VM / "crontab.txt").read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#") and "=" not in l.split()[0]]


# ── crontab.txt ──────────────────────────────────────────────────────────────

def test_every_scheduled_script_ships_in_a_release():
    bundled = set(re.findall(r'(ops/\S+)', (OPS / "build_release.sh").read_text()))
    bundled = {p for b in bundled for p in b.split()}
    scheduled = {m for l in (VM / "crontab.txt").read_text().splitlines()
                 for m in re.findall(r'\$HOME/midfreq/current/(\S+)', l)}
    assert scheduled, "no scripts found in crontab.txt"
    for path in scheduled:
        assert (repo_root() / path).is_file(), f"{path} is scheduled but does not exist"
        assert os.access(repo_root() / path, os.X_OK), f"{path} is not executable"
        assert any(path in b for b in bundled) or path.startswith("ops/run_"), f"{path} is not in the bundle"


def test_the_schedule_never_reaches_github_or_the_old_clone():
    for line in active_lines():
        assert "git" not in line and "midfreq-trading-bot" not in line and "/bin/preflight" not in line, line


def test_the_session_fires_at_both_us_open_times_on_weekdays():
    session = [l for l in active_lines() if "run_node.sh" in l or "run_sp100_forward_test.sh" in l]
    assert len(session) == 1, "exactly one US session launcher may be active"
    assert "run_node.sh" in session[0] and "NODE_MODE=paper" in session[0]
    assert "NODE_CONFIG=config/node_sp100.yaml" in session[0]
    assert session[0].split()[:5] == ["35", "21,22", "*", "*", "1-5"]
    assert "--scheduled" in session[0] and "MIDFREQ_ROOT=$HOME/midfreq/runtime" in session[0]


def test_the_session_runner_line_is_kept_for_rollback():
    text = (VM / "crontab.txt").read_text()
    rollback = [l for l in text.splitlines() if l.startswith("#ROLLBACK ")]
    assert len(rollback) == 1 and "run_sp100_forward_test.sh --scheduled" in rollback[0]
    assert rollback[0][len("#ROLLBACK "):].split()[:5] == ["35", "21,22", "*", "*", "1-5"]


def test_every_active_line_has_five_time_fields():
    field = re.compile(r'^(\*|\d+(-\d+)?(,\d+(-\d+)?)*)(/\d+)?$')
    for line in active_lines():
        assert all(field.match(f) for f in line.split()[:5]), line


# ── maintain.sh ──────────────────────────────────────────────────────────────

def maintain(home, date, opend=None):
    env = dict(os.environ, MIDFREQ_HOME=str(home), MAINTAIN_DATE=date,
               OPEND_LOG_DIR=str(opend or home / "no-opend"))
    return subprocess.run(["bash", str(VM / "maintain.sh")], env=env, capture_output=True,
                          text=True, check=True).stdout


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "midfreq"
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "cron_sp100.log").write_text("october session output\n")
    (h / "logs" / "memlog.txt").write_text("trims itself\n")
    sessions = h / "runtime" / "live_sessions"
    sessions.mkdir(parents=True)
    (sessions / "console_sp100_reversal_20261016_213501.log").write_text("SESSION SUMMARY\n")
    (sessions / "state_crosssectionalreversal.json").write_text("{}")
    return h


def test_logs_are_archived_once_a_month_and_emptied(home):
    maintain(home, "2026-11-01")
    archived = home / "archive" / "cron_sp100.2026-11-01.log.gz"
    assert subprocess.run(["gzip", "-dc", str(archived)], capture_output=True, text=True).stdout \
        == "october session output\n"
    assert (home / "logs" / "cron_sp100.log").read_text() == ""
    assert (home / "logs" / "memlog.txt").read_text() == "trims itself\n"        # not a .log
    (home / "logs" / "cron_sp100.log").write_text("more\n")
    maintain(home, "2026-11-02")                                                   # same month: nothing
    assert (home / "logs" / "cron_sp100.log").read_text() == "more\n"
    maintain(home, "2026-12-01")
    assert (home / "archive" / "cron_sp100.2026-12-01.log.gz").exists()


def test_only_the_newest_twelve_archives_are_kept(home):
    (home / "archive").mkdir()
    for m in range(1, 15):
        (home / "archive" / f"cron_sp100.2025-{m:02d}-01.log.gz").write_bytes(b"x")
    maintain(home, "2026-11-01")
    kept = sorted(p.name for p in (home / "archive").glob("cron_sp100.*"))
    assert len(kept) == 12 and kept[-1] == "cron_sp100.2026-11-01.log.gz"
    assert "cron_sp100.2025-01-01.log.gz" not in kept


def test_debug_logs_past_retention_are_deleted_and_recent_ones_kept(home, tmp_path):
    opend = tmp_path / "Log"
    opend.mkdir()
    now = time.time()
    for name, age_h in (("py_old.log", 40), ("py_new.log", 10), ("a.ftlog", 15 * 24 + 1), ("b.ftlog", 24)):
        f = opend / name
        f.write_text("x")
        os.utime(f, (now - age_h * 3600, now - age_h * 3600))
    maintain(home, "2026-11-01", opend)
    assert sorted(p.name for p in opend.iterdir()) == ["b.ftlog", "py_new.log"]


def test_the_trading_record_is_never_touched_and_health_is_logged(home):
    before = {p.name: p.read_text() for p in (home / "runtime" / "live_sessions").iterdir()}
    maintain(home, "2026-11-01")
    after = {p.name: p.read_text() for p in (home / "runtime" / "live_sessions").iterdir()}
    assert before == after
    health = (home / "logs" / "health.log").read_text()
    assert "session=ok console_sp100_reversal_20261016_213501.log" in health and "disk=" in health


# ── tidy_home.sh ─────────────────────────────────────────────────────────────

@pytest.fixture
def vm_home(tmp_path):
    h = tmp_path / "home"
    for f in ("pos.py", "quota.py", "hk_fee.log", "preflight.log", "cron_sp100.log", "memlog.txt",
              "crontab.bak.20261009b", "bin/preflight.sh", "backup_20261015_phase0/crontab.txt",
              "opend/moomoo_OpenD_10.8.6818_Ubuntu18.04.tar.gz", "opend/moomoo_OpenD_10.8.6818_Ubuntu18.04/OpenD",
              "midfreq-trading-bot/README.md", "notes_i_want.txt"):
        (h / f).parent.mkdir(parents=True, exist_ok=True)
        (h / f).write_text(f)
    return h


def tidy(h, *args):
    return subprocess.run(["bash", str(VM / "tidy_home.sh"), *args], env=dict(os.environ, TIDY_HOME=str(h)),
                          capture_output=True, text=True, check=True).stdout


def snapshot(h):
    return sorted(str(p.relative_to(h)) for p in h.rglob("*"))


def test_tidy_dry_run_changes_nothing(vm_home):
    before = snapshot(vm_home)
    out = tidy(vm_home)
    assert snapshot(vm_home) == before
    assert "dry run" in out and "left alone (not on the list): notes_i_want.txt" in out


def test_tidy_apply_moves_known_items_and_leaves_the_rest(vm_home):
    tidy(vm_home, "--apply")
    m = vm_home / "midfreq"
    archives = list((m / "archive").glob("legacy-home-*.tar.gz"))
    assert len(archives) == 1
    names = subprocess.run(["tar", "-tzf", str(archives[0])], capture_output=True, text=True).stdout.split()
    assert sorted(names) == ["hk_fee.log", "pos.py", "preflight.log", "quota.py"]
    assert not (vm_home / "pos.py").exists()
    assert (m / "logs" / "cron_sp100.log").read_text() == "cron_sp100.log"
    assert (m / "backups" / "bin" / "preflight.sh").exists()
    assert (m / "backups" / "crontab.bak.20261009b").exists()
    assert not (vm_home / "opend" / "moomoo_OpenD_10.8.6818_Ubuntu18.04.tar.gz").exists()
    assert (vm_home / "opend" / "moomoo_OpenD_10.8.6818_Ubuntu18.04" / "OpenD").exists()      # the program stays
    assert (vm_home / "midfreq-trading-bot" / "README.md").exists()                        # removed by hand later
    assert (vm_home / "notes_i_want.txt").exists()


# ── memlog.sh ────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("free"), reason="free(1) is Linux-only; runs in CI")
def test_memlog_writes_one_line_into_the_logs_folder(tmp_path):
    env = dict(os.environ, MIDFREQ_HOME=str(tmp_path / "midfreq"))
    subprocess.run(["bash", str(VM / "memlog.sh")], env=env, check=True)
    lines = (tmp_path / "midfreq" / "logs" / "memlog.txt").read_text().splitlines()
    assert len(lines) == 1 and "ram_used=" in lines[0]


# ── sync_logs.sh ─────────────────────────────────────────────────────────────

def test_sync_logs_does_nothing_until_configured(tmp_path):
    env = dict(os.environ, MIDFREQ_HOME=str(tmp_path / "midfreq"), PATH="/usr/bin:/bin")  # no gcloud on PATH
    r = subprocess.run(["bash", str(VM / "sync_logs.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_sync_logs_never_deletes_from_the_bucket():
    text = (VM / "sync_logs.sh").read_text()
    assert "rsync" in text and "--delete" not in text


def test_the_schedule_sets_a_path_with_gcloud():
    assert "PATH=/usr/local/bin:/usr/bin:/bin:/snap/bin" in (VM / "crontab.txt").read_text()


# ── broker_snapshot.sh ───────────────────────────────────────────────────────

def test_broker_snapshots_are_published_every_five_minutes_into_their_own_log():
    (line,) = [l for l in active_lines() if "broker_snapshot.sh" in l]
    assert line.split()[:5] == ["*/5", "*", "*", "*", "*"]
    assert line.endswith(">> $HOME/midfreq/logs/broker_snapshot.log 2>&1")


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def snapshot_home(tmp_path):
    """A VM home in miniature: `current/.venv/bin/python` runs this interpreter (which has the
    packages), and runtime/config is the repository's, with OpenD on a port nothing listens on."""
    h = tmp_path / "midfreq"
    py = h / "current" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    shutil.copytree(repo_root() / "config", h / "runtime" / "config")
    (h / "runtime" / "live_sessions").mkdir()
    port = _closed_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIDFREQ_")}
    env.update(MIDFREQ_HOME=str(h), MIDFREQ_MOOMOO_PORT=str(port))
    return h, env


def broker_snapshot(env):
    return subprocess.run(["bash", str(VM / "broker_snapshot.sh")], env=env, capture_output=True, text=True,
                          timeout=120)


def test_broker_snapshot_does_nothing_when_no_account_publishes(snapshot_home):
    h, env = snapshot_home
    conns = h / "runtime" / "config" / "connections.yaml"
    conns.write_text(conns.read_text().replace("publish: true", "publish: false"))
    r = broker_snapshot(env)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert list((h / "runtime" / "live_sessions").iterdir()) == []


def test_broker_snapshot_logs_an_unreachable_gateway_and_keeps_the_last_snapshot(snapshot_home):
    h, env = snapshot_home
    last = h / "runtime" / "live_sessions" / "broker_moomoo-us.json"
    last.write_text('{"as_of": "2026-10-16T20:00:00+00:00"}\n')
    r = broker_snapshot(env)
    assert r.returncode == 1 and r.stderr == ""
    lines = r.stdout.splitlines()
    assert len(lines) == 2                                                  # moomoo-us and moomoo-hk
    for line, account in zip(lines, ("moomoo-us", "moomoo-hk")):
        assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d broker snapshot " + account + ": OpenD is not reachable", line)
    assert last.read_text() == '{"as_of": "2026-10-16T20:00:00+00:00"}\n'
    assert sorted(p.name for p in last.parent.iterdir()) == ["broker_moomoo-us.json"]
