from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError


def _parse_ts(s: str) -> datetime:
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".")
        s = f"{head}.{frac[:6]}"  # fromisoformat caps at microseconds
    return datetime.fromisoformat(s)


class CoinbaseData:
    """Coinbase's public Exchange API (free, keyless) — primary crypto provider."""

    name = "coinbase"
    BASE = "https://api.exchange.coinbase.com"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(base_url=self.BASE, timeout=10, transport=transport)

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        try:
            r = self._client.get(path, params=params or {})
        except httpx.HTTPError as e:
            raise MarketDataError(f"coinbase request failed: {e}") from e
        if r.status_code == 404:
            raise UnknownSymbolError(path.split("/")[2])
        if r.status_code != 200:
            raise MarketDataError(f"coinbase returned {r.status_code}")
        return r

    def get_quote(self, symbol: str) -> Quote:
        r = self._get(f"/products/{symbol}/ticker")
        body = r.json()
        return Quote(symbol=symbol, price=Decimal(str(body["price"])),
                     as_of=_parse_ts(body["time"]))

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        r = self._get(f"/products/{symbol}/candles", params={"granularity": 86400})
        rows = r.json()  # [[time, low, high, open, close, volume], ...], newest-first
        bars = [
            Bar(timestamp=datetime.fromtimestamp(row[0], tz=timezone.utc).replace(tzinfo=None),
                low=Decimal(str(row[1])), high=Decimal(str(row[2])),
                open=Decimal(str(row[3])), close=Decimal(str(row[4])),
                volume=int(row[5]))
            for row in rows
        ]
        bars.reverse()  # Coinbase returns newest-first; callers expect oldest-first
        return bars[-limit:]
