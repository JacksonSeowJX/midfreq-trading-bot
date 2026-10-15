# Write an event-driven strategy

This tutorial writes a strategy against the event-driven Strategy API and runs it on the venue
engine across two venues at once: Tencent on HKEX, settled in Hong Kong dollars, and Apple on
Nasdaq, settled in US dollars. It then reads the fills, changes the fill and slippage models,
and makes the risk engine and the venue refuse orders.

**Needs:** the [installation](../getting_started/installation.md) and the price cache's hourly
candles for `HK.00700` and `US.AAPL`. The engine reads the cache through `LegacyDataReader`,
which never writes to it. The code blocks form one script; run them in order.

## 1. The strategy

A channel breakout: buy when a bar closes above the highest high of the previous 20 bars, and
sell the whole position when one closes below the lowest low of the previous 10. Each entry
spends a fixed amount of the instrument's currency, which the venue rounds down to whole lots.

```python
from collections import deque
from dataclasses import dataclass, field

from midfreq.core.model import OrderSide
from midfreq.strategies.trading import Strategy, StrategyConfig


@dataclass
class BreakoutConfig(StrategyConfig):
    bar_types: tuple = ()
    entry_lookback: int = 20       # buy when a bar closes above the highest high of the last 20 bars
    exit_lookback: int = 10        # sell when one closes below the lowest low of the last 10
    notional: dict = field(default_factory=dict)   # the amount to buy per entry, by currency


class Breakout(Strategy):
    """A channel breakout: long on a new high, flat on a new low."""

    def __init__(self, config: BreakoutConfig):
        super().__init__(config)
        self.highs, self.lows = {}, {}
        self.fills, self.refused = [], []

    def on_start(self):
        for bar_type in self.config.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        iid = bar.instrument_id
        highs = self.highs.setdefault(iid, deque(maxlen=self.config.entry_lookback))
        lows = self.lows.setdefault(iid, deque(maxlen=self.config.exit_lookback))
        held = self.portfolio.net_qty(iid)
        if held == 0 and len(highs) == highs.maxlen and bar.close > max(highs):
            instrument = self.cache.instrument(iid)
            qty = instrument.make_qty(self.config.notional[instrument.currency] / bar.close)
            if qty > 0:
                self.submit_order(self.order_factory.market(iid, OrderSide.BUY, qty))
        elif held > 0 and len(lows) == lows.maxlen and bar.close < min(lows):
            self.close_position(iid, exit_reason="breakdown")
        highs.append(bar.high)
        lows.append(bar.low)

    def on_order_filled(self, event):
        self.fills.append(event)

    def on_order_rejected(self, event):        # refused by the venue
        self.refused.append(("rejected", str(event.instrument_id), event.reason))

    def on_order_denied(self, event):          # stopped by the risk engine
        self.refused.append(("denied", str(event.instrument_id), event.reason))
```

What the strategy does not do is as important as what it does. It never touches cash or
positions: `self.portfolio` and `self.cache` are read-only views. It decides with orders, and
learns what happened from events. The highs and lows are appended after the decision, so a bar
is compared with the bars before it, never with itself.

## 2. Run it on two venues

```python
from datetime import datetime

from midfreq.backtest import BacktestEngine, BacktestEngineConfig
from midfreq.core.paths import data_dir
from midfreq.data.persistence import LegacyDataReader

reader = LegacyDataReader(data_dir())                  # the price cache, read in true UTC
START, END = datetime(2025, 9, 25), datetime(2026, 8, 28)
HK, US = "00700.XHKG-1-HOUR-LAST-EXTERNAL", "AAPL.XNAS-1-HOUR-LAST-EXTERNAL"


def backtest(config=None, notional=None, **venue_models):
    """Tencent on HKEX and Apple on Nasdaq, each venue with its own currency account."""
    engine = BacktestEngine(config)
    engine.add_venue("XHKG", {"HKD": 1_000_000}, **venue_models)
    engine.add_venue("XNAS", {"USD": 100_000}, **venue_models)
    engine.add_data(reader.read_bars("00700.XHKG", "1-HOUR", START, END))
    engine.add_data(reader.read_bars("AAPL.XNAS", "1-HOUR", START, END))
    strategy = Breakout(BreakoutConfig(bar_types=(HK, US),
                                       notional=notional or {"HKD": 500_000, "USD": 50_000}))
    engine.add_strategy(strategy)
    return engine.run(), strategy


result, strategy = backtest()
print(result.summary())
```

```text
Backtest 2025-09-25 02:30:00+00:00 .. 2026-08-28 20:00:00+00:00: 2972 bars, 92 fills, orders {'FILLED': 92}
  HKD: 1,000,000.00 -> 843,586.14 (-15.64%), Sharpe -1.90, max drawdown 17.61%, benchmark -29.50%
  USD: 100,000.00 -> 104,078.95 (+4.08%), Sharpe 0.58, max drawdown 8.10%, benchmark +26.33%
```

The engine merges the two streams in true UTC, so a Hong Kong bar closing at 10:30 HKT
(02:30 UTC) is processed before a New York bar of the same calendar day. Each venue charges its
own fee from `config/venues.json` (0.16% per side on HKEX, 0.005% on Nasdaq), and each currency
has its own account and its own metrics: results are never converted or summed.

## 3. Fills and trades

Each fill arrives as an `OrderFilled` event; the result also keeps every fill as a row:

```python
fill = strategy.fills[0]
print(fill.instrument_id, fill.side.value, fill.last_qty, round(fill.last_px, 2), round(fill.commission, 2),
      fill.currency, fill.liquidity_side.value)

trades = result.trades()
print(trades.groupby(["instrument_id", "side"]).size().to_dict())
print(trades[["timestamp", "instrument_id", "side", "qty", "price", "commission", "exit_reason"]]
      .head(4).to_string(index=False))
```

```text
00700.XHKG BUY 700.0 675.2 756.22 HKD TAKER
{('00700.XHKG', 'BUY'): 21, ('00700.XHKG', 'SELL'): 20, ('AAPL.XNAS', 'BUY'): 26, ('AAPL.XNAS', 'SELL'): 25}
                timestamp instrument_id side   qty     price  commission exit_reason
2025-10-02 02:30:00+00:00    00700.XHKG  BUY 700.0 675.20000  756.224000        None
2025-10-10 02:30:00+00:00    00700.XHKG SELL 700.0 657.70000  736.624000   breakdown
2025-10-17 18:30:00+00:00     AAPL.XNAS  BUY 198.0 251.37143    2.488577        None
2025-10-21 02:30:00+00:00    00700.XHKG  BUY 700.0 638.20000  714.784000        None
```

HK$500,000 at HK$675.20 is 740.5 shares; the venue rounded the order down to 700, seven board
lots of 100. Apple, with a lot of one share, got 198 shares for US$50,000. The `exit_reason`
tag given to `close_position` is carried onto the fill. Each instrument ends the run with one
position open, which is why there is one more buy than sell.

## 4. Fill and slippage models

By default a market order fills at the close of the bar it was decided on, with no slippage, as
in the candle backtester. Filling at the next bar's open instead, and paying half the spread:

```python
from midfreq.backtest import BarFillModel, SpreadSlippage

realistic, _ = backtest(fill_model=BarFillModel(timing="next_open"), slippage_model=SpreadSlippage())
for ccy in ("HKD", "USD"):
    print(f"{ccy}: at the close {result.return_pct(ccy):+.2f}%, "
          f"at the next open with half the spread {realistic.return_pct(ccy):+.2f}%")
```

```text
HKD: at the close -15.64%, at the next open with half the spread -16.05%
USD: at the close +4.08%, at the next open with half the spread +3.41%
```

There are no quotes in the price cache, so `SpreadSlippage` uses its assumed half-spread of
3.33 basis points, the median measured on the Hong Kong stocks. The models and their options are
in [Backtesting](../concepts/backtesting.md#fill-models-and-slippage-models).

## 5. Denied and rejected orders

The risk engine checks an order before it reaches the venue, and the venue can still refuse it.
A limit of HK$300,000 per order, then a US entry larger than the account:

```python
from midfreq.backtest.risk import RiskConfig

limited, s1 = backtest(BacktestEngineConfig(risk=RiskConfig(max_order_notional={"HKD": 300_000})))
print(limited.orders_by_status(), s1.refused[0])

too_big, s2 = backtest(notional={"HKD": 500_000, "USD": 150_000})
print(too_big.orders_by_status(), s2.refused[0])
```

```text
{'DENIED': 47, 'FILLED': 51} ('denied', '00700.XHKG', 'order notional 472,640.00 HKD exceeds the limit 300,000.00')
{'FILLED': 41, 'REJECTED': 95} ('rejected', 'AAPL.XNAS', 'insufficient USD cash (100,000.00 < 149,824.86)')
```

In the first run every Tencent entry is denied, so the strategy stays flat there and tries again
at each new breakout; only the Apple orders fill. In the second, every Apple entry is rejected by
the venue for want of cash, and only the Tencent orders fill. Each refusal reaches the strategy
as an event with its reason, and the strategy decides what to do about it.

## Next steps

- [Strategies](../concepts/strategies.md#the-event-driven-strategy-api): slices for
  cross-sectional decisions, and signals between strategies.
- The native cross-sectional pair, `CrossSectionalRanker` and `EqualWeightBasket`, in the
  [API reference](../api/python/midfreq/strategies/event_driven/cross_sectional.md).
- [Instruments and venues](../concepts/instruments_and_venues.md): lot sizes, fees and sessions
  per venue.
