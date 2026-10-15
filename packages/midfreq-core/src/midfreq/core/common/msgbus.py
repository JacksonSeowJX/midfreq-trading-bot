"""
MessageBus: topic-based publish/subscribe, after NautilusTrader's `common.msgbus`.

Components never call each other's handlers directly; they publish to a topic and whoever
subscribed receives the message. Topics used by the engine:

    data.bars.<bar_type>         every Bar, e.g. data.bars.00700.XHKG-1-HOUR-LAST-EXTERNAL
    data.slices                  a TimeSlice once all bars with the same ts_event are out
    data.signals.<name>          Signals published by strategies
    events.order.<strategy_id>   order events for the strategy that owns the order

Subscriptions may use '*' and '?' wildcards ('data.bars.*.XHKG-*'). Delivery is synchronous and
in subscription order, which is what makes a backtest deterministic.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Callable, Dict, List, Tuple

Handler = Callable[[Any], None]


class MessageBus:
    def __init__(self):
        self._subs: List[Tuple[str, Handler]] = []
        self._resolved: Dict[str, List[Handler]] = {}
        self.sent = 0

    def subscribe(self, topic: str, handler: Handler) -> None:
        if (topic, handler) not in self._subs:
            self._subs.append((topic, handler))
            self._resolved.clear()

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        self._subs = [s for s in self._subs if s != (topic, handler)]
        self._resolved.clear()

    def has_subscribers(self, topic: str) -> bool:
        return bool(self._handlers(topic))

    def _handlers(self, topic: str) -> List[Handler]:
        handlers = self._resolved.get(topic)
        if handlers is None:
            handlers = [h for pattern, h in self._subs if pattern == topic or fnmatchcase(topic, pattern)]
            self._resolved[topic] = handlers
        return handlers

    def publish(self, topic: str, message: Any) -> None:
        self.sent += 1
        for handler in self._handlers(topic):
            handler(message)
