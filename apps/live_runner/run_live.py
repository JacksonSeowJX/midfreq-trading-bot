"""
Run the live trading engine from a checkout: `uv run python apps/live_runner/run_live.py --list`.

The runner itself is midfreq.live.run_live, installed as the `midfreq-live` command, so a
release on the VM runs the same code without this file.
"""
from midfreq.live.run_live import main

if __name__ == "__main__":
    main()
