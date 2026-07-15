from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.engine.valuation import take_snapshots
from app.models import EquitySnapshot, Order
from app.replay.execution import ReplayExecution
from tests.factories import (make_account, make_replay_account,
                             make_replay_bar, make_replay_session)
from tests.fakes import FakeMarketData
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)


def make_stock_session(session, closes=(("2024-06-03", "100"),
                                        ("2024-06-04", "101"))):
    row = make_replay_session(session, symbols=("SPY",),
                              start=closes[0][0], end=closes[-1][0])
    for day, close in closes:
        make_replay_bar(session, row.id, "SPY", day, open_=close, close=close)
    acct = make_replay_account(session, row.id)
    return row, acct


def test_replay_placement_validates_and_stays_pending(session):
    row, acct = make_stock_session(session)
    execution = ReplayExecution()
    order = execution.place_order(session, account_id=acct.id, symbol="SPY",
                                  side="buy", order_type="market", qty=10)
    assert order.status == "pending"
    assert order.placed_at.date() == date(2024, 6, 3)  # virtual, not wall clock
    rejected = execution.place_order(session, account_id=acct.id, symbol="AAPL",
                                     side="buy", order_type="market", qty=1)
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "unknown symbol: AAPL"


def test_replay_cancel_releases_reservation(session):
    row, acct = make_stock_session(session)
    execution = ReplayExecution()
    order = execution.place_order(session, account_id=acct.id, symbol="SPY",
                                  side="buy", order_type="limit", qty=10,
                                  limit_price=Decimal("90"))
    cancelled = execution.cancel_order(session, order.id)
    assert cancelled.status == "cancelled"


def test_sim_adapters_never_touch_replay_orders(deps):
    from app.jobs import run_process_pending
    with deps.session_factory() as s:
        row, acct = make_stock_session(s)
        order = ReplayExecution().place_order(
            s, account_id=acct.id, symbol="SPY", side="buy",
            order_type="market", qty=1)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "pending"


def test_take_snapshots_skips_replay_accounts(session):
    make_account(session)  # paper account, snapshotted
    row, replay_acct = make_stock_session(session)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    take_snapshots(session, lambda s: md)
    session.flush()
    snaps = session.scalars(select(EquitySnapshot)).all()
    assert {s.account_id for s in snaps} == {1}
    assert replay_acct.id not in {s.account_id for s in snaps}


def test_execution_for_routes_replay_accounts():
    from types import SimpleNamespace

    from app.main import AppDeps
    deps = AppDeps(settings=None, session_factory=None, market_data="md",
                   calendar=None, engine=None, execution="stock-exec",
                   runner=None, crypto_market_data="cmd", crypto_calendar=None,
                   crypto_engine=None, crypto_execution="crypto-exec")
    assert isinstance(deps.execution_for(SimpleNamespace(mode="replay"), "SPY"),
                      ReplayExecution)
    assert deps.execution_for(SimpleNamespace(mode="paper"), "SPY") == "stock-exec"
