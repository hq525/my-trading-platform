from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Account, EquitySnapshot, Fill, JournalNote, Order, Position, ReplayBar, ReplaySession
from app.replay.service import (ReplayCreationError, ReplaySources,
                                create_session, delete_session, session_lock)
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.fakes import FakeMarketData

TODAY = date(2024, 7, 1)


def make_sources(symbol_days: dict[str, list[tuple[str, str]]]):
    """symbol -> [(iso_date, close)] built directly as Bar objects."""
    from datetime import datetime

    from app.marketdata.base import Bar
    md = FakeMarketData()
    for sym, days in symbol_days.items():
        md.bars[sym] = [Bar(timestamp=datetime.fromisoformat(d),
                            open=Decimal(c), high=Decimal(c), low=Decimal(c),
                            close=Decimal(c), volume=1000)
                        for d, c in days]
    return ReplaySources(stock=md, crypto_primary=md, crypto_fallback=md)


def test_create_session_loads_bars_accounts_and_cursor(session):
    sources = make_sources({"SPY": [("2024-06-03", "100"), ("2024-06-04", "101"),
                                    ("2024-06-05", "102")]})
    row = create_session(session, sources, symbols=["spy"],
                         start_date=date(2024, 6, 4), strategies=["SmaCross"],
                         known_strategies={"SmaCross"},
                         starting_cash=Decimal("50000"), today=TODAY)
    assert row.symbols == ["SPY"]  # upper-cased
    assert row.cursor_date == date(2024, 6, 4)
    assert row.end_date == date(2024, 6, 5)
    assert row.name == "SPY from 2024-06-04"
    accounts = session.scalars(select(Account).where(
        Account.replay_session_id == row.id)).all()
    assert {a.name for a in accounts} == {
        f"replay:{row.id}:manual", f"replay:{row.id}:strategy:SmaCross"}
    assert all(a.mode == "replay" and a.cash == Decimal("50000") for a in accounts)
    assert session.scalars(select(ReplayBar).where(
        ReplayBar.session_id == row.id)).all().__len__() == 3


def test_create_session_drops_todays_partial_bar(session):
    sources = make_sources({"SPY": [("2024-06-28", "100"), ("2024-07-01", "101")]})
    row = create_session(session, sources, symbols=["SPY"],
                         start_date=date(2024, 6, 28), strategies=[],
                         known_strategies=set(), starting_cash=Decimal("1000"),
                         today=TODAY)
    dates = [b.date for b in session.scalars(select(ReplayBar))]
    assert date(2024, 7, 1) not in dates
    assert row.end_date == date(2024, 6, 28)


def test_create_session_requires_coverage_at_start(session):
    sources = make_sources({"SPY": [("2024-06-10", "100"), ("2024-06-11", "101")]})
    with pytest.raises(ReplayCreationError, match="history starts"):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=[],
                       known_strategies=set(), starting_cash=Decimal("1000"),
                       today=TODAY)


def test_create_session_validates_inputs(session):
    sources = make_sources({"SPY": [("2024-06-03", "100")]})
    with pytest.raises(ReplayCreationError, match="at least one symbol"):
        create_session(session, sources, symbols=[], start_date=date(2024, 6, 3),
                       strategies=[], known_strategies=set(),
                       starting_cash=Decimal("1000"), today=TODAY)
    with pytest.raises(ReplayCreationError, match="unknown strategies: Nope"):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=["Nope"],
                       known_strategies={"SmaCross"},
                       starting_cash=Decimal("1000"), today=TODAY)


def test_create_session_provider_failure_writes_nothing(session):
    md = FakeMarketData()
    md.fail = True
    sources = ReplaySources(stock=md, crypto_primary=md, crypto_fallback=md)
    with pytest.raises(ReplayCreationError):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=[],
                       known_strategies=set(), starting_cash=Decimal("1000"),
                       today=TODAY)
    assert session.scalars(select(ReplaySession)).all() == []
    assert session.scalars(select(Account)).all() == []


def test_crypto_uses_fallback_when_primary_fails(session):
    from datetime import datetime

    from app.marketdata.base import Bar
    primary = FakeMarketData()
    primary.fail = True
    fallback = FakeMarketData()
    fallback.bars["BTC-USD"] = [
        Bar(timestamp=datetime(2024, 6, 3), open=Decimal("65000"),
            high=Decimal("65000"), low=Decimal("65000"), close=Decimal("65000"),
            volume=1)]
    sources = ReplaySources(stock=FakeMarketData(), crypto_primary=primary,
                            crypto_fallback=fallback)
    row = create_session(session, sources, symbols=["BTC-USD"],
                         start_date=date(2024, 6, 3), strategies=[],
                         known_strategies=set(), starting_cash=Decimal("1000"),
                         today=TODAY)
    assert row.end_date == date(2024, 6, 3)


def test_delete_session_cascades_everything_including_notes(session):
    row = make_replay_session(session)
    acct = make_replay_account(session, row.id)
    make_replay_bar(session, row.id, "SPY", "2024-06-03")
    order = Order(account_id=acct.id, symbol="SPY", side="buy",
                  order_type="market", qty=Decimal("1"), status="filled")
    session.add(order)
    session.flush()
    session.add(Fill(order_id=order.id, price=Decimal("100"), qty=Decimal("1")))
    session.add(JournalNote(order_id=order.id, text="replay note"))
    session.add(Position(account_id=acct.id, symbol="SPY", qty=Decimal("1"),
                         avg_cost=Decimal("100"), realized_pnl=Decimal("0")))
    session.add(EquitySnapshot(account_id=acct.id, date=date(2024, 6, 3),
                               equity=Decimal("1"), cash=Decimal("1")))
    session.flush()
    delete_session(session, row.id)
    for model in (ReplaySession, ReplayBar, Order, Fill, JournalNote,
                  Position, EquitySnapshot):
        assert session.scalars(select(model)).all() == []
    assert session.scalars(select(Account)).all() == []


def test_session_lock_is_stable_per_session():
    assert session_lock(1) is session_lock(1)
    assert session_lock(1) is not session_lock(2)
