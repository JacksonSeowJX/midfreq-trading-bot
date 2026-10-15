# Concepts

These pages explain how the framework is organized and why: its packages and their layers,
the data model it trades on, the two engines that run strategies over history, the method used
to tell a real result from a lucky one, and the live system. For step-by-step code, see the
[Tutorials](../tutorials/index.md); for every class and function, the
[API reference](../api/index.md).

## Foundations

<div class="grid cards" markdown>

-   **[Architecture](architecture.md)**

    ---

    The six packages and the import layers between them, the research data flow, and the
    venue-based engine and how it maps to NautilusTrader.

</div>

## Data and the domain model

<div class="grid cards" markdown>

-   **[Data and connectors](data.md)**

    ---

    Connectors and their shared contract, the Parquet price cache, the data catalog, and the
    read-only account clients.

-   **[Instruments and venues](instruments_and_venues.md)**

    ---

    Identifiers, instruments and bar types; each venue's timezone, sessions, lot rule and fees;
    and how every bar is stamped with its close in true UTC.

</div>

## Trading logic and research

<div class="grid cards" markdown>

-   **[Strategies](strategies.md)**

    ---

    The candle strategy API and its registry of 13 strategies, against the event-driven
    Strategy API of orders, events and signals.

-   **[Backtesting](backtesting.md)**

    ---

    The candle backtester, the venue engine, fill and slippage models, and each market's
    trading costs.

-   **[Optimization and validation](optimization_and_validation.md)**

    ---

    Grid search, walk-forward, the two-configuration standard, White's Reality Check and the
    Probability of Backtest Overfitting.

-   **[Saved runs](saved_runs.md)**

    ---

    The run store: every backtest, comparison and optimization kept with what is needed to
    read it back and reproduce it.

</div>

## Operation

<div class="grid cards" markdown>

-   **[Live trading](live_trading.md)**

    ---

    The live engine and order gateway, session logs and state, broker snapshots, and the
    reconciliation of the bot's record against the broker's.

-   **[The dashboard](dashboard.md)**

    ---

    Every view of the web dashboard, and five worked examples that reproduce the project's
    main findings.

</div>
