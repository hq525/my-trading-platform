from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.assets import is_crypto_symbol
from app.config import Settings
from app.db import Base, make_session_factory
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.main import AppDeps, create_app
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def client(session_factory, tmp_path):
    fake_md = FakeMarketData()
    fake_md.set_quote("SPY", "100")
    fake_cal = FakeCalendar(open_=True)
    engine = TradingEngine(fake_md)
    execution = SimAdapter(engine, fake_md, fake_cal,
                           owns_symbol=lambda s: not is_crypto_symbol(s))

    crypto_fake_md = FakeMarketData()
    crypto_fake_md.set_quote("BTC-USD", "65000")
    crypto_fake_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_fake_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_fake_md, crypto_fake_cal,
                                  owns_symbol=is_crypto_symbol)

    settings = Settings(password="pw", secret_key="test-secret")
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_fake_md if is_crypto_symbol(symbol) else fake_md

    runner = StrategyRunner(Path(strategies_dir), session_factory, execution_for_symbol,
                            market_data_for_symbol, settings.starting_cash)
    deps = AppDeps(settings=settings, session_factory=session_factory,
                   market_data=fake_md, calendar=fake_cal, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_fake_md, crypto_calendar=crypto_fake_cal,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.fake_md = fake_md
    c.fake_cal = fake_cal
    c.crypto_fake_md = crypto_fake_md
    c.crypto_fake_cal = crypto_fake_cal
    return c
