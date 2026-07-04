from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.jobs import build_scheduler

from app.api import accounts, auth, journal, market, orders, strategies
from app.assets import is_crypto_symbol
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.engine.calendar import MarketCalendar
from app.engine.crypto_calendar import CryptoCalendar
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.marketdata.alpaca import AlpacaData
from app.marketdata.binance import BinanceData
from app.marketdata.coinbase import CoinbaseData
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
    crypto_market_data: object
    crypto_calendar: object
    crypto_engine: TradingEngine
    crypto_execution: SimAdapter

    def execution_for_symbol(self, symbol: str) -> SimAdapter:
        return self.crypto_execution if is_crypto_symbol(symbol) else self.execution

    def market_data_for_symbol(self, symbol: str):
        return self.crypto_market_data if is_crypto_symbol(symbol) else self.market_data


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
    execution = SimAdapter(engine, market_data, calendar,
                           owns_symbol=lambda s: not is_crypto_symbol(s))

    crypto_calendar = CryptoCalendar()
    crypto_market_data = MarketDataService([CoinbaseData(), BinanceData()])
    crypto_engine = TradingEngine(crypto_market_data)
    crypto_execution = SimAdapter(crypto_engine, crypto_market_data, crypto_calendar,
                                  owns_symbol=is_crypto_symbol)

    def execution_for_symbol(symbol: str) -> SimAdapter:
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_market_data if is_crypto_symbol(symbol) else market_data

    runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution_for_symbol,
                            market_data_for_symbol, settings.starting_cash)
    return AppDeps(settings=settings, session_factory=session_factory,
                   market_data=market_data, calendar=calendar, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_market_data, crypto_calendar=crypto_calendar,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)


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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if start_scheduler:
            scheduler = build_scheduler(deps)
            scheduler.start()
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Paper Trading Platform", lifespan=lifespan)
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
    app.include_router(market.router, prefix="/api")
    app.include_router(journal.router, prefix="/api")
    app.include_router(strategies.router, prefix="/api")
    return app
