from decimal import Decimal
from pathlib import Path

import httpx

from app.assets import is_crypto_symbol, is_option_symbol
from app.config import Settings
from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.main import AppDeps
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData


def default_live_handler(request):
    path = request.url.path
    if path == "/v2/account":
        return httpx.Response(200, json={"cash": "50000"})
    if path == "/v2/positions":
        return httpx.Response(200, json=[])
    if request.method == "POST" and path == "/v2/orders":
        return httpx.Response(200, json={"id": "b-live-1", "status": "accepted"})
    if request.method == "DELETE":
        return httpx.Response(204)
    if request.method == "GET" and path.startswith("/v2/orders/"):
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "100"})
    return httpx.Response(404)


def make_live_deps(session_factory, tmp_path, live_handler=None):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    # yfinance can resolve crypto tickers, so the stock service may too;
    # this exercises the live adapter's own crypto guard.
    md.set_quote("BTC-USD", "65000")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal,
                           owns_order=lambda o: o.account.mode == "paper"
                           and not is_crypto_symbol(o.symbol)
                           and not is_option_symbol(o.symbol))

    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    crypto_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, crypto_cal,
                                  owns_order=lambda o: o.account.mode == "paper"
                                  and is_crypto_symbol(o.symbol))

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir(exist_ok=True)

    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_md if is_crypto_symbol(symbol) else md

    runner = StrategyRunner(Path(strategies_dir), session_factory,
                            execution_for_symbol, market_data_for_symbol,
                            Decimal("100000"))
    live_execution = None
    if live_handler is not None:
        live_execution = AlpacaLiveAdapter(
            engine, "https://paper-api.test", "key", "secret",
            transport=httpx.MockTransport(live_handler))
    return AppDeps(settings=Settings(password="pw", secret_key="test-secret"),
                   session_factory=session_factory, market_data=md,
                   calendar=cal, engine=engine, execution=execution,
                   runner=runner, crypto_market_data=crypto_md,
                   crypto_calendar=crypto_cal, crypto_engine=crypto_engine,
                   crypto_execution=crypto_execution,
                   live_execution=live_execution)
