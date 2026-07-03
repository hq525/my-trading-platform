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


@dataclass(frozen=True)
class Bar:
    timestamp: datetime  # naive UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> Quote: ...

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]: ...
