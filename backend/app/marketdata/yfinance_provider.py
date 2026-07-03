from __future__ import annotations

from decimal import Decimal

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


class YFinanceData:
    """Keyless fallback provider via yfinance."""

    name = "yfinance"

    def __init__(self, ticker_factory=None):
        if ticker_factory is None:
            import yfinance as yf

            ticker_factory = yf.Ticker
        self._ticker = ticker_factory

    def get_quote(self, symbol: str) -> Quote:
        try:
            price = self._ticker(symbol).fast_info["last_price"]
        except UnknownSymbolError:
            raise
        except Exception as e:
            raise MarketDataError(f"yfinance: {e}") from e
        if price is None:
            raise UnknownSymbolError(symbol)
        return Quote(symbol=symbol, price=Decimal(str(price)), as_of=utcnow())

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        try:
            df = self._ticker(symbol).history(period="2y", interval="1d", auto_adjust=False)
        except Exception as e:
            raise MarketDataError(f"yfinance: {e}") from e
        if df.empty:
            raise UnknownSymbolError(symbol)
        bars = []
        for idx, row in df.tail(limit).iterrows():
            ts = idx.tz_convert("UTC").tz_localize(None) if idx.tzinfo else idx
            bars.append(Bar(
                timestamp=ts.to_pydatetime(),
                open=Decimal(str(row["Open"])), high=Decimal(str(row["High"])),
                low=Decimal(str(row["Low"])), close=Decimal(str(row["Close"])),
                volume=int(row["Volume"]),
            ))
        return bars
