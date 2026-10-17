# Dashboard guide

The dashboard is the front end to this project, *Quantitative Trading Bot for Mid-Frequency
Stock Trading*. It shows the bot's live paper trading, every research result held to the
project's validation standard, and runs backtests on request. This guide walks through each
page step by step and then reproduces the project's main findings.

The hosted dashboard is at <https://midfreq-dashboard-123943780205.asia-southeast1.run.app>.
This guide is the dashboard's **Guide** page and also `docs/DASHBOARD_GUIDE.md` in the
repository; both show the same file. Links marked **Try it** open a page of the dashboard with
an example's settings filled in. They work in the dashboard; on GitHub they lead nowhere, so
open the page named in the link from the dashboard's left-hand menu instead.

## A three-minute tour

1. **Live** opens first. The orange panel, *S&P 100 forward test*, is the strategy trading now
   on paper: its value, profit or loss, every trade and what it holds. The blue panel,
   *Paper account*, is the broker's own view of the account. See
   [Example A](#example-a-the-live-forward-test).
2. Open **Overview** in the left-hand menu. The figures at the top say that 217 results were
   held to the validation standard and 13 passed. Scroll down to *Corrected for the search*:
   the verdict reads *Does not pass*. See
   [Example B](#example-b-the-13-results-that-pass-and-the-correction-for-the-search).
3. **Try it:** [Backtest, with the live forward test's settings](/backtest?preset=forward-test).
   Press **Run backtest**. A few seconds later the result shows a return of +56.83% over the
   year to 25 September 2026. [Example C](#example-c-the-live-settings-in-a-backtest-and-the-cost-of-fees)
   explains why that figure is not evidence of an edge.

## What the dashboard is

A web app (`apps/web`) over a read-only API (`apps/api`). It shows:

- the live paper trading, from two independent sources (below), on **Live**;
- every research result against the validation standard, and the correction for the number of
  things tried, on **Overview**, with the raw result files on **Research files**;
- backtests, comparisons and optimizations run on request, with the same backtester and price
  data as the studies, on **Backtest**, **Compare** and **Optimize**.

The dashboard only reads data: it never talks to the trading bot and cannot place an order.

### Two live sources, kept apart

| | Broker account (blue) | Bot record (orange) |
|---|---|---|
| What it is | The account as the broker (moomoo) reports it: balance, positions and orders | What the bot wrote in its session logs: each session, each fill, and the account totals it read at the start and end |
| On the hosted site | A snapshot the trading VM publishes every 5 minutes, badged *Snapshot from the VM* with its age | The session logs, which the VM uploads every 2 minutes |
| On a local install | Read live through the broker's gateway, moomoo OpenD, when it is running | The `live_sessions/` folder |

The two are deliberately not merged, and each panel is labelled with its source, so that one
can be checked against the other: a fill in the bot's record should appear among the broker's
orders, and the bot's holdings among the broker's positions. A difference would point to a
fault in the bot's record keeping. Only the VM can reach the broker's gateway, which is why the
hosted site shows the broker account as a snapshot rather than live.

### The layout

- **Left-hand menu**: the pages, in four groups (Monitor, Research, Run, Help). On a narrow
  window it shrinks to icons; hovering over one shows its name.
- **Top bar**: the page's name; the market switch, **US** or **HK**, which sets the market for
  Live and for the run forms (currency, symbols, universes and default commission); when the
  data on screen was last fetched, with a button that fetches it again; the API's status; and
  the theme (light, dark, or as the system).
- Tables sort when a column header is clicked. Profit and loss is green or red and always
  carries its sign as well.

## Live

What it shows, from the top:

1. **The header**: *Live trading · United States (USD)* (or Hong Kong), and *Auto-refresh every
   60 s*. The page fetches again every 60 seconds and when the browser tab regains focus.
2. **Paper account** (blue, the broker). If more than one account is configured, a selector
   comes first. Then *Total assets*, *Cash*, *Market value* and *Open positions*, the open
   positions table, and *Recent orders (14 days)*. On the hosted site the badge *Snapshot from
   the VM* gives the snapshot's time and age, and a warning appears once it is more than 15
   minutes old. Before the first snapshot arrives the panel says *No broker snapshot yet*
   ([why](#why-does-the-broker-panel-show-a-snapshot-or-nothing-at-all)).
3. **S&P 100 forward test** (orange, the bot record): Cross-Sectional Reversal trading 98 S&P
   100 stocks with US$100,000 of the paper account, which holds about US$1,000,000 in all.
   - *Value now*, *Profit / loss* and *P&L %* are for the US$100,000. Profit / loss is the
     change in the broker's reported account total since the test began, so it is after fees
     and includes any dividends.
   - *Banked from sales* is the profit on positions already sold, before fees; the rest of the
     profit or loss is in the stocks still held.
   - *Sessions* counts the sessions since 28 September 2026 (one is started each US trading
     day), *Rebalances* how many times the strategy has re-ranked the stocks and traded, and
     *Orders filled* the fills, the first on 5 October 2026.
   - Below: the value over time against the US$100,000, then two tabs. **Trades** lists every
     filled order, newest first; *Decided on candle* is the hourly candle whose ranking
     produced it, and sales show their profit. **Holdings at the last recorded candle** lists
     what it holds.
4. **Session history**: every session the bot ran in the chosen market, newest first, with its
   length, strategy, number of symbols, candles, trades, the account's end assets and its
   status (*Crashed* when its console log records an error). Clicking a row opens the
   session's detail: the account total at its start and end, its fills and the events it
   logged. Beneath the table, *Account equity over time* is drawn from the session totals.

Switching the market to **HK** shows the earlier Hong Kong sessions, the single-stock
strategies that ran from July to September 2026. There is no Hong Kong forward test, and the
panel says so.

## Overview

1. Read the four figures at the top: *Results tested* 217, *Passed the standard* 13 (6.0% of
   results), *Studies* 9 and *Strategy families* 9. The note beneath states the standard (see
   [the glossary](#glossary)).
2. In the **Results** card, narrow the rows with the search box, the switch *Show only results
   that passed*, or the family buttons. Three tabs show them:
   - **Full table**: one row per result. *Result* (Pass or Fail), *Family*, *Strategy*,
     *Run on* (a stock, a universe or a fee level), then for each configuration the mean
     out-of-sample return per test window (*A OOS / window*, *B OOS / window*) and the share of
     windows that were profitable (*A windows +*, *B windows +*), *Worst config* (the lower
     of the two mean returns) and *File*, the results file the row comes from. Passes are
     highlighted. **CSV** downloads the rows shown.
   - **Config A vs Config B**: each result as a point, its Config A mean return across and its
     Config B mean return up. Only the top-right quadrant can pass. Hovering over a point
     gives its details.
   - **Pass rate by family**: how many results each family had tested and passed.
3. Scroll to **Corrected for the search**: White's Reality Check for each configuration
   (*By luck · 9 windows*, *By luck · 15 windows*), the 5% needed to pass, the verdict, and
   the Probability of Backtest Overfitting of the S&P 100 reversal (*PBO · S&P 100*). One
   chart per configuration shows each candidate's mean test return, the winner in green, and
   a dotted red line where luck alone reaches 5% of the time.
4. The header's switch *Include superseded runs (pre-audit studies)* adds the studies run
   before the testing code was audited. They are kept for the record and should not be
   relied on.

## Research files

1. The list on the left has one entry per study (its newest file), the headline and current
   studies first. Type in the filter box to narrow it. The header's switch *Show all raw result
   files (including early exploration runs)* lists every file instead.
2. Click an entry. A CSV file opens as a sortable table, with a *Quick view* chart where one is
   obvious; a JSON file opens as a tree whose branches fold.
3. The page's address names the open file (`/research?file=...`), so it can be linked to.

**Try it:** [Research files, the cost sweep of the S&P 100 result](/research?file=cross_sectional_cost_sensitivity_20260922_231346.csv)
and [Research files, the Reality Check](/research?file=reality_check_20260929_050842.json).

## Backtest

One strategy over one period, run by the API on the cached prices. The form is on the left
and the result on the right.

1. Choose the market in the top bar.
2. **Strategy**: pick one. Its parameters appear with their allowed range and step (for
   example *5–30, step 5*).
3. **Data**: *Symbol* (type to search; only symbols with cached prices are offered). The
   cross-sectional strategies rank the stocks of a whole universe against each other, so they
   ask for a *Universe* instead of a symbol and trade all of it; the universe must hold more
   stocks than the *Basket Size*, or there is nothing to rank. *Timeframe* is 1h or 1d (every
   study uses 1h). Then *Start* and *End*.
4. **Money**: *Starting capital*; *Commission per side (%)*, which defaults to the market's
   calibrated rate (US 0.005%, HK 0.160%; the note beneath says what it covers, and *Reset to*
   restores it); and *Slippage (bps)*.
5. **Risk & sizing** is folded; its summary line shows the settings in force. It holds the
   stop-loss, trailing stop and take-profit (all off by default), the *Max drawdown halt* (10%
   by default, measured from the starting capital) and the sizing method (100 shares a trade
   by default; the cross-sectional strategies always split the money equally across the stocks
   held).
6. Press **Run backtest**. A progress bar shows while it runs. The page can be left; the run
   carries on.
7. Read the result:
   - *Return*, *Final value*, *Sharpe ratio*, *Max drawdown*, *Win rate*, *Trades*, *Profit
     factor*, and *Buy and hold*: the same stocks bought in equal amounts at the start and
     held, with *alpha*, the return minus buy and hold.
   - The price as candles, with the strategy's indicators (over the price, or in a pane
     beneath it) and its buys and sells marked.
   - The equity curve against the starting capital, and every trade, newest first.
   - For a cross-sectional strategy, which trades no one stock, the price is replaced by its
     equity against holding every stock of the universe in equal amounts (dashed): *Buy and
     hold* over time, so the gap between the two lines is the alpha.

The run's id goes into the address (`/backtest?job=...`), so a result survives a reload and can
be linked to while the server keeps it. *Recent runs*, below the result, reopens earlier
results. The form remembers its last inputs in this browser.

## Compare

1. Under **Strategies**, choose what to *Compare on*: *One stock* lists the strategies that
   trade a single stock (and Pairs Trading, marked *needs a pair*); *A universe* lists the
   cross-sectional strategies, which rank the stocks of a universe against each other. Tick
   two to eight. *Params*, beside a ticked strategy, opens its parameters; otherwise each runs
   with its defaults.
2. Set the data, money and risk as for a backtest: a *Symbol* on one stock (and a *Pair
   symbol* for Pairs Trading), a *Universe* on a universe. Every strategy runs on the same.
3. Press **Compare strategies**.
4. The *Metrics* table gives each strategy's *Return*, *Alpha*, *Sharpe*, *Max DD*, *Win rate*,
   *Trades* and *Final value*, with the best of each column outlined (for drawdown, the
   lowest). Below it the equity curves are overlaid, each strategy keeping its colour, with
   buy and hold dashed: the stock, or every stock of the universe in equal amounts.

A comparison is on one stock or on one universe, never both. The two kinds of strategy trade
different stocks, size their positions differently and are measured against different buy
and hold, so their rows would not be like-for-like. Each side keeps its own ticks.

## Optimize

Optimize searches a strategy's parameters, in one of two ways:

- **Grid search** runs every combination over one period and ranks them by the chosen
  objective, with a heatmap when two parameters vary. It scores the best on the same data it
  was picked from, so it overstates what to expect.
- **Walk-forward** is the validation method used throughout the project. It cuts the period
  into consecutive windows, picks the parameters on the first part of each (the training span)
  and scores them on the rest (the test span), which they have not seen.

Optimize is not available on the hosted site: its page says so and gives the commands for a
local install ([why](#why-is-optimize-not-available-on-the-hosted-site); see
[Running the dashboard locally](#running-the-dashboard-locally)). On a local install:

1. Pick a strategy and its data, as for a backtest. The note under the strategy gives the
   number of combinations in its full grid.
2. Under **Optimization**, choose the *Method* and the *Objective* (Sharpe ratio, total return,
   profit factor or max drawdown). For walk-forward, set *Rolling windows* and *Train share
   (%)*; for grid search, *Max combinations*.
3. Press **Run walk-forward** (or **Run grid search**). A walk-forward on a large universe takes
   several minutes; the page can be left while it runs.
4. A walk-forward's result gives *Mean test return* (out of sample), *Mean train return* (in
   sample, for contrast), *Windows positive* and *WF efficiency* (the test return per day as a
   share of the train return per day; below about 50% is a warning). The *Windows* table gives
   each window's dates, chosen parameters, and train and test returns, and a chart shows the
   test return of each window.

## Reproducing the main findings

Five examples, each with its steps and the figures it should show. The backtests use the last
full year of the price cache, 25 September 2025 to 25 September 2026, which the **Try it**
links fill in; with other dates the figures differ. A link fills in the form only: the run
starts when its button is pressed.

### Example A: the live forward test

The finding: the strategy the research selected trades on paper, unattended, and the bot's
record of it agrees with the broker's.

1. Open [Live](/live) with the market set to **US**.
2. In *S&P 100 forward test*, read *Value now* and *Profit / loss*: the result so far on the
   US$100,000, after fees. It is the project's only result on data that did not exist when the
   strategy and its settings were chosen. The figures change with each session, so they are
   not quoted here.
3. Open the **Trades** tab. At each rebalance, every 19 hourly candles, the strategy ranks the
   stocks by their return over the last 30 candles and holds the 3 with the lowest return,
   selling any that have left that basket and buying any that have entered it. The orders
   therefore come in groups, one per rebalance, which share a *Decided on candle*.
4. Check the record against the broker. In *Paper account* (the US account), compare *Open
   positions* with the **Holdings at the last recorded candle** tab, and *Recent orders
   (14 days)* with the **Trades** tab: the same stocks, sides, share counts and prices. The
   broker's *Total assets* is the whole paper account, not the forward test's US$100,000. On
   the hosted site the broker figures are a snapshot, with its age beside it; while the panel
   says *No broker snapshot yet*, this step has to wait for the first one.
5. Under *Session history*, click a session to see the account totals the bot read at its
   start and end, and its fills.

### Example B: the 13 results that pass, and the correction for the search

1. Open [Overview](/overview). The figures read 217 results tested and 13 passed (6.0% of
   results).
2. Turn on *Show only results that passed*. Thirteen rows remain:

   | Rows | What they are |
   |---|---|
   | 7 | *Buy & Hold benchmark*: simply holding one stock (US.MSFT, US.XOM, US.GOOGL, US.JPM, HK.00939, HK.00005, HK.00883). They are the yardstick, not trading strategies. |
   | 3 | Cross-sectional reversal on the S&P 100: the study's own row, and two rows of its cost sweep (at 0% and 0.005% commission), that is, the same strategy at different fees. |
   | 1 | *Buy & Hold + Risk Overlay* on US.JPM: holding the stock with a stop-loss and a trailing stop. |
   | 2 | *ML Direction Classifier* on US.GOOGL and on HK.00005, with mean test returns between +0.01% and +0.22% per window. |

   Leaving aside holding a stock, with or without a risk overlay, the only strategy that passes
   with returns clearly above zero is cross-sectional reversal on the S&P 100: +5.88% per test
   window under Config A and +0.65% under Config B, with 56% and 67% of windows profitable;
   at the broker's real fee of 0.005% per side, +5.73% and +0.63%.
3. Turn the switch off and open **Pass rate by family**: *Single-Stock Strategies* passed 1 of
   57, *Single-Stock Strategies (US)* 0 of 45 and *Single-Stock Strategies (new sectors)* 0 of 24.
4. Type `cost sweep` in the search box. The S&P 100 result passes at 0% and 0.005%
   commission, and fails at 0.03% and at 0.16%, the Hong Kong rate.
5. Scroll to **Corrected for the search**. Eight cross-sectional combinations were tried,
   reversal and momentum on each of 4 universes over the same 3 years ending 28 August 2026,
   and the best of eight can look good by luck alone. White's Reality Check removes every
   candidate's edge (it centres each one's test returns on zero), redraws the test windows
   20,000 times, and counts how often the best of the eight does as well as the real winner:
   - under Config A (9 windows) the winner, *Reversal, S&P 100* at +6.15% per window, is
     matched or beaten by luck in 9.4% of the redraws (5.8% had it been tested alone);
   - under Config B (15 windows) the winner, *Reversal, US 15* at +3.84%, in 9.8% (1.5% alone).

   Both are above the 5% needed, so the verdict reads *Does not pass*: the best result cannot be
   told apart from the luck of picking the best of eight.
6. *PBO · S&P 100* reads 0.274 over 96 configurations: within the S&P 100 reversal, the
   parameter set the search picks as best lands in the bottom half on unseen data in about a
   quarter of the splits tried. Pure noise would give 0.5.

### Example C: the live settings in a backtest, and the cost of fees

1. **Try it:** [Backtest, with the live forward test's settings](/backtest?preset=forward-test).
   The form fills in Cross-Sectional Reversal with *Lookback (candles)* 30, *Basket Size* 3 and
   *Rebalance Every N Candles* 19, the universe *US — S&P 100*, hourly candles from
   25 September 2025 to 25 September 2026, US$100,000, commission 0.005% per side and no
   slippage. A note above the form says what was filled in.
2. Press **Run backtest**. It trades the 100 S&P 100 stocks with cached prices (the live test
   trades 98 of them). There is no one stock's price to chart, so the chart is of the equity
   against holding all 100 in equal amounts (dashed).
3. The result: *Return* +56.83%, *Final value* USD 156,828, *Sharpe ratio* 1.44, *Max
   drawdown* 20.50%, *Win rate* 57.0%, 449 trades, *Profit factor* 1.50, and *Buy and hold*
   +24.86%, so alpha +31.97%. The dashed line ends at that +24.86%.
4. **Try it:** [Backtest, the same at the Hong Kong commission of 0.16%](/backtest?preset=forward-test-hk-fees),
   or change *Commission per side (%)* from 0.005 to 0.160 by hand, and run it again.
5. The same 449 trades now return +24.82% (USD 124,819), with a Sharpe ratio of 0.79 and a
   max drawdown of 21.83%, against the same buy and hold of +24.86%: alpha -0.04%. The higher
   fee takes 32 percentage points of the return, all of the margin over simply holding the
   stocks.

A single backtest over one period is not evidence of an edge. The live settings were chosen by
a grid search over the last few months of this same year (the choice is recorded in
`config/sp100_forward_test.json`), so this backtest partly scores them on the data they were
picked from. [Example E](#example-e-a-walk-forward-validation) shows the size of that effect, and
[Example B](#example-b-the-13-results-that-pass-and-the-correction-for-the-search) the result on
unseen data: +5.73% and +0.63% per test window at this fee, which the Reality Check cannot tell
apart from luck. The cost effect is the same there: the cost sweep fails at 0.16%.

### Example D: three indicator strategies on one stock

1. **Try it:** [Compare, with RSI, MACD and Bollinger Bands on US.AAPL](/compare?preset=oscillators).
   The form fills in the three strategies with their default parameters, compared on one
   stock, US.AAPL, over the same year at US costs.
2. Press **Compare strategies**.
3. The *Metrics* table:

   | Strategy | Return | Alpha | Sharpe | Max DD | Win rate | Trades | Final value |
   |---|---|---|---|---|---|---|---|
   | RSI | +12.32% | -22.45% | 3.09 | 2.56% | 73.9% | 47 | USD 112,322 |
   | MACD | +9.61% | -25.17% | 2.14 | 2.08% | 45.5% | 111 | USD 109,607 |
   | Bollinger Bands | +0.49% | -34.28% | 0.12 | 4.62% | 60.0% | 40 | USD 100,494 |

4. All three made money and two have high Sharpe ratios, yet every alpha is negative: holding
   US.AAPL over the same year returned +34.78% (the return minus the alpha; a backtest of any
   of the three shows it as *Buy and hold*, and the dashed *Buy and hold US.AAPL* line under
   the table ends there). With the default sizing of 100 shares a trade,
   much of the capital stays in cash, which also keeps the drawdowns small. Out of sample the
   single-stock strategies fare no better: on Overview, 1 of the 126 results in the three
   single-stock families passed.

### Example E: a walk-forward validation

This needs a local install ([Running the dashboard locally](#running-the-dashboard-locally)).

1. **Try it:** [Optimize, with a walk-forward of Cross-Sectional Reversal on the US 15](/optimize?preset=walk-forward).
   The form fills in Cross-Sectional Reversal on the universe *US — 15 large caps*, the same
   year at US costs, the method *Walk-forward*, the objective Sharpe ratio, 3 rolling windows
   and a 60% training share. The full grid has 96 combinations.
2. Press **Run walk-forward**. It took about 30 seconds on a laptop when this guide was written.
3. The result: *Mean test return* +3.45%, *Mean train return* +17.66%, *Windows positive* 100%,
   *WF efficiency* 29%. The *Windows* table gives test returns of +0.49%, +7.79% and +2.06%,
   against train returns of +24.13%, +13.15% and +15.71%, and the chosen parameters differ
   between windows (lookback 10, basket 2, rebalance every 7 in the first and third; lookback
   25, basket 2, rebalance every 1 in the second).
4. The parameters a search picks earn far less on the span that follows than on the span they
   were picked on. The test spans are shorter than the training spans, so *WF efficiency*
   compares the two per day: 29%, below the 50% the page treats as a warning. That gap is why
   the project judges a strategy only by its test returns, and why the +56.83% of
   [Example C](#example-c-the-live-settings-in-a-backtest-and-the-cost-of-fees) is not evidence.

The project's standard runs the same method over 3 years, twice: Config A with 9 windows and
Config B with 15, each with a 70% training share. Setting the dates, *Rolling windows* and
*Train share (%)* that way shows the method at full size, but the figures on Overview come from
the study scripts in `research/studies/`, with their own end dates and costs; the root
`README.md` lists the commands that reproduce them.

## Running the dashboard locally

This needs a copy of the repository, [uv](https://docs.astral.sh/uv/), Node 22.12 or later and,
for runs, the local price cache (`data/`, which is not in the repository; the scripts in
`scripts/data/` download it through moomoo OpenD). From the repository root, as the root
`README.md` describes:

```bash
uv sync                                            # once: the Python packages
(cd apps/web && npm ci && npm run build)           # once, and after changes to the web app
uv run uvicorn midfreq.api.main:app --port 8000    # then open http://127.0.0.1:8000
```

Everything works as on the hosted site, and Optimize is available. The broker panel reads the
accounts live through OpenD when it is running and logged in; otherwise it says why an account
is unavailable, and the bot record still loads from `live_sessions/`
(`./ops/fetch_live_logs.sh` copies the latest logs from the VM). For development,
`npm run dev` in `apps/web` serves the app with hot reload at http://localhost:5173 beside the
API (see `apps/web/README.md`).

## Glossary

| Term | Meaning |
|---|---|
| Out-of-sample | Data a strategy's parameters were not chosen on: in a walk-forward, the test span of each window. Only out-of-sample returns count as evidence. |
| In-sample | The data the parameters were chosen on (the training span). Returns there overstate what to expect. |
| Walk-forward | The validation method: the period is cut into consecutive windows; in each, a grid search picks the parameters on the first part (training) and they are scored on the rest (test). |
| Config A, Config B | The project's two walk-forward splits of the same 3 years: 9 windows (A) and 15 windows (B), each 70% training and 30% test. |
| Dual-configuration standard | A result passes only if, under both Config A and Config B, its mean out-of-sample return per test window is positive and at least 50% of its test windows are profitable. Requiring both guards against a result that depends on where the windows happen to fall. Also called the two-configuration standard. |
| Consistency | The share of test windows with a positive return: *A windows +* and *B windows +* on Overview, *Windows positive* on Optimize. |
| Commission per side | The fee on each buy and on each sell, as a percentage of the trade's value: 0.005% in the US (moomoo Singapore's flat US$0.99 plus GST per order, at these position sizes) and 0.160% in Hong Kong (commission, platform fee, 0.1% stamp duty and levies). |
| Slippage (bps) | An allowance for trading at a worse price than the candle's: buys pay more and sells receive less, by this many basis points (1 bps = 0.01%). |
| Sharpe ratio | The mean daily return divided by its standard deviation, annualized over 252 trading days, with no risk-free rate. It measures return per unit of volatility; higher is better. |
| Max drawdown | The largest fall in equity from an earlier peak, as a percentage of that peak. |
| Drawdown halt | A circuit breaker (*Max drawdown halt*, 10% by default). While equity is that far below the starting capital, the strategy acts on no signals; stops still apply, and it resumes if equity recovers. It is measured from the starting capital, not from the peak, so a backtest's max drawdown can exceed it, as in Example C. |
| Rebalance | For the cross-sectional strategies, re-ranking the universe and trading to hold the new basket. *Rebalance Every N Candles* sets how often; the forward test rebalances every 19 hourly candles. |
| Cross-sectional reversal | Ranks every stock in a universe by its return over the lookback and holds, in equal amounts, the few with the lowest return (the basket), on the expectation that they catch up with the rest. |
| Universe | The stocks a cross-sectional strategy ranks: *HK — live roster (19)*, *HK — Hang Seng Index (88)*, *US — 15 large caps (15)* and *US — S&P 100 (100)*. |
| Buy and hold, alpha | Buy and hold is the return from buying the same stocks in equal amounts at the start and holding them to the end. Alpha is the strategy's return minus it. |
| White's Reality Check | A test of a selection: given that the best of several candidates was picked, how often would the best of the same number of candidates with no edge do as well? That share is the p-value; under 5% is needed to call the winner real. |
| PBO | Probability of Backtest Overfitting: how often the parameter set a search picks as best on part of the data lands in the bottom half on the rest. Pure noise gives 0.5; lower is better. |

## Frequently asked questions

### Why does the broker panel show a snapshot, or nothing at all?

Reading a broker account needs the broker's gateway, moomoo OpenD, which runs only on the
trading VM. The VM therefore publishes a read-only snapshot of each paper account every 5
minutes, and the hosted dashboard shows that, badged *Snapshot from the VM* with its time and
age. It warns when the snapshot is more than 15 minutes old, which means the VM is off or
outside its schedule. Until the first snapshot has arrived, the panel says *No broker snapshot
yet*. The bot record does not depend on it. On a local install the panel reads the account
live when OpenD is running.

### Why does a backtest's chart end before the end date I chose?

Runs use the price cache, which holds the candles downloaded so far, and a run covers only the
part of the chosen period the cache holds. When this guide was written the cache ended on
25 September 2026. The form's default end date is today, so by default a chart ends at the
last cached candle. The **Try it** links use 25 September 2025 to 25 September 2026.

### Why is Optimize not available on the hosted site?

A grid search or a walk-forward runs hundreds of backtests (the cross-sectional strategies'
full grid alone has 96 combinations, searched again in every window), and the hosted dashboard
runs one job at a time for all its visitors. Its Optimize page says so and gives the commands
for a local install, where it works as described [above](#optimize).

### How fresh is the data?

| What | Comes from | Updated |
|---|---|---|
| Bot record, on Live | The bot's session logs | Hosted: uploaded by the VM every 2 minutes. Local: when `./ops/fetch_live_logs.sh` is run. The page fetches again every 60 s. |
| Broker account, on Live | Hosted: the VM's snapshot. Local: OpenD | Hosted: every 5 minutes. Local: read live. |
| Overview and Research files | The study output in `results/` | Fixed: deployed with the dashboard. |
| Backtest, Compare and Optimize | The price cache | Ended on 25 September 2026 when this guide was written. |

The top bar shows when the data on screen was fetched, and its button fetches everything on the
page again.

### Why did my run not start, or where has a result gone?

The hosted dashboard runs one job at a time. If another is running, possibly another visitor's,
the page says so and links to it; try again when it has finished. Runs are kept in the server's
memory, the most recent 50, so a restart of the server clears them; start the run again from the
form.
