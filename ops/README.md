# ops

The shell scripts cron runs on the GCP VM, the VM's deploy scripts (`vm/`), and the runbook
for moving the VM onto the monorepo layout.

> **On hold (15 Oct 2026):** the VM is NOT to be switched to this layout for now. It keeps
> running the code it has.

## Migration: from "the VM pulls main" to manual, versioned releases

Until 15 Oct 2026 the VM deployed by `git pull`: cron pulled `main` at 09:10 SGT and merged it
at 16:15 SGT, so whatever was on `main` traded that night, and the VM pushed its session logs
and state back to `main`. The target is the usual model: CI builds versioned wheels, a release
is deployed to the VM by hand when no session is running, and the VM has no tie to GitHub.

| Phase | When | What | Status |
|---|---|---|---|
| 0. Cut the GitHub link | 15 Oct 2026 | Comment out the `preflight.sh` and `postclose.sh` cron lines (backup in `~/backup_20261015_phase0/` on the VM). The VM keeps trading on the code it has; the session launcher, memlog and log cleanup stay. Session logs come to a laptop with `ops/fetch_live_logs.sh`, which also reports what preflight checked. | **done** |
| 1. Build the release model | before 13 Nov, on this branch, no VM changes | Wheels built in CI and attached to a GitHub Release on a `vX.Y.Z` tag; a `midfreq-live` entry point; `ops/deploy.sh vX.Y.Z` installs a release into `~/midfreq/releases/vX.Y.Z/` (VM-pinned versions), smoke-tests it and switches `~/midfreq/current`, rollback = switch back; runtime data (config, bar cache, state, logs) in `~/midfreq/runtime/` via `MIDFREQ_ROOT` | to do |
| 2. Cutover | after the forward test (~13 Nov) | Copy data/, live_sessions/ and config into the runtime folder; deploy v0.1.0; point the launcher's cron line at `~/midfreq/current`; dry run; first session checked (7 candles, clock +7, bot == broker); remove the VM's GitHub SSH key; keep the old clone a week for rollback | to do |
| 3. Clean up | after cutover | Delete `vm/preflight.sh`, `vm/postclose.sh`, `vm/deploy_lib.sh` (the CI gate below, superseded); stop committing to `live_sessions/` | to do |

The CI gate described below was the first answer to "main goes live untested"; Phase 1
replaces it, and its scripts are not installed on the VM.

## CI/CD

**CI** (`.github/workflows/ci.yml`) runs on every push and pull request, except commits that
only touch `live_sessions/` (the VM's session logs). Two jobs, one per environment:

| Job | Python | Versions | Runs |
|---|---|---|---|
| `research` | 3.12 | `uv.lock` (the Anaconda versions every reported result was produced with) | all tests, `lint-imports` |
| `vm` | 3.10 | `vm/requirements-vm.txt` (the VM's live venv, frozen 15 Oct 2026, plus PyYAML) | all tests, launcher dry run |

**CD** stays pull-based: the VM deploys itself at 09:10 (preflight) and 16:15 SGT (postclose),
the only times no session is running, rather than GitHub pushing into it mid-session. What
changes is a gate (`vm/deploy_lib.sh`): the VM merges `origin/main` only if CI passed on
byte-identical code (everything outside `live_sessions/`). It fails closed: CI still running,
failed, or GitHub unreachable all mean "stay on the current code and retry at the next sync".
Before this, postclose merged `main` with no test at all, so a morning push went live that
night untested (9 Oct). The gate is tested in `tests/e2e/test_deploy_gate.py`.

| File in `vm/` | Installed as | What it does |
|---|---|---|
| `preflight.sh` | `~/bin/preflight.sh` | 09:10: self-heal git, gated pull, sync the venv, run all tests, roll back on failure |
| `postclose.sh` | `~/bin/postclose.sh` | 16:15: commit session logs, gated merge of `main`, sync the venv, push |
| `deploy_lib.sh` | `~/bin/deploy_lib.sh` | `ci_green`, `has_incoming`, `sync_env` |
| `git_selfheal.sh`, `memlog.sh` | `~/bin/` | unchanged copies of what runs on the VM |
| `requirements-vm.txt` | (read by `sync_env`) | the VM venv's exact package versions |

The `~/bin` copies are installed by hand (step 4 below), never run from the working tree:
bash reads a script while running it, and a pull that rewrote the running script would
corrupt it.

| Script | What it does |
|---|---|
| `run_sp100_forward_test.sh` | The S&P 100 cross-sectional reversal forward test, configured from `config/sp100_forward_test.json`. With `--scheduled` it runs only within 30 minutes after the 09:30 ET open, so cron can fire it at both 21:35 and 22:35 SGT across the US clock change. `DRY_RUN=1` prints the command without trading. |
| `run_daily_candidates.sh` | The Hong Kong roster: three parallel sessions until 16:01 HKT. Paused while the S&P 100 test runs (the two together exceed the subscription quota). |

Both `cd` to the repository root, run `apps/live_runner/run_live.py` with `$PYTHON`
(default `.venv/bin/python`, the workspace venv) and log to `live_sessions/`. Apart from those
paths they behave exactly as `scripts/live/run_*.sh` did on `main`.

OpenD's host, port and trading environment come from the `moomoo` entry of
`config/connections.yaml`. It ships as `127.0.0.1:11111`, `trading_env: simulate`; real money
is used only if that entry, or `MIDFREQ_MOOMOO_TRADING_ENV`, says `real`. Do not set either on
the VM.

## VM migration runbook

**Do this only after the forward test has ended (about 13 Nov 2026).** The VM's preflight
(09:10 SGT) and postclose (16:15 SGT) both merge `origin/main`, so merging this layout into
`main` deploys it at the next of those two times. Merge after the last session's postclose,
or stop the cron entries first.

The VM's helper scripts in `~/bin` (`preflight.sh`, `postclose.sh`, `git_selfheal.sh`,
`memlog.sh`) are not in git. Back each one up before editing it, and keep the rule that no
automated script runs `git reset --hard`: `live_sessions/state_*.json` is tracked and changes
every night.

### 1. Before merging (on a development machine)

```bash
uv sync
uv run pytest -q -p no:cacheprovider
uv run lint-imports
# CI must be green on the branch (both jobs) before it is merged
# Needs the bar cache: point MIDFREQ_ROOT at a checkout that has data/ (it is only read)
uv run python scripts/live/replay_live_sessions.py      # must end with ALL INVARIANTS HELD
DRY_RUN=1 FAKE_ET=2026-11-16T09:35:01 ./ops/run_sp100_forward_test.sh --scheduled
```

### 2. On the VM

```bash
# Note the commit currently deployed, for rollback
cd ~/midfreq-trading-bot && git rev-parse HEAD > ~/pre_monorepo_commit.txt
for f in ~/bin/*.sh; do cp "$f" "$f.pre_monorepo"; done
crontab -l > ~/crontab.pre_monorepo

git pull -q --rebase --autostash origin main

# Keep the VM's own venv (Python 3.10, its current versions); add PyYAML and the
# six midfreq packages, installed editable without touching any other version
.venv/bin/python -m pip install -r ops/vm/requirements-vm.txt
.venv/bin/python -m pip install --no-deps -e packages/midfreq-core -e packages/midfreq-data \
    -e packages/midfreq-strategies -e packages/midfreq-backtest -e packages/midfreq-live \
    -e packages/midfreq-research
```

Do NOT run `uv sync` on the VM: it would rebuild `.venv` from `uv.lock`, the research
environment, which moves the live engine to Python 3.12 and downgrades the moomoo SDK
(10.8.6808 -> 10.2.6208) and numpy (2.2.6 -> 1.26.4) mid forward test. The VM's versions are
the ones in `vm/requirements-vm.txt`, which CI's `vm` job tests on every push.

### 3. Cron

Only the script paths change; the times stay as they are. For example:

```cron
# before
35 21 * * 1-5  cd ~/midfreq-trading-bot && ./scripts/live/run_sp100_forward_test.sh --scheduled >> ...
35 22 * * 1-5  cd ~/midfreq-trading-bot && ./scripts/live/run_sp100_forward_test.sh --scheduled >> ...
# after
35 21 * * 1-5  cd ~/midfreq-trading-bot && ./ops/run_sp100_forward_test.sh --scheduled >> ...
35 22 * * 1-5  cd ~/midfreq-trading-bot && ./ops/run_sp100_forward_test.sh --scheduled >> ...
```

Likewise `scripts/live/run_daily_candidates.sh` becomes `ops/run_daily_candidates.sh` if the
HK roster is re-enabled. The cron line's `PYTHON=.../.venv/bin/python` stays as it is.

### 4. Preflight and postclose

Install the tracked versions, which add the CI gate and the venv sync, over the backups
taken in step 2:

```bash
install -m 755 ops/vm/preflight.sh ops/vm/postclose.sh ops/vm/deploy_lib.sh \
    ops/vm/git_selfheal.sh ops/vm/memlog.sh ~/bin/
```

- The preflight's test step becomes `.venv/bin/python -m pytest -q -p no:cacheprovider` (every
  package's tests plus `tests/`; the slow real-data tests stay out of the default run).
- The gate needs `curl` and `jq` (both on the VM) and reads the public GitHub API without a
  token (60 requests an hour; it makes two a day). CI must have passed on `main` before the
  first gated sync, or the VM holds its current code until it has.
- Process checks still match: `pgrep -f "[r]un_live.py"` finds
  `apps/live_runner/run_live.py`.

### 5. Verify

```bash
DRY_RUN=1 FAKE_ET=$(TZ=America/New_York date +%Y-%m-%d)T09:35:01 ./ops/run_sp100_forward_test.sh --scheduled
~/bin/preflight.sh            # or however it is normally started; must pass
```

The dry run must print `apps/live_runner/run_live.py found` and a duration ending one to two
minutes after 16:00 ET. After the first real session, check as before: 7 candles per symbol
per day in the cache, the rebalance clock advanced by 7, and bot positions equal to the
broker's.

### Rollback

Go back to the previous commit's code, keeping the current session logs and state:

```bash
# On a development machine: undo the merge on main, so the VM's twice-daily
# merge of origin/main does not bring the new layout straight back
git revert -m 1 <merge commit> && git push origin main

# On the VM
cd ~/midfreq-trading-bot
git pull --ff-only origin main      # code now equals the previous commit (check: git diff "$(cat ~/pre_monorepo_commit.txt)" -- . ':!live_sessions')
crontab ~/crontab.pre_monorepo
for f in ~/bin/*.sh.pre_monorepo; do cp "$f" "${f%.pre_monorepo}"; done
```

This restores `scripts/live/*.sh`, `run_live.py` at the root and the previous `~/bin` scripts.
Do not roll back with `git checkout <previous commit> -- .` or `git reset --hard`: both would
also restore the old `live_sessions/state_*.json`, losing the rebalance clock and position
ownership recorded since. The state file format is unchanged by the layout, so a rolled-back
session resumes from the current state. The `.venv/` folder can be left in place.
