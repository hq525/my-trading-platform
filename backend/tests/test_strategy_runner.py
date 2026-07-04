from decimal import Decimal
from pathlib import Path

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

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


def _stock_only(execution=None, market_data=None):
    def execution_for_symbol(symbol):
        return execution

    def market_data_for_symbol(symbol):
        return market_data

    return execution_for_symbol, market_data_for_symbol


@pytest.fixture
def runner(tmp_path, session_factory):
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    (tmp_path / "exploder.py").write_text(BAD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    execution_for_symbol, market_data_for_symbol = _stock_only(execution, md)
    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
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


def test_broken_strategy_file_is_skipped(tmp_path, session_factory):
    (tmp_path / "broken.py").write_text("def broken(:\n")
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    execution_for_symbol, market_data_for_symbol = _stock_only(execution, md)
    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
    r.discover()
    assert set(r.strategies) == {"BuyOne"}


class _GoodSchedule:
    schedule = "daily_after_close"


class _BadSchedule:
    schedule = "not a cron"


def test_invalid_cron_schedule_is_skipped(runner):
    runner.strategies = {"Good": _GoodSchedule, "Bad": _BadSchedule}
    scheduler = BackgroundScheduler()
    runner.register_jobs(scheduler)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "strategy:Good" in job_ids
    assert "strategy:Bad" not in job_ids


def test_strategy_can_trade_both_stock_and_crypto_symbols(tmp_path, session_factory):
    mixed_strategy = '''
from decimal import Decimal
from app.strategy.base import Strategy

class MixedTrader(Strategy):
    def run(self, ctx):
        ctx.buy("SPY", qty=1)
        ctx.buy("BTC-USD", qty=Decimal("0.01"))
'''
    (tmp_path / "mixed.py").write_text(mixed_strategy)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, FakeCalendar(open_=True))

    def execution_for_symbol(symbol):
        return crypto_execution if "-" in symbol else execution

    def market_data_for_symbol(symbol):
        return crypto_md if "-" in symbol else md

    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
    r.discover()
    r.sync_accounts()
    with session_factory() as s:
        state = s.query(StrategyState).filter_by(name="MixedTrader").one()
        state.enabled = True
        s.commit()
    run = r.run_strategy("MixedTrader")
    assert run.status == "ok"
    assert run.detail == "orders placed: 2"
    with session_factory() as s:
        symbols = {o.symbol for o in s.query(Order).all()}
        assert symbols == {"SPY", "BTC-USD"}
