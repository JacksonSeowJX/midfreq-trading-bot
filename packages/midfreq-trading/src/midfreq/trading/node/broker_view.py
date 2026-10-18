"""
The broker's view of the node's accounts: the account snapshot and the positions as the venue
reports them (a failed read is never "holds nothing"), the reconciliation of the orders and the
books with the venues, the heartbeat, and whether a venue is in a trading session.
"""
from __future__ import annotations

import collections
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from midfreq.core.model.identifiers import Venue, as_instrument_id
from midfreq.data.adapters.base import PositionReport
from midfreq.trading.config.settings import instrument_for_symbol
from midfreq.trading.core.interfaces import TOPIC_HEARTBEAT, Heartbeat, ReconcileReport
from midfreq.trading.node.clock import NANOS
from midfreq.trading.ops.logs import describe_heartbeat, get_logger, log_context, resident_memory_mb
from midfreq.trading.strategy.host import legacy_symbol_for

log = get_logger("node")


class BrokerViewMixin:
    """TradingNode's view of the broker. Relies on the node's `_account_sources`,
    `accounts_at_start`, `config`, `ctx`, `host`, `oms`, `positions`, `refdata`, `venues`,
    `bar_types`, `msgbus`, `test_clock`, `_sleep` (between position reads), and `_last_bar_at` and
    `_last_bar_ts` (the heartbeat's stream lag); keeps what reconciliation has already reported in
    `_reported`. Calls `now_ns` and `_seed_position_keeper` (start-up)."""

    # ── the broker's view ────────────────────────────────────────────────────────────────────
    def account_snapshot(self, account_id: str) -> Optional[Dict[str, Any]]:
        """{'cash', 'total_assets', 'market_value'} as the venue reports the account, or None."""
        src = self._account_sources.get(account_id)
        if src is None:
            return None
        try:
            snap = src.snapshot()
        except Exception as exc:
            log.warning("account %s: snapshot failed (%s)", account_id, exc)
            return None
        if snap is None:
            return None
        if isinstance(snap, Mapping):
            return dict(snap)
        return {"cash": float(snap.cash), "total_assets": float(snap.total_assets),
                "market_value": float(snap.market_value)}

    def broker_positions(self, account_id: str, attempts: int = 3, wait_s: float = 2.0
                         ) -> Tuple[Optional[Dict[str, Dict[str, float]]], Optional[List[PositionReport]]]:
        """The account's holdings as {symbol: {'qty', 'entry_price'}} (the state rules' form) and
        as PositionReports; (None, None) when they could not be read (never 'holds nothing')."""
        src = self._account_sources.get(account_id)
        if src is None:
            return None, None
        for i in range(max(1, attempts)):
            try:
                reports = src.positions()
            except Exception as exc:
                reports = None
                log.warning("account %s: position query failed (%s)", account_id, exc)
            if reports is not None and not any(r.quantity for r in reports):
                snap = self.account_snapshot(account_id)
                if snap and float(snap.get("market_value") or 0.0) > 0:
                    # "No positions" from an account the broker values at more than nothing is a
                    # failed read, never "holds nothing" (it would wipe every claim).
                    log.warning("account %s: no positions listed but the account holds %s in securities; "
                                "treated as a failed read", account_id, f"{float(snap['market_value']):,.2f}")
                    reports = None
            if reports is not None:
                out: Dict[str, Dict[str, float]] = {}
                for r in reports:
                    if r.quantity > 0:
                        ap = r.average_price
                        out[self._legacy_name(r)] = {"qty": float(r.quantity),
                                                     "entry_price": float(ap) if ap is not None and ap == ap else 0.0}
                return out, list(reports)
            if i < attempts - 1 and not self.test_clock:
                self._sleep(wait_s)
        return None, None

    def _legacy_name(self, report: PositionReport) -> str:
        if report.instrument_id:
            try:
                return legacy_symbol_for(as_instrument_id(report.instrument_id), self.venues)
            except Exception:
                pass
        return report.symbol

    def _report_venue(self, report: PositionReport, account_id: str) -> Optional[Venue]:
        if report.instrument_id:
            try:
                return as_instrument_id(report.instrument_id).venue
            except Exception:
                return None
        try:
            return instrument_for_symbol(report.symbol, self.venues).venue
        except Exception:
            return None

    def _reconcile(self, reason: str = "periodic") -> None:
        """The OMS against the venues' orders and fills; then, per account, each candle strategy
        against the broker (the original engine's shrink-only sync_with_broker, which also bounds
        its cash), its PositionKeeper book made the same, and the other strategies' books
        reconciled shrink-only by the PositionKeeper."""
        if self.oms is None:
            return
        if reason != "start-up":                         # at start-up it ran before the strategies opened
            self._reconcile_orders(reason)
        legacy_ids = {str(a.id) for a in self.host.legacy}
        others = [s.id for s in self.config.strategies if s.id not in legacy_ids]
        for aid in self.ctx.execution_clients:
            account = self.account_snapshot(aid) if reason != "start-up" else self.accounts_at_start.get(aid)
            legacy, reports = self.broker_positions(aid, attempts=1)
            if reason != "start-up":
                for a in self.host.legacy:
                    if a.settings.account == aid:
                        with log_context(strategy=str(a.id)):
                            a.reconcile(account, legacy)
                        self._seed_position_keeper(a)
            mine = [sid for sid in others if self.config.strategy(sid).account == aid]
            if reports is None or not mine:
                continue
            by_venue: Dict[Venue, List[PositionReport]] = collections.defaultdict(list)
            for r in reports:
                v = self._report_venue(r, aid)
                if v is not None:
                    by_venue[v].append(r)
            for v in {bt.instrument_id.venue for bt in self.bar_types}:
                if self.ctx.execution_by_venue.get(v) is not self.ctx.execution_clients[aid]:
                    continue
                try:
                    self._log_report(self.positions.reconcile(v, by_venue.get(v, []), strategy_ids=mine), reason)
                except Exception as exc:
                    log.error("position reconciliation for %s failed (%s): %s", v, reason, exc)

    def _reconcile_orders(self, reason: str) -> None:
        try:
            report = self.oms.reconcile()
            self._log_report(report, reason)
        except Exception as exc:
            log.error("order reconciliation failed (%s): %s", reason, exc)

    def _log_report(self, report: Any, reason: str) -> None:
        """Adjustments are logged each time; an unexplained difference is an ERROR the first time
        it is seen and quiet after (it would otherwise repeat every 5 minutes); what belongs to
        others on the shared account is INFO, once."""
        if not isinstance(report, ReconcileReport):
            return
        seen = self.__dict__.setdefault("_reported", set())
        for adj in report.adjustments:
            log.warning("reconcile %s (%s): %s", report.source, reason, adj)
        for u in report.unexplained:
            if ("u", u) in seen:
                log.debug("reconcile %s (%s): still unexplained: %s", report.source, reason, u)
                continue
            seen.add(("u", u))
            log.error("reconcile %s (%s): unexplained: %s", report.source, reason, u)
        for f in getattr(report, "foreign", ()):
            if ("f", f) not in seen:
                seen.add(("f", f))
                log.info("reconcile %s (%s): not ours (another session or a manual trade): %s",
                         report.source, reason, f)

    def _heartbeat(self, now: int) -> None:
        try:
            open_orders = len(self.oms.open_orders())
        except Exception:
            open_orders = -1
        lag = (now - self._last_bar_at) / NANOS if self._last_bar_at is not None else None
        hb = Heartbeat(now, self._last_bar_ts, lag, open_orders, self.host.positions(), resident_memory_mb())
        self.msgbus.publish(TOPIC_HEARTBEAT, hb)
        log.info(describe_heartbeat(hb))

    def _calendar(self, venue: Venue) -> Any:
        if self.refdata is None or not callable(getattr(self.refdata, "calendar", None)):
            return None
        try:
            return self.refdata.calendar(venue)
        except Exception:
            return None

    def market_open(self, venue: Venue, now_ns: Optional[int] = None, grace_s: float = 0.0) -> bool:
        """Whether `venue` is in a trading session now: its sessions in config/venues.json (lunch
        breaks included), and, where the reference data has a calendar, its holidays and early
        closes. `grace_s` extends each session's end."""
        now = self.now_ns() if now_ns is None else now_ns
        spec = self.venues.get(venue)
        if spec.is_24_7:
            return True
        ts = pd.Timestamp(now, unit="ns", tz="UTC")
        local = ts.tz_convert(spec.timezone).tz_localize(None)
        grace = pd.Timedelta(seconds=grace_s)
        close_utc = None
        cal = self._calendar(venue)
        session = "unknown"
        if cal is not None:
            try:
                session = cal.session(local.date())
            except Exception:
                session = "unknown"
            if session is None:
                return False
            if session != "unknown":
                close_utc = pd.Timestamp(session[1])
                close_utc = close_utc.tz_localize("UTC") if close_utc.tzinfo is None else close_utc.tz_convert("UTC")
        if session == "unknown" and local.weekday() not in spec.weekdays:
            return False
        tod = local - local.normalize()
        in_session = any(s.open_offset <= tod < s.close_offset + grace for s in spec.sessions)
        if in_session and close_utc is not None and ts >= close_utc + grace:
            return False
        return in_session
