import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.live_fixtures import default_live_handler, make_live_deps


def make_client(session_factory, tmp_path, live_handler=default_live_handler):
    deps = make_live_deps(session_factory, tmp_path, live_handler)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.deps = deps
    return c


@pytest.fixture
def live_client(session_factory, tmp_path):
    return make_client(session_factory, tmp_path)


def account_by_name(client, name):
    accounts = client.get("/api/accounts").json()
    return next(a for a in accounts if a["name"] == name)


def test_accounts_expose_mode_and_sync_fields(live_client):
    live = account_by_name(live_client, "live")
    assert live["mode"] == "live"
    assert live["cash"] == "50000"
    assert live["last_synced_at"] is not None
    assert live["sync_detail"] is None
    manual = account_by_name(live_client, "manual")
    assert manual["mode"] == "paper"
    assert manual["last_synced_at"] is None


def test_account_detail_includes_mode(live_client):
    live = account_by_name(live_client, "live")
    detail = live_client.get(f"/api/accounts/{live['id']}").json()
    assert detail["mode"] == "live"
    assert detail["sync_detail"] is None


def test_live_order_stays_pending_at_placement(live_client):
    live = account_by_name(live_client, "live")
    r = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "10"})
    assert r.status_code == 201
    body = r.json()
    # Live orders are never filled at placement — Alpaca decides via the poll.
    assert body["status"] == "pending"


def test_paper_order_still_fills_immediately(live_client):
    manual = account_by_name(live_client, "manual")
    r = live_client.post(f"/api/accounts/{manual['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "10"})
    assert r.status_code == 201
    assert r.json()["status"] == "filled"


def test_crypto_on_live_account_is_rejected(live_client):
    live = account_by_name(live_client, "live")
    r = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "BTC-USD", "side": "buy", "order_type": "market",
        "qty": "0.5"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "crypto not supported in live trading yet"


def test_cancel_live_order_returns_pending_until_poll(live_client):
    live = account_by_name(live_client, "live")
    placed = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "limit", "qty": "5",
        "limit_price": "90"}).json()
    r = live_client.post(f"/api/orders/{placed['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_cancel_when_broker_unreachable_returns_502(session_factory, tmp_path):
    def handler(request):
        if request.method == "DELETE":
            raise httpx.ConnectError("down")
        return default_live_handler(request)

    client = make_client(session_factory, tmp_path, handler)
    live = account_by_name(client, "live")
    placed = client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "limit", "qty": "5",
        "limit_price": "90"}).json()
    r = client.post(f"/api/orders/{placed['id']}/cancel")
    assert r.status_code == 502


def test_journal_trades_carry_account_mode(live_client):
    from app.jobs import run_process_pending

    manual = account_by_name(live_client, "manual")
    live = account_by_name(live_client, "live")
    live_client.post(f"/api/accounts/{manual['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1"})
    live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1"})
    run_process_pending(live_client.deps)  # poll mirrors the live fill

    paper_trades = live_client.get(
        f"/api/journal?account_id={manual['id']}").json()
    assert paper_trades[0]["account_mode"] == "paper"
    live_trades = live_client.get(
        f"/api/journal?account_id={live['id']}").json()
    assert live_trades[0]["account_mode"] == "live"
