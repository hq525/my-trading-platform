from __future__ import annotations

from datetime import datetime

from app.marketdata.base import Bar, MarketDataError, MarketDataProvider, Quote, UnknownSymbolError
from app.timeutil import utcnow


class MarketDataService:
    """Tries providers in order; caches quotes briefly to respect free tiers."""

    def __init__(self, providers: list[MarketDataProvider],
                 quote_ttl_seconds: int = 30, now_fn=utcnow):
        self._providers = providers
        self._ttl = quote_ttl_seconds
        self._now = now_fn
        self._cache: dict[str, tuple[Quote, datetime]] = {}

    def get_quote(self, symbol: str) -> Quote:
        cached = self._cache.get(symbol)
        if cached and (self._now() - cached[1]).total_seconds() < self._ttl:
            return cached[0]
        errors: list[str] = []
        for p in self._providers:
            try:
                quote = p.get_quote(symbol)
            except UnknownSymbolError:
                raise
            except MarketDataError as e:
                errors.append(f"{p.name}: {e}")
                continue
            self._cache[symbol] = (quote, self._now())
            return quote
        raise MarketDataError("; ".join(errors) or "no providers configured")

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        errors: list[str] = []
        for p in self._providers:
            try:
                return p.get_bars(symbol, timeframe, limit)
            except UnknownSymbolError:
                raise
            except MarketDataError as e:
                errors.append(f"{p.name}: {e}")
        raise MarketDataError("; ".join(errors) or "no providers configured")
