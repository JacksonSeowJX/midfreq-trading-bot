"""
Subscriptions for LiveMarketDataService: one venue stream per bar type, and one per instrument's
quotes, however many strategies want it, within each venue account's subscription quota.

Subscriptions are reference counted: the venue stream opens with the first subscriber and closes
with the last. Each client's quota (SubscriptionQuota) is checked before anything is subscribed,
so a request that does not fit is refused whole with a SubscriptionLimitError. At stop every
stream is unsubscribed, one request per client, except on a client whose connection is down.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from midfreq.core.model.data import BarSpecification, BarType
from midfreq.core.model.identifiers import InstrumentId, Venue, as_instrument_id
from midfreq.data.adapters.base import DataClient
from midfreq.trading.data.cross_section import _Universe
from midfreq.trading.data.finality import _BarState

log = logging.getLogger("midfreq.trading.marketdata")

__all__ = ["SubscriptionLimitError", "SubscriptionQuota", "SubscriptionsMixin"]


class SubscriptionLimitError(RuntimeError):
    """A subscription would take a venue account past the number of streams it may hold."""


@dataclass
class _Stream:
    """One live bar stream: one client, one bar specification (all its instruments)."""
    client_index: int
    spec: BarSpecification
    venues: Set[Venue] = field(default_factory=set)
    bar_types: Set[BarType] = field(default_factory=set)
    last_update_ns: Optional[int] = None    # the stall clock (pushes, the start, a resubscribe)
    last_push_ns: Optional[int] = None      # when the last push was received (pushes only)
    resubscribed: bool = False
    fatal: bool = False


class SubscriptionQuota:
    """The subscription quota of each data client: how many streams its venue account may hold at
    once (the client's subscription_limit(), asked once), and which it holds now.

    `limit_unit` is what the limit counts: "stream" (one per instrument and data type) or
    "instrument" (one per instrument, whatever its data types). Clients are known by their index
    in `clients`; `source(index)` names one in messages.
    """

    def __init__(self, clients: List[DataClient], source: Callable[[int], str], limit_unit: str):
        self._clients = clients
        self._source = source
        self._limit_unit = limit_unit
        self._limits: Dict[int, Optional[int]] = {}
        self._in_use: Dict[int, Dict[Tuple[str, ...], int]] = {i: {} for i in range(len(self._clients))}

    @property
    def unit(self) -> str:
        """What the limit counts: "stream" or "instrument"."""
        return self._limit_unit

    def in_use(self, idx: int) -> int:
        """How many streams (or instruments) client `idx` holds now."""
        return len(self._in_use[idx])

    def limit(self, idx: int) -> Optional[int]:
        """Client `idx`'s limit, or None when it is unknown."""
        if idx not in self._limits:
            try:
                self._limits[idx] = self._clients[idx].subscription_limit()
            except Exception as exc:                       # noqa: BLE001 - unknown limit: the venue will say
                log.warning("%s: subscription limit unknown (%s)", self._source(idx), exc)
                self._limits[idx] = None
        return self._limits[idx]

    def key(self, iid: InstrumentId, what: str) -> Tuple[str, ...]:
        """What one subscription to `what` ("bars:<spec>" or "quotes") of `iid` counts as."""
        return (str(iid), what) if self._limit_unit == "stream" else (str(iid),)

    def check(self, idx: int, keys: Set[Tuple[str, ...]], what: str) -> None:
        """Raise SubscriptionLimitError if client `idx` has no room for the keys it does not hold."""
        limit = self.limit(idx)
        if limit is None:
            return
        in_use = self._in_use[idx]
        new = [k for k in keys if k not in in_use]
        if len(in_use) + len(new) > limit:
            raise SubscriptionLimitError(
                f"{self._source(idx)} allows {limit} {self._limit_unit}s at once on this account; "
                f"{len(in_use)} in use and {len(new)} more requested ({what}). Unsubscribe something, "
                f"or share one subscription between strategies.")

    def acquire(self, idx: int, key: Tuple[str, ...]) -> None:
        self._in_use[idx][key] = self._in_use[idx].get(key, 0) + 1

    def release(self, idx: int, key: Tuple[str, ...]) -> None:
        n = self._in_use[idx].get(key, 0) - 1
        if n <= 0:
            self._in_use[idx].pop(key, None)
        else:
            self._in_use[idx][key] = n


class SubscriptionsMixin:
    """Subscribing and unsubscribing LiveMarketDataService's bar and quote streams.

    It relies on the service's `_bars`, `_streams`, `_universes`, `_quote_refs`, `_clients`,
    `_quota` (a SubscriptionQuota), `_queue`, `_clock`, `_started`, `_stopped` and `_down`, and on
    its `_client_index` and `_source`.
    """

    # ── subscriptions ────────────────────────────────────────────────────────────────────────

    def subscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None:
        """Stream `bar_type` (once per bar type, however many callers), as part of `universe`
        when given. Raises SubscriptionLimitError when the venue account has no room for it."""
        self.subscribe_bars_many([bar_type], universe)

    def subscribe_bars_many(self, bar_types: Iterable[BarType], universe: Optional[str] = None) -> None:
        """Several bar types at once, checked against the subscription limit as a whole first, so
        a universe that does not fit is refused before any of it is subscribed."""
        bts = list(dict.fromkeys(bar_types))
        if not bts:
            return
        if self._stopped:
            raise RuntimeError("the market data service has stopped")
        for bt in bts:
            self._client_index(bt.instrument_id.venue)
        if universe is not None:
            spec = self._universes[universe].spec if universe in self._universes else bts[0].spec
            odd = [str(bt) for bt in bts if bt.spec != spec]
            if odd:
                raise ValueError(f"universe {universe!r} is {spec} bars; cannot add {odd}")
        # Room at each client for the streams that are new.
        wanted: Dict[int, Set[Tuple[str, ...]]] = {}
        for bt in bts:
            state = self._bars.get(bt)
            if state is None or state.refs == 0:
                idx = self._client_index(bt.instrument_id.venue)
                wanted.setdefault(idx, set()).add(self._quota.key(bt.instrument_id, f"bars:{bt.spec}"))
        for idx, keys in wanted.items():
            self._quota.check(idx, keys, ", ".join(sorted(str(bt) for bt in bts))[:200])
        if universe is not None and universe not in self._universes:
            self._universes[universe] = _Universe(universe, bts[0].spec)
        new: Dict[int, List[BarType]] = {}
        for bt in bts:
            state = self._state(bt)
            if state.refs == 0:
                new.setdefault(self._client_index(bt.instrument_id.venue), []).append(bt)
        for idx, batch in new.items():                     # one request per client (Moomoo: per K-line type)
            self._clients[idx].subscribe_bars_many(batch, self._queue.put)
        for bt in bts:
            state = self._state(bt)
            if state.refs == 0:
                idx = self._client_index(bt.instrument_id.venue)
                self._quota.acquire(idx, self._quota.key(bt.instrument_id, f"bars:{bt.spec}"))
                stream = self._stream(idx, bt)
                state.stream_key = (idx, bt.spec)
                if self._started and stream.last_update_ns is None:
                    stream.last_update_ns = self._clock.timestamp_ns()
                log.debug("subscribed %s", bt)
            state.refs += 1
            if universe is not None:
                members = self._universes[universe].members
                members[bt] = members.get(bt, 0) + 1

    def unsubscribe_bars(self, bar_type: BarType, universe: Optional[str] = None) -> None:
        """Drop one subscription; the venue stream closes with the last one."""
        state = self._bars.get(bar_type)
        if state is None or state.refs <= 0:
            log.debug("unsubscribe_bars(%s): not subscribed", bar_type)
            return
        if universe is not None and universe in self._universes:
            members = self._universes[universe].members
            if bar_type in members:
                members[bar_type] -= 1
                if members[bar_type] <= 0:
                    del members[bar_type]
        state.refs -= 1
        if state.refs == 0:
            idx = self._client_index(bar_type.instrument_id.venue)
            try:
                self._clients[idx].unsubscribe_bars(bar_type)
            except Exception as exc:                       # noqa: BLE001 - the venue may refuse; we stop listening anyway
                log.warning("unsubscribe %s failed: %s", bar_type, exc)
            self._quota.release(idx, self._quota.key(bar_type.instrument_id, f"bars:{bar_type.spec}"))
            state.forming = None

    def subscribe_quotes(self, instrument_id: "str | InstrumentId") -> None:
        iid = as_instrument_id(instrument_id)
        if self._stopped:
            raise RuntimeError("the market data service has stopped")
        if self._quote_refs.get(iid, 0) == 0:
            idx = self._client_index(iid.venue)
            key = self._quota.key(iid, "quotes")
            self._quota.check(idx, {key}, f"{iid} quotes")
            self._clients[idx].subscribe_quotes(iid, self._queue.put)
            self._quota.acquire(idx, key)
        self._quote_refs[iid] = self._quote_refs.get(iid, 0) + 1

    def unsubscribe_quotes(self, instrument_id: "str | InstrumentId") -> None:
        iid = as_instrument_id(instrument_id)
        refs = self._quote_refs.get(iid, 0)
        if refs <= 0:
            return
        self._quote_refs[iid] = refs - 1
        if refs == 1:
            idx = self._client_index(iid.venue)
            try:
                self._clients[idx].unsubscribe_quotes(iid)
            except Exception as exc:                       # noqa: BLE001
                log.warning("unsubscribe quotes %s failed: %s", iid, exc)
            self._quota.release(idx, self._quota.key(iid, "quotes"))

    def subscriptions(self) -> Dict[str, Dict[str, Any]]:
        """Per client: streams in use and its limit, for the ops log."""
        out = {}
        for idx in range(len(self._clients)):
            out[self._source(idx)] = {"in_use": self._quota.in_use(idx), "limit": self._quota.limit(idx),
                                      "unit": self._quota.unit}
        return out

    def universe(self, name: str) -> Tuple[InstrumentId, ...]:
        u = self._universes.get(name)
        return tuple(bt.instrument_id for bt in u.members) if u else ()

    # ── bookkeeping ──────────────────────────────────────────────────────────────────────────

    def _state(self, bar_type: BarType) -> _BarState:
        state = self._bars.get(bar_type)
        if state is None:
            state = self._bars[bar_type] = _BarState(bar_type)
        return state

    def _stream(self, idx: int, bar_type: BarType) -> _Stream:
        key = (idx, bar_type.spec)
        stream = self._streams.get(key)
        if stream is None:
            stream = self._streams[key] = _Stream(idx, bar_type.spec)
        stream.venues.add(bar_type.instrument_id.venue)
        stream.bar_types.add(bar_type)
        return stream

    def _unsubscribe_all(self) -> None:
        """At stop: one request per client; none to a client whose connection is down (its
        subscriptions went with it, and each call would wait out a timeout)."""
        by_client: Dict[int, List[BarType]] = {}
        for bt, state in self._bars.items():
            if state.refs > 0:
                by_client.setdefault(self._client_index(bt.instrument_id.venue), []).append(bt)
        down = set()
        for idx, bts in by_client.items():
            if self._source(idx) in self._down:
                down.add(idx)
                log.warning("%s is disconnected: its %d subscriptions are not unsubscribed", self._source(idx), len(bts))
                continue
            try:
                self._clients[idx].unsubscribe_bars_many(bts)
            except Exception as exc:                       # noqa: BLE001 - shutting down regardless
                log.warning("unsubscribe of %d bar types failed: %s", len(bts), exc)
        for iid, refs in self._quote_refs.items():
            if refs > 0 and self._client_index(iid.venue) not in down:
                try:
                    self._clients[self._client_index(iid.venue)].unsubscribe_quotes(iid)
                except Exception as exc:                   # noqa: BLE001
                    log.warning("unsubscribe quotes %s failed: %s", iid, exc)
