from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.marketdata.base import Bar
from app.replay.service import ReplaySources
from tests.fakes import FakeMarketData
from tests.live_fixtures import make_live_deps


def bars_for(days):
    return [Bar(timestamp=datetime.fromisoformat(d), open=Decimal(o),
                high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=1000)
            for d, o, h, l, c in days]


@pytest.fixture
def client(session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    md = FakeMarketData()
    md.bars["SPY"] = bars_for([
        ("2024-06-03", "100", "100", "100", "100"),
        ("2024-06-04", "104", "106", "103", "105"),
        ("2024-06-05", "105", "107", "104", "106")])
    deps.replay_sources = ReplaySources(stock=md, crypto_primary=md,
                                        crypto_fallback=md)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.deps = deps
    return c


def create(client, **overrides):
    body = {"symbols": ["SPY"], "start_date": "2024-06-03", "strategies": []}
    body.update(overrides)
    return client.post("/api/replay/sessions", json=body)


def test_create_list_and_detail(client):
    r = create(client, name="my run")
    assert r.status_code == 201
    detail = r.json()
    assert detail["name"] == "my run"
    assert detail["symbols"] == ["SPY"]
    assert detail["cursor_date"] == "2024-06-03"
    assert detail["end_date"] == "2024-06-05"
    assert detail["exhausted"] is False
    assert detail["coverage"] == [{"symbol": "SPY", "first_date": "2024-06-03",
                                   "last_date": "2024-06-05"}]
    assert [a["role"] for a in detail["accounts"]] == ["manual"]

    sessions = client.get("/api/replay/sessions").json()
    assert len(sessions) == 1
    assert client.get(f"/api/replay/sessions/{detail['id']}").status_code == 200
    assert client.get("/api/replay/sessions/999").status_code == 404


def test_create_validation_errors(client):
    assert create(client, symbols=[]).status_code == 400
    assert create(client, start_date="2020-01-01").status_code == 400
    assert create(client, strategies=["Ghost"]).status_code == 400


def test_place_step_and_quote_flow(client):
    session_id = create(client).json()["id"]
    detail = client.get(f"/api/replay/sessions/{session_id}").json()
    manual_id = detail["accounts"][0]["id"]

    q = client.get(f"/api/replay/sessions/{session_id}/quote/SPY").json()
    assert q["price"] == "100"

    placed = client.post(f"/api/accounts/{manual_id}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market",
        "qty": "1"}).json()
    assert placed["status"] == "pending"   # nothing fills at placement

    r = client.post(f"/api/replay/sessions/{session_id}/step").json()
    assert r["cursor_date"] == "2024-06-04"
    assert r["fills"] == [{"order_id": placed["id"], "symbol": "SPY",
                           "side": "buy", "qty": "1", "price": "104"}]

    bars = client.get(
        f"/api/replay/sessions/{session_id}/bars/SPY").json()
    assert len(bars) == 2                  # bars <= cursor only
    assert bars[-1]["close"] == "105"

    r2 = client.post(
        f"/api/replay/sessions/{session_id}/step?steps=250").json()
    assert r2["exhausted"] is True

    assert client.post(
        f"/api/replay/sessions/{session_id}/step?steps=0").status_code == 422


def test_delete_session(client):
    session_id = create(client).json()["id"]
    assert client.delete(
        f"/api/replay/sessions/{session_id}").status_code == 200
    assert client.get(
        f"/api/replay/sessions/{session_id}").status_code == 404
    assert client.delete("/api/replay/sessions/999").status_code == 404


def test_quote_unknown_symbol_404(client):
    session_id = create(client).json()["id"]
    assert client.get(
        f"/api/replay/sessions/{session_id}/quote/AAPL").status_code == 404
