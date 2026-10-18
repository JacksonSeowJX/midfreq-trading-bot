"""
The session log, `session_<YYYYmmdd_HHMMSS>_<strategy>_<pid>_<n>.jsonl`, in exactly the original
engine's format (midfreq.live.live_engine.LiveTradingEngine._log_event and its callers), so the
dashboard, the API (apps/api, routers/record.py) and midfreq.research.forward_test.load read a
session written by the node as they read one written by the original engine.

One JSON object per line, written with `json.dumps(event)` (default separators) and appended as
it happens, so a crash loses at most the line being written. The events and their keys, in order:

    session_start  timestamp, strategy, symbols, timeframe, account
    candle_close   symbol, timestamp, close, equity
    trade          timestamp, symbol, side, qty, price, order_id, exit_reason
    stall          timestamp, silent_min, resubscribed_before
    session_end    timestamp, candles_processed, trades, account, positions

`timestamp` on session_start, stall and session_end is the machine's local wall-clock time with
no zone (`str(datetime.now())`; the VM runs on Singapore time); on candle_close and trade it is
the decision candle's timestamp in the strategy's convention (the exchange's local close time
tagged as UTC, e.g. '2026-10-14 10:30:00+00:00'). `account` is {'cash', 'total_assets',
'market_value'} as the broker reports the whole account; `positions` on session_end is the
broker's holdings, {symbol: {'qty', 'entry_price'}}.

The file name's counter is per process across every session log opened in it, as the original's
class attribute was, so parallel strategies started in the same second never share a file.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

_seq = itertools.count(1)
_seq_lock = threading.Lock()


def _next_seq() -> int:
    with _seq_lock:
        return next(_seq)


def session_file_name(started: datetime, strategy_slug: str, pid: int, seq: int) -> str:
    return f"session_{started:%Y%m%d_%H%M%S}_{strategy_slug}_{pid}_{seq}.jsonl"


class SessionLog:
    """Appends one strategy's session events to its session file."""

    def __init__(self, directory: "str | Path", strategy_slug: str,
                 now: Callable[[], datetime] = datetime.now, pid: Optional[int] = None,
                 seq: Optional[int] = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.strategy_slug = strategy_slug
        self._now = now
        self.pid = os.getpid() if pid is None else int(pid)
        self.seq = _next_seq() if seq is None else int(seq)
        self.opened_at = now()
        self.path = self.directory / session_file_name(self.opened_at, strategy_slug, self.pid, self.seq)
        self.events_written = 0
        self.trades_logged = 0
        self.candles_logged = 0

    # ── the writer ───────────────────────────────────────────────────────────────────────────
    def write(self, event: Mapping[str, Any]) -> None:
        line = json.dumps(dict(event)) + "\n"
        with open(self.path, "a") as f:
            f.write(line)
        self.events_written += 1

    # ── the events ───────────────────────────────────────────────────────────────────────────
    def session_start(self, strategy: str, symbols: List[str], timeframe: str,
                      account: Optional[Mapping[str, Any]], timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        event = {"type": "session_start", "timestamp": str(timestamp or self._now()),
                 "strategy": strategy, "symbols": list(symbols), "timeframe": timeframe,
                 "account": dict(account) if account is not None else {}}
        self.write(event)
        return event

    def candle_close(self, symbol: str, timestamp: Any, close: float, equity: float) -> Dict[str, Any]:
        event = {"type": "candle_close", "symbol": symbol, "timestamp": str(timestamp),
                 "close": close, "equity": equity}
        self.write(event)
        self.candles_logged += 1
        return event

    def trade(self, timestamp: Any, symbol: str, side: str, qty: float, price: float,
              order_id: Any, exit_reason: Optional[str]) -> Dict[str, Any]:
        event = {"type": "trade", "timestamp": str(timestamp), "symbol": symbol, "side": side,
                 "qty": qty, "price": price, "order_id": order_id, "exit_reason": exit_reason}
        self.write(event)
        self.trades_logged += 1
        return event

    def stall(self, silent_min: float, resubscribed_before: bool,
              timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        event = {"type": "stall", "timestamp": str(timestamp or self._now()),
                 "silent_min": silent_min, "resubscribed_before": resubscribed_before}
        self.write(event)
        return event

    def session_end(self, candles_processed: int, trades: int, account: Optional[Mapping[str, Any]],
                    positions: Optional[Mapping[str, Any]], timestamp: Optional[datetime] = None) -> Dict[str, Any]:
        event = {"type": "session_end", "timestamp": str(timestamp or self._now()),
                 "candles_processed": candles_processed, "trades": trades,
                 "account": dict(account) if account is not None else {},
                 "positions": {k: dict(v) for k, v in (positions or {}).items()}}
        self.write(event)
        return event


def read_events(path: "str | Path") -> List[Dict[str, Any]]:
    """The events of a session log; a line cut short by a crash is skipped."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out
