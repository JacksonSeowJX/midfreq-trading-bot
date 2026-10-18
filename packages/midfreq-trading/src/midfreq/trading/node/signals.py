"""
SIGTERM and SIGINT while the node runs: the handler only sets the stop flag, which the loop reads
at its next turn, and the previous handlers are put back when the loop ends.
"""
from __future__ import annotations

import signal
import threading


class SignalsMixin:
    """TradingNode's signal handlers, installed by run() on the main thread. Relies on the node's
    `_previous_signals`, and sets its `_stop_flag`."""

    # ── signals ──────────────────────────────────────────────────────────────────────────────
    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, frame):
            # Only a flag: taking the queue's lock here could deadlock with the loop's get().
            self._stop_flag = f"signal {signal.Signals(signum).name}"
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._previous_signals[sig] = signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def _restore_signal_handlers(self) -> None:
        for sig, previous in self._previous_signals.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass
        self._previous_signals.clear()
