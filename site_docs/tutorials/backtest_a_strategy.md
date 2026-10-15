# Backtest a strategy

This tutorial runs the candle backtester from Python: it loads the price cache, backtests a
single-stock strategy, reads its trades and equity, changes the costs and the risk settings,
and finally backtests a cross-sectional strategy over the S&P 100. Two of the results reproduce,
to the cent, examples from the [dashboard guide](../concepts/dashboard.md).

**Needs:** the [installation](../getting_started/installation.md) and the price cache's hourly
candles for `US.AAPL` and the S&P 100. The code blocks form one script; run them in order, in
one file or one interpreter session.

## 1. Look at the data

`DataStorage` reads the price cache, `data/<MARKET>_<CODE>/<timeframe>.parquet`
([Data and connectors](../concepts/data.md#the-price-cache)):

```python
from midfreq.data.storage import DataStorage

storage = DataStorage()                        # the price cache, data/
candles = storage.load_data("US.AAPL", "1h")
print(candles.tail(3))
print(len(candles), "candles from", candles.index[0], "to", candles.index[-1])
```

```text
                             open    high     low     close     volume
timestamp                                                             
2026-09-25 14:30:00+00:00  340.11  340.46  339.60  339.8599  1264096.0
2026-09-25 15:30:00+00:00  339.84  340.48  339.81  340.0200  2795012.0
2026-09-25 16:00:00+00:00  340.01  341.67  339.90  341.0700  7257698.0
5460 candles from 2023-08-14 10:30:00+00:00 to 2026-09-25 16:00:00+00:00
```

Each timestamp is the candle's close in New York time, with a UTC label: the last candle of
25 September 2026 is the half hour to the 16:00 close.

## 2. Run a backtest

The RSI strategy with its default parameters, which the registry holds, on one year of Apple at
the US commission:

```python
from datetime import datetime

from midfreq.backtest.backtester import Backtester
from midfreq.core.candle import Timeframe
from midfreq.core.portfolio import Portfolio
from midfreq.strategies.candle_strategies import STRATEGY_REGISTRY, RSIStrategy

START, END = datetime(2025, 9, 25), datetime(2026, 9, 25)
US_FEE = 0.00005                               # 0.005% per side

params = {name: p["default"] for name, p in STRATEGY_REGISTRY["RSI"]["params"].items()}
print(params)

portfolio = Portfolio(initial_cash=100_000, commission_rate=US_FEE)
metrics = Backtester(storage, portfolio).run(RSIStrategy, ["US.AAPL"], Timeframe.HOUR_1,
                                             start_date=START, end_date=END, **params)
```

```text
{'rsi_period': 14, 'oversold': 30, 'overbought': 70}
=== Initializing Backtest Simulator ===
Cash: $100,000.00 | Timeframe: 1h | Symbols: ['US.AAPL']
  [+] Loaded 1758 candles for US.AAPL
Starting RSI Strategy (Period: 14, Oversold: 30, Overbought: 70)

=== Simulating Trades (1758 events, chronological) ===

=== Simulation Complete ===
```

`run` returns a dictionary of metrics ([Backtesting](../concepts/backtesting.md#the-candle-backtester)).
A small helper prints the ones this tutorial compares:

```python
def show(m):
    print(f"return {m['return_pct']:+.2f}%  final {m['final_equity']:,.0f}  "
          f"Sharpe {m['sharpe_ratio']:.2f}  max DD {m['max_drawdown']:.2f}%  "
          f"win rate {m['win_rate']:.1f}%  trades {m['total_trades']}  "
          f"buy and hold {m['benchmark_return_pct']:+.2f}%  alpha {m['alpha']:+.2f}%")

show(metrics)
```

```text
return +12.32%  final 112,322  Sharpe 3.09  max DD 2.56%  win rate 73.9%  trades 47  buy and hold +34.78%  alpha -22.45%
```

These are the RSI row of [Example D](../concepts/dashboard.md#example-d-three-indicator-strategies-on-one-stock)
in the dashboard guide. A Sharpe ratio of 3.09 and a win rate of 74% look strong, yet holding
the stock returned almost three times as much: with 100 shares a trade, most of the capital
sits in cash, which also keeps the drawdown small.

## 3. Trades and equity

The portfolio keeps every trade and, through the backtester, the equity at every candle:

```python
import pandas as pd

trades = pd.DataFrame(portfolio.trade_history)
print(trades[["timestamp", "action", "qty", "price", "commission", "exit_reason"]].head(4).to_string(index=False))

equity = pd.DataFrame(portfolio.equity_curve_detailed).set_index("timestamp")["equity"]
daily = equity.resample("D").last().dropna()
print(f"{len(equity)} equity points, {len(daily)} trading days; "
      f"lowest {equity.min():,.0f} on {equity.idxmin():%Y-%m-%d}, highest {equity.max():,.0f}")
```

```text
                timestamp action  qty      price  commission exit_reason
2025-10-07 11:30:00+00:00    BUY  100 255.435417    1.277177         NaN
2025-10-08 14:30:00+00:00   SELL  100 256.965764    1.284829      signal
2025-10-10 11:30:00+00:00    BUY  100 248.203133    1.241016         NaN
2025-10-20 10:30:00+00:00   SELL  100 258.549912    1.292750      signal
1758 equity points, 252 trading days; lowest 99,759 on 2026-01-20, highest 112,322
```

A sale records why it happened in `exit_reason`: `signal` for the strategy's own rule, or
`stop_loss`, `trailing_stop` and `take_profit` for the risk manager's exits. The Sharpe ratio is
computed from the daily returns of this equity curve.

## 4. Costs

To compare variants, a helper that runs one backtest quietly:

```python
import contextlib
import io


def run(strategy, symbols, commission_rate, slippage_bps=0.0, risk_manager=None, **params):
    """One backtest, with the backtester's narration silenced; returns the metrics."""
    p = Portfolio(initial_cash=100_000, commission_rate=commission_rate)
    with contextlib.redirect_stdout(io.StringIO()):
        return Backtester(storage, p, risk_manager=risk_manager, slippage_bps=slippage_bps).run(
            strategy, symbols, Timeframe.HOUR_1, start_date=START, end_date=END, **params)


show(run(RSIStrategy, ["US.AAPL"], US_FEE, slippage_bps=5, **params))           # 5 bps of slippage
show(run(RSIStrategy, ["US.AAPL"], Portfolio.HK_FEE_RATE, **params))            # Hong Kong's 0.16%
```

```text
return +11.66%  final 111,664  Sharpe 2.94  max DD 2.62%  win rate 73.9%  trades 47  buy and hold +34.78%  alpha -23.11%
return +10.28%  final 110,283  Sharpe 2.62  max DD 2.79%  win rate 73.9%  trades 47  buy and hold +34.78%  alpha -24.49%
```

The trades are the same in all three runs; only what each costs changes. At 47 trades a year
the effect is small. A strategy that trades several times a day pays it hundreds of times
([Trading costs](../concepts/backtesting.md#trading-costs)).

## 5. Risk settings

A `RiskManager` adds stops, a drawdown halt and a position sizer. Here: a 2% stop-loss, and a
sizer that risks 2% of equity on each trade:

```python
from midfreq.core.risk_manager import RiskManager, SizingMethod, create_position_sizer

risk = RiskManager(stop_loss_pct=0.02, max_drawdown_pct=0.10,
                   position_sizer=create_position_sizer(SizingMethod.FIXED_FRACTIONAL, risk_pct=0.02))
p = Portfolio(initial_cash=100_000, commission_rate=US_FEE)
with contextlib.redirect_stdout(io.StringIO()):
    m = Backtester(storage, p, risk_manager=risk).run(RSIStrategy, ["US.AAPL"], Timeframe.HOUR_1,
                                                      start_date=START, end_date=END, **params)
show(m)
print(pd.DataFrame(p.trade_history)["exit_reason"].fillna("entry").value_counts().to_dict())
```

```text
return +49.77%  final 149,770  Sharpe 2.98  max DD 8.18%  win rate 70.0%  trades 61  buy and hold +34.78%  alpha +14.99%
{'entry': 31, 'signal': 22, 'stop_loss': 8}
```

The return quadruples, but not because of the stop. The fixed-fractional sizer buys
`equity × risk_pct / (entry − stop)` shares; with the stop 2% below the entry that is
`equity / price`, the whole account in every trade, about four times the default 100 shares at
Apple's price. Eight trades were stopped out. The rule is the same; the exposure is not, which is
why every comparison in the project fixes the sizing.

## 6. A cross-sectional strategy

The cross-sectional strategies rank a whole universe at each rebalance. The S&P 100 universe
comes from `config/symbols.json`, limited to the symbols with cached candles, and a basket is
sized by splitting the equity equally across the stocks it holds:

```python
from midfreq.core.config import ConfigLoader
from midfreq.strategies.candle_strategies import CrossSectionalReversal

sp100 = ConfigLoader().get_universe("sp100", with_data_only=True)
print(len(sp100), "symbols with cached hourly candles")

basket = RiskManager(max_drawdown_pct=0.10,
                     position_sizer=create_position_sizer(SizingMethod.EQUAL_DOLLAR, n_positions=3))
show(run(CrossSectionalReversal, sp100, US_FEE, risk_manager=basket,
         lookback=30, top_n=3, rebalance_every=19))
```

```text
100 symbols with cached hourly candles
return +56.83%  final 156,828  Sharpe 1.44  max DD 20.50%  win rate 57.0%  trades 449  buy and hold +24.86%  alpha +31.97%
```

Every 19 hourly candles, the strategy ranks the 100 stocks by their return over the last 30
candles and holds, in equal amounts, the 3 with the lowest. These are the live forward test's
settings, and the figures are those of
[Example C](../concepts/dashboard.md#example-c-the-live-settings-in-a-backtest-and-the-cost-of-fees),
where the dashboard runs the same backtest. The maximum drawdown of 20.50% exceeds the 10% halt
because the halt is measured from the starting capital, not from the peak.

!!! warning "This is not evidence of an edge"
    The settings were chosen by a grid search over the last few months of this same year, so
    this backtest partly scores them on the data they were picked from. The next tutorial,
    [Walk-forward a strategy](walk_forward.md), measures the same strategy on data its parameters
    were not chosen on.
