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


def _accepting_post(request):
    return httpx.Response(200, json={"id": "broker-9", "status": "accepted"})


def place_pending(session, account, poll_response_json=None,
                  poll_error=False):
    """Adapter whose POST accepts and whose GET returns the given order json."""
    def handler(request):
        if request.method == "POST" and request.url.path == "/v2/orders":
            return _accepting_post(request)
        if poll_error:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=poll_response_json)

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=account.id, symbol="AAPL",
                                side="buy", order_type="market", qty=10)
    assert order.status == "pending"
    return adapter, order


def test_poll_mirrors_fill_at_alpaca_average_price(session, live_account):
    adapter, order = place_pending(
        session, live_account,
        {"status": "filled", "filled_avg_price": "179.55"})
    adapter.process_pending(session)
    session.flush()
    assert order.status == "filled"
    assert live_account.cash == Decimal("100000") - Decimal("179.55") * 10


def test_poll_mirrors_cancellation(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "canceled"})
    adapter.process_pending(session)
    assert order.status == "cancelled"


def test_poll_mirrors_expiry(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "expired"})
    adapter.process_pending(session)
    assert order.status == "expired"


def test_poll_mirrors_rejection_with_reason(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "rejected"})
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "broker rejected: unspecified"


def test_poll_leaves_nonterminal_statuses_pending(session, live_account):
    adapter, order = place_pending(
        session, live_account,
        {"status": "partially_filled", "filled_avg_price": "179.00"})
    adapter.process_pending(session)
    assert order.status == "pending"


def test_poll_network_failure_keeps_order_pending(session, live_account):
    adapter, order = place_pending(session, live_account, poll_error=True)
    adapter.process_pending(session)
    assert order.status == "pending"


def test_poll_ignores_paper_orders(session, live_account):
    paper = make_account(session, name="paper-acct")
    session.commit()
    polled = []

    def handler(request):
        if request.method == "POST":
            return _accepting_post(request)
        polled.append(request.url.path)
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "1"})

    adapter = make_adapter(handler)
    live_order = adapter.place_order(session, account_id=live_account.id,
                                     symbol="AAPL", side="buy",
                                     order_type="market", qty=1)
    paper_order = adapter.engine.place_order(
        session, account_id=paper.id, symbol="AAPL", side="buy",
        order_type="limit", qty=1, limit_price=Decimal("100"))
    adapter.process_pending(session)
    assert polled == [f"/v2/orders/{live_order.broker_order_id}"]
    assert paper_order.status == "pending"


def sync_adapter(account_json, positions_json, fail=False):
    def handler(request):
        if fail:
            raise httpx.ConnectError("down")
        if request.url.path == "/v2/account":
            return httpx.Response(200, json=account_json)
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=positions_json)
        if request.method == "POST":
            return _accepting_post(request)
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "180"})

    return make_adapter(handler)


def test_sync_overwrites_cash_and_stamps_time(session, live_account):
    adapter = sync_adapter({"cash": "98765.43"}, [])
    adapter.sync_account(session)
    assert live_account.cash == Decimal("98765.43")
    assert live_account.last_synced_at is not None
    assert live_account.sync_detail is None


def test_sync_detects_position_mismatch(session, live_account):
    adapter = sync_adapter({"cash": "98204.50"},
                           [{"symbol": "AAPL", "qty": "12"}])
    # Establish a local position of 10 AAPL via a mirrored fill.
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    adapter.process_pending(session)
    assert order.status == "filled"
    adapter.sync_account(session)
    assert live_account.sync_detail == "AAPL: local 10, alpaca 12"


def test_sync_match_clears_previous_mismatch(session, live_account):
    adapter = sync_adapter({"cash": "98204.50"},
                           [{"symbol": "AAPL", "qty": "10"}])
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    adapter.process_pending(session)
    assert order.status == "filled"
    live_account.sync_detail = "stale mismatch"
    adapter.sync_account(session)
    assert live_account.sync_detail is None


def test_sync_failure_changes_nothing(session, live_account):
    adapter = sync_adapter({}, [], fail=True)
    adapter.sync_account(session)
    assert live_account.cash == Decimal("100000")
    assert live_account.last_synced_at is None


def test_sync_without_live_account_is_a_noop(session):
    make_account(session, name="paper-only")
    session.commit()
    adapter = sync_adapter({"cash": "1"}, [])
    adapter.sync_account(session)  # must not raise
