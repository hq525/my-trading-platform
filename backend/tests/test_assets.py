from datetime import date
from decimal import Decimal

import pytest

from app.assets import contract_multiplier, is_crypto_symbol, is_option_symbol, parse_occ


def test_dash_means_crypto():
    assert is_crypto_symbol("BTC-USD") is True


def test_no_dash_means_stock():
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("SPY") is False


def test_occ_symbol_is_option():
    assert is_option_symbol("SPY260821C00625000") is True
    assert is_option_symbol("AAPL260117P00190000") is True


def test_non_options_are_not_options():
    assert is_option_symbol("SPY") is False
    assert is_option_symbol("BTC-USD") is False
    assert is_option_symbol("SPY260821X00625000") is False  # bad right
    assert is_option_symbol("SPY261341C00625000") is False  # month 13: bad date
    assert is_option_symbol("spy260821c00625000") is False  # lowercase
    assert is_option_symbol("TOOLONGG260821C00625000") is False  # 8-char root


def test_option_symbols_never_classify_as_crypto():
    assert is_crypto_symbol("SPY260821C00625000") is False


def test_parse_occ_round_trip():
    c = parse_occ("SPY260821C00625000")
    assert c.underlying == "SPY"
    assert c.expiry == date(2026, 8, 21)
    assert c.right == "call"
    assert c.strike == Decimal("625")


def test_parse_occ_put_and_fractional_strike():
    c = parse_occ("F260918P00007500")
    assert c.underlying == "F"
    assert c.right == "put"
    assert c.strike == Decimal("7.5")


def test_parse_occ_rejects_non_option():
    with pytest.raises(ValueError):
        parse_occ("SPY")


def test_contract_multiplier():
    assert contract_multiplier("SPY260821C00625000") == Decimal("100")
    assert contract_multiplier("SPY") == Decimal("1")
    assert contract_multiplier("BTC-USD") == Decimal("1")
