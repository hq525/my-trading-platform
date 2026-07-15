from datetime import date, datetime
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.replay.market_data import ReplayMarketData, virtual_now
from tests.factories import make_replay_bar, make_replay_session


@pytest.fixture
def stock_session(session):
    row = make_replay_session(session, symbols=("SPY",),
                              start="2024-06-03", end="2024-06-06")
    make_replay_bar(session, row.id, "SPY", "2024-06-03", close="100")
    make_replay_bar(session, row.id, "SPY", "2024-06-04", close="101")
    make_replay_bar(session, row.id, "SPY", "2024-06-05", close="102")
    make_replay_bar(session, row.id, "SPY", "2024-06-06", close="103")
    return row


def test_virtual_now_convention():
    assert virtual_now(date(2024, 6, 3)) == datetime(2024, 6, 3, 21, 0)


def test_quote_is_latest_close_at_or_before_cursor(session, stock_session):
    stock_session.cursor_date = date(2024, 6, 4)
    md = ReplayMarketData(session, stock_session)
    q = md.get_quote("SPY")
    assert q.price == Decimal("101")
    assert q.as_of == datetime(2024, 6, 4, 21, 0)


def test_quote_never_sees_past_cursor(session, stock_session):
    md = ReplayMarketData(session, stock_session)  # cursor at start: 06-03
    assert md.get_quote("SPY").price == Decimal("100")


def test_out_of_universe_symbol_is_unknown(session, stock_session):
    md = ReplayMarketData(session, stock_session)
    with pytest.raises(UnknownSymbolError):
        md.get_quote("AAPL")
    with pytest.raises(UnknownSymbolError):
        md.get_bars("AAPL")


def test_stale_quote_served_over_weekend_gap(session):
    row = make_replay_session(session, symbols=("SPY", "BTC-USD"),
                              start="2024-06-07", end="2024-06-10",
                              cursor="2024-06-08")
    make_replay_bar(session, row.id, "SPY", "2024-06-07", close="100")
    make_replay_bar(session, row.id, "SPY", "2024-06-10", close="105")
    make_replay_bar(session, row.id, "BTC-USD", "2024-06-08", close="65000")
    md = ReplayMarketData(session, row)
    q = md.get_quote("SPY")  # Saturday cursor; future SPY bars exist
    assert q.price == Decimal("100")
    assert q.as_of == datetime(2024, 6, 7, 21, 0)


def test_strict_mode_rejects_coverage_ended_symbol(session):
    row = make_replay_session(session, symbols=("SPY", "XYZ"),
                              start="2024-06-03", end="2024-06-05",
                              cursor="2024-06-05")
    make_replay_bar(session, row.id, "SPY", "2024-06-05", close="100")
    make_replay_bar(session, row.id, "XYZ", "2024-06-03", close="50")
    with pytest.raises(MarketDataError):
        ReplayMarketData(session, row).get_quote("XYZ")
    # valuation view still serves the last close
    q = ReplayMarketData(session, row, strict=False).get_quote("XYZ")
    assert q.price == Decimal("50")


def test_get_bars_bounded_by_cursor_and_limit(session, stock_session):
    stock_session.cursor_date = date(2024, 6, 5)
    md = ReplayMarketData(session, stock_session)
    bars = md.get_bars("SPY")
    assert [b.close for b in bars] == [Decimal("100"), Decimal("101"), Decimal("102")]
    assert bars[0].timestamp == datetime(2024, 6, 3)
    assert [b.close for b in md.get_bars("SPY", limit=2)] == [Decimal("101"), Decimal("102")]
    with pytest.raises(ValueError):
        md.get_bars("SPY", timeframe="1m")
