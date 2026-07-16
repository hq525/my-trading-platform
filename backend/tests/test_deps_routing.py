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


from types import SimpleNamespace

from app.main import AppDeps


def _bare_deps(live_execution):
    return AppDeps(settings=None, session_factory=None, market_data="stock-md",
                   calendar=None, engine=None, execution="stock-exec",
                   runner=None, crypto_market_data="crypto-md",
                   crypto_calendar=None, crypto_engine=None,
                   crypto_execution="crypto-exec",
                   live_execution=live_execution)


def test_execution_for_routes_live_account_to_live_adapter():
    deps = _bare_deps("live-exec")
    assert deps.execution_for(SimpleNamespace(mode="live"), "AAPL") == "live-exec"


def test_execution_for_routes_paper_account_by_symbol_shape():
    deps = _bare_deps(None)
    paper = SimpleNamespace(mode="paper")
    assert deps.execution_for(paper, "AAPL") == "stock-exec"
    assert deps.execution_for(paper, "BTC-USD") == "crypto-exec"


OCC = "SPY260821C00625000"


def test_execution_for_symbol_routes_option_to_options_stack(client):
    deps = client.app.state.deps
    assert deps.execution_for_symbol(OCC) is deps.options_execution


def test_market_data_for_symbol_routes_option_to_options_stack(client):
    deps = client.app.state.deps
    assert deps.market_data_for_symbol(OCC) is deps.options_market_data


def test_execution_for_routes_paper_option_to_options_adapter():
    deps = _bare_deps(None)
    deps.options_execution = "options-exec"
    assert deps.execution_for(SimpleNamespace(mode="paper"), OCC) == "options-exec"


def test_every_mode_symbol_pair_claimed_by_exactly_one_sim(client):
    deps = client.app.state.deps
    adapters = [deps.execution, deps.crypto_execution, deps.options_execution]
    for mode in ("paper", "live", "replay"):
        for sym in ("AAPL", "BTC-USD", OCC):
            order = SimpleNamespace(symbol=sym, account=SimpleNamespace(mode=mode))
            claims = sum(1 for a in adapters if a.owns_order(order))
            expected = 1 if mode == "paper" else 0
            assert claims == expected, f"{mode}/{sym}: {claims} claims"
