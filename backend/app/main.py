from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.jobs import build_scheduler

from app.api import accounts, auth, journal, market, orders, replay, strategies
from app.assets import is_crypto_symbol
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
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
from app.replay.execution import ReplayExecution
from app.replay.service import ReplaySources
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
    live_execution: AlpacaLiveAdapter | None = None
    replay_execution: ReplayExecution = field(default_factory=ReplayExecution)
    replay_sources: ReplaySources | None = None

    def execution_for_symbol(self, symbol: str) -> SimAdapter:
        return self.crypto_execution if is_crypto_symbol(symbol) else self.execution

    def market_data_for_symbol(self, symbol: str):
        return self.crypto_market_data if is_crypto_symbol(symbol) else self.market_data

    def execution_for(self, account, symbol: str):
        if account.mode == "replay":
            return self.replay_execution
        if account.mode == "live":
            return self.live_execution
        return self.execution_for_symbol(symbol)


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
                           owns_order=lambda o: o.account.mode == "paper"
                           and not is_crypto_symbol(o.symbol))

    crypto_calendar = CryptoCalendar()
    crypto_market_data = MarketDataService([CoinbaseData(), BinanceData()])
    crypto_engine = TradingEngine(crypto_market_data)
    crypto_execution = SimAdapter(crypto_engine, crypto_market_data, crypto_calendar,
                                  owns_order=lambda o: o.account.mode == "paper"
                                  and is_crypto_symbol(o.symbol))

    live_execution = None
    if settings.alpaca_trading_key_id:
        live_execution = AlpacaLiveAdapter(
            engine, settings.alpaca_trading_base,
            settings.alpaca_trading_key_id, settings.alpaca_trading_secret)

    replay_sources = ReplaySources(stock=YFinanceData(),
                                   crypto_primary=BinanceData(),
                                   crypto_fallback=CoinbaseData())

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
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution,
                   live_execution=live_execution, replay_sources=replay_sources)


def create_app(deps: AppDeps | None = None, start_scheduler: bool = True) -> FastAPI:
    deps = deps or build_deps()

    with deps.session_factory() as session:
        if session.scalar(select(Account).where(Account.name == "manual")) is None:
            session.add(Account(name="manual", kind="manual",
                                cash=deps.settings.starting_cash,
                                starting_cash=deps.settings.starting_cash))
            session.commit()

    if deps.live_execution is not None:
        with deps.session_factory() as session:
            if session.scalar(select(Account).where(Account.mode == "live")) is None:
                # Cash placeholder 0: the sync below immediately replaces it
                # with Alpaca's real figure, so the UI never sees it.
                session.add(Account(name="live", kind="manual", mode="live",
                                    cash=Decimal("0"),
                                    starting_cash=Decimal("0")))
                session.commit()
            deps.live_execution.sync_account(session)
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
    app.include_router(replay.router, prefix="/api")
    return app
