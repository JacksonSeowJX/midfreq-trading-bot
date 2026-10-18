"""
midfreq.trading: the live trading node.

A strategy's orders go to an order management system (OMS) that owns every order's state, after
a pre-trade risk check, and reach a venue only through that venue's connector
(midfreq.data.adapters: ExecutionClient for orders, DataClient streams for market data). Fills
come back as events; positions, cash and P&L are kept per strategy and reconciled with the
broker. Market data arrives through one MarketDataService shared by every strategy, and
instrument rules, trading calendars and corporate actions through ReferenceData.

Pure Python; nothing here imports a venue's SDK. Design: docs/LIVE_ARCHITECTURE.md. One folder per
domain, each importing only the layers below it (the root pyproject.toml's import-linter contract):

    cli          the midfreq-node command
    replay       recorded sessions replayed through the node
    node         the kernel: event loop, start-up and shutdown, default components
    strategy     the strategy host: candle strategies unchanged, native ones
    execution    the OMS, unfilled-order policy, reconciliation, journal, shadow execution
    risk         pre-trade checks and the kill switch
    portfolio    positions, cash and P&L per strategy
    data         market data: subscriptions, bar finality, cross-sections, stalls, the store
    refdata      instruments, trading calendars, corporate actions
    config       the node's YAML configuration
    persistence  state files and session logs, in the session runner's formats
    testing      the simulated venue
    ops          logging, alerts, health
    core         the contracts between the parts (protocols and events)
"""
