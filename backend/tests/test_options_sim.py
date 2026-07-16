from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.engine.engine import TradingEngine
from app.engine.options_sim_adapter import OptionsSimAdapter
from app.models import Fill, Order
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData

OCC = "SPY260821C00625000"        # expires 2026-08-21
ZERO_DTE = "SPY260701C00625000"   # expires 2026-07-01 (Clock default day)


def setup(session, cash="100000", commission="0", open_=True):
    md = FakeMarketData()
    md.set_option_quote(OCC, bid="4.90", ask="5.10")
    cal = FakeCalendar(open_=open_)
    clock = Clock()
    engine = TradingEngine(md, now_fn=clock)
    adapter = OptionsSimAdapter(engine, md, cal, now_fn=clock)
    account = make_account(session, cash=cash, commission=commission)
    return md, cal, clock, adapter, account


def buy(adapter, session, account, symbol=OCC, qty="1", order_type="market",
        limit_price=None, tif="day"):
    return adapter.place_order(session, account_id=account.id, symbol=symbol,
                               side="buy", order_type=order_type,
                               qty=Decimal(qty), tif=tif, limit_price=limit_price)


def sell(adapter, session, account, symbol=OCC, qty="1", order_type="market",
         limit_price=None):
    return adapter.place_order(session, account_id=account.id, symbol=symbol,
                               side="sell", order_type=order_type,
                               qty=Decimal(qty), limit_price=limit_price)


def fill_price(session, order):
    return session.scalar(select(Fill.price).where(Fill.order_id == order.id))


def test_market_buy_fills_at_ask_when_open(session):
    md, cal, clock, adapter, account = setup(session)
    order = buy(adapter, session, account)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.10")
    assert account.cash == Decimal("100000") - Decimal("510")


def test_market_sell_fills_at_bid(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="2")
    order = sell(adapter, session, account, qty="1")
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("4.90")


def test_market_order_pends_while_closed_then_fills_at_open(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account)
    assert order.status == "pending"
    cal.open = True
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.10")


def test_limit_buy_fills_at_ask_once_crossed(session):
    md, cal, clock, adapter, account = setup(session)
    order = buy(adapter, session, account, order_type="limit",
                limit_price=Decimal("5.00"))
    assert order.status == "pending"  # ask 5.10 > limit
    md.set_option_quote(OCC, bid="4.80", ask="4.95")
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("4.95")  # ask, not limit


def test_limit_sell_fills_at_bid_once_crossed(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="1")
    order = sell(adapter, session, account, order_type="limit",
                 limit_price=Decimal("5.00"))
    assert order.status == "pending"  # bid 4.90 < limit
    md.set_option_quote(OCC, bid="5.20", ask="5.40")
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.20")


def test_buy_with_no_ask_stays_pending(session):
    md, cal, clock, adapter, account = setup(session)
    md.set_option_quote(OCC, bid="4.90", last="5.00")  # no ask
    order = buy(adapter, session, account)
    assert order.status == "pending"
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "pending"


def test_sell_with_zero_bid_stays_pending(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="1")
    md.set_option_quote(OCC, bid="0", ask="5.10", last="5.00")
    order = sell(adapter, session, account, qty="1")
    assert order.status == "pending"
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "pending"


def test_at_fill_recheck_prices_at_ask_not_mid(session):
    # cash 500; queued while closed at ask 4.90 (reserve 490). Overnight the
    # spread widens to bid 4.40 / ask 5.60: mid 5.00 -> 500 would PASS the
    # recheck, but the fill is at ask 5.60 -> 560. Must reject, cash intact.
    md, cal, clock, adapter, account = setup(session, cash="500", open_=False)
    md.set_option_quote(OCC, bid="4.70", ask="4.90")  # reserve at ask = 490
    order = buy(adapter, session, account, tif="gtc")
    assert order.status == "pending" and order.reserved_cash == Decimal("490")
    md.set_option_quote(OCC, bid="4.40", ask="5.60")
    cal.open = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert "insufficient cash at fill" in order.reject_reason
    assert account.cash == Decimal("500")


def test_dead_contract_expires_instead_of_filling(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = buy(adapter, session, account, symbol=ZERO_DTE, tif="gtc")
    assert order.status == "pending"
    engine_available_before = account.cash  # reserved but not spent
    clock.now = datetime(2026, 7, 2, 14, 0)  # next day; quote still crossed
    cal.open = True
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "expired"
    assert order.reject_reason == "contract expired"
    assert account.cash == engine_available_before
    assert session.scalar(select(Fill).where(Fill.order_id == order.id)) is None


def test_day_tif_expires_via_calendar_and_releases_reserved_cash(session):
    # No session.refresh here: the calendar stays closed, so process_pending
    # early-returns before its flush — refresh would revert the in-memory
    # expiry back to "pending" (same style as tests/test_sim_limit_expiry.py).
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account, tif="day")
    assert order.status == "pending"
    cal.expiry_at = datetime(2026, 7, 1, 20, 0)
    clock.now = datetime(2026, 7, 1, 21, 0)
    adapter.process_pending(session)
    assert order.status == "expired"
    assert adapter.engine.available_cash(session, account) == Decimal("100000")


def test_market_data_error_rejects_market_order(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account)
    md.fail = True
    cal.open = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"
