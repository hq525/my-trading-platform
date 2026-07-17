from datetime import date
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, OptionChainRow, Quote, UnknownSymbolError
from tests.fakes import FakeMarketData, FakeOptionsData


def test_quote_bid_ask_default_none():
    from app.timeutil import utcnow
    q = Quote(symbol="SPY", price=Decimal("100"), as_of=utcnow())
    assert q.bid is None and q.ask is None


def test_set_option_quote_two_sided_prices_at_mid():
    md = FakeMarketData()
    md.set_option_quote("SPY260821C00625000", bid="4.90", ask="5.10")
    q = md.get_quote("SPY260821C00625000")
    assert q.bid == Decimal("4.90") and q.ask == Decimal("5.10")
    assert q.price == Decimal("5.0000")


def test_set_option_quote_clamps_zero_bid_and_falls_back_to_last():
    md = FakeMarketData()
    md.set_option_quote("SPY260821C00625000", bid="0", ask="5.10", last="5.05")
    q = md.get_quote("SPY260821C00625000")
    assert q.bid is None
    assert q.ask == Decimal("5.10")
    assert q.price == Decimal("5.05")


def test_fake_options_data_chain_and_expirations():
    od = FakeOptionsData()
    row = OptionChainRow(symbol="SPY260821C00625000", strike=Decimal("625"),
                         right="call", bid=Decimal("4.9"), ask=Decimal("5.1"),
                         last=None, open_interest=Decimal("120"), iv=Decimal("0.17"),
                         delta=Decimal("0.55"), gamma=None, theta=Decimal("-0.12"),
                         vega=None)
    od.set_expirations("SPY", [date(2026, 8, 21)])
    od.set_chain("SPY", date(2026, 8, 21), calls=[row], puts=[])
    assert od.get_expirations("SPY") == [date(2026, 8, 21)]
    calls, puts = od.get_chain("SPY", date(2026, 8, 21))
    assert calls[0].strike == Decimal("625") and puts == []
    with pytest.raises(UnknownSymbolError):
        od.get_expirations("XXXX")
    with pytest.raises(MarketDataError):
        od.get_bars("SPY260821C00625000")
