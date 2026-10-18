"""
`state_<strategy>.json`: one strategy's own positions, its cash under the capital cap and its
rebalance clock, carried from one session to the next, in exactly the original engine's format
(midfreq.live.live_engine.LivePortfolio.save_state and its constructor), so the node can take
over from the original engine's state file and the original engine can take it back.

    {
     "saved_at": "2026-10-15 04:01:04.743088",          str(datetime.now()), local, no zone
     "positions": {"US.INTC": {"qty": 302.0, "entry_price": 109.585}, ...},
     "peak_prices": {"US.INTC": 125.22, ...},             only for symbols still held
     "cross_section_count": 70,
     "next_rebalance_at": 76,
     "cash": 4738.62                                      null when the session has no cap
    }

written with `json.dumps(..., indent=1)`. The rules for reading it back are the original's:

- an unreadable file stops the start-up (a position ledger is never guessed);
- a position is resumed at no more than the broker holds (shrink-only: the broker's holdings are
  the whole account's, shared with other strategies, so a surplus is never adopted); when the
  broker cannot be asked, the file is trusted rather than read as "holds nothing";
- under a capital cap the session's cash is its own ledger, bounded above by the broker's cash.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from midfreq.trading.ops.logs import get_logger

log = get_logger("state")

# What a broker's holdings look like to the state rules: {symbol: {'qty': ..., 'entry_price': ...}}
# (midfreq.live.order_gateway.OrderGateway.get_positions), or None when they could not be read.
BrokerPositions = Optional[Mapping[str, Mapping[str, float]]]


class StateFileError(RuntimeError):
    """The state file exists but cannot be read: refuse to start rather than guess positions."""


def state_file_name(strategy_slug: str) -> str:
    return f"state_{strategy_slug}.json"


def state_path(directory: "str | Path", strategy_slug: str) -> Path:
    return Path(directory) / state_file_name(strategy_slug)


def load_state(path: "str | Path") -> Optional[Dict[str, Any]]:
    """The saved state, or None when there is no file yet."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise StateFileError(
            f"CRITICAL: state file {path} is not valid JSON ({e}). "
            "Refusing to start with an unreadable position ledger — fix or "
            "restore the file from git history before retrying."
        ) from e


def state_payload(portfolio: Any, cross_section_count: int, next_rebalance_at: Optional[int],
                  capital_cap: Optional[float], saved_at: datetime) -> Dict[str, Any]:
    """The dict the original engine saves, key for key and in the same order."""
    return {
        "saved_at": str(saved_at),
        "positions": portfolio.positions,
        "peak_prices": {s: p for s, p in portfolio._peak_prices.items() if s in portfolio.positions},
        "cross_section_count": cross_section_count,
        "next_rebalance_at": next_rebalance_at,
        "cash": portfolio.cash if capital_cap is not None else None,
    }


def render_state(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=1)


def write_state(path: "str | Path", payload: Mapping[str, Any]) -> None:
    """Write atomically (a temporary file in the same folder, synced to disk, then a rename), so
    a crash or a power cut while saving leaves the previous state rather than half a file. The
    bytes are the original's; the file is 0644 as the original's (mkstemp makes 0600)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_state(payload)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        try:                                            # the rename itself, on disk
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def state_anomalies(path: "str | Path", portfolio: Any) -> List[str]:
    """Positions whose entry price changed with no buy this session: something outside the
    trade path changed the ledger (the 2026-07-23 drift the original engine guards against)."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        prior = json.loads(path.read_text()).get("positions", {})
    except Exception:
        prior = {}
    buys = {t["symbol"] for t in portfolio.trade_history if t["action"] == "BUY"}
    out = []
    for symbol, pos in portfolio.positions.items():
        old = prior.get(symbol)
        if old and old["entry_price"] != pos["entry_price"] and symbol not in buys:
            out.append(f"STATE ANOMALY: {symbol} entry_price changed {old['entry_price']} -> "
                       f"{pos['entry_price']} (qty {old['qty']} -> {pos['qty']}) with NO buy this "
                       f"session. trade_history: {portfolio.trade_history}")
    return out


def save_state(path: "str | Path", portfolio: Any, cross_section_count: int,
               next_rebalance_at: Optional[int], capital_cap: Optional[float],
               saved_at: datetime) -> Dict[str, Any]:
    """Check for anomalies (logged loudly, as the original prints them), then write."""
    for message in state_anomalies(path, portfolio):
        log.error(message)
    payload = state_payload(portfolio, cross_section_count, next_rebalance_at, capital_cap, saved_at)
    write_state(path, payload)
    return payload


# ── start-up: the opening cash, and resuming a saved state ──────────────────────────────────────

def opening_cash(account: Optional[Mapping[str, Any]], capital_cap: Optional[float]) -> tuple:
    """(initial_cash, cash) for a new session, from the broker's account and the cap, as the
    original LivePortfolio computes them before any state is read."""
    acc = dict(account or {})
    initial_cash = acc.get("total_assets", 0.0) or acc.get("cash", 0.0)
    if capital_cap is not None:
        initial_cash = min(initial_cash, capital_cap) if initial_cash else capital_cap
    cash = acc.get("cash", initial_cash)
    if capital_cap is not None:
        cash = min(cash, capital_cap)
    return initial_cash, cash


@dataclass
class Resumed:
    """What a saved state restored."""
    cross_section_count: int = 0
    next_rebalance_at: Optional[int] = None
    positions: Dict[str, float] = field(default_factory=dict)
    shrunk: Dict[str, tuple] = field(default_factory=dict)        # symbol -> (saved qty, resumed qty)
    raw_positions: Dict[str, Any] = field(default_factory=dict)
    had_state: bool = False
    cash: Optional[float] = None


def resume(portfolio: Any, state: Optional[Mapping[str, Any]], account: Optional[Mapping[str, Any]],
           broker_positions: BrokerPositions, capital_cap: Optional[float]) -> Resumed:
    """Apply a saved state to a freshly opened portfolio (positions, peaks, cash), with the
    original engine's shrink-only and capped-cash rules. The portfolio's `cash` must already be
    the opening cash (`opening_cash`)."""
    out = Resumed()
    if state is None:
        return out
    acc = dict(account or {})
    out.had_state = True
    out.raw_positions = dict(state.get("positions", {}))
    out.cross_section_count = int(state.get("cross_section_count", 0) or 0)
    for symbol, pos in state.get("positions", {}).items():
        if broker_positions is None:
            qty = pos["qty"]
        else:
            broker_qty = broker_positions.get(symbol, {}).get("qty", 0)
            qty = min(pos["qty"], broker_qty)
            if qty < pos["qty"]:
                out.shrunk[symbol] = (pos["qty"], qty)
        if qty > 0:
            portfolio.positions[symbol] = {"qty": qty, "entry_price": pos["entry_price"]}
            out.positions[symbol] = qty
    for symbol, peak in state.get("peak_prices", {}).items():
        if symbol in portfolio.positions:
            portfolio._peak_prices[symbol] = peak
    nra = state.get("next_rebalance_at")
    out.next_rebalance_at = int(nra) if nra else None
    if capital_cap is not None:
        if state.get("cash") is not None:
            portfolio.cash = min(float(state["cash"]), acc.get("cash", float(state["cash"])))
        else:
            own_cost = sum(p["qty"] * p["entry_price"] for p in portfolio.positions.values())
            portfolio.cash = max(0.0, min(acc.get("cash", capital_cap), capital_cap - own_cost))
        out.cash = portfolio.cash
    return out


def shrink_to_broker(portfolio: Any, account: Optional[Mapping[str, Any]], broker_positions: BrokerPositions,
                     capital_cap: Optional[float], on_shrink: Optional[Callable[[str, float, float], None]] = None) -> bool:
    """The periodic reconciliation (the original's sync_with_broker): cash bounded by the broker's
    under a cap (adopted without one); each claim shrunk to what the broker holds, never grown.
    Returns False when the broker's positions could not be read (nothing was changed then)."""
    if account:
        if capital_cap is not None:
            portfolio.cash = min(portfolio.cash, account["cash"])
        else:
            portfolio.cash = account["cash"]
    if broker_positions is None:
        return False
    for symbol in list(portfolio.positions.keys()):
        local_qty = portfolio.get_position_qty(symbol)
        broker_qty = broker_positions.get(symbol, {}).get("qty", 0)
        if broker_qty < local_qty:
            if on_shrink is not None:
                on_shrink(symbol, local_qty, broker_qty)
            if broker_qty <= 0:
                del portfolio.positions[symbol]
            else:
                portfolio.positions[symbol]["qty"] = broker_qty
    return True
