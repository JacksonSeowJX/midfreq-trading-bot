"""
The node's health at a glance: the process's resident memory, and the heartbeat as one readable
line for the ops log.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Optional


# ── heartbeat and resident memory ────────────────────────────────────────────────────────────

def resident_memory_mb() -> Optional[float]:
    """The process's resident memory: current on Linux (/proc), peak elsewhere."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0, 1)
    except Exception:
        return None


def describe_heartbeat(hb: Any) -> str:
    """One readable line for a midfreq.trading.interfaces.Heartbeat."""
    if hb.last_bar_ts:
        last = datetime.fromtimestamp(hb.last_bar_ts / 1e9, tz=timezone.utc).strftime("%H:%M:%S UTC")
    else:
        last = "none yet"
    lag = f"{hb.stream_lag_s:.0f}s" if hb.stream_lag_s is not None else "n/a"
    held = ", ".join(f"{k} {v:g}" for k, v in sorted(hb.positions.items())) or "none"
    mem = f"{hb.memory_mb:.0f} MB" if hb.memory_mb is not None else "n/a"
    return f"heartbeat: last bar {last}, stream silent {lag}, open orders {hb.open_orders}, held {held}, rss {mem}"
