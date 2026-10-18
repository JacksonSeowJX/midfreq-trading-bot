"""
The node's inbox: a thread-safe, first-in-first-out queue that the connectors' callbacks put on
and the node's one loop takes from; and two of the items it carries besides market data, order
events and timers: a request to stop, and a change in an execution client's connection.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any, Optional

from midfreq.data.adapters.base import StreamStatus


# ── queue items ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StopRequest:
    reason: str = "requested"


@dataclass(frozen=True)
class ExecutionStatus:
    """A change in an execution client's connection (ExecutionClient.set_status_handler),
    tagged with the account it serves."""
    account_id: str
    status: StreamStatus


class NodeEventQueue:
    """The node's inbox (midfreq.trading.interfaces.EventQueue): thread-safe, first in first out."""

    def __init__(self):
        self._q: "queue.Queue[Any]" = queue.Queue()
        self.put_count = 0

    def put(self, item: Any) -> None:
        self._q.put(item)
        self.put_count += 1

    def get(self, timeout: Optional[float] = None) -> Any:
        """The next item, or None when nothing arrived within `timeout` (0: do not wait)."""
        try:
            if timeout is not None and timeout <= 0:
                return self._q.get_nowait()
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()
