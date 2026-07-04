from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


def _to_binance_symbol(symbol: str) -> str:
    base, _, quote = symbol.partition("-")
    if quote == "USD":
        quote = "USDT"
    return f"{base}{quote}"


class BinanceData:
    """Binance's public API (free, keyless) — fallback crypto provider."""

    name = "binance"
    BASE = "https://api.binance.com"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=self.BASE, timeout=10, transport=transport)

    def _get(self, path: str, params: dict) -> httpx.Response:
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise MarketDataError(f"binance request failed: {e}") from e
        if r.status_code == 400:
            body = r.json()
            if body.get("code") == -1121:
                raise UnknownSymbolError(params.get("symbol", ""))
            raise MarketDataError(f"binance returned 400: {body.get('msg')}")
        if r.status_code != 200:
            raise MarketDataError(f"binance returned {r.status_code}")
        return r

    def get_quote(self, symbol: str) -> Quote:
        binance_symbol = _to_binance_symbol(symbol)
        r = self._get("/api/v3/ticker/price", params={"symbol": binance_symbol})
        return Quote(symbol=symbol, price=Decimal(str(r.json()["price"])), as_of=utcnow())

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        binance_symbol = _to_binance_symbol(symbol)
        r = self._get("/api/v3/klines", params={
            "symbol": binance_symbol, "interval": "1d", "limit": limit,
        })
        return [
            Bar(timestamp=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
                .replace(tzinfo=None),
                open=Decimal(str(row[1])), high=Decimal(str(row[2])),
                low=Decimal(str(row[3])), close=Decimal(str(row[4])),
                volume=int(float(row[5])))
            for row in r.json()
        ]
