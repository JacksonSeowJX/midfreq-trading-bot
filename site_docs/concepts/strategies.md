# Strategies

A strategy is a trading rule with no knowledge of how it is run. The framework has two
strategy APIs, in `midfreq-strategies`:

| | Candle API | Event-driven Strategy API |
|---|---|---|
| Module | `midfreq.strategies.candle_strategies` | `midfreq.strategies.trading` |
| Base class | `BaseStrategy` | `Strategy`, configured by a `StrategyConfig` |
| Receives | `on_data(symbol, candle)` for each closed candle of every symbol, in time order | `on_bar`, `on_slice`, `on_signal`, `on_quote_tick`, `on_trade_tick`, `on_order_book`, for the streams it subscribes to |
| Decides by | calling `self.portfolio.execute_trade(...)`, which fills at once at the price given | submitting an `Order` built by `self.order_factory`; the venue decides the fill |
| Learns of fills | from the portfolio, which it may read and write | from events: `on_order_filled`, `on_order_rejected`, `on_order_denied`, `on_order_canceled` |
| Sees | the portfolio itself | read-only views of the cache and the portfolio |
| Runs on | the candle backtester, the optimizer, the dashboard, the live engine; and the venue engine through the legacy adapter | the venue engine (`BacktestEngine`) |

Every reported result came from the candle API. The event-driven API is the venue engine's,
modelled on NautilusTrader's `Strategy`.

## The candle API

A candle strategy subclasses `BaseStrategy`, is constructed with the portfolio, an optional
`RiskManager` and its parameters, and implements `on_data`. It trades by calling
`portfolio.execute_trade(symbol, is_buy, qty, price, timestamp, exit_reason=None)`, which books
the trade at once at the price given, with the portfolio's commission (and the backtester's
slippage, which wraps the call). The base class supplies the risk helpers the library uses:
`_check_risk_exits` (stop-loss, trailing stop and take-profit on an open position),
`_is_halted` (the drawdown circuit breaker) and `_get_trade_qty` (the risk manager's position
size, or 100 shares without one).

The backtester merges every symbol into one stream ordered by time, so candles of different
symbols that share a timestamp arrive one after another. The cross-sectional strategies buffer
them into a cross-section and rank the universe when the next timestamp begins.

In live trading the same class runs unchanged: the live engine hands it a `LivePortfolio`,
whose `execute_trade` sends an order to the broker and records the trade only when the broker
confirms the fill ([Live trading](live_trading.md)).

### The registry

`STRATEGY_REGISTRY` maps each strategy's display name to its class and its parameters, each with
a label, default, minimum, maximum and step. The optimizer builds its grid from these ranges,
the dashboard builds its forms from them, and the live runner looks strategies up by name, so a
strategy added to the registry is available everywhere.

| Name | Class | Rule |
|---|---|---|
| SMA Crossover | `MovingAverageCrossover` | buy when the fast simple moving average crosses above the slow one, sell when it crosses back |
| RSI | `RSIStrategy` | buy when the RSI falls below the oversold threshold, sell when it rises above the overbought one |
| MACD | `MACDStrategy` | buy and sell on crossovers of the MACD line and its signal line |
| Bollinger Bands | `BollingerBandsStrategy` | buy at the lower band, sell at the upper band |
| Z-Score Mean Reversion | `ZScoreMeanReversion` | trade a rolling z-score of the price back towards its mean |
| Pairs Trading | `PairsTradingStrategy` | trade the price ratio of two correlated stocks when its z-score deviates |
| Ensemble Voting | `EnsembleStrategy` | trade when enough of RSI, MACD and Bollinger Bands agree |
| Regime Switch | `RegimeSwitchStrategy` | classify trending or ranging by Kaufman's efficiency ratio, and trade the style that suits it |
| HMM Regime Switch | `HMMRegimeSwitchStrategy` | the same, with a Gaussian hidden Markov model (the `hmm` extra) |
| Cross-Sectional Reversal | `CrossSectionalReversal` | rank a universe by recent return and hold the laggards in equal amounts |
| Cross-Sectional Momentum | `CrossSectionalMomentum` | the same ranking, holding the leaders |
| ML Direction Classifier | `MLDirectionClassifier` | a classifier on recent returns predicts the next move's direction |
| Buy & Hold + Risk Overlay | `BuyAndHoldRiskOverlay` | buy once and exit only by a stop-loss or trailing stop |

The parameters of each are in the [API reference](../api/python/midfreq/strategies/candle_strategies.md).

## The event-driven Strategy API

A `Strategy` reacts to data and decides; it does not touch cash or positions. In `on_start` it
subscribes to what it needs: `subscribe_bars(bar_type)`, `subscribe_slices()`,
`subscribe_signals(name)`, `subscribe_quote_ticks`, `subscribe_trade_ticks`,
`subscribe_order_book`. It expresses decisions as orders built by `self.order_factory`
(`market`, `limit`) and sent with `submit_order`; `close_position` sells a whole long position.
What happened to an order comes back as events. Before an order reaches the venue, the risk
engine may deny it (`OrderDenied`); the venue may then reject it (`OrderRejected`) for cash, lot
size, minimum notional or position, or fill it in one or more `OrderFilled` events.

The `cache` and `portfolio` a strategy sees are read-only views, so a strategy cannot, for
example, give itself cash the way a candle strategy could by writing to `portfolio.cash`.

- **Slices.** `on_slice` runs once every bar with the same `ts_event` has been published, with
  all of them in a `TimeSlice`. Cross-sectional decisions belong there; an order submitted in
  `on_slice` fills at that slice's closes. (A slice is LEAN's term; NautilusTrader leaves this
  to user code.)
- **Signals.** `publish_signal(name, value)` puts a `Signal` on the message bus, and any
  strategy subscribed to that name receives it in `on_signal`. A signal's value may be a ranking
  rather than a number, which lets one strategy rank and another trade the ranking.

### The same rule in both APIs

A rule small enough to read at a glance, written once for each API:

```python
from dataclasses import dataclass

from midfreq.core.model import OrderSide
from midfreq.strategies.candle_strategies import BaseStrategy
from midfreq.strategies.trading import Strategy, StrategyConfig


class UpCandle(BaseStrategy):
    """Holds 100 shares after a candle that closes above its open; sells after one that does not."""

    def on_data(self, symbol, candle):
        held = self.portfolio.get_position_qty(symbol)
        if candle.close > candle.open and held == 0:
            self.portfolio.execute_trade(symbol, True, 100, candle.close, candle.timestamp)
        elif candle.close <= candle.open and held > 0:
            self.portfolio.execute_trade(symbol, False, held, candle.close, candle.timestamp)


@dataclass
class UpCandleConfig(StrategyConfig):
    bar_type: str = ""


class UpCandleOrders(Strategy):
    """The same rule, expressed as orders."""

    def on_start(self):
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar):
        iid = bar.instrument_id
        held = self.portfolio.net_qty(iid)
        if bar.close > bar.open and held == 0:
            self.submit_order(self.order_factory.market(iid, OrderSide.BUY, 100))
        elif bar.close <= bar.open and held > 0:
            self.submit_order(self.order_factory.market(iid, OrderSide.SELL, held))
```

Run on Apple's hourly candles over the same year, the first on the candle backtester and the
second on the venue engine with the same fee:

```python
import contextlib
import io
from datetime import datetime

from midfreq.backtest import BacktestEngine
from midfreq.backtest.backtester import Backtester
from midfreq.core.candle import Timeframe
from midfreq.core.model import PercentFeeModel
from midfreq.core.paths import data_dir
from midfreq.core.portfolio import Portfolio
from midfreq.data.persistence import LegacyDataReader
from midfreq.data.storage import DataStorage

start, end = datetime(2025, 9, 25), datetime(2026, 9, 25)

portfolio = Portfolio(initial_cash=100_000, commission_rate=0.00005)
with contextlib.redirect_stdout(io.StringIO()):          # the backtester narrates to stdout
    metrics = Backtester(DataStorage(), portfolio).run(UpCandle, ["US.AAPL"], Timeframe.HOUR_1,
                                                       start_date=start, end_date=end)
print(f"candle API: {metrics['total_trades']} trades, final equity {metrics['final_equity']:,.2f}")

reader = LegacyDataReader(data_dir())
engine = BacktestEngine()
engine.add_venue("XNAS", {"USD": 100_000}, fee_model=PercentFeeModel(0.00005))
engine.add_data(reader.read_bars("AAPL.XNAS", "1-HOUR", start, end))
engine.add_strategy(UpCandleOrders(UpCandleConfig(bar_type="AAPL.XNAS-1-HOUR-LAST-EXTERNAL")))
result = engine.run()
print(f"event API:  {len(result.trades())} trades, final equity {result.final_equity['USD']:,.2f}")
```

```text
candle API: 913 trades, final equity 96,678.12
event API:  913 trades, final equity 96,678.12
```

With the venue engine's default fill model (a market order fills at the close of the bar it was
decided on) and the venue's fee equal to the portfolio's commission, the two make the same 913
trades to the cent. Holding a stock for one hour after every rising candle lost 3.3% over a
year in which the stock itself rose 34.78%.

<!-- include: docs/ARCHITECTURE.md#native-strategies-midfreqstrategiesevent_driven -->

The [Write an event-driven strategy](../tutorials/event_driven_strategy.md) tutorial builds a
strategy on this API step by step, with limit orders, rejections and two venues.
