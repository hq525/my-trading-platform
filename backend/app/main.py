from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import accounts, auth, orders
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.engine.calendar import MarketCalendar
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.marketdata.alpaca import AlpacaData
from app.marketdata.service import MarketDataService
from app.marketdata.yfinance_provider import YFinanceData
from app.models import Account
from app.strategy.runner import StrategyRunner

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"


@dataclass
class AppDeps:
    settings: Settings
    session_factory: object
    market_data: object
    calendar: object
    engine: TradingEngine
    execution: SimAdapter
    runner: StrategyRunner


def build_deps(settings: Settings | None = None, market_data=None,
               calendar=None) -> AppDeps:
    settings = settings or Settings()
    db_engine = make_engine(f"sqlite:///{settings.db_path}")
    init_db(db_engine)
    session_factory = make_session_factory(db_engine)
    if market_data is None:
        providers = []
        if settings.alpaca_key_id:
            providers.append(AlpacaData(settings.alpaca_key_id, settings.alpaca_secret))
        providers.append(YFinanceData())
        market_data = MarketDataService(providers)
    calendar = calendar or MarketCalendar()
    engine = TradingEngine(market_data)
    execution = SimAdapter(engine, market_data, calendar)
    runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution,
                            market_data, calendar, settings.starting_cash)
    return AppDeps(settings=settings, session_factory=session_factory,
                   market_data=market_data, calendar=calendar, engine=engine,
                   execution=execution, runner=runner)


def create_app(deps: AppDeps | None = None, start_scheduler: bool = True) -> FastAPI:
    deps = deps or build_deps()

    with deps.session_factory() as session:
        if session.scalar(select(Account).where(Account.name == "manual")) is None:
            session.add(Account(name="manual", kind="manual",
                                cash=deps.settings.starting_cash,
                                starting_cash=deps.settings.starting_cash))
            session.commit()
    deps.runner.discover()
    deps.runner.sync_accounts()

    app = FastAPI(title="Paper Trading Platform")
    app.state.deps = deps
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.include_router(auth.router, prefix="/api")
    app.include_router(accounts.router, prefix="/api")
    app.include_router(orders.router, prefix="/api")
    return app
