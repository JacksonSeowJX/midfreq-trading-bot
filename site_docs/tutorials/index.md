# Tutorials

Step-by-step walkthroughs. Every code block on these pages was run as shown, and the output
beneath it is what it printed. The tutorials that backtest need the price cache
([Installation](../getting_started/installation.md#the-price-cache)); their figures are for the
period stated in each, and other dates give other figures.

<div class="grid cards" markdown>

-   :material-chart-box-outline:{ .lg .middle } **[Backtest a strategy](backtest_a_strategy.md)**

    ---

    Load the price cache, backtest a single-stock strategy, read its trades and equity, change
    its costs and risk settings, and backtest a cross-sectional strategy over the S&P 100.

-   :material-calendar-range:{ .lg .middle } **[Walk-forward a strategy](walk_forward.md)**

    ---

    Validate a strategy on data its parameters were not chosen on, read in-sample against
    out-of-sample, apply the two-configuration standard and correct for the search.

-   :material-lightning-bolt-outline:{ .lg .middle } **[Write an event-driven strategy](event_driven_strategy.md)**

    ---

    A strategy that submits orders and handles fill, rejection and denial events, on two venues
    in two currencies, with different fill and slippage models.

-   :material-power-plug-outline:{ .lg .middle } **[Add a connector](add_a_connector.md)**

    ---

    A connector for a new REST source: symbology, paging, rate limits and UTC close times,
    registered for a venue and put through the contract tests. Runs offline.

</div>
