from decimal import Decimal

import httpx
import pandas as pd
import pytest

from app.marketdata.alpaca import AlpacaData
from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.yfinance_provider import YFinanceData


def alpaca_with(handler):
    return AlpacaData("key", "secret", transport=httpx.MockTransport(handler))


def test_alpaca_quote_parses_price_and_time():
    def handler(request):
        assert request.url.path == "/v2/stocks/AAPL/trades/latest"
        assert request.headers["APCA-API-KEY-ID"] == "key"
        return httpx.Response(200, json={
            "symbol": "AAPL",
            "trade": {"p": 189.34, "t": "2026-07-02T19:59:59.123456789Z"},
        })

    q = alpaca_with(handler).get_quote("AAPL")
    assert q.price == Decimal("189.34")
    assert q.as_of.year == 2026 and q.as_of.tzinfo is None


def test_alpaca_unknown_symbol():
    def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    with pytest.raises(UnknownSymbolError):
        alpaca_with(handler).get_quote("XXXX")


def test_alpaca_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        alpaca_with(handler).get_quote("AAPL")


def test_alpaca_bars_parse():
    def handler(request):
        assert request.url.path == "/v2/stocks/SPY/bars"
        return httpx.Response(200, json={"bars": [
            {"t": "2026-06-30T04:00:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100},
            {"t": "2026-07-01T04:00:00Z", "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 200},
        ]})

    bars = alpaca_with(handler).get_bars("SPY", "1D", 2)
    assert bars[-1].close == Decimal("2.0")
    assert bars[-1].volume == 200


class StubQuoteTicker:
    def __init__(self, price):
        self.fast_info = {"last_price": price}


class StubBarsTicker:
    def history(self, period, interval, auto_adjust):
        idx = pd.date_range("2026-06-01", periods=3, freq="B", tz="America/New_York")
        return pd.DataFrame(
            {"Open": [1.0, 2.0, 3.0], "High": [1.1, 2.1, 3.1],
             "Low": [0.9, 1.9, 2.9], "Close": [1.5, 2.5, 3.5],
             "Volume": [100, 200, 300]},
            index=idx,
        )


def test_yfinance_quote():
    provider = YFinanceData(ticker_factory=lambda s: StubQuoteTicker(123.45))
    assert provider.get_quote("SPY").price == Decimal("123.45")


def test_yfinance_missing_price_is_unknown_symbol():
    provider = YFinanceData(ticker_factory=lambda s: StubQuoteTicker(None))
    with pytest.raises(UnknownSymbolError):
        provider.get_quote("XXXX")


def test_yfinance_bars():
    provider = YFinanceData(ticker_factory=lambda s: StubBarsTicker())
    bars = provider.get_bars("SPY", "1D", 2)
    assert len(bars) == 2
    assert bars[-1].close == Decimal("3.5")
    assert bars[-1].timestamp.tzinfo is None
