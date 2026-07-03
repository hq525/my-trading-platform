from decimal import Decimal

from app.models import Account


def make_account(session, name="manual", cash="100000", commission="0"):
    acct = Account(name=name, cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal(commission))
    session.add(acct)
    session.flush()
    return acct
