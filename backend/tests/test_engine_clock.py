from datetime import datetime
from decimal import Decimal

from app.engine.engine import TradingEngine
from app.timeutil import utcnow
from tests.factories import make_account
from tests.fakes import FakeMarketData


def _engine(now_fn=None):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    return TradingEngine(md, now_fn=now_fn) if now_fn else TradingEngine(md)


def test_engine_stamps_orders_and_fills_with_injected_clock(session):
    virtual = datetime(2024, 6, 3, 21, 0)
    engine = _engine(now_fn=lambda: virtual)
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
    assert order.placed_at == virtual
    fill = engine.apply_fill(session, order, Decimal("100"))
    assert fill.filled_at == virtual


def test_engine_defaults_to_wall_clock(session):
    engine = _engine()
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
    assert abs((utcnow() - order.placed_at).total_seconds()) < 5
