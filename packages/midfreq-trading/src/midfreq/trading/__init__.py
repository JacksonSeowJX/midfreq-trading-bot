"""
midfreq.trading: the live trading node.

A strategy's orders go to an order management system (OMS) that owns every order's state, after
a pre-trade risk check, and reach a venue only through that venue's connector
(midfreq.data.adapters: ExecutionClient for orders, DataClient streams for market data). Fills
come back as events; positions, cash and P&L are kept per strategy and reconciled with the
broker. Market data arrives through one MarketDataService shared by every strategy, and
instrument rules, trading calendars and corporate actions through ReferenceData.

Pure Python; nothing here imports a venue's SDK. Design: docs/LIVE_ARCHITECTURE.md; the
contracts between the parts: midfreq.trading.interfaces.
"""
