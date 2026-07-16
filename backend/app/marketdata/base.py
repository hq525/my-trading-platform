from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class MarketDataError(Exception):
    pass


class UnknownSymbolError(MarketDataError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    as_of: datetime  # naive UTC
    # Options only (spread-crossing fills); None for stocks/crypto.
    bid: Decimal | None = None
    ask: Decimal | None = None


@dataclass(frozen=True)
class Bar:
    timestamp: datetime  # naive UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class OptionChainRow:
    symbol: str
    strike: Decimal
    right: str  # "call" | "put"
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    open_interest: Decimal | None
    iv: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> Quote: ...

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]: ...
