from datetime import timedelta
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.service import MarketDataService
from tests.fakes import Clock, FakeMarketData


def test_returns_quote_from_primary():
    primary = FakeMarketData()
    primary.set_quote("SPY", "500.10")
    svc = MarketDataService([primary])
    assert svc.get_quote("SPY").price == Decimal("500.10")


def test_falls_back_when_primary_down():
    primary, fallback = FakeMarketData(), FakeMarketData()
    primary.fail = True
    fallback.set_quote("SPY", "501")
    svc = MarketDataService([primary, fallback])
    assert svc.get_quote("SPY").price == Decimal("501")


def test_all_providers_down_raises():
    p = FakeMarketData()
    p.fail = True
    with pytest.raises(MarketDataError):
        MarketDataService([p]).get_quote("SPY")


def test_unknown_symbol_does_not_fall_back():
    primary, fallback = FakeMarketData(), FakeMarketData()
    fallback.set_quote("XXXX", "1")
    with pytest.raises(UnknownSymbolError):
        MarketDataService([primary, fallback]).get_quote("XXXX")


def test_quote_cached_within_ttl_then_refreshed():
    clock = Clock()
    p = FakeMarketData()
    p.set_quote("SPY", "500")
    svc = MarketDataService([p], quote_ttl_seconds=30, now_fn=clock)
    assert svc.get_quote("SPY").price == Decimal("500")
    p.set_quote("SPY", "510")
    assert svc.get_quote("SPY").price == Decimal("500")  # cached
    clock.now += timedelta(seconds=31)
    assert svc.get_quote("SPY").price == Decimal("510")  # expired


def test_get_bars_falls_back():
    primary, fallback = FakeMarketData(), FakeMarketData()
    primary.fail = True
    fallback.set_bars("SPY", ["1", "2", "3"])
    bars = MarketDataService([primary, fallback]).get_bars("SPY", "1D", 2)
    assert [b.close for b in bars] == [Decimal("2"), Decimal("3")]
