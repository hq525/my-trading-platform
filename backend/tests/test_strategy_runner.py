from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.models import Account, Order, StrategyRun, StrategyState
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData

GOOD_STRATEGY = '''
from decimal import Decimal
from app.strategy.base import Strategy

class BuyOne(Strategy):
    def run(self, ctx):
        ctx.buy("SPY", qty=1)
'''

BAD_STRATEGY = '''
from app.strategy.base import Strategy

class Exploder(Strategy):
    def run(self, ctx):
        raise RuntimeError("boom")
'''


@pytest.fixture
def runner(tmp_path, session_factory):
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    (tmp_path / "exploder.py").write_text(BAD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    r = StrategyRunner(Path(tmp_path), session_factory, execution, md,
                       FakeCalendar(), Decimal("100000"))
    r.discover()
    r.sync_accounts()
    return r


def enable(session_factory, name):
    with session_factory() as s:
        state = s.query(StrategyState).filter_by(name=name).one()
        state.enabled = True
        s.commit()


def test_discovery_finds_strategies(runner):
    assert set(runner.strategies) == {"BuyOne", "Exploder"}


def test_accounts_created_disabled_by_default(runner, session_factory):
    with session_factory() as s:
        acct = s.query(Account).filter_by(name="strategy:BuyOne").one()
        assert acct.kind == "strategy"
        assert acct.cash == Decimal("100000")
        assert s.query(StrategyState).filter_by(name="BuyOne").one().enabled is False


def test_disabled_strategy_does_not_run(runner, session_factory):
    assert runner.run_strategy("BuyOne") is None
    with session_factory() as s:
        assert s.query(StrategyRun).count() == 0


def test_enabled_strategy_places_order_in_own_account(runner, session_factory):
    enable(session_factory, "BuyOne")
    run = runner.run_strategy("BuyOne")
    assert run.status == "ok"
    assert run.detail == "orders placed: 1"
    with session_factory() as s:
        order = s.query(Order).one()
        acct = s.get(Account, order.account_id)
        assert acct.name == "strategy:BuyOne"
        assert order.status == "filled"


def test_error_is_contained_and_recorded(runner, session_factory):
    enable(session_factory, "Exploder")
    run = runner.run_strategy("Exploder")
    assert run.status == "error"
    assert "boom" in run.detail


def test_sync_accounts_is_idempotent(runner, session_factory):
    runner.sync_accounts()
    with session_factory() as s:
        assert s.query(Account).filter_by(name="strategy:BuyOne").count() == 1
