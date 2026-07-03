from datetime import date, datetime
from decimal import Decimal

import pytest

from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity, ny_date, position_values, take_snapshots
from app.models import EquitySnapshot
from tests.factories import make_account
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def md():
    f = FakeMarketData()
    f.set_quote("SPY", "100")
    return f


@pytest.fixture
def engine(md):
    return TradingEngine(md)


def open_position(engine, session, acct, qty=10, price="100"):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=qty)
    engine.apply_fill(session, order, Decimal(price))


def test_ny_date_converts_from_utc():
    # 01:00 UTC on July 3 is still July 2 in New York (EDT, UTC-4).
    assert ny_date(datetime(2026, 7, 3, 1, 0)) == date(2026, 7, 2)


def test_position_values_and_unrealized(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    [pv] = position_values(session, acct, md)
    assert pv.market_value == Decimal("1100")
    assert pv.unrealized_pnl == Decimal("100")


def test_account_equity(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    assert account_equity(session, acct, md) == Decimal("100100")  # 99000 + 1100


def test_take_snapshots_writes_one_row_per_account(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, md, FakeCalendar(), now=datetime(2026, 7, 2, 20, 10))
    snap = session.query(EquitySnapshot).one()
    assert snap.date == date(2026, 7, 2)
    assert snap.equity == Decimal("100000")  # 99000 cash + 1000 position


def test_take_snapshots_same_day_updates_not_duplicates(engine, session, md):
    acct = make_account(session)
    now = datetime(2026, 7, 2, 20, 10)
    take_snapshots(session, md, FakeCalendar(), now=now)
    take_snapshots(session, md, FakeCalendar(), now=now)
    assert session.query(EquitySnapshot).count() == 1


def test_take_snapshots_skips_non_trading_day(session, md):
    make_account(session)
    take_snapshots(session, md, FakeCalendar(trading_day=False),
                   now=datetime(2026, 7, 3, 20, 10))
    assert session.query(EquitySnapshot).count() == 0


def test_take_snapshots_skips_account_on_data_outage(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    md.fail = True
    take_snapshots(session, md, FakeCalendar(), now=datetime(2026, 7, 2, 20, 10))
    assert session.query(EquitySnapshot).count() == 0
