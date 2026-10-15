# The forward test

The forward test trades the project's strongest result on a moomoo paper account, unattended,
on data that did not exist when the strategy and its settings were chosen. It is the project's
only result of that kind. It began on 28 September 2026 and runs until the end of the project;
its first orders filled on 5 October 2026.

| | |
|---|---|
| Strategy | Cross-Sectional Reversal, from the candle library, unchanged |
| Universe | 98 S&P 100 stocks: the 100 with cached prices, less two whose data had stopped updating when the settings were chosen (US.WMT, US.XOM) |
| Settings | every 19 hourly candles, rank the stocks by their return over the last 30 candles and hold, in equal amounts, the 3 with the lowest return |
| Capital | US$100,000 of the paper account, which holds about US$1,000,000, so that the test trades at the size its backtest was measured at (about US$33,300 a holding) |
| Risk settings | equal-dollar sizing across the 3 holdings, no stop-loss, a drawdown halt at 15% |
| Sessions | one each US trading day, started by cron on the trading VM within 30 minutes after the open and ending just after the close |
| Configuration | [`config/sp100_forward_test.json`](repo:config/sp100_forward_test.json) |

## How the settings were chosen

The settings were picked the way each walk-forward window picks them: a grid search of the
strategy's 96 configurations, by Sharpe ratio, over the most recent training span, the 84 days
to 25 September 2026, at the US fee of 0.005% per side and 5 basis points of slippage. Going live
is the next window's test span. The search's in-sample figures (a return of 54.2% and a Sharpe
ratio of 5.45 over those 84 days) are what it selected on, not a forecast; the validated
out-of-sample expectation at this fee is +5.73% per window under Config A and +0.63% under
Config B. Two of the chosen values, the 30-candle lookback and the 19-candle rebalance interval,
are at the edge of their grids, which the configuration records as a warning.

## What is measured

Everything is recorded in the bot's session logs, and read back by
`midfreq.research.forward_test` without OpenD:

- **Profit or loss**: the change in the broker's reported account total since the test began.
  The test is the only activity on the US paper account, so the change in the account is the
  test's result, net of fees and including any dividends the broker credits.
- **Trades**: every fill the broker confirmed, with the profit realized on each sale before
  fees, and the candle whose ranking produced it.
- **Holdings**: the positions at the end of the latest session.

The figures change with each session, so they are not quoted here. The dashboard's **Live**
page shows them, beside the broker's own view of the account
([Example A](../concepts/dashboard.md#example-a-the-live-forward-test)); the hosted dashboard is
private, with access by invitation.

## Checking the record against the broker

The bot's record and the broker's account are shown side by side and never merged: a fill in
the record should appear among the broker's orders, and the record's holdings among the broker's
positions, with the same stocks, sides, share counts and prices. A difference would point to a
fault in the bot's record keeping. The trading VM publishes the broker's view every 5 minutes, so
the hosted dashboard can show it without reaching the broker
([Live trading](../concepts/live_trading.md#two-sources-never-merged)).

## Before the forward test

From July to September 2026 the trading VM ran the Hong Kong single-stock strategies on the
Hong Kong paper account. Those sessions are in the same record, under the **HK** market of the
dashboard's Live page; their fills are also what the Hong Kong fee of 0.16% per side was
calibrated against.
