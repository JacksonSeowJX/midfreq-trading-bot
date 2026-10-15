# Instruments and venues

The venue engine's domain model, in `midfreq.core.model` and `midfreq.core.venues`, follows
NautilusTrader's: a trade happens on a **venue**, in an **instrument**, from **bars** whose
type names their source completely. The candle backtester and the live engine still use the
original model, a moomoo symbol string such as `HK.00700` and a `Candle`; the legacy reader and
the legacy adapter translate between the two ([Backtesting](backtesting.md)).

## Identifiers

| Identifier | Example | Meaning |
|---|---|---|
| `Venue` | `XHKG`, `XNAS`, `BINANCE` | an exchange, by its ISO 10383 Market Identifier Code where one exists, else by name (crypto exchanges have none) |
| `Symbol` | `00700`, `AAPL`, `BRK.B`, `BTCUSDT` | the code the venue itself lists, not any data vendor's spelling of it |
| `InstrumentId` | `00700.XHKG`, `BRK.B.XNYS` | `<symbol>.<venue>`; `InstrumentId.from_str` splits on the last dot, so share classes keep their own |
| `StrategyId`, `ClientOrderId`, `TradeId`, `AccountId`, `ClientId` | `MACrossover-001` | a strategy instance, an order, a fill, a cash account, a connector |

Each connector translates an `InstrumentId` to its own spelling and back: `HK.00700` for moomoo,
`0700.HK` for yfinance, `700` on SEHK for Interactive Brokers
([Integrations](../integrations/index.md)).

## Instruments

An instrument carries the rules an order must satisfy: the currency it settles in, the price
increment, and the smallest tradable quantity. `Equity` has a board lot (100 shares for Tencent,
400 for HSBC, 1 for any US stock); `CurrencyPair`, a crypto spot pair, has a size step on a
fractional quantity, a minimum quantity and a minimum notional value. `make_qty` rounds a
quantity **down** to a tradable amount, so an order never spends more than the strategy asked
for.

A connector's instrument provider loads the real rules from its source (moomoo's market
snapshot gives each Hong Kong stock's board lot; Binance's `exchangeInfo` gives each pair's
filters). Where a source has none, the venue's defaults apply: `VenueSpec.default_instrument`.

## Bars and other market data

A `BarType` names a stream of bars completely, so it serves as a catalog key and a message-bus
topic. `00700.XHKG-1-HOUR-LAST-EXTERNAL` is hourly bars of Tencent on HKEX, built from
last-trade prices by the data vendor (`EXTERNAL`) rather than by the framework. Its
`BarSpecification` is the step, the aggregation and the price type (`1-HOUR-LAST`); the price
cache's `1h` and `1d` files are the same intervals as `1-HOUR` and `1-DAY`.

A `Bar` has `open`, `high`, `low`, `close`, `volume` and two timestamps, both integers of
nanoseconds since the epoch in UTC: `ts_event`, the time the bar **closed**, and `ts_init`, the
time the object was created (equal to `ts_event` for historical data). The engine also accepts
`QuoteTick` (best bid and ask with sizes), `TradeTick` (a print with its aggressor side) and
`OrderBookDepth` (L2 snapshots of price levels).

```python
from midfreq.core.model import BarSpecification, BarType, InstrumentId
from midfreq.core.venues import default_venues

iid = InstrumentId.from_str("BRK.B.XNYS")          # splits on the last dot
print(repr(iid.symbol), repr(iid.venue))

bar_type = BarType.from_str("00700.XHKG-1-HOUR-LAST-EXTERNAL")
print(bar_type.instrument_id, bar_type.spec.interval, bar_type.spec.timedelta)
print(BarSpecification.from_legacy("1d"))            # the price cache's '1d' files

xhkg = default_venues().get("XHKG")
tencent = xhkg.default_instrument("00700")           # the venue's default trading rules
print(tencent.currency, tencent.lot_size, tencent.price_increment, tencent.make_qty(250))
print(xhkg.fee_model(), [(str(s.open), str(s.close)) for s in xhkg.sessions])
```

```text
Symbol('BRK.B') Venue('XNYS')
00700.XHKG 1-HOUR 1:00:00
1-DAY-LAST
HKD 100.0 0.001 200.0
PercentFeeModel(rate_per_side=0.0016) [('09:30:00', '12:00:00'), ('13:00:00', '16:00:00')]
```

<!-- include: docs/ARCHITECTURE.md#venues-configvenuesjson -->

The configured venues:

<!-- venues-table -->

<!-- include: docs/ARCHITECTURE.md#time -->
