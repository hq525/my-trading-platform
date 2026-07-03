def place(client, body=None):
    payload = {"symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_quote_endpoint(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    assert r.json()["price"] == "100"
    assert "as_of" in r.json()


def test_quote_unknown_symbol_404(client):
    assert client.get("/api/market/quote/XXXX").status_code == 404


def test_quote_outage_503(client):
    client.fake_md.fail = True
    assert client.get("/api/market/quote/SPY").status_code == 503


def test_bars_endpoint(client):
    client.fake_md.set_bars("SPY", ["1", "2", "3"])
    bars = client.get("/api/market/bars/SPY?limit=2").json()
    assert len(bars) == 2
    assert bars[-1]["close"] == "3"


def test_journal_lists_trades_with_notes(client):
    order = place(client).json()
    client.put(f"/api/orders/{order['id']}/note", json={"text": "entry note"})
    place(client, {"side": "sell", "qty": 5})
    trades = client.get("/api/journal?account_id=1").json()
    assert len(trades) == 2
    sell, buy = trades  # newest first
    assert sell["side"] == "sell"
    assert sell["realized_pnl"] == "0"
    assert buy["note"] == "entry note"


def test_journal_stats(client):
    place(client)                                   # buy 10 @ 100
    place(client, {"side": "sell", "qty": 5})       # realized 0 (neither win nor loss)
    client.fake_md.set_quote("SPY", "120")
    place(client, {"side": "sell", "qty": 5})       # realized +100 -> win
    stats = client.get("/api/journal/stats?account_id=1").json()
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 1


def test_strategies_list_and_toggle(client):
    # the client fixture's strategies dir is empty -> empty list
    assert client.get("/api/strategies").json() == []
    assert client.post("/api/strategies/Nope/toggle").status_code == 404


def test_strategy_toggle_and_runs(client, tmp_path):
    # register a strategy directly on the app's runner
    from app.strategy.base import Strategy

    class Manual(Strategy):
        def run(self, ctx):
            pass

    runner = client.app.state.deps.runner
    runner.strategies["Manual"] = Manual
    runner.sync_accounts()

    [s] = client.get("/api/strategies").json()
    assert s["name"] == "Manual" and s["enabled"] is False
    toggled = client.post("/api/strategies/Manual/toggle").json()
    assert toggled["enabled"] is True
    assert client.get("/api/strategies/Manual/runs").json() == []
