"""
The node's stopping steps, in TradingNode.stop's order: draining what was already received,
finalising the bars whose window has elapsed, ending the strategies' sessions, the session's
summary in the log, and disconnecting the clients and the connectors. (Cancelling the orders
still working is in `orders`; saving the strategies' state is the StrategyHost's.)
"""
from __future__ import annotations

from typing import Dict

from midfreq.trading.node.clock import TimerEvent
from midfreq.trading.node.event_queue import StopRequest
from midfreq.trading.ops.logs import get_logger

log = get_logger("node")


class ShutdownMixin:
    """TradingNode's stopping steps. Relies on the node's `ctx`, `host`, `marketdata`,
    `_account_sources` and `integration_notes`. Calls `now_ns`, the loop's `_next_item` and
    `_dispatch`, and `account_snapshot` and `broker_positions` (the broker's view)."""

    # ── stopping steps ───────────────────────────────────────────────────────────────────────
    def _drain_data(self) -> None:
        """Process everything already received (trading is off by now)."""
        for _ in range(10_000_000):
            item = self._next_item(timeout=0)
            if item is None:
                return
            if isinstance(item, (StopRequest, TimerEvent)):
                continue
            self._dispatch(item)

    def _finalise(self) -> None:
        """Bars whose window has elapsed are final now: publish them and their cross-sections,
        then feed the strategies what never made a cross-section."""
        self.marketdata.on_timer(self.now_ns())
        self._drain_data()
        self.host.finalize()

    def _end_sessions(self) -> None:
        cache: Dict[str, tuple] = {}
        for a in self.host.legacy:
            aid = a.settings.account
            if aid not in cache:
                cache[aid] = (self.account_snapshot(aid), self.broker_positions(aid, attempts=1)[0])
            account, positions = cache[aid]
            a.end_session(account or {}, positions or {})

    def _summary(self) -> None:
        log.info("=" * 60)
        log.info("SESSION SUMMARY")
        log.info("=" * 60)
        for a in self.host.legacy:
            log.info("%s: candles processed %d, orders sent %d, trades booked %d, clock %s, held %s", a.id,
                     a.candles_processed, a.orders_sent, len(a.portfolio.trade_history), a.cross_section_count,
                     a.positions() or "none")
        for r in self.host.native:
            log.info("%s: orders sent %d", r.id, r.orders_sent)
        for aid in self.ctx.execution_clients:
            acc = self.account_snapshot(aid)
            if acc:
                log.info("account %s: cash=%s total=%s", aid, f"{acc.get('cash', 0):,.2f}", f"{acc.get('total_assets', 0):,.2f}")
        for a in self.host.legacy:
            if a.session_log is not None:
                log.info("Full session log:  %s", a.session_log.path)
        if self.integration_notes:
            for note in self.integration_notes:
                log.info("integration note: %s", note)

    def _disconnect(self) -> None:
        if self.ctx is None:
            return
        for aid, client in self.ctx.execution_clients.items():
            try:
                client.disconnect()
            except Exception as exc:
                log.warning("disconnect %s failed: %s", aid, exc)
        for src in self._account_sources.values():
            if src is not None and src not in self.ctx.execution_clients.values():
                try:
                    src.close()
                except Exception:
                    pass
        for name, conn in self.ctx.connectors.items():
            try:
                conn.close()
            except Exception as exc:
                log.warning("close %s failed: %s", name, exc)
