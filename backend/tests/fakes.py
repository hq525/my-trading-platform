from datetime import datetime, timedelta
from decimal import Decimal

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


class FakeMarketData:
    """Stands in for both a provider and the whole MarketDataService."""

    name = "fake"

    def __init__(self):
        self.quotes: dict[str, Quote] = {}
        self.bars: dict[str, list[Bar]] = {}
        self.fail = False

    def set_quote(self, symbol: str, price) -> None:
        self.quotes[symbol] = Quote(symbol=symbol, price=Decimal(str(price)), as_of=utcnow())

    def set_bars(self, symbol: str, closes: list) -> None:
        self.bars[symbol] = [
            Bar(timestamp=datetime(2026, 1, 1) + timedelta(days=i),
                open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)),
                close=Decimal(str(c)), volume=1000)
            for i, c in enumerate(closes)
        ]

    def get_quote(self, symbol: str) -> Quote:
        if self.fail:
            raise MarketDataError("provider down")
        if symbol not in self.quotes:
            raise UnknownSymbolError(symbol)
        return self.quotes[symbol]

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if self.fail:
            raise MarketDataError("provider down")
        if symbol not in self.bars:
            raise UnknownSymbolError(symbol)
        return self.bars[symbol][-limit:]


class FakeCalendar:
    def __init__(self, open_: bool = True, trading_day: bool = True):
        self.open = open_
        self.trading_day = trading_day
        self.next_open_at = datetime(2026, 7, 6, 13, 30)
        self.expiry_at = datetime(2026, 7, 6, 20, 0)

    def is_open(self, at):
        return self.open

    def is_trading_day(self, d):
        return self.trading_day

    def next_open(self, after):
        return self.next_open_at

    def expiry_time(self, placed_at):
        return self.expiry_at


class Clock:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 7, 1, 12, 0)

    def __call__(self) -> datetime:
        return self.now
