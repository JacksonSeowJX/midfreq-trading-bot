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
| Configuration | [`config/sp100_forward_test.json`](repo:config/sp100_forward_test.json); on the trading node, [`config/node_sp100.yaml`](repo:config/node_sp100.yaml), which reads it |

The sessions reported on this page, from 28 September to 16 October 2026, were run by the session
runner, `midfreq.live`. The trading VM's schedule runs the test on the trading node,
`midfreq.trading`, with the same strategy, settings, capital cap and state file; the session
runner's line stays in the schedule for rollback ([Live trading](../concepts/live_trading.md)).

## How the settings were chosen

The settings were picked the way each walk-forward window picks them: a grid search of the
strategy's 96 configurations, by Sharpe ratio, over the most recent training span, the 84 days
to 25 September 2026, at the US fee of 0.005% per side and 5 basis points of slippage. Going live
is the next window's test span. The search's in-sample figures (a return of 54.3% and a Sharpe
ratio of 5.49 over those 84 days, to the close on 25 September) are what it selected on, not a
forecast. The search as first run left out the window's final hourly bar and scored 54.2% and
5.45; the same configuration wins either way, and the configuration records both. The validated
out-of-sample expectation at this fee is +5.73% per window under Config A and +0.63% under
Config B. Two of the chosen values, the 30-candle lookback and the 19-candle rebalance interval,
are at the edge of their grids, which the configuration records as a warning. A study that
extends the grid past both edges finds the setting a narrow peak that fails all three of its
pre-registered stability tests
([Parameter stability](../concepts/optimization_and_validation.md#parameter-stability)).

## What is measured

Everything is recorded in the bot's session logs, and read back by
`midfreq.research.forward_test` without OpenD:

- **Profit or loss**: the change in the broker's reported account total since the test began.
  The test is the only activity on the US paper account, so the change in the account is the
  test's result, net of fees and including any dividends the broker credits.
- **Trades**: every fill the broker confirmed, with the profit realized on each sale before
  fees, and the candle whose ranking produced it.
- **Holdings**: the positions at the end of the latest session.

The dashboard's **Live** page shows the current figures, beside the broker's own view of the
account ([Example A](../concepts/dashboard.md#example-a-the-live-forward-test)); the hosted
dashboard is private, with access by invitation.

## Results to 16 October 2026

At the close on 16 October 2026, after 15 sessions and four rebalances, as in Section 4.8 of the
final report:

| | |
|---|---|
| The US$100,000 allocation | **US$94,707**, a loss of **5.29%**, net of fees and including a dividend of US$283.86 the broker credited between the sessions of 12 and 13 October |
| An equal-weight index of the same 98 stocks | **+2.03%**, from the first candle of 5 October, the first rebalance's trading day, to the close on 16 October |
| Rebalances | 4, traded on 5, 8, 13 and 15 October: 16 filled orders, US$526,120 traded |
| Fees | US$16.12 for the 16 orders, 0.003% of the value traded |
| Realized profit on sales | −US$2,406.22, before fees |
| Holdings at the close | Applied Materials (74 shares) and Micron (35), and cash |

| Session close | The allocation (US$) | The index (US$) |
|---|---|---|
| 5 October | 99,859 | 100,366 |
| 6 October | 101,396 | 100,622 |
| 7 October | 100,732 | 100,396 |
| 8 October | 102,166 | 100,721 |
| 9 October | 101,581 | 101,019 |
| 12 October | 101,363 | 101,406 |
| 13 October | 103,801 | 101,146 |
| 14 October | 99,409 | 101,169 |
| 15 October | 98,045 | 102,353 |
| 16 October | 94,707 | 102,026 |

- **Execution.** At the rebalances of 13 and 15 October the stocks traded were exactly those the
  strategy's rule selects, and the nine fills averaged 0.2 basis points from the decision
  candle's close, none more than 5.7 away, against the 5 basis points the backtests assume. At
  the first two rebalances the orders were sent when the following candle completed rather than
  at the decision, so they are not compared with the decision price; and at the 8 October
  rebalance the strategy kept Qualcomm where the rule, applied to the complete candles, selects
  Salesforce, because the sessions of 5 to 7 October had not recorded each day's final hourly
  candle (on the candles they had received, the rule selects what was held).
- **An unfilled order.** On 15 October one of the three buys, Lam Research (116 shares), did not
  fill within the session runner's 30-second wait and was cancelled, so a third of the allocation
  stayed in cash until the next rebalance. On the trading node such an order is re-priced up to
  twice before it is cancelled ([Orders left unfilled](../concepts/trading_node.md#orders-left-unfilled)).
- **What it can show.** Ten trading days and four rebalances are less than half of one Config A
  test window. The forward test shows that the validated configuration runs correctly end to end
  against a real broker; it cannot confirm or refute the result.

The figures are computed with `midfreq.research.forward_test` from the session logs, and the
index from the closes those logs recorded. From the first rebalance on, the bot's holdings equal
the broker's at the close of every session.

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
