# Backtesting

`midfreq-backtest` runs strategies over history with two engines, kept side by side and checked
against each other:

- the **candle backtester**, `midfreq.backtest.backtester.Backtester`, which replays the price
  cache candle by candle through a [candle strategy](strategies.md#the-candle-api). Every
  reported result was produced with it. Its core now runs in C++ underneath
  ([the C++ engine](native_engine.md)) with identical results; the pure-Python implementation is
  kept as `PythonBacktester`, the reference.
- the **venue engine**, `midfreq.backtest.BacktestEngine`, which runs
  [event-driven strategies](strategies.md#the-event-driven-strategy-api) on simulated venues
  with fill, slippage and fee models, a risk engine and one cash account per currency. Every
  candle strategy also runs in it, unchanged, with identical results.

## The candle backtester

```text
Backtester(storage, portfolio, risk_manager=None, slippage_bps=0.0)
    .run(strategy_class, symbols, timeframe, start_date=None, end_date=None,
         progress_callback=None, end_inclusive=True, **strategy_params) -> dict of metrics
```

A run:

1. loads each symbol's file from the price cache through `DataStorage` and keeps the candles
   from `start_date` and, by default, up to the end of `end_date`'s day (the optimizer passes
   `end_inclusive=False` for a training span, so that it does not overlap the test span that
   starts on the same day);
2. merges every symbol into one stream ordered by timestamp, so candles that share a timestamp
   arrive one after another;
3. calls the strategy's `on_data(symbol, candle)` for each candle, and records the portfolio's
   equity once per timestamp;
4. returns the metrics below, with an equal-weight buy-and-hold of the same symbols as the
   benchmark.

A trade fills at the price the strategy passed (in the library, the close of the candle it
decided on). Slippage moves it against the trader: a buy pays `slippage_bps` more and a sell
receives that much less. The commission is the trade's value times the portfolio's
`commission_rate`, on each side. A buy the cash cannot pay for, and a sell of more than is held,
are refused; there is no short selling. Quantities are not rounded to board lots.

| Metric | Meaning |
|---|---|
| `return_pct`, `final_equity` | the change in equity over the run, with open positions at their last price |
| `sharpe_ratio` | the mean daily return over its standard deviation, annualized over 252 trading days, with no risk-free rate |
| `max_drawdown` | the largest fall in equity from an earlier peak, in percent of that peak |
| `win_rate`, `profit_factor` | over completed round trips: the share that made money, and gross profit over gross loss |
| `total_trades` | fills, buys and sells counted separately |
| `benchmark_return_pct`, `alpha` | the equal-weight buy-and-hold return of the same symbols, and the strategy's return minus it |
| `open_positions`, `cash_balance`, `total_events`, `slippage_bps` | the state at the end and the inputs of the run |

The portfolio keeps every trade in `trade_history` and the equity at every timestamp in
`equity_curve_detailed`.

### Trading costs

The commission is set per market. The rates the studies use:

| Market | Commission per side | Source |
|---|---|---|
| Hong Kong | 0.16% all-in: commission, platform fee, 0.1% stamp duty, exchange and regulatory levies | calibrated against moomoo paper fills on 9 July 2026; `Portfolio.HK_FEE_RATE`, the portfolio's default |
| United States | 0.005% | moomoo Singapore's published US schedule (US$0.99 plus GST per order, at these position sizes) |

Slippage is 0 by default. The studies use 5 basis points (`SLIPPAGE_BPS` in the study scripts;
the two HMM cost studies also run a gross comparison without costs), and so did the search that
chose the forward test's settings.
The spread on the Hong Kong stocks was measured at a median half-spread of 3.33
basis points (`scripts/calibration/`), which the venue engine's `SpreadSlippage` assumes when no
quotes are available.

Costs decide results at this frequency. The S&P 100 cross-sectional reversal passes the
project's validation standard at 0% and 0.005% per side and fails at 0.03% and at the Hong Kong
rate of 0.16% ([Results](../results/index.md)).

### Risk rules

A `RiskManager` (`midfreq.core.risk_manager`) adds exits, sizing and a circuit breaker to a
strategy that uses the base class's helpers:

| Setting | Effect |
|---|---|
| `stop_loss_pct`, `take_profit_pct` | exit an open position when the price falls (rises) that far from the entry |
| `trailing_stop_pct` | exit when the price falls that far below its peak since entry |
| `max_drawdown_pct` (default 0.10) | while equity is that far below the **starting** capital, act on no new signals; stops still apply, and trading resumes if equity recovers |
| `position_sizer` (default 100 shares) | `FixedQuantitySizer`, `FixedFractionalSizer` (a share of equity at risk), `KellySizer` (a fraction of the Kelly bet, capped), `EqualDollarSizer` (equity split equally across a basket, less a cost buffer) |

Because the halt is measured from the starting capital rather than from the peak, a backtest's
maximum drawdown can exceed it.

## The venue engine

<!-- include: docs/ARCHITECTURE.md#backtesting strip-title -->

<!-- include: docs/ARCHITECTURE.md#fill-models-and-slippage-models -->

<!-- include: docs/ARCHITECTURE.md#running-candle-strategies-unchanged -->

## Which engine to use

| Use | Engine |
|---|---|
| Reproduce or extend the reported results; anything the dashboard or the optimizer runs | the candle backtester |
| A strategy across venues in different timezones or currencies | the venue engine |
| Limit orders, partial fills, board lots, minimum notionals, quotes, trades or order books | the venue engine |
| Check the candle backtester against an independent implementation | the venue engine with the legacy adapter |
