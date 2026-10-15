# ops

How code gets from this repository to the GCP VM that runs the live paper-trading sessions,
and the scripts involved.

> **Status (15 Oct 2026):** the VM keeps running the code it has until the forward test ends
> (~13 Nov). Phase 0 (cut its GitHub link) and Phase 1 (release tooling) are done; nothing
> has been deployed to it with this tooling yet.

## The model

The VM never pulls code. A version is released, then deployed by hand:

1. **Release.** `ops/set_version.sh X.Y.Z` and commit (not needed for 0.1.0), push `main`, then
   either Actions -> Release -> **Run workflow** with the version, or push a tag
   (`git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`). The workflow
   (`.github/workflows/release.yml`) builds the six wheels into a bundle, installs it exactly as
   the VM will, runs every test against the installed wheels, and publishes a GitHub Release
   with the bundle (creating the tag, when started from the button).
2. **Deploy.** `ops/deploy.sh vX.Y.Z` from a laptop, when no session is running. It downloads
   that bundle, checks its checksum, copies it to the VM and runs its installer there.
3. **Run.** Cron starts each session from `~/midfreq/current`. The new release trades from the
   next session; a session already running keeps the code it started with.
4. **Roll back.** `ops/deploy.sh --rollback` points `current` at the previous release again.

Steps 2 and 4 can also be run from GitHub: Actions -> **Deploy** -> Run workflow (see
"Deploy workflow" below). Both routes run the same installer on the VM.

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
  runtime/                 MIDFREQ_ROOT: config/, data/ (bar cache), live_sessions/ (kept forever)
  logs/                    operations logs: cron_sp100.log, deploy.log, maintain.log, health.log, memlog.txt
  archive/                 monthly .log.gz of logs/ (12 per log), legacy-home tarball
  bin/                     deploy_gate.sh only (installed by hand, never by a release)
  backups/                 crontab and ~/bin from before the cutover, authorized_keys
~/opend/                   the OpenD program (systemd service opend.service)
~/.com.moomoo.OpenD/       OpenD's own logs and cache (retention in vm/maintain.sh)
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

## Deploy workflow

`.github/workflows/deploy.yml`, started by hand: Actions -> Deploy -> Run workflow, with an
action (`status`, `deploy`, `stage`, `switch`, `rollback`), a version and a `force` box.

1. **plan** checks the inputs and that the release is published, and shows the exact command.
2. **apply** waits for approval (the `production` environment), then runs that command on the
   VM over SSH and puts the VM's answer in the run summary.

The workflow's SSH key can run one thing on the VM: `vm/deploy_gate.sh`. Its line in the VM's
`~/.ssh/authorized_keys` is

```
command="/home/jacksonetherchainstake/midfreq/bin/deploy_gate.sh",restrict ssh-ed25519 AAAA... github-deploy midfreq-trading-bot
```

so whatever is sent arrives as `$SSH_ORIGINAL_COMMAND`, and the gate accepts only `status`,
`rollback`, `switch X.Y.Z` and `deploy X.Y.Z [--stage] [--force]`. For a deploy the VM downloads
the GitHub Release itself and checks its checksum; the key never uploads code, and `restrict`
denies it a terminal and all forwarding. Tested 15 Oct 2026: arbitrary commands, a shell and a
tunnel to OpenD's port were all refused. Every request is logged in `~/midfreq/deploy.log`.

**Setup (done 15 Oct 2026 unless noted)**

- Key pair: `~/.ssh/midfreq_github_deploy` on the laptop (ed25519, no passphrase).
- VM: `~/midfreq/bin/deploy_gate.sh` (a copy of `vm/deploy_gate.sh`; reinstall by hand after
  changing it) and the `authorized_keys` line above (backup of the file before:
  `~/backup_20261015_deploykey/`).
- GitHub, Settings -> Environments -> New environment `production`:
  - Required reviewers: yourself. Deployment branches and tags: selected, `main`.
  - Secret `VM_SSH_KEY`: the private key (`pbcopy < ~/.ssh/midfreq_github_deploy`).
  - Variables `VM_HOST` = `34.126.79.81`, `VM_USER` = `jacksonetherchainstake`,
    `VM_KNOWN_HOSTS` = `34.126.79.81 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOJK9cv/uXcuOnh9P83quhChMKiGwBS13GJHOWqqBGPa`
    (the VM's host key, read from `/etc/ssh/ssh_host_ed25519_key.pub`).

The VM's external IP is not reserved: if the VM is stopped and started it may change, and
`VM_HOST` and `VM_KNOWN_HOSTS` must follow (or reserve the address in GCP).

**Revoke:** delete the `github-deploy midfreq-trading-bot` line from the VM's
`~/.ssh/authorized_keys` and the `VM_SSH_KEY` secret. **Rotate:** make a new key pair, swap the
line and the secret.

## Hosted dashboard

The dashboard (apps/web over apps/api) also runs on **Cloud Run** as a public, read-only site,
separate from the trading VM. It shows the research results built into its image and the bot's
record from a private Cloud Storage bucket, `gs://midfreq-trading-bot-live-sessions/live_sessions/`,
which it mirrors at most once a minute. It has no broker account panels: they need OpenD, which
only the VM and a laptop have (run the dashboard locally for those).

```
VM  --(sync_logs.sh, every 2 min, uploader account: this bucket only)-->  bucket
Cloud Run (dashboard-runtime account: read this bucket only)  <--  browser
```

- One-time setup: `ops/dashboard/setup_gcp.sh` (done 16 Oct 2026: bucket with public access
  prevented, Artifact Registry `midfreq`, the `dashboard-runtime` account).
- Deploy: `ops/dashboard/deploy_dashboard.sh` builds the committed code (`git archive HEAD`, never
  local edits) with Cloud Build and deploys the service `midfreq-dashboard` in asia-southeast1.
- Until the cutover the bucket is filled from a laptop:
  `./ops/fetch_live_logs.sh && gcloud storage rsync live_sessions gs://midfreq-trading-bot-live-sessions/live_sessions`.
  From the cutover the VM uploads every 2 minutes (`vm/sync_logs.sh`, step 4b below).

## Cutover runbook (Phase 2)

Planned for Saturday 17 Oct 2026, after the report's final forward-test numbers are taken from
the old setup. No session runs at weekends; the first session on releases is Monday 21:35 SGT.

**1. Release v0.1.1** (laptop; it is the first release with `vm/crontab.txt`, `maintain.sh`,
`memlog.sh` and `tidy_home.sh`)

```bash
./ops/set_version.sh 0.1.1 && git commit -am "Release 0.1.1" && git push origin main
# Actions -> Release -> Run workflow, version 0.1.1; wait for it to pass
./ops/fetch_live_logs.sh                       # the old setup's final logs, for the record
```

**2. Runtime folder** (VM: `gcloud compute ssh trading-bot --zone asia-southeast1-b`)

```bash
pgrep -af "[r]un_live" && echo "a session is running: stop here"
mkdir -p ~/backup_cutover && crontab -l > ~/backup_cutover/crontab.txt
mkdir -p ~/midfreq/runtime
# copies, so the old clone stays intact for rollback (about 45 MB)
cp -a ~/midfreq-trading-bot/config ~/midfreq-trading-bot/data ~/midfreq-trading-bot/live_sessions ~/midfreq/runtime/
```

**3. Deploy v0.1.1** (Actions -> Deploy -> `deploy`, `0.1.1`, approve; or `./ops/deploy.sh v0.1.1`).
The installer must report `127.0.0.1:11111, trading environment SIMULATE` and lists the config
files the runtime lacks. Copy those in on the VM (`-n` never overwrites, so
`sp100_forward_test.json` stays the VM's own):

```bash
cp -n ~/midfreq/current/defaults/config/* ~/midfreq/runtime/config/
```

**4. Schedule** (VM): replace the crontab with the versioned one, which runs everything from
`~/midfreq/current` and drops the disabled git jobs.

```bash
crontab ~/midfreq/current/ops/vm/crontab.txt && crontab -l
```

**4b. Log uploads for the hosted dashboard** (laptop): an account that may only read and write
objects in the dashboard's bucket, its key copied to the VM and deleted locally.

```bash
P=midfreq-trading-bot; SA=vm-log-uploader@$P.iam.gserviceaccount.com
gcloud iam service-accounts create vm-log-uploader --display-name "VM: uploads the bot's record"
gcloud storage buckets add-iam-policy-binding gs://$P-live-sessions --member serviceAccount:$SA --role roles/storage.objectUser
K=$(mktemp) && gcloud iam service-accounts keys create "$K" --iam-account $SA
gcloud compute ssh trading-bot --zone asia-southeast1-b --command 'mkdir -m 700 -p ~/midfreq/secrets'
gcloud compute scp --zone asia-southeast1-b "$K" trading-bot:midfreq/secrets/uploader.json && rm -f "$K"
gcloud compute ssh trading-bot --zone asia-southeast1-b --command \
  "chmod 600 ~/midfreq/secrets/uploader.json && echo gs://$P-live-sessions/live_sessions > ~/midfreq/secrets/bucket \
   && ~/midfreq/current/ops/vm/sync_logs.sh && echo uploaded"
```

**5. Tidy the home folder** (VM): dry run first, read it, then apply.

```bash
~/midfreq/current/ops/vm/tidy_home.sh            # the plan; anything unexpected is "left alone"
~/midfreq/current/ops/vm/tidy_home.sh --apply
```

**6. Verify** (VM)

```bash
DRY_RUN=1 FAKE_ET=2026-10-19T09:35:01 MIDFREQ_ROOT=$HOME/midfreq/runtime \
    ~/midfreq/current/ops/run_sp100_forward_test.sh --scheduled
~/midfreq/current/ops/vm/maintain.sh && tail -1 ~/midfreq/logs/health.log
```

The dry run must print `midfreq.live.run_live importable with .../midfreq/releases/0.1.1/.venv/bin/python`
and duration 386; the health line must say `opend=up release=0.1.1`. Also run the Deploy
workflow's `status`. After Monday's session (`./ops/fetch_live_logs.sh`): 7 new candles per
symbol, the rebalance clock advanced by 7, bot positions equal to the broker's.

**7. Afterwards:** remove the VM's own GitHub SSH key (the one its old clone used to push:
`~/.ssh/id_*` on the VM and its entry under GitHub -> Settings -> SSH keys or the repository's
deploy keys); after a week of normal sessions, `rm -rf ~/midfreq-trading-bot`.

**Rollback** (any time before the old clone is removed): `mv ~/midfreq/backups/bin ~/bin` (if
step 5 ran), `crontab ~/midfreq/backups/backup_cutover/crontab.txt` (or `~/backup_cutover/`
before step 5), and copy `~/midfreq/runtime/live_sessions/state_*.json` back into
`~/midfreq-trading-bot/live_sessions/` if a session ran on the release, so the old clone
resumes from the current rebalance clock and positions.

## Scripts

| Script | Runs on | What it does |
|---|---|---|
| `run_sp100_forward_test.sh` | VM (cron) | The S&P 100 forward test from `config/sp100_forward_test.json`. With `--scheduled` it runs only within 30 minutes after the 09:30 ET open, so cron fires it at both 21:35 and 22:35 SGT across the US clock change. `DRY_RUN=1` prints the command without trading. |
| `run_daily_candidates.sh` | VM (cron, paused) | The Hong Kong roster: three sessions until 16:01 HKT. Paused while the S&P 100 test runs (the two exceed the subscription quota). |
| `fetch_live_logs.sh` | laptop | Copies `live_sessions/` from the VM and reports its health (session running, OpenD listening, crashes, disk) |
| `deploy.sh` | laptop | Deploy, stage, switch, roll back, status (see The model) |
| `build_release.sh` | CI, laptop | Builds `dist/midfreq-X.Y.Z.tar.gz` |
| `set_version.sh` | laptop | One version on every package |
| `vm/install_release.sh` | VM (via `deploy.sh` or the gate) | Installs a bundle, smoke-tests it, switches `current` |
| `vm/deploy_gate.sh` | VM (the Deploy workflow's key) | The only command the workflow's SSH key may run |
| `vm/requirements-vm.txt` | VM | The VM's pinned third-party versions (frozen from its venv 15 Oct 2026, plus PyYAML) |
| `vm/crontab.txt` | VM | The whole schedule; installed with `crontab ~/midfreq/current/ops/vm/crontab.txt` |
| `vm/maintain.sh` | VM (cron, 17:05) | Monthly archive of `~/midfreq/logs/*.log` into `~/midfreq/archive` (12 kept), SDK/OpenD log retention (36 h / 14 days), a line in `logs/health.log` |
| `vm/memlog.sh` | VM (cron, every minute) | RAM and top processes into `~/midfreq/logs/memlog.txt` (~10 days) |
| `vm/sync_logs.sh` | VM (cron, every 2 min) | Uploads `runtime/live_sessions` to the hosted dashboard's bucket with its own gcloud config; never deletes; does nothing until configured |
| `dashboard/` | laptop | `setup_gcp.sh` (one-time cloud setup), `deploy_dashboard.sh`, the Dockerfile and Cloud Build config of the hosted dashboard |
| `vm/tidy_home.sh` | VM (once, cutover) | Moves the home folder into the layout below; dry run unless `--apply` |
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
