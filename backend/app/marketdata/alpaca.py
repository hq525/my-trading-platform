from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


def _parse_ts(s: str) -> datetime:
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".")
        s = f"{head}.{frac[:6]}"  # fromisoformat caps at microseconds
    return datetime.fromisoformat(s)


class AlpacaData:
    """Alpaca free market data (IEX feed). Free API key, no brokerage account."""

    name = "alpaca"
    BASE = "https://data.alpaca.markets"

    def __init__(self, key_id: str, secret: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=self.BASE,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
            timeout=10,
            transport=transport,
        )

    def _get(self, path: str, params: dict) -> httpx.Response:
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise MarketDataError(f"alpaca request failed: {e}") from e
        if r.status_code in (404, 422):
            raise UnknownSymbolError(path.split("/")[3])
        if r.status_code != 200:
            raise MarketDataError(f"alpaca returned {r.status_code}")
        return r

    def get_quote(self, symbol: str) -> Quote:
        r = self._get(f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
        trade = r.json()["trade"]
        return Quote(symbol=symbol, price=Decimal(str(trade["p"])), as_of=_parse_ts(trade["t"]))

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        start = (utcnow() - timedelta(days=limit * 2)).date().isoformat()
        r = self._get(f"/v2/stocks/{symbol}/bars", params={
            "timeframe": "1Day", "start": start, "limit": limit,
            "adjustment": "split", "feed": "iex",
        })
        return [
            Bar(timestamp=_parse_ts(b["t"]), open=Decimal(str(b["o"])),
                high=Decimal(str(b["h"])), low=Decimal(str(b["l"])),
                close=Decimal(str(b["c"])), volume=int(b["v"]))
            for b in (r.json().get("bars") or [])
        ]
