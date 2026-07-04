from decimal import Decimal

import httpx
import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.binance import BinanceData
from app.marketdata.coinbase import CoinbaseData


def coinbase_with(handler):
    return CoinbaseData(transport=httpx.MockTransport(handler))


def test_coinbase_quote_parses_price_and_time():
    def handler(request):
        assert request.url.path == "/products/BTC-USD/ticker"
        return httpx.Response(200, json={
            "trade_id": 123, "price": "65432.10", "size": "0.01",
            "time": "2026-07-04T12:00:00.123456Z",
        })

    q = coinbase_with(handler).get_quote("BTC-USD")
    assert q.price == Decimal("65432.10")
    assert q.as_of.year == 2026 and q.as_of.tzinfo is None


def test_coinbase_unknown_symbol():
    def handler(request):
        return httpx.Response(404, json={"message": "NotFound"})

    with pytest.raises(UnknownSymbolError):
        coinbase_with(handler).get_quote("XXX-USD")


def test_coinbase_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        coinbase_with(handler).get_quote("BTC-USD")


def test_coinbase_bars_reversed_to_oldest_first():
    def handler(request):
        assert request.url.path == "/products/BTC-USD/candles"
        assert request.url.params["granularity"] == "86400"
        # Coinbase returns candles newest-first.
        return httpx.Response(200, json=[
            [1751500800, 100.0, 105.0, 101.0, 104.0, 10.0],  # newer
            [1751414400, 95.0, 99.0, 96.0, 98.0, 20.0],       # older
        ])

    bars = coinbase_with(handler).get_bars("BTC-USD", "1D", 2)
    assert bars[0].close == Decimal("98.0")    # oldest first
    assert bars[-1].close == Decimal("104.0")  # newest last


def binance_with(handler):
    return BinanceData(transport=httpx.MockTransport(handler))


def test_binance_quote_translates_symbol_and_parses_price():
    def handler(request):
        assert request.url.path == "/api/v3/ticker/price"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "65430.50"})

    q = binance_with(handler).get_quote("BTC-USD")
    assert q.price == Decimal("65430.50")
    assert q.as_of.tzinfo is None


def test_binance_unknown_symbol():
    def handler(request):
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with pytest.raises(UnknownSymbolError):
        binance_with(handler).get_quote("XXX-USD")


def test_binance_other_400_is_marketdataerror():
    def handler(request):
        return httpx.Response(400, json={"code": -1100, "msg": "Illegal characters."})

    with pytest.raises(MarketDataError):
        binance_with(handler).get_quote("BTC-USD")


def test_binance_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        binance_with(handler).get_quote("BTC-USD")


def test_binance_bars_oldest_first():
    def handler(request):
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1d"
        return httpx.Response(200, json=[
            [1751414400000, "95.0", "99.0", "96.0", "98.0", "20.0", 1751500799999],
            [1751500800000, "100.0", "105.0", "101.0", "104.0", "10.5", 1751587199999],
        ])

    bars = binance_with(handler).get_bars("BTC-USD", "1D", 2)
    assert bars[0].close == Decimal("98.0")
    assert bars[-1].close == Decimal("104.0")
    assert bars[-1].volume == 10
