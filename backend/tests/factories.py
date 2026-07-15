import json
from datetime import date
from decimal import Decimal

from app.models import Account


def make_account(session, name="manual", cash="100000", commission="0",
                 mode="paper"):
    acct = Account(name=name, cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal(commission), mode=mode)
    session.add(acct)
    session.flush()
    return acct


def make_replay_session(session, symbols=("SPY",), start="2024-06-03",
                        cursor=None, end="2024-06-28", strategies=(),
                        starting_cash="100000", name="test session"):
    from app.models import ReplaySession
    row = ReplaySession(name=name, symbols_json=json.dumps(list(symbols)),
                        strategies_json=json.dumps(list(strategies)),
                        start_date=date.fromisoformat(start),
                        cursor_date=date.fromisoformat(cursor or start),
                        end_date=date.fromisoformat(end),
                        starting_cash=Decimal(starting_cash))
    session.add(row)
    session.flush()
    return row


def make_replay_bar(session, session_id, symbol, day, open_="100", high=None,
                    low=None, close=None, volume=1000):
    from app.models import ReplayBar
    bar = ReplayBar(session_id=session_id, symbol=symbol,
                    date=date.fromisoformat(day),
                    open=Decimal(open_), high=Decimal(high or open_),
                    low=Decimal(low or open_), close=Decimal(close or open_),
                    volume=volume)
    session.add(bar)
    session.flush()
    return bar


def make_replay_account(session, session_id, role="manual", cash="100000"):
    suffix = "manual" if role == "manual" else f"strategy:{role}"
    acct = Account(name=f"replay:{session_id}:{suffix}", kind="manual",
                   mode="replay", cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal("0"), replay_session_id=session_id)
    session.add(acct)
    session.flush()
    return acct
