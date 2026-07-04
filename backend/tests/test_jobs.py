from decimal import Decimal
from pathlib import Path

import pytest

from app.assets import is_crypto_symbol
from app.config import Settings
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.jobs import build_scheduler, run_process_pending, run_snapshots
from app.main import AppDeps
from app.models import EquitySnapshot
from app.strategy.runner import StrategyRunner
from tests.factories import make_account
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def deps(session_factory, tmp_path):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal,
                           owns_symbol=lambda s: not is_crypto_symbol(s))

    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    crypto_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, crypto_cal,
                                  owns_symbol=is_crypto_symbol)

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_md if is_crypto_symbol(symbol) else md

    runner = StrategyRunner(Path(strategies_dir), session_factory, execution_for_symbol,
                            market_data_for_symbol, Decimal("100000"))
    with session_factory() as s:
        make_account(s)
        s.commit()
    return AppDeps(settings=Settings(), session_factory=session_factory,
                   market_data=md, calendar=cal, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_md, crypto_calendar=crypto_cal,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)


def test_run_process_pending_fills_queued_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.execution.place_order(
            s, account_id=acct.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    deps.calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_run_snapshots_writes_rows(deps):
    run_snapshots(deps)
    with deps.session_factory() as s:
        assert s.query(EquitySnapshot).count() == 1


def test_build_scheduler_registers_jobs(deps):
    sched = build_scheduler(deps)
    ids = {job.id for job in sched.get_jobs()}
    assert {"process_pending", "snapshots"} <= ids


def test_run_process_pending_fills_queued_crypto_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.crypto_calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.crypto_execution.place_order(
            s, account_id=acct.id, symbol="BTC-USD", side="buy",
            order_type="market", qty=Decimal("0.01"))
        s.commit()
        order_id = order.id
    deps.crypto_calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"
