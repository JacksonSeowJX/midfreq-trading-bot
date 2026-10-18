"""
Candle strategies live: `LiveLegacyAdapter` runs one candle strategy
(midfreq.strategies.candle_strategies) unchanged on the node, the live counterpart of
midfreq.backtest.legacy_adapter.

- adapter.py: the adapter, with opening, warm-up, the bus, and stopping and records;
- orders.py: execute_trade -> an order for the OMS (OrderRoutingMixin);
- fills.py: fills -> the strategy's book (FillBookingMixin), and fills journaled but never booked;
- portfolio.py: the portfolio the strategy sees (LiveLegacyPortfolio) and the helpers the node
  builds a candle strategy with.

How a candle strategy trades live
---------------------------------
A candle strategy trades by calling `self.portfolio.execute_trade(symbol, is_buy, qty, price,
timestamp)` and reads `portfolio.cash` and `portfolio.positions` directly. The adapter gives it a
`LiveLegacyPortfolio`, a midfreq.core.portfolio.Portfolio subclass whose `execute_trade`:

1. does nothing while trading is off (warm-up, shutdown) or for a quantity of zero;
2. rounds a buy down to the instrument's lot, and skips it below one lot;
3. prices it at the live market (the latest pushed price of the instrument; the decision
   candle's close only when there is none yet), with the configured rule (`last`: `slip`
   through the last price, as the original engine did; `quote`: through the bid or ask);
4. trims a buy to what the strategy's cash can pay at the worst case (the limit plus
   commission), and a sell to what the strategy holds;
5. sends one LIMIT DAY order to the OMS (a Moomoo GTC order would outlive the session) with
   `reference_price` = the decision candle's close the strategy passed, and the decision
   candle's timestamp and exit reason in its tags.

The strategy's portfolio is booked only when fills arrive (OrderFilled on the strategy's order
topic), by the original Portfolio bookkeeping, one booking per fill, never on submission. Each
booked fill is written to the session log as a `trade` event and the state file is saved.

By default (`confirm_fills`) `execute_trade` then waits for that order's outcome before it
returns, processing only order events (and the OMS's timers) meanwhile on the node's thread. The
outcome is the outcome of the order's family: when the OMS re-prices an unfilled order it
cancels it and sends a child order with a new id (tags parent, root, attempt), and the wait
follows the family (`OrderManager.family_done`) until no order of it is working and none is to
come; the fills of every child are booked as the strategy's trade. The order's id comes from
the OMS (`next_order_id`, its `<strategy>-<date>-<seq>` format) before the adapter records it,
so the OMS never renames it; the adapter's own counter is kept in tags['strategy_order_id'].
The original engine did the
same (place, confirm, then book), and a candle strategy depends on it:
CrossSectionalReversal sizes its buys on the cash its sells just raised and stops buying once it
holds `top_n` names, so if a sell were still open when the buys are sized, the basket would
come out different (or, when the sell then fails, larger than top_n). Bars arriving meanwhile
are held and processed afterwards, in order. With `confirm_fills: false` execute_trade returns
at once and the fills are booked whenever they arrive.

Candles reach the strategy as midfreq.core.candle.Candle in the convention it was written for:
the exchange's local close time tagged as UTC (VenueSpec.to_legacy_index), one cross-section at
a time (the MarketDataService's CrossSection for the strategy's universe), in the universe's
order. Closed bars that never make a published cross-section (fewer than the minimum share of
the universe reported) are fed when a newer cross-section arrives, or at shutdown, as the
original engine fed every closed candle. The live settings the original engine gave the strategy
are given here too: `min_cross_section` (90% of the universe), `defer_unready_rebalance`, and
`full_cross_section` (the universe's size; for a cross-section the MarketDataService published
incomplete at its window's end, the number of bars it holds, so it is decided on now rather than
when the next candle arrives an hour later).

Records: `session_*.jsonl` through midfreq.trading.session_log and `state_<strategy>.json`
through midfreq.trading.state, both in the original engine's formats.
"""
from midfreq.trading.strategy.legacy.adapter import LiveLegacyAdapter
from midfreq.trading.strategy.legacy.portfolio import (
    LiveLegacyPortfolio,
    build_risk_manager,
    legacy_symbol_for,
    round_to_tick,
)

__all__ = ["build_risk_manager", "legacy_symbol_for", "LiveLegacyAdapter", "LiveLegacyPortfolio", "round_to_tick"]
