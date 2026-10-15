# Results

The research question was whether any systematic strategy shows a real, tradable edge on hourly
Hong Kong and US equities once it is tested properly. After 13 strategies on 4 stock universes,
the answer reached is that none does: no result survived the correction for the number of
things tried, and the single-stock strategies did not beat holding the same stocks.

| | |
|---|---|
| Strategies | 13: trend following, mean reversion, pairs trading, regime switching, cross-sectional reversal and momentum, an ML direction classifier, and buy-and-hold with a risk overlay |
| Universes | 19 Hong Kong stocks, 15 large US stocks, the S&P 100, and the Hang Seng Index (88 stocks) |
| Standard | walk-forward over 3 years under two configurations, Config A (9 windows) and Config B (15), each 70% training; a result passes only if, under both, its mean out-of-sample return per test window is positive and at least 50% of its test windows are profitable ([Optimization and validation](../concepts/optimization_and_validation.md#the-two-configuration-standard)) |
| Held to the standard | **217** results, from 9 studies in 9 families |
| Passed | **13** (6.0%) |
| Corrected for the search | White's Reality Check over the 8 cross-sectional candidates: **p = 0.094** (Config A) and **p = 0.098** (Config B), above the 0.05 needed |
| Overfitting of the strongest result | Probability of Backtest Overfitting **0.274** over 96 configurations (pure noise gives 0.5) |

## The 13 results that passed

| Results | What they are |
|---|---|
| 7 | *Buy & Hold benchmark*: simply holding one stock (US.MSFT, US.XOM, US.GOOGL, US.JPM, HK.00939, HK.00005, HK.00883). They are the yardstick, not trading strategies. |
| 3 | Cross-sectional reversal on the S&P 100: the study's own row, and two rows of its cost sweep, at 0% and at 0.005% commission, that is, the same strategy at different fees. |
| 1 | *Buy & Hold + Risk Overlay* on US.JPM: holding the stock with a stop-loss and a trailing stop. |
| 2 | *ML Direction Classifier* on US.GOOGL and on HK.00005, with mean test returns between +0.01% and +0.22% per window. |

Leaving aside holding a stock, with or without a risk overlay, the only strategy that passes with
returns clearly above zero is cross-sectional reversal on the S&P 100: +5.88% per test window
under Config A and +0.65% under Config B, with 56% and 67% of windows profitable. Of the
single-stock strategies, 1 of 57 results passed on the Hong Kong stocks, 0 of 45 on the US stocks
and 0 of 24 on the stocks of the new sectors.

## The strongest result

Cross-sectional reversal ranks the S&P 100 by recent return and holds, in equal amounts, the few
stocks with the lowest return, on the expectation that they catch up with the rest.

- **Costs.** It passes at the broker's real US fee of 0.005% per side (+5.73% and +0.63% per
  test window) and at 0%, and fails at 0.03% and at the Hong Kong rate of 0.16%.
- **Overfitting.** Within the strategy, over 96 parameter configurations and 10 subsets of the
  data, the configuration the search picks lands in the bottom half out of sample in 27.4% of the
  252 splits (PBO 0.274).
- **The correction for the search.** Eight cross-sectional candidates were tried, reversal and
  momentum on each of the four universes, over the same three years ending 28 August 2026. White's
  Reality Check resamples their test windows 20,000 times with every candidate's edge removed:

    | | Winner | Mean per test window | p, winner alone | p, corrected for 8 candidates |
    |---|---|---|---|---|
    | Config A (9 windows) | Reversal, S&P 100 | +6.15% | 0.058 | **0.094** |
    | Config B (15 windows) | Reversal, US 15 | +3.84% | 0.015 | **0.098** |

    Both corrected values are above 0.05: the best result cannot be told apart from the luck of
    picking the best of eight.
- **Stability.** The result also passes or fails depending on the week its data ends.

## What the project delivers

No strategy beat chance once the number of things tried was accounted for. What the project
delivers is the framework documented on this site, a validation method that catches results a
single backtest would have reported as real (the strongest result's settings return +56.83% in a
single backtest over the last year; see [Backtest a strategy](../tutorials/backtest_a_strategy.md#6-a-cross-sectional-strategy)),
and a live system checked against a real broker ([The forward test](forward_test.md)).

## On the dashboard

The dashboard's **Overview** page shows every one of the 217 results, the pass rate by family,
the Config A against Config B chart, and the correction for the search; **Research files** opens
each study's raw output. [Example B](../concepts/dashboard.md#example-b-the-13-results-that-pass-and-the-correction-for-the-search)
of the dashboard guide walks through these figures.

!!! info "The hosted dashboard"
    The hosted dashboard, <https://midfreq-dashboard-123943780205.asia-southeast1.run.app>, is
    private: access is by invitation, with a Google sign-in. Every view also runs on a
    [local install](../getting_started/installation.md#the-dashboard), from the committed
    `results/`.

[Reproducing the numbers](reproducing.md) gives the commands that recompute them from the price
cache.
