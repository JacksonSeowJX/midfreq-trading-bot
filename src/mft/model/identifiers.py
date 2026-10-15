"""
Identifiers, after NautilusTrader's `model.identifiers`.

Venues are named by ISO 10383 Market Identifier Code where one exists (XHKG, XNAS, XNYS, XSES,
XSHG, XSHE, XTKS) and by the exchange's own name for crypto venues, which have no MIC (BINANCE,
COINBASE, KRAKEN, OKX). An instrument is `<symbol>.<venue>`: `00700.XHKG`, `AAPL.XNAS`,
`BTCUSDT.BINANCE`. The symbol is the code the exchange itself lists, not any one data vendor's
spelling of it; each connector translates to its own format (`HK.00700` for Moomoo, `0700.HK` for
yfinance, `700` on SEHK for IBKR).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_VENUE_RE = re.compile(r"^[A-Z0-9_]+$")


@dataclass(frozen=True, slots=True, order=True)
class Identifier:
    value: str

    def __post_init__(self):
        if not isinstance(self.value, str) or not self.value or any(c.isspace() for c in self.value):
            raise ValueError(f"invalid {type(self).__name__}: {self.value!r}")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self.value}')"


@dataclass(frozen=True, slots=True, order=True, repr=False)
class Venue(Identifier):
    """A trading venue: an exchange (by MIC) or a crypto exchange (by name)."""

    def __post_init__(self):
        Identifier.__post_init__(self)
        if not _VENUE_RE.match(self.value):
            raise ValueError(f"venue must be upper-case letters, digits or '_': {self.value!r}")


@dataclass(frozen=True, slots=True, order=True, repr=False)
class Symbol(Identifier):
    """The venue's own code for an instrument: '00700', 'AAPL', 'BRK.B', 'BTCUSDT', 'BTC-USD'."""


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    symbol: Symbol
    venue: Venue

    @classmethod
    def from_str(cls, value: str) -> "InstrumentId":
        """Parse '00700.XHKG'. Splits on the LAST dot, so share classes such as 'BRK.B.XNYS'
        keep their own dot in the symbol."""
        if not isinstance(value, str) or "." not in value:
            raise ValueError(f"instrument id must be '<symbol>.<venue>': {value!r}")
        symbol, venue = value.rsplit(".", 1)
        return cls(Symbol(symbol), Venue(venue))

    @classmethod
    def of(cls, symbol: str, venue: str | Venue) -> "InstrumentId":
        return cls(Symbol(symbol), venue if isinstance(venue, Venue) else Venue(venue))

    @property
    def value(self) -> str:
        return f"{self.symbol.value}.{self.venue.value}"

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"InstrumentId('{self.value}')"


@dataclass(frozen=True, slots=True, order=True, repr=False)
class StrategyId(Identifier):
    """Names one strategy instance, e.g. 'XSReversal-001'."""


@dataclass(frozen=True, slots=True, order=True, repr=False)
class ClientId(Identifier):
    """Names a connector / data client: MOOMOO, IBKR, YFINANCE, BINANCE, CCXT."""

    def __post_init__(self):
        Identifier.__post_init__(self)
        object.__setattr__(self, "value", self.value.upper())


@dataclass(frozen=True, slots=True, order=True, repr=False)
class ClientOrderId(Identifier):
    """Order id assigned by the strategy's OrderFactory."""


@dataclass(frozen=True, slots=True, order=True, repr=False)
class TradeId(Identifier):
    """Fill id assigned by the (simulated) venue."""


@dataclass(frozen=True, slots=True, order=True, repr=False)
class AccountId(Identifier):
    """One cash account, e.g. 'SIM-USD'."""


def as_instrument_id(value: "str | InstrumentId") -> InstrumentId:
    return value if isinstance(value, InstrumentId) else InstrumentId.from_str(value)


def as_venue(value: "str | Venue") -> Venue:
    return value if isinstance(value, Venue) else Venue(value.upper())
