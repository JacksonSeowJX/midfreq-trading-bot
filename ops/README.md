# ops

The shell scripts cron runs on the GCP VM, and the runbook for moving the VM onto the
monorepo layout.

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

# Install uv (once) and build the workspace venv at the repository root (.venv/)
curl -LsSf https://astral.sh/uv/install.sh | sh
git pull --ff-only origin main
uv sync
```

`uv sync` installs the six `midfreq-*` packages editable, so `.venv/bin/python` imports
`midfreq.*` with no `sys.path` set-up. `/opt/anaconda3` is no longer used by these scripts and
can stay installed for rollback.

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
HK roster is re-enabled. If cron sets `PYTHON=/opt/anaconda3/bin/python`, remove it: that
interpreter does not have the `midfreq` packages installed.

### 4. Preflight and postclose

- The preflight's test step becomes `uv run pytest -q -p no:cacheprovider` (it ran pytest
  under /opt/anaconda3). It collects every package's tests plus `tests/`; the slow
  real-data tests stay out of the default run.
- Any `python` or `python3` call in `~/bin` that imports project code becomes `uv run python`
  (or `.venv/bin/python`). Any path under `scripts/live/run_*.sh` becomes `ops/run_*.sh`.
- Run `uv sync` after each `git merge`/`git pull` in preflight and postclose, so a change to
  dependencies is installed before the next session.
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

This restores `scripts/live/*.sh`, `run_live.py` at the root and the anaconda interpreter.
Do not roll back with `git checkout <previous commit> -- .` or `git reset --hard`: both would
also restore the old `live_sessions/state_*.json`, losing the rebalance clock and position
ownership recorded since. The state file format is unchanged by the layout, so a rolled-back
session resumes from the current state. The `.venv/` folder can be left in place.
