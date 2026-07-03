from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Order


def test_account_cash_round_trips_as_decimal(session):
    session.add(Account(name="manual", cash=Decimal("100000.50"),
                        starting_cash=Decimal("100000.50")))
    session.commit()
    session.expire_all()
    acct = session.query(Account).one()
    assert acct.cash == Decimal("100000.50")
    assert isinstance(acct.cash, Decimal)


def test_account_names_are_unique(session):
    session.add(Account(name="manual", cash=Decimal("1"), starting_cash=Decimal("1")))
    session.commit()
    session.add(Account(name="manual", cash=Decimal("1"), starting_cash=Decimal("1")))
    with pytest.raises(IntegrityError):
        session.commit()


def test_order_defaults(session):
    acct = Account(name="a", cash=Decimal("1000"), starting_cash=Decimal("1000"))
    session.add(acct)
    session.flush()
    session.add(Order(account_id=acct.id, symbol="SPY", side="buy",
                      order_type="market", qty=10))
    session.commit()
    session.expire_all()
    o = session.query(Order).one()
    assert o.status == "pending"
    assert o.tif == "day"
    assert o.reserved_cash == Decimal("0")
    assert o.limit_price is None
