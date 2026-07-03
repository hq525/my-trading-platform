from datetime import datetime
from decimal import Decimal

import pytest

from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData


@pytest.fixture
def setup(session):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    clock = Clock(datetime(2026, 7, 2, 15, 0))
    engine = TradingEngine(md)
    adapter = SimAdapter(engine, md, cal, now_fn=clock)
    return md, cal, clock, adapter


def place_limit(adapter, session, acct, side="buy", limit="95", tif="day", qty=10):
    return adapter.place_order(session, account_id=acct.id, symbol="SPY",
                               side=side, order_type="limit", qty=qty,
                               tif=tif, limit_price=Decimal(limit))


def test_buy_limit_waits_above_limit(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, limit="95")
    adapter.process_pending(session)
    assert order.status == "pending"


def test_buy_limit_fills_at_limit_price_when_crossed(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, limit="95")
    md.set_quote("SPY", "94")
    adapter.process_pending(session)
    assert order.status == "filled"
    assert order.account.cash == Decimal("99050")  # filled at 95, not 94


def test_sell_limit_fills_when_crossed(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    buy = adapter.place_order(session, account_id=acct.id, symbol="SPY",
                              side="buy", order_type="market", qty=10)
    assert buy.status == "filled"
    order = place_limit(adapter, session, acct, side="sell", limit="105")
    md.set_quote("SPY", "110")
    adapter.process_pending(session)
    assert order.status == "filled"
    assert acct.cash == Decimal("100050")  # 99000 + 10*105


def test_day_order_expires_after_session_close(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, tif="day")
    cal.expiry_at = datetime(2026, 7, 2, 20, 0)
    adapter.process_pending(session, now=datetime(2026, 7, 2, 20, 1))
    assert order.status == "expired"


def test_gtc_order_survives_expiry_sweep(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, tif="gtc")
    adapter.process_pending(session, now=datetime(2026, 7, 10, 20, 1))
    assert order.status == "pending"


def test_outage_leaves_limit_order_pending(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct)
    md.fail = True
    adapter.process_pending(session)
    assert order.status == "pending"
