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

    def set_option_quote(self, symbol: str, bid=None, ask=None, last=None) -> None:
        bid_d = Decimal(str(bid)) if bid is not None else None
        ask_d = Decimal(str(ask)) if ask is not None else None
        if bid_d is not None and bid_d <= 0:
            bid_d = None  # zero/negative bid = no bid, matching the provider
        if ask_d is not None and ask_d <= 0:
            ask_d = None
        if bid_d is not None and ask_d is not None:
            price = ((bid_d + ask_d) / 2).quantize(Decimal("0.0001"))
        elif last is not None:
            price = Decimal(str(last))
        else:
            price = ask_d or bid_d or Decimal("0")
        self.quotes[symbol] = Quote(symbol=symbol, price=price, as_of=utcnow(),
                                    bid=bid_d, ask=ask_d)

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
        # Derived from the wall clock so queued day orders never expire under
        # tests that run process_pending against real utcnow() (hardcoded
        # dates rotted once the calendar passed them).
        base = utcnow() + timedelta(days=1)
        self.next_open_at = base.replace(hour=13, minute=30, second=0, microsecond=0)
        self.expiry_at = base.replace(hour=20, minute=0, second=0, microsecond=0)

    def is_open(self, at):
        return self.open

    def is_trading_day(self, d):
        return self.trading_day

    def next_open(self, after):
        return self.next_open_at

    def expiry_time(self, placed_at):
        return self.expiry_at


class FakeOptionsData(FakeMarketData):
    """Fake for the options data service: quotes plus chains/expirations."""

    def __init__(self):
        super().__init__()
        self.expirations: dict[str, list] = {}
        self.chains: dict[tuple, tuple[list, list]] = {}

    def set_expirations(self, underlying: str, dates: list) -> None:
        self.expirations[underlying] = dates

    def set_chain(self, underlying: str, expiry, calls: list, puts: list) -> None:
        self.chains[(underlying, expiry)] = (calls, puts)

    def get_expirations(self, underlying: str) -> list:
        if self.fail:
            raise MarketDataError("provider down")
        if underlying not in self.expirations:
            raise UnknownSymbolError(underlying)
        return self.expirations[underlying]

    def get_chain(self, underlying: str, expiry) -> tuple[list, list]:
        if self.fail:
            raise MarketDataError("provider down")
        if (underlying, expiry) not in self.chains:
            raise UnknownSymbolError(underlying)
        return self.chains[(underlying, expiry)]

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        raise MarketDataError("bars not available for option contracts")


class Clock:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 7, 1, 12, 0)

    def __call__(self) -> datetime:
        return self.now
