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


def place_market(adapter, session, acct):
    return adapter.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=10)


def test_market_order_fills_immediately_when_open(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    assert order.status == "filled"
    assert acct.cash == Decimal("99000")


def test_market_order_queues_when_closed(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    assert order.status == "pending"


def test_queued_market_order_fills_on_next_open(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    cal.open = True
    md.set_quote("SPY", "102")  # next session's opening price
    adapter.process_pending(session)
    assert order.status == "filled"
    assert acct.cash == Decimal("98980")


def test_no_quote_at_fill_time_rejects_market_order(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    cal.open = True
    md.fail = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"


def test_process_pending_does_nothing_while_closed(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    adapter.process_pending(session)
    assert order.status == "pending"


def test_rejected_placement_passes_through(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session, cash="10")
    order = place_market(adapter, session, acct)
    assert order.status == "rejected"
