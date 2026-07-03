import asyncio
import pytest
from decimal import Decimal
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from fastapi.testclient import TestClient
import httpx

from app.config import Settings


# Monkey-patch ASGITransport to work with sync httpx.Client
_original_asgi_transport_init = httpx.ASGITransport.__init__

def _patched_asgi_transport_init(self, app, client_class=None):
    _original_asgi_transport_init(self, app, client_class)
    # Add sync handle_request method
    if not hasattr(self, 'handle_request'):
        original_handle_async = self.handle_async_request
        def handle_request(request):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(original_handle_async(request))
                # Convert the async stream to sync stream
                if hasattr(response.stream, 'read'):
                    return response
                # If it's an AsyncByteStream, we need to make it sync
                try:
                    from httpx._content import ByteStream
                    content = loop.run_until_complete(response.aread())
                    response.stream = ByteStream(content)
                except:
                    pass
                return response
            finally:
                loop.close()
        self.handle_request = handle_request

httpx.ASGITransport.__init__ = _patched_asgi_transport_init
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
    execution = SimAdapter(engine, fake_md, fake_cal)
    settings = Settings(password="pw", secret_key="test-secret")
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    runner = StrategyRunner(Path(strategies_dir), session_factory, execution,
                            fake_md, fake_cal, settings.starting_cash)
    deps = AppDeps(settings=settings, session_factory=session_factory,
                   market_data=fake_md, calendar=fake_cal, engine=engine,
                   execution=execution, runner=runner)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.fake_md = fake_md
    c.fake_cal = fake_cal
    return c
