import json
from decimal import Decimal

import httpx
import pytest

from app.engine.alpaca_live_adapter import AlpacaLiveAdapter, BrokerError
from app.engine.engine import InvalidOrderState, TradingEngine
from tests.factories import make_account
from tests.fakes import FakeMarketData


def make_adapter(handler, extra_quotes=None):
    md = FakeMarketData()
    md.set_quote("AAPL", "180")
    for sym, price in (extra_quotes or {}).items():
        md.set_quote(sym, price)
    return AlpacaLiveAdapter(TradingEngine(md), "https://paper-api.test",
                             "key", "secret",
                             transport=httpx.MockTransport(handler))


@pytest.fixture
def live_account(session):
    acct = make_account(session, name="live", mode="live")
    session.commit()
    return acct


def test_place_order_submits_and_stores_broker_id(session, live_account):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["APCA-API-KEY-ID"]
        return httpx.Response(200, json={"id": "broker-1", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "pending"
    assert order.broker_order_id == "broker-1"
    assert seen["path"] == "/v2/orders"
    assert seen["key"] == "key"
    assert seen["body"]["symbol"] == "AAPL"
    assert seen["body"]["qty"] == "10"
    assert seen["body"]["side"] == "buy"
    assert seen["body"]["type"] == "market"
    assert seen["body"]["time_in_force"] == "day"
    assert seen["body"]["client_order_id"] == str(order.id)
    assert "limit_price" not in seen["body"]


def test_place_limit_order_includes_limit_price(session, live_account):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "broker-2", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, tif="gtc", limit_price=Decimal("175.50"))
    assert order.status == "pending"
    assert seen["body"]["limit_price"] == "175.50"
    assert seen["body"]["time_in_force"] == "gtc"


def test_local_validation_rejects_before_any_submit(session):
    poor = make_account(session, name="live", cash="100", mode="live")
    session.commit()

    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("submitted to broker despite local rejection")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=poor.id, symbol="AAPL",
                                side="buy", order_type="market", qty=10)
    assert order.status == "rejected"
    assert "insufficient cash" in order.reject_reason
    assert order.broker_order_id is None


def test_crypto_symbol_rejected_without_submit(session, live_account):
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("submitted crypto order to stock broker")

    # yfinance can resolve BTC-USD, so engine validation may pass; the
    # adapter's own guard must still reject it.
    adapter = make_adapter(handler, extra_quotes={"BTC-USD": "65000"})
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="BTC-USD", side="buy",
                                order_type="market", qty=Decimal("0.5"))
    assert order.status == "rejected"
    assert order.reject_reason == "crypto not supported in live trading yet"


def test_broker_rejection_rejects_locally_and_releases_reservation(
        session, live_account):
    def handler(request):
        return httpx.Response(
            403, json={"code": 40310000, "message": "insufficient buying power"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "rejected"
    assert order.reject_reason == "broker rejected: insufficient buying power"
    assert adapter.engine.available_cash(session, live_account) == \
        Decimal("100000")


def test_network_failure_rejects_locally(session, live_account):
    def handler(request):
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "rejected"
    assert order.reject_reason.startswith("broker unreachable:")


def test_cancel_sends_delete_and_leaves_order_pending(session, live_account):
    deletes = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "broker-3",
                                             "status": "accepted"})
        deletes.append(request.url.path)
        return httpx.Response(204)

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    result = adapter.cancel_order(session, order.id)
    assert deletes == ["/v2/orders/broker-3"]
    # Not finalized locally: a cancel can race a fill; the poll mirrors
    # Alpaca's final answer.
    assert result.status == "pending"


def test_cancel_network_failure_raises_broker_error(session, live_account):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "broker-4",
                                             "status": "accepted"})
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    with pytest.raises(BrokerError):
        adapter.cancel_order(session, order.id)


def test_cancel_unknown_order_raises_value_error(session, live_account):
    adapter = make_adapter(lambda request: httpx.Response(204))
    with pytest.raises(ValueError):
        adapter.cancel_order(session, 999)


def test_cancel_non_pending_order_raises_invalid_state(session, live_account):
    def handler(request):
        return httpx.Response(200, json={"id": "broker-5", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    order.status = "filled"
    with pytest.raises(InvalidOrderState):
        adapter.cancel_order(session, order.id)


def test_cancel_without_broker_id_cancels_locally(session, live_account):
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no broker order to cancel")

    adapter = make_adapter(handler)
    # Created directly via the engine: pending but never submitted.
    order = adapter.engine.place_order(
        session, account_id=live_account.id, symbol="AAPL", side="buy",
        order_type="limit", qty=5, limit_price=Decimal("150"))
    result = adapter.cancel_order(session, order.id)
    assert result.status == "cancelled"
