from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

OCC = "SPY260821C00625000"


def chain_row(**over):
    from app.marketdata.base import OptionChainRow
    base = dict(symbol=OCC, strike=Decimal("625"), right="call",
                bid=Decimal("4.9"), ask=Decimal("5.1"), last=Decimal("5.05"),
                open_interest=Decimal("120"), iv=Decimal("0.172"),
                delta=Decimal("0.55"), gamma=Decimal("0.01"),
                theta=Decimal("-0.12"), vega=Decimal("0.35"))
    base.update(over)
    return OptionChainRow(**base)


def test_expirations_endpoint(client):
    client.options_fake_md.set_expirations("SPY", [date(2026, 8, 21), date(2026, 9, 18)])
    r = client.get("/api/market/options/SPY/expirations")
    assert r.status_code == 200
    assert r.json() == {"underlying": "SPY",
                        "expirations": ["2026-08-21", "2026-09-18"]}


def test_expirations_unknown_underlying_404(client):
    r = client.get("/api/market/options/XXXX/expirations")
    assert r.status_code == 404
    assert r.json()["detail"] == "no options listed for symbol"


def test_expirations_provider_down_503(client):
    client.options_fake_md.set_expirations("SPY", [date(2026, 8, 21)])
    client.options_fake_md.fail = True
    r = client.get("/api/market/options/SPY/expirations")
    assert r.status_code == 503
    client.options_fake_md.fail = False


def test_chain_endpoint(client):
    put = chain_row(symbol="SPY260821P00600000", strike=Decimal("600"),
                    right="put", delta=Decimal("-0.4"), last=None, iv=None)
    client.options_fake_md.set_chain("SPY", date(2026, 8, 21),
                                     calls=[chain_row()], puts=[put])
    r = client.get("/api/market/options/SPY/chain?expiry=2026-08-21")
    assert r.status_code == 200
    body = r.json()
    assert body["underlying"] == "SPY" and body["expiry"] == "2026-08-21"
    call = body["calls"][0]
    assert call["symbol"] == OCC
    assert call["strike"] == "625" and call["bid"] == "4.9" and call["ask"] == "5.1"
    assert call["open_interest"] == "120" and call["theta"] == "-0.12"
    assert body["puts"][0]["last"] is None and body["puts"][0]["iv"] is None


def test_quote_endpoint_returns_bid_ask_for_options(client):
    r = client.get(f"/api/market/quote/{OCC}")
    assert r.status_code == 200
    body = r.json()
    assert body["bid"] == "4.9" and body["ask"] == "5.1"
    assert body["price"] == "5"


def test_quote_endpoint_stock_has_null_bid_ask(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    body = r.json()
    assert body["bid"] is None and body["ask"] is None


def test_bars_endpoint_on_option_is_503_not_500(client):
    r = client.get(f"/api/market/bars/{OCC}")
    assert r.status_code == 503
    assert r.json()["detail"] == "market data unavailable"


def test_post_option_order_fills_at_ask_times_100(client):
    accounts = client.get("/api/accounts").json()
    account_id = accounts[0]["id"]
    cash_before = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    r = client.post(f"/api/accounts/{account_id}/orders", json={
        "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
    assert r.status_code == 201
    assert r.json()["status"] == "filled"
    cash_after = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    assert cash_before - cash_after == Decimal("510")  # 5.10 ask * 1 * 100


def test_post_option_order_lowercase_symbol_routes_to_options_adapter(client):
    # A lowercase OCC symbol must still classify as an option (is_option_symbol
    # is case-sensitive), so the order has to be uppercased before routing.
    # The stock fake has no quote for the OCC contract and would reject it as
    # "unknown symbol", so a fill here proves it reached the options adapter.
    accounts = client.get("/api/accounts").json()
    account_id = accounts[0]["id"]
    cash_before = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    r = client.post(f"/api/accounts/{account_id}/orders", json={
        "symbol": OCC.lower(), "side": "buy", "order_type": "market", "qty": "1"})
    assert r.status_code == 201
    assert r.json()["status"] == "filled"
    cash_after = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    assert cash_before - cash_after == Decimal("510")  # 5.10 ask * 1 * 100


def test_post_order_with_settle_prefixed_idempotency_key_is_rejected(client):
    accounts = client.get("/api/accounts").json()
    account_id = accounts[0]["id"]
    r = client.post(f"/api/accounts/{account_id}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1",
        "idempotency_key": "settle:1:SPY260821C00625000"})
    assert r.status_code == 422
    assert r.json()["detail"] == \
        "idempotency keys with the 'settle:' prefix are reserved"


def test_post_option_order_503_when_options_not_wired(client):
    deps = client.app.state.deps
    saved = deps.options_execution
    deps.options_execution = None
    try:
        accounts = client.get("/api/accounts").json()
        r = client.post(f"/api/accounts/{accounts[0]['id']}/orders", json={
            "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
        assert r.status_code == 503
        assert r.json()["detail"] == "options trading not configured"
    finally:
        deps.options_execution = saved


def test_live_adapter_rejects_options(session):
    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.engine.engine import TradingEngine
    from tests.factories import make_account
    from tests.fakes import FakeMarketData

    md = FakeMarketData()
    md.set_quote(OCC, "5.05")  # yfinance CAN resolve OCC tickers: fence must hit
    engine = TradingEngine(md)
    adapter = AlpacaLiveAdapter(
        engine, "https://example.invalid", "k", "s",
        transport=httpx.MockTransport(
            lambda request: pytest.fail("must never reach the broker")))
    account = make_account(session, name="live", mode="live")
    order = adapter.place_order(session, account_id=account.id, symbol=OCC,
                                side="buy", order_type="market", qty=Decimal("1"))
    assert order.status == "rejected"
    assert order.reject_reason == "options not supported on live"


def test_strategy_context_rejects_options_before_any_engine_call(session):
    from app.strategy.base import Context
    from tests.factories import make_account
    from sqlalchemy import select
    from app.models import Order

    account = make_account(session)
    ctx = Context(session, account,
                  execution_for_symbol=lambda s: pytest.fail("must not route"),
                  market_data_for_symbol=lambda s: pytest.fail("must not route"))
    with pytest.raises(ValueError, match="strategies cannot trade options"):
        ctx.buy(OCC, Decimal("1"))
    assert session.scalars(select(Order)).all() == []


def test_strategy_context_rejects_lowercase_options_before_any_engine_call(session):
    from sqlalchemy import select

    from app.models import Order
    from app.strategy.base import Context
    from tests.factories import make_account

    account = make_account(session)
    ctx = Context(session, account,
                  execution_for_symbol=lambda s: pytest.fail("must not route"),
                  market_data_for_symbol=lambda s: pytest.fail("must not route"))
    with pytest.raises(ValueError, match="strategies cannot trade options"):
        ctx.buy(OCC.lower(), Decimal("1"))
    assert session.scalars(select(Order)).all() == []


def test_replay_creation_rejects_options_before_any_fetch(session):
    from app.replay.service import ReplayCreationError, create_session

    with pytest.raises(ReplayCreationError,
                       match="options are not supported in replay"):
        # sources=None proves the fence fires before any history fetch.
        create_session(session, None, symbols=["SPY", OCC],
                       start_date=date(2026, 1, 5), strategies=[],
                       known_strategies=set(),
                       starting_cash=Decimal("100000"))


def test_replay_create_endpoint_returns_400_for_option_symbols(client):
    deps = client.app.state.deps
    saved = deps.replay_sources
    deps.replay_sources = object()  # fence fires before sources are touched
    try:
        r = client.post("/api/replay/sessions", json={
            "symbols": ["SPY", OCC], "start_date": "2026-01-05"})
        assert r.status_code == 400
        assert r.json()["detail"] == "options are not supported in replay"
    finally:
        deps.replay_sources = saved


def test_replay_placement_rejects_option_symbols(client):
    from tests.factories import (make_replay_account, make_replay_bar,
                                 make_replay_session)

    deps = client.app.state.deps
    with deps.session_factory() as s:
        row = make_replay_session(s, symbols=("SPY",))
        make_replay_bar(s, row.id, "SPY", "2024-06-03")
        acct = make_replay_account(s, row.id)
        s.commit()
        acct_id = acct.id
    # Options can never be in a session universe, so the strict
    # ReplayMarketData placement guard rejects the contract as unknown.
    r = client.post(f"/api/accounts/{acct_id}/orders", json={
        "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"].startswith("unknown symbol")


def test_strategy_context_option_data_access(session):
    from decimal import Decimal as D

    from app.marketdata.base import MarketDataError
    from app.strategy.base import Context
    from tests.factories import make_account
    from tests.fakes import FakeOptionsData

    od = FakeOptionsData()
    od.set_option_quote(OCC, bid="4.90", ask="5.10")
    account = make_account(session)
    ctx = Context(session, account,
                  execution_for_symbol=lambda s: pytest.fail("data-only test"),
                  market_data_for_symbol=lambda s: od)
    q = ctx.get_quote(OCC)  # read-only quote access is permitted
    assert q.ask == D("5.1")
    with pytest.raises(MarketDataError, match="bars not available"):
        ctx.get_bars(OCC)
