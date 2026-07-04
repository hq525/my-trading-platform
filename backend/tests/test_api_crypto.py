def place_crypto(client, body=None):
    payload = {"symbol": "BTC-USD", "side": "buy", "order_type": "market",
              "qty": "0.01"}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_crypto_market_order_fills_via_crypto_stack(client):
    r = place_crypto(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "BTC-USD"
    assert body["qty"] == "0.01"


def test_crypto_order_rejects_fractional_qty_precision(client):
    r = place_crypto(client, {"qty": "0.123456789"})
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "quantity precision exceeds 8 decimal places"


def test_stock_order_still_rejects_fractional_qty(client):
    r = client.post("/api/accounts/1/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1.5"})
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "quantity must be a whole share count"


def test_cancel_crypto_order_routes_to_crypto_stack(client):
    client.crypto_fake_cal.open = False
    order = place_crypto(client).json()
    assert order["status"] == "pending"
    r = client.post(f"/api/orders/{order['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_account_detail_blends_stock_and_crypto_positions(client):
    client.post("/api/accounts/1/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10})
    place_crypto(client)
    detail = client.get("/api/accounts/1").json()
    symbols = {p["symbol"] for p in detail["positions"]}
    assert symbols == {"SPY", "BTC-USD"}


def test_quote_endpoint_routes_crypto_symbol(client):
    r = client.get("/api/market/quote/BTC-USD")
    assert r.status_code == 200
    assert r.json()["price"] == "65000"


def test_quote_endpoint_still_routes_stock_symbol(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    assert r.json()["price"] == "100"
