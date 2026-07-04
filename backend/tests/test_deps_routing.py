def test_execution_for_symbol_routes_stock_to_stock_stack(client):
    deps = client.app.state.deps
    assert deps.execution_for_symbol("AAPL") is deps.execution


def test_execution_for_symbol_routes_crypto_to_crypto_stack(client):
    deps = client.app.state.deps
    assert deps.execution_for_symbol("BTC-USD") is deps.crypto_execution


def test_market_data_for_symbol_routes_stock_to_stock_stack(client):
    deps = client.app.state.deps
    assert deps.market_data_for_symbol("AAPL") is deps.market_data


def test_market_data_for_symbol_routes_crypto_to_crypto_stack(client):
    deps = client.app.state.deps
    assert deps.market_data_for_symbol("BTC-USD") is deps.crypto_market_data
