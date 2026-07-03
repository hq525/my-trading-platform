def place(client, body=None):
    payload = {"symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_place_market_order_fills(client):
    r = place(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "SPY"


def test_rejected_order_reports_reason(client):
    r = place(client, {"qty": 10_000_000})
    assert r.status_code == 201
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"].startswith("insufficient cash")


def test_idempotency_key_returns_same_order(client):
    a = place(client, {"idempotency_key": "k1"}).json()
    b = place(client, {"idempotency_key": "k1"}).json()
    assert a["id"] == b["id"]


def test_list_orders_filters_by_status(client):
    place(client)
    client.fake_cal.open = False
    place(client)  # queues -> pending
    filled = client.get("/api/accounts/1/orders?status=filled").json()
    pending = client.get("/api/accounts/1/orders?status=pending").json()
    assert len(filled) == 1 and len(pending) == 1


def test_cancel_pending_order(client):
    client.fake_cal.open = False
    order = place(client).json()
    r = client.post(f"/api/orders/{order['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_filled_order_is_409(client):
    order = place(client).json()
    assert client.post(f"/api/orders/{order['id']}/cancel").status_code == 409


def test_account_detail_shows_positions_and_equity(client):
    place(client)
    client.fake_md.set_quote("SPY", "110")
    detail = client.get("/api/accounts/1").json()
    assert detail["name"] == "manual"
    assert detail["cash"] == "99000"
    assert detail["equity"] == "100100"
    [pos] = detail["positions"]
    assert pos["symbol"] == "SPY"
    assert pos["unrealized_pnl"] == "100"


def test_account_detail_503_when_data_down(client):
    place(client)
    client.fake_md.fail = True
    assert client.get("/api/accounts/1").status_code == 503


def test_note_upsert(client):
    order = place(client).json()
    r = client.put(f"/api/orders/{order['id']}/note", json={"text": "breakout entry"})
    assert r.status_code == 200
    assert client.put(f"/api/orders/{order['id']}/note",
                      json={"text": "revised"}).status_code == 200


def test_snapshots_endpoint_empty_initially(client):
    assert client.get("/api/accounts/1/snapshots").json() == []
