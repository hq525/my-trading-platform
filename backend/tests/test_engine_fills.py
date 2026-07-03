from decimal import Decimal

import pytest

from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Fill, Position
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


def buy(engine, session, acct, qty, price):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=qty)
    return engine.apply_fill(session, order, Decimal(price))


def sell(engine, session, acct, qty, price):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="sell", order_type="market", qty=qty)
    return engine.apply_fill(session, order, Decimal(price))


def test_buy_fill_updates_cash_and_position(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    assert acct.cash == Decimal("99000")
    pos = session.query(Position).one()
    assert pos.qty == 10
    assert pos.avg_cost == Decimal("100.0000")


def test_avg_cost_is_weighted(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    buy(engine, session, acct, 10, "110")
    pos = session.query(Position).one()
    assert pos.qty == 20
    assert pos.avg_cost == Decimal("105.0000")


def test_sell_realizes_pnl(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    fill = sell(engine, session, acct, 5, "120")
    pos = session.query(Position).one()
    assert fill.realized_pnl == Decimal("100.0000")
    assert pos.realized_pnl == Decimal("100.0000")
    assert pos.qty == 5
    assert acct.cash == Decimal("99600")  # 99000 + 600


def test_commission_charged_on_both_sides(engine, session):
    acct = make_account(session, commission="1")
    buy(engine, session, acct, 10, "100")
    assert acct.cash == Decimal("98999")  # -1000 - 1
    fill = sell(engine, session, acct, 10, "110")
    assert fill.realized_pnl == Decimal("99.0000")  # 100 - 1
    assert acct.cash == Decimal("100098")  # 98999 + 1100 - 1


def test_fill_marks_order_filled_and_creates_row(engine, session):
    acct = make_account(session)
    fill = buy(engine, session, acct, 10, "100")
    assert fill.order.status == "filled"
    assert session.query(Fill).count() == 1


def test_cannot_fill_non_pending(engine, session):
    acct = make_account(session)
    fill = buy(engine, session, acct, 10, "100")
    with pytest.raises(InvalidOrderState):
        engine.apply_fill(session, fill.order, Decimal("100"))
