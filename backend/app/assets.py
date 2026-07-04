"""Single source of truth for stock-vs-crypto routing.

A "-" in a symbol means crypto (e.g. "BTC-USD"); no dash means a stock
ticker (stock tickers never contain "-"). Every place that needs to route
between the stock and crypto pipelines imports this function rather than
reimplementing the check.
"""


def is_crypto_symbol(symbol: str) -> bool:
    return "-" in symbol
