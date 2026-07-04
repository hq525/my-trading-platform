from datetime import date, datetime
from decimal import Decimal

import pytest

from app.assets import is_crypto_symbol
from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity, ny_date, position_values, take_snapshots
from app.models import EquitySnapshot
from tests.factories import make_account
from tests.fakes import FakeMarketData


@pytest.fixture
def md():
    f = FakeMarketData()
    f.set_quote("SPY", "100")
    return f


@pytest.fixture
def engine(md):
    return TradingEngine(md)


def open_position(engine, session, acct, symbol="SPY", qty=10, price="100"):
    order = engine.place_order(session, account_id=acct.id, symbol=symbol,
                               side="buy", order_type="market", qty=qty)
    engine.apply_fill(session, order, Decimal(price))


def test_ny_date_converts_from_utc():
    # 01:00 UTC on July 3 is still July 2 in New York (EDT, UTC-4).
    assert ny_date(datetime(2026, 7, 3, 1, 0)) == date(2026, 7, 2)


def test_position_values_and_unrealized(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    [pv] = position_values(session, acct, lambda s: md)
    assert pv.market_value == Decimal("1100")
    assert pv.unrealized_pnl == Decimal("100")


def test_account_equity(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    assert account_equity(session, acct, lambda s: md) == Decimal("100100")  # 99000 + 1100


def test_take_snapshots_writes_one_row_per_account(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 2, 20, 10))
    snap = session.query(EquitySnapshot).one()
    assert snap.date == date(2026, 7, 2)
    assert snap.equity == Decimal("100000")  # 99000 cash + 1000 position


def test_take_snapshots_same_day_updates_not_duplicates(engine, session, md):
    acct = make_account(session)
    now = datetime(2026, 7, 2, 20, 10)
    take_snapshots(session, lambda s: md, now=now)
    take_snapshots(session, lambda s: md, now=now)
    assert session.query(EquitySnapshot).count() == 1


def test_take_snapshots_skips_account_on_data_outage(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    md.fail = True
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 2, 20, 10))
    assert session.query(EquitySnapshot).count() == 0


def test_take_snapshots_runs_every_day_regardless_of_stock_calendar(engine, session, md):
    # Phase 1 skipped snapshots on non-trading days; Phase 2 removes that gate
    # because a mixed account's crypto positions can move on any day.
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 4, 20, 10))  # a Saturday
    assert session.query(EquitySnapshot).count() == 1


def test_position_values_mixed_account_routes_by_symbol(engine, session, md):
    md.set_quote("BTC-USD", "60000")  # needed so the engine can open the position at all
    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")  # different price proves routing picks this one
    acct = make_account(session)
    open_position(engine, session, acct, symbol="SPY", qty=10, price="100")
    open_position(engine, session, acct, symbol="BTC-USD", qty=Decimal("0.01"), price="60000")

    def market_data_for_symbol(symbol):
        return crypto_md if is_crypto_symbol(symbol) else md

    values = {pv.symbol: pv for pv in position_values(session, acct, market_data_for_symbol)}
    assert values["SPY"].last_price == Decimal("100")
    assert values["BTC-USD"].last_price == Decimal("65000")
    equity = account_equity(session, acct, market_data_for_symbol)
    assert equity == Decimal("100050")  # 98400 cash + 1000 SPY + 650 BTC-USD
