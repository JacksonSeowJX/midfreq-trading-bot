"""
Read-only view of a crypto-exchange account through ccxt (balances, derivative positions, orders).

Credentials come only from the environment (or the untracked .env) through
midfreq.core.connections.get_secret, under the variable names the account entry lists in
`secrets`. Each name is matched to a ccxt credential by its ending: ..._SECRET or ..._SECRET_KEY
-> secret, ..._PASSPHRASE or ..._PASSWORD -> password, ..._UID -> uid, any other ..._KEY -> apiKey.
The client only calls fetch_* methods, but use a key that cannot trade or withdraw: that is the
guarantee.

Valuation is in the account's quote currency `quote` (the venue's currency in config/venues.json,
USDT for OKX, unless the account options name one):

- cash is the quote currency's total balance and nothing else (another stablecoin is a holding);
- every other asset with a non-zero total balance is a spot holding, priced at the last trade
  price of `<ASSET>/<quote>` from fetch_tickers. An asset with no such market, or no last price,
  gets market_price and market_value None and is left out of the snapshot's market_value (a
  warning names it): nothing is converted through a third currency or estimated;
- the snapshot's total_assets is cash plus the priced holdings;
- spot balances carry no cost basis, so a spot holding's average_price and unrealized_pnl
  are None (unknown, not zero);
- derivative positions (fetch_positions, where the exchange has it) are listed by positions(),
  quantity in base units (contracts x contract size, negative when short), but not added to the
  snapshot: their margin and unrealised P&L are already in the settlement currency's balance on
  exchanges that report equity there (OKX does).

Orders are fetch_open_orders plus fetch_closed_orders(since=now - days), merged by id; how far
back an exchange's endpoint reaches is its own (OKX's default closed-order endpoint covers 7 days
of the default market type, spot; `exchange_options` can choose another).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

from midfreq.data.adapters.base import (
    AccountClient, AccountSnapshot, AccountUnavailableError, ConnectorError, OrderReport, PositionReport,
)
from midfreq.core.common.timestamps import ms_to_ns, ns_to_ms, ns_to_timestamp
from midfreq.core.connections import get_secret as _env_secret

log = logging.getLogger(__name__)

# Variable-name ending -> ccxt credential, most specific first.
_CREDENTIAL_SUFFIXES = (("_SECRET_KEY", "secret"), ("_SECRET", "secret"), ("_PASSPHRASE", "password"),
                        ("_PASSWORD", "password"), ("_UID", "uid"), ("_KEY", "apiKey"))

# The only exchange methods this client calls (besides close(), which ends the HTTP session).
READ_METHODS = ("fetch_balance", "load_markets", "fetch_tickers", "fetch_ticker", "fetch_positions",
                "fetch_open_orders", "fetch_closed_orders", "fetch_orders")


def credential_field(var: str) -> str:
    """The ccxt credential an environment variable holds, from its name."""
    up = var.upper()
    for suffix, field in _CREDENTIAL_SUFFIXES:
        if up.endswith(suffix):
            return field
    raise ValueError(f"cannot tell which ccxt credential {var!r} holds; name it ..._KEY, ..._SECRET, "
                     "..._PASSPHRASE or ..._UID")


def ccxt_exchange(exchange_id: str, config: Dict[str, Any]):
    """A ccxt exchange object (no request is made)."""
    try:
        import ccxt  # type: ignore
    except ImportError:
        raise AccountUnavailableError("the ccxt package is not installed (the `crypto` extra)") from None
    if exchange_id not in getattr(ccxt, "exchanges", ()):
        raise ConnectorError(f"ccxt has no exchange {exchange_id!r}")
    return getattr(ccxt, exchange_id)(config)


class CcxtAccountClient(AccountClient):
    """One account at a ccxt exchange, read with a read-only API key."""

    def __init__(self, exchange_id: str, secrets: Sequence[str] = (), quote: str = "USDT",
                 exchange_factory: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 exchange_options: Optional[Dict[str, Any]] = None,
                 get_secret: Callable[[str], Optional[str]] = _env_secret,
                 instrument_id: Optional[Callable[[str], Optional[str]]] = None,
                 timeout_ms: int = 5000, now_ns: Callable[[], int] = time.time_ns,
                 environment: Optional[str] = None):
        """`exchange_factory(exchange_id, config)` makes the exchange object (ccxt's class by
        default; tests pass a fake); `config` holds the credentials, enableRateLimit and the
        request timeout, plus `exchange_options`. `environment` says which account is read:
        'paper' the exchange's sandbox (OKX demo trading), the account a paper execution client
        trades; 'live', or None (the dashboard's read-only view of the account the key belongs
        to), the production account. The trading node always passes its execution client's."""
        from midfreq.data.adapters.ccxt.execution import environment_word
        self.environment = environment_word(environment) if environment else "live"
        self.exchange_id = str(exchange_id).lower()
        self.secrets = [str(s) for s in secrets]
        self._fields = {var: credential_field(var) for var in self.secrets}
        if len(set(self._fields.values())) != len(self._fields):
            raise ValueError(f"{self.exchange_id}: two secrets hold the same ccxt credential: {self._fields}")
        self.quote = str(quote).upper()
        self._factory = exchange_factory or ccxt_exchange
        self.exchange_options = dict(exchange_options or {})
        self._get_secret = get_secret
        self._instrument_id = instrument_id
        self.timeout_ms = int(timeout_ms)
        self._now_ns = now_ns
        self._ex = None

    def __repr__(self) -> str:
        return f"CcxtAccountClient({self.exchange_id}, {self.environment}, quote={self.quote})"

    # ── session ──────────────────────────────────────────────────────

    def available(self):
        """Checks what can be checked without a request: the key is in the environment, ccxt is
        installed and knows the exchange. A network or key problem surfaces from the reads, as
        AccountUnavailableError within the request timeout."""
        try:
            self._exchange()
        except ConnectorError as exc:
            return False, str(exc)
        except Exception as exc:   # never raise from a probe
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    def close(self) -> None:
        ex, self._ex = self._ex, None
        if ex is not None:
            ex.close()

    def _exchange(self):
        if self._ex is None:
            if not self.secrets:
                raise AccountUnavailableError(f"no API key configured for {self.exchange_id}: list the "
                                              "environment variables of a read-only key under `secrets`")
            values = {var: self._get_secret(var) for var in self.secrets}
            missing = [var for var, value in values.items() if not value]
            if missing:
                raise AccountUnavailableError(f"missing {', '.join(missing)} (a read-only {self.exchange_id} "
                                              "API key, in the environment or .env)")
            config = {"enableRateLimit": True, "timeout": self.timeout_ms, **self.exchange_options}
            config.update({self._fields[var]: value for var, value in values.items()})
            ex = self._factory(self.exchange_id, config)
            if self.environment == "paper":
                try:
                    ex.set_sandbox_mode(True)
                except Exception as exc:
                    raise ConnectorError(f"{self.exchange_id} has no sandbox in ccxt ({type(exc).__name__}: {exc}); "
                                         "its paper account cannot be read") from exc
            self._ex = ex
        return self._ex

    def _has(self, feature: str) -> bool:
        return bool((getattr(self._exchange(), "has", None) or {}).get(feature))

    def _call(self, name: str, *args, optional: bool = False, **kwargs):
        """One exchange request; any failure is AccountUnavailableError. With `optional`, an
        exchange that turns out not to support the call gives None."""
        method = getattr(self._exchange(), name)
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            if optional and type(exc).__name__ == "NotSupported":
                return None
            raise AccountUnavailableError(f"{self.exchange_id} {name}: {type(exc).__name__}: {exc}") from exc

    # ── reads ────────────────────────────────────────────────────────

    def snapshot(self) -> AccountSnapshot:
        ts = ns_to_timestamp(self._now_ns())
        holdings = self._balances()
        cash = holdings.pop(self.quote, 0.0)
        prices = self._prices(holdings)
        market_value = sum(qty * prices[a] for a, qty in holdings.items() if prices[a] is not None)
        unpriced = sorted(a for a in holdings if prices[a] is None)
        if unpriced:
            log.warning("%s: no %s price for %s; left out of market_value", self.exchange_id, self.quote, unpriced)
        return AccountSnapshot(currency=self.quote, total_assets=cash + market_value, cash=cash,
                               market_value=market_value, ts=ts)

    def positions(self) -> List[PositionReport]:
        holdings = self._balances()
        holdings.pop(self.quote, None)
        prices = self._prices(holdings)
        markets = self._markets()
        out = []
        for asset, qty in holdings.items():
            symbol = f"{asset}/{self.quote}"
            listed = symbol in markets
            price = prices[asset]
            out.append(PositionReport(
                symbol=symbol if listed else asset, quantity=qty, average_price=None,
                market_price=price, market_value=qty * price if price is not None else None,
                unrealized_pnl=None, instrument_id=self._map(symbol) if listed else None))
        if self._has("fetchPositions"):
            for p in self._call("fetch_positions", optional=True) or []:
                contracts = _num(p.get("contracts")) or 0.0
                if contracts == 0:
                    continue
                qty = contracts * (_num(p.get("contractSize")) or 1.0)
                if str(p.get("side") or "").lower() == "short":
                    qty = -abs(qty)
                notional = _num(p.get("notional"))
                out.append(PositionReport(
                    symbol=str(p["symbol"]), quantity=qty, average_price=_num(p.get("entryPrice")),
                    market_price=_num(p.get("markPrice")),
                    market_value=math.copysign(abs(notional), qty) if notional is not None else None,
                    unrealized_pnl=_num(p.get("unrealizedPnl"))))
        return out

    def orders(self, days: int = 14) -> List[OrderReport]:
        if days < 0:
            raise ValueError(f"days must be >= 0, got {days}")
        cutoff = ns_to_timestamp(self._now_ns()) - timedelta(days=days)
        since = ns_to_ms(cutoff.value)
        found: Dict[str, Dict[str, Any]] = {}
        if self._has("fetchOpenOrders") or self._has("fetchClosedOrders"):
            if self._has("fetchOpenOrders"):
                found.update((str(o["id"]), o) for o in self._call("fetch_open_orders") or [])
            if self._has("fetchClosedOrders"):   # after the open ones: a closed state is final
                found.update((str(o["id"]), o) for o in self._call("fetch_closed_orders", since=since) or [])
        elif self._has("fetchOrders"):
            found.update((str(o["id"]), o) for o in self._call("fetch_orders", since=since) or [])
        else:
            raise AccountUnavailableError(f"{self.exchange_id} cannot list orders through ccxt")
        reports = [r for r in (self._order(o) for o in found.values()) if r is not None and r.created >= cutoff]
        return sorted(reports, key=lambda r: (r.created, r.order_id), reverse=True)

    # ── helpers ──────────────────────────────────────────────────────

    def _balances(self) -> Dict[str, float]:
        """Asset -> total balance, non-zero only."""
        balance = self._call("fetch_balance") or {}
        out = {}
        for asset, amount in (balance.get("total") or {}).items():
            value = _num(amount)
            if value:
                out[str(asset).upper()] = value
        return out

    def _markets(self) -> Dict[str, Any]:
        return self._call("load_markets") or {}     # ccxt caches markets after the first call

    def _prices(self, holdings: Dict[str, float]) -> Dict[str, Optional[float]]:
        """Asset -> last price in the quote currency, None where the exchange has no such market or
        no last price."""
        markets = self._markets() if holdings else {}
        symbols = {a: f"{a}/{self.quote}" for a in holdings if f"{a}/{self.quote}" in markets}
        tickers: Dict[str, Any] = {}
        if symbols:
            if self._has("fetchTickers"):
                tickers = self._call("fetch_tickers", list(symbols.values())) or {}
            else:
                tickers = {s: self._call("fetch_ticker", s) for s in symbols.values()}
        return {a: _num((tickers.get(symbols[a]) or {}).get("last")) if a in symbols else None for a in holdings}

    def _order(self, o: Dict[str, Any]) -> Optional[OrderReport]:
        if o.get("timestamp") is None:
            log.debug("%s order %s has no timestamp; skipped", self.exchange_id, o.get("id"))
            return None
        filled = _num(o.get("filled")) or 0.0
        updated = o.get("lastUpdateTimestamp") or o.get("lastTradeTimestamp")
        return OrderReport(
            order_id=str(o["id"]), symbol=str(o.get("symbol") or ""), side=str(o.get("side") or "").upper(),
            quantity=_num(o.get("amount")) or 0.0, filled_quantity=filled, price=_positive(o.get("price")),
            average_fill_price=_positive(o.get("average")) if filled > 0 else None,
            status=str(o.get("status") or ""), created=ns_to_timestamp(ms_to_ns(int(o["timestamp"]))),
            updated=ns_to_timestamp(ms_to_ns(int(updated))) if updated else None)

    def _map(self, symbol: str) -> Optional[str]:
        if self._instrument_id is None:
            return None
        try:
            return self._instrument_id(symbol)
        except (ValueError, KeyError, ConnectorError):
            return None


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _positive(value: Any) -> Optional[float]:
    out = _num(value)
    return out if out is not None and out > 0 else None
