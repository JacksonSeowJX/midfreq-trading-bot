# ops

How code gets from this repository to the GCP VM that runs the live paper-trading sessions,
and the scripts involved.

> **Status (15 Oct 2026):** the VM keeps running the code it has until the forward test ends
> (~13 Nov). Phase 0 (cut its GitHub link) and Phase 1 (release tooling) are done; nothing
> has been deployed to it with this tooling yet.

## The model

The VM never pulls code. A version is released, then deployed by hand:

1. **Release.** `ops/set_version.sh X.Y.Z`, commit, `git tag vX.Y.Z`, push the tag. CI
   (`.github/workflows/release.yml`) builds the six wheels into a bundle, installs it exactly as
   the VM will, runs every test against the installed wheels, and attaches the bundle to a
   GitHub Release.
2. **Deploy.** `ops/deploy.sh vX.Y.Z` from a laptop, when no session is running. It downloads
   that bundle, checks its checksum, copies it to the VM and runs its installer there.
3. **Run.** Cron starts each session from `~/midfreq/current`. The new release trades from the
   next session; a session already running keeps the code it started with.
4. **Roll back.** `ops/deploy.sh --rollback` points `current` at the previous release again.

On the VM:

```
~/midfreq/
  releases/0.1.0/          one folder per release, never modified once installed
    .venv/                   its own environment: vm/requirements-vm.txt + the wheels
    ops/                     the session launchers
    defaults/config/         config/ as committed at that release
    MANIFEST                 version, commit, wheel checksums
  current -> releases/0.1.0
  previous                   the release before the last switch
  runtime/                 MIDFREQ_ROOT: config/, data/ (bar cache), live_sessions/
```

Code and data are separate: a release never writes to `runtime/`, and nothing in `runtime/`
is in git. Third-party versions on the VM are exactly `vm/requirements-vm.txt` (Python 3.10,
the moomoo SDK the VM's OpenD works with), not `uv.lock`, which is the research environment.

## Migration from "the VM pulls main"

Until 15 Oct 2026 cron on the VM pulled `main` at 09:10 SGT and merged it at 16:15 SGT, so
whatever was on `main` traded that night, and the VM pushed its session logs and state back
to `main`.

| Phase | When | What | Status |
|---|---|---|---|
| 0. Cut the GitHub link | 15 Oct 2026 | `preflight.sh` and `postclose.sh` cron lines commented out (backup in `~/backup_20261015_phase0/` on the VM). The VM keeps trading on the code it has; the session launcher, memlog and log cleanup stay. Session logs come to a laptop with `ops/fetch_live_logs.sh`. | **done** |
| 1. Release tooling | 15 Oct 2026 | `midfreq-live` entry point; `build_release.sh`, `vm/install_release.sh`, `deploy.sh`, `set_version.sh`; release workflow; CI's `vm` job tests the installed bundle | **done** |
| 2. Cutover | after the forward test (~13 Nov) | runbook below | to do |
| 3. Clean up | after cutover | delete `vm/preflight.sh`, `vm/postclose.sh`, `vm/git_selfheal.sh`, `vm/deploy_lib.sh` and `tests/e2e/test_deploy_gate.py` (the pull-based deploy and its CI gate); stop committing to `live_sessions/` | to do |

## Cutover runbook (Phase 2)

Only after the forward test's last session has ended, on a weekday before 21:00 SGT or at a
weekend.

**On a laptop**

```bash
git tag v0.1.0 && git push origin v0.1.0        # versions already say 0.1.0
# wait for the Release workflow to pass, then fetch the final logs for the record
./ops/fetch_live_logs.sh
```

**On the VM** (`gcloud compute ssh trading-bot --zone asia-southeast1-b`)

```bash
pgrep -f "[r]un_live" && echo "a session is running: stop here"
mkdir -p ~/backup_cutover && crontab -l > ~/backup_cutover/crontab.txt

# the runtime folder: copies, so the old clone stays intact for rollback
mkdir -p ~/midfreq/runtime
cp -a ~/midfreq-trading-bot/config ~/midfreq-trading-bot/data ~/midfreq-trading-bot/live_sessions ~/midfreq/runtime/
df -h /                                          # the bar cache is copied once; check space first
```

**On a laptop**

```bash
./ops/deploy.sh v0.1.0
```

The installer notes config files the runtime lacks (the old clone has no `connections.yaml` or
`venues.json`) and prints the OpenD address and trading environment the release will use;
it must say `127.0.0.1:11111, trading environment SIMULATE`. Copy the missing files in:

```bash
# on the VM; -n never overwrites, so sp100_forward_test.json stays the VM's own
cp -n ~/midfreq/current/defaults/config/* ~/midfreq/runtime/config/
```

**Cron on the VM** (`crontab -e`): the session line now runs the current release against the
runtime folder; times are unchanged.

```cron
# before
35 21,22 * * 1-5 cd ~/midfreq-trading-bot && PYTHON=~/midfreq-trading-bot/.venv/bin/python ./scripts/live/run_sp100_forward_test.sh --scheduled >> ~/cron_sp100.log 2>&1
# after
35 21,22 * * 1-5 MIDFREQ_ROOT=$HOME/midfreq/runtime $HOME/midfreq/current/ops/run_sp100_forward_test.sh --scheduled >> $HOME/cron_sp100.log 2>&1
```

Remove the commented-out `preflight.sh` and `postclose.sh` lines. `memlog.sh` and the 17:05
log cleanup stay.

**Verify**

```bash
# on the VM
DRY_RUN=1 FAKE_ET=$(TZ=America/New_York date +%Y-%m-%d)T09:35:01 MIDFREQ_ROOT=$HOME/midfreq/runtime \
    ~/midfreq/current/ops/run_sp100_forward_test.sh --scheduled
```

It must print `midfreq.live.run_live importable with /home/.../midfreq/releases/0.1.0/.venv/bin/python`
and a duration ending one to two minutes after 16:00 ET. After the first real session
(`ops/fetch_live_logs.sh`): 7 candles per symbol in `runtime/data`, the rebalance clock
advanced by 7, bot positions equal to the broker's.

**Then:** delete the VM's GitHub SSH key from the GitHub account (it can push to this
repository), and after a week of normal sessions, `rm -rf ~/midfreq-trading-bot`.

**Rollback during cutover:** `crontab ~/backup_cutover/crontab.txt` restores the old session
line, which runs the untouched old clone (its state file is in the old clone; copy
`~/midfreq/runtime/live_sessions/state_*.json` back over it first if a new-model session ran).

## Scripts

| Script | Runs on | What it does |
|---|---|---|
| `run_sp100_forward_test.sh` | VM (cron) | The S&P 100 forward test from `config/sp100_forward_test.json`. With `--scheduled` it runs only within 30 minutes after the 09:30 ET open, so cron fires it at both 21:35 and 22:35 SGT across the US clock change. `DRY_RUN=1` prints the command without trading. |
| `run_daily_candidates.sh` | VM (cron, paused) | The Hong Kong roster: three sessions until 16:01 HKT. Paused while the S&P 100 test runs (the two exceed the subscription quota). |
| `fetch_live_logs.sh` | laptop | Copies `live_sessions/` from the VM and reports its health (session running, OpenD listening, crashes, disk) |
| `deploy.sh` | laptop | Deploy, stage, switch, roll back, status (see The model) |
| `build_release.sh` | CI, laptop | Builds `dist/midfreq-X.Y.Z.tar.gz` |
| `set_version.sh` | laptop | One version on every package |
| `vm/install_release.sh` | VM (via `deploy.sh`) | Installs a bundle, smoke-tests it, switches `current` |
| `vm/requirements-vm.txt` | VM | The VM's pinned third-party versions (frozen from its venv 15 Oct 2026, plus PyYAML) |
| `vm/memlog.sh` | VM (cron) | RAM and top processes once a minute |
| `vm/preflight.sh`, `vm/postclose.sh`, `vm/git_selfheal.sh`, `vm/deploy_lib.sh` | not installed | The pull-based deploy (disabled in Phase 0) and its CI gate; deleted in Phase 3 |

The launchers find their code from their own location (a checkout, or a release with `.venv/`
beside `ops/`) and their data from `MIDFREQ_ROOT` (a checkout is its own runtime root). Both
start the engine as `python -m midfreq.live.run_live`, so `pgrep -f "[r]un_live"` finds a
session under either.

OpenD's host, port and trading environment come from the `moomoo` entry of
`config/connections.yaml`: `127.0.0.1:11111`, `trading_env: simulate`. Real money is used
only if that entry, or `MIDFREQ_MOOMOO_TRADING_ENV`, says `real`; without the file the engine
falls back to the same paper defaults. Do not set either on the VM.

## CI

`.github/workflows/ci.yml` runs on every push and pull request (except commits that only touch
`live_sessions/`):

| Job | Python | Versions | Runs |
|---|---|---|---|
| `research` | 3.12 | `uv.lock` (the Anaconda versions every reported result was produced with) | all tests, `lint-imports` |
| `vm` | 3.10 | `vm/requirements-vm.txt` | builds the bundle, installs it with `vm/install_release.sh` (smoke test, launcher dry run), runs all tests against the installed wheels |
