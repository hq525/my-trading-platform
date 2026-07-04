from decimal import Decimal

import pytest

from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Position
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


def place(engine, session, acct, **kw):
    args = dict(account_id=acct.id, symbol="SPY", side="buy",
                order_type="market", qty=10)
    args.update(kw)
    return engine.place_order(session, **args)


def test_market_buy_is_pending_with_reservation(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("1000")


def test_insufficient_cash_rejected(engine, session):
    acct = make_account(session, cash="500")
    order = place(engine, session, acct)
    assert order.status == "rejected"
    assert order.reject_reason.startswith("insufficient cash")


def test_reservations_count_against_available_cash(engine, session):
    acct = make_account(session, cash="100000")
    assert place(engine, session, acct, qty=600).status == "pending"   # reserves 60000
    assert place(engine, session, acct, qty=600).status == "rejected"  # only 40000 left


def test_unknown_symbol_rejected(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, symbol="XXXX")
    assert order.status == "rejected"
    assert order.reject_reason == "unknown symbol: XXXX"


def test_market_data_down_rejected(engine, session, md):
    acct = make_account(session)
    md.fail = True
    order = place(engine, session, acct)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"


def test_nonpositive_qty_rejected(engine, session):
    acct = make_account(session)
    assert place(engine, session, acct, qty=0).status == "rejected"


def test_limit_requires_price(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, order_type="limit")
    assert order.status == "rejected"
    assert order.reject_reason == "limit price required"


def test_limit_buy_reserves_at_limit_price(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, order_type="limit",
                  limit_price=Decimal("95"), qty=10)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("950")


def test_sell_without_shares_rejected(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, side="sell")
    assert order.status == "rejected"
    assert order.reject_reason == "insufficient shares"


def test_idempotency_key_returns_same_order(engine, session):
    acct = make_account(session)
    a = place(engine, session, acct, idempotency_key="abc")
    b = place(engine, session, acct, idempotency_key="abc")
    assert a.id == b.id


def test_cancel_pending_releases_reservation(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    cancelled = engine.cancel_order(session, order.id)
    assert cancelled.status == "cancelled"
    assert engine.available_cash(session, acct) == Decimal("100000")


def test_cancel_non_pending_raises(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    engine.cancel_order(session, order.id)
    with pytest.raises(InvalidOrderState):
        engine.cancel_order(session, order.id)


def test_sell_full_position_accepted(engine, session):
    acct = make_account(session)
    session.add(Position(account_id=acct.id, symbol="SPY", qty=100,
                         avg_cost=Decimal("100"), realized_pnl=Decimal("0")))
    session.flush()
    order = place(engine, session, acct, side="sell", qty=100)
    assert order.status == "pending"


def test_pending_sells_reserve_shares(engine, session):
    acct = make_account(session)
    session.add(Position(account_id=acct.id, symbol="SPY", qty=100,
                         avg_cost=Decimal("100"), realized_pnl=Decimal("0")))
    session.flush()
    assert place(engine, session, acct, side="sell", qty=60).status == "pending"
    second = place(engine, session, acct, side="sell", qty=60)
    assert second.status == "rejected"
    assert second.reject_reason == "insufficient shares"


def test_crypto_buy_allows_fractional_qty(engine, session, md):
    md.set_quote("BTC-USD", "65000")
    acct = make_account(session, cash="100000")
    order = engine.place_order(session, account_id=acct.id, symbol="BTC-USD",
                               side="buy", order_type="market",
                               qty=Decimal("0.005"))
    assert order.status == "pending"
    assert order.qty == Decimal("0.005")


def test_crypto_buy_rejects_over_precise_qty(engine, session, md):
    md.set_quote("BTC-USD", "65000")
    acct = make_account(session, cash="100000")
    order = engine.place_order(session, account_id=acct.id, symbol="BTC-USD",
                               side="buy", order_type="market",
                               qty=Decimal("0.123456789"))
    assert order.status == "rejected"
    assert order.reject_reason == "quantity precision exceeds 8 decimal places"


def test_stock_buy_rejects_fractional_qty(engine, session):
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market",
                               qty=Decimal("1.5"))
    assert order.status == "rejected"
    assert order.reject_reason == "quantity must be a whole share count"
