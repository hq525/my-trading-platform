from app.assets import is_crypto_symbol


def test_dash_means_crypto():
    assert is_crypto_symbol("BTC-USD") is True


def test_no_dash_means_stock():
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("SPY") is False
