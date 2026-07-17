from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx

from app.assets import is_option_symbol, parse_occ
from app.marketdata.alpaca import _parse_ts
from app.marketdata.base import (MarketDataError, OptionChainRow, Quote,
                                 UnknownSymbolError)
from app.timeutil import utcnow

# ~2 years out. expiration_date_lte MUST always be sent: Alpaca defaults it
# to the upcoming weekend, silently truncating expirations to this week.
EXPIRATIONS_WINDOW_DAYS = 730


def _positive(v) -> Decimal | None:
    """Prices: zero/negative means "no side" (e.g. bp=0 = no bid)."""
    if v is None:
        return None
    d = Decimal(str(v))
    return d if d > 0 else None


def _maybe(v) -> Decimal | None:
    """Signed values (greeks, IV): keep negatives, only None passes through."""
    return None if v is None else Decimal(str(v))


class AlpacaOptionsData:
    """Alpaca options data: indicative-feed snapshots + contract discovery.

    Two hosts, same key pair: data.alpaca.markets for snapshots and the
    trading host (paper-api by default; separate rate bucket) for
    /v2/options/contracts.
    """

    name = "alpaca-options"

    def __init__(self, key_id: str, secret: str, feed: str = "indicative",
                 data_base: str = "https://data.alpaca.markets",
                 contracts_base: str = "https://paper-api.alpaca.markets",
                 data_transport: httpx.BaseTransport | None = None,
                 contracts_transport: httpx.BaseTransport | None = None):
        headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self._feed = feed
        self._data = httpx.Client(base_url=data_base, headers=headers,
                                  timeout=10, transport=data_transport)
        self._contracts = httpx.Client(base_url=contracts_base, headers=headers,
                                       timeout=10, transport=contracts_transport)

    def _get(self, client: httpx.Client, path: str, params: dict) -> dict:
        try:
            r = client.get(path, params=params)
        except httpx.HTTPError as e:
            raise MarketDataError(f"alpaca options request failed: {e}") from e
        if r.status_code == 404:
            raise UnknownSymbolError(path)
        if r.status_code != 200:
            # 422 = bad params, NOT unknown symbol (the stock mapper's trap).
            raise MarketDataError(f"alpaca options returned {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise MarketDataError("alpaca options returned malformed JSON") from e

    def get_quote(self, symbol: str) -> Quote:
        data = self._get(self._data, "/v1beta1/options/snapshots",
                         {"symbols": symbol, "feed": self._feed})
        snap = (data.get("snapshots") or {}).get(symbol)
        if snap is None:
            raise UnknownSymbolError(symbol)
        return _quote_from_snapshot(symbol, snap)

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        raise MarketDataError("bars not available for option contracts")

    def get_contracts(self, underlying: str) -> list[dict]:
        today = utcnow().date()
        params = {
            "underlying_symbols": underlying,
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": (today + timedelta(
                days=EXPIRATIONS_WINDOW_DAYS)).isoformat(),
            "limit": 10000,
        }
        rows: list[dict] = []
        token = None
        while True:
            page = dict(params, **({"page_token": token} if token else {}))
            data = self._get(self._contracts, "/v2/options/contracts", page)
            rows.extend(data.get("option_contracts") or [])
            token = data.get("next_page_token")
            if not token:
                return rows

    def get_chain_snapshots(self, underlying: str, expiry: date) -> dict[str, dict]:
        params = {"expiration_date": expiry.isoformat(), "feed": self._feed,
                  "limit": 1000}
        snaps: dict[str, dict] = {}
        token = None
        while True:
            page = dict(params, **({"page_token": token} if token else {}))
            data = self._get(self._data,
                             f"/v1beta1/options/snapshots/{underlying}", page)
            snaps.update(data.get("snapshots") or {})
            token = data.get("next_page_token")
            if not token:
                return snaps


def _quote_from_snapshot(symbol: str, snap: dict) -> Quote:
    q = snap.get("latestQuote") or {}
    t = snap.get("latestTrade") or {}
    bid = _positive(q.get("bp"))
    ask = _positive(q.get("ap"))
    last = _positive(t.get("p"))
    if bid is not None and ask is not None:
        price = ((bid + ask) / 2).quantize(Decimal("0.0001"))
        as_of_raw = q.get("t") or t.get("t")
    elif last is not None:
        price = last
        as_of_raw = t.get("t")
    else:
        raise MarketDataError("no quote for contract")
    as_of = _parse_ts(as_of_raw) if as_of_raw else utcnow()
    return Quote(symbol=symbol, price=price, as_of=as_of, bid=bid, ask=ask)


class OptionsDataService:
    """Caching wrapper over AlpacaOptionsData. One upstream snapshots call
    per chain render; contracts (expirations + open interest) refresh every
    15 minutes; per-contract quotes share the platform's 30s TTL."""

    def __init__(self, provider, quote_ttl_seconds: int = 30,
                 chain_ttl_seconds: int = 30,
                 contracts_ttl_seconds: int = 900, now_fn=utcnow):
        self._p = provider
        self._quote_ttl = quote_ttl_seconds
        self._chain_ttl = chain_ttl_seconds
        self._contracts_ttl = contracts_ttl_seconds
        self._now = now_fn
        self._quotes: dict[str, tuple[Quote, datetime]] = {}
        self._chains: dict[tuple[str, date],
                           tuple[tuple[list, list], datetime]] = {}
        self._contract_rows: dict[str, tuple[list[dict], datetime]] = {}

    def _fresh(self, entry, ttl: int) -> bool:
        return entry is not None and (self._now() - entry[1]).total_seconds() < ttl

    def get_quote(self, symbol: str) -> Quote:
        entry = self._quotes.get(symbol)
        if self._fresh(entry, self._quote_ttl):
            return entry[0]
        quote = self._p.get_quote(symbol)
        self._quotes[symbol] = (quote, self._now())
        return quote

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        return self._p.get_bars(symbol, timeframe, limit)

    def _contracts(self, underlying: str) -> list[dict]:
        entry = self._contract_rows.get(underlying)
        if self._fresh(entry, self._contracts_ttl):
            return entry[0]
        rows = self._p.get_contracts(underlying)
        self._contract_rows[underlying] = (rows, self._now())
        return rows

    def get_expirations(self, underlying: str) -> list[date]:
        dates = sorted({date.fromisoformat(r["expiration_date"])
                        for r in self._contracts(underlying)
                        if r.get("expiration_date")})
        if not dates:
            raise UnknownSymbolError(underlying)
        return dates

    def get_chain(self, underlying: str, expiry: date
                  ) -> tuple[list[OptionChainRow], list[OptionChainRow]]:
        entry = self._chains.get((underlying, expiry))
        if self._fresh(entry, self._chain_ttl):
            return entry[0]
        snaps = self._p.get_chain_snapshots(underlying, expiry)
        oi = {r.get("symbol"): Decimal(str(r["open_interest"]))
              for r in self._contracts(underlying)
              if r.get("symbol") and r.get("open_interest") is not None}
        calls: list[OptionChainRow] = []
        puts: list[OptionChainRow] = []
        for symbol, snap in snaps.items():
            if not is_option_symbol(symbol):
                continue  # adjusted/non-standard contracts are filtered out
            contract = parse_occ(symbol)
            q = snap.get("latestQuote") or {}
            t = snap.get("latestTrade") or {}
            greeks = snap.get("greeks") or {}
            row = OptionChainRow(
                symbol=symbol, strike=contract.strike, right=contract.right,
                bid=_positive(q.get("bp")), ask=_positive(q.get("ap")),
                last=_positive(t.get("p")), open_interest=oi.get(symbol),
                iv=_maybe(snap.get("impliedVolatility")),
                delta=_maybe(greeks.get("delta")), gamma=_maybe(greeks.get("gamma")),
                theta=_maybe(greeks.get("theta")), vega=_maybe(greeks.get("vega")))
            (calls if contract.right == "call" else puts).append(row)
        calls.sort(key=lambda r: r.strike)
        puts.sort(key=lambda r: r.strike)
        result = (calls, puts)
        self._chains[(underlying, expiry)] = (result, self._now())
        return result
