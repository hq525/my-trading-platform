# Options Paper Trading (Phase 4) — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Long-only options paper trading — OCC-symbol contracts, Alpaca indicative chain/quote data, a fourth `OptionsSimAdapter` pipeline with cross-the-spread fills, and cash settlement at intrinsic value at expiry.

**Architecture:** Options are a first-class asset class routed by a new `is_option_symbol` predicate (classification order option → crypto → stock everywhere). A dedicated data provider+service (`app/marketdata/alpaca_options.py`) serves chains and per-contract quotes; a dedicated `OptionsSimAdapter` owns `mode=="paper" and is_option_symbol` orders; the ×100 contract multiplier lives in one helper threaded through every notional site; a 16:05-NY job settles expired positions via direct order construction + `apply_fill` (never `place_order`).

**Tech Stack:** FastAPI, SQLAlchemy 2/SQLite (SqliteDecimal TEXT), httpx (+MockTransport in tests), APScheduler, pytest.

**Spec:** `docs/superpowers/specs/2026-07-17-options-phase-4-design.md`

## Global Constraints

- All money/qty math is `Decimal` end-to-end; store via `SqliteDecimal`; never sum/compare money in SQL — do it in Python.
- Classification order at every call site: **option → crypto → stock**. OCC regex: `^[A-Z]{1,6}\d{6}[CP]\d{8}$` plus valid-date check.
- The ×100 appears ONLY via `contract_multiplier(symbol)` — never a literal `100` in engine/valuation/adapter code.
- Notional sites (all must multiply): buy reservation, `apply_fill` cash both sides, realized P&L, at-fill recheck, `position_values` market_value AND unrealized_pnl.
- Fills cross the spread: market/limit buys fill at **ask**, sells at **bid**; the at-fill cash recheck prices at the **ask** (the actual fill price), never `quote.price` (mid). One-sided or zero-bid quotes stay pending — never fabricate a fill.
- `apply_fill` gains optional `commission: Decimal | None = None` (None ⇒ `account.commission`); settlement passes `Decimal("0")`.
- Settlement NEVER goes through `place_order`; idempotency = query for a **filled** order with key `settle:{account_id}:{symbol}`; release dead pending orders BEFORE settling positions.
- Alpaca requests: always `feed=indicative` (from `settings.alpaca_options_feed`); contracts endpoint always sends explicit `expiration_date_lte` (Alpaca defaults it to the upcoming weekend); pagination = send `page_token`, read `next_page_token` (both endpoints); 422 maps to `MarketDataError`, never `UnknownSymbolError`.
- Exact copy strings: `contract expired`, `options not supported on live`, `strategies cannot trade options`, `options are not supported in replay`, `no options listed for symbol`, `bars not available for option contracts`, `market data unavailable`, `no quote for contract`, `options trading not configured`, `options data not configured`.
- Scheduler: option expiry cron **16:05 America/New_York, mon-fri**, job id `option_expiry`, registered so it runs before the 16:10 snapshots.
- All test commands run from `backend/` using the project venv: `cd backend && .venv/bin/python -m pytest <path> -v` — bare `python` lacks the dependencies, and cwd drift between repo root and backend has burned prior sessions. Baseline before Task 1: 231 passed.
- No schema changes: contract identity lives entirely in the OCC symbol string.

---

### Task 1: Asset predicates — `is_option_symbol`, `parse_occ`, `contract_multiplier`

**Files:**
- Modify: `backend/app/assets.py`
- Test: `backend/tests/test_assets.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `is_option_symbol(symbol: str) -> bool`; `parse_occ(symbol: str) -> OccContract` where `OccContract(underlying: str, expiry: date, right: str, strike: Decimal)` with `right` in `("call", "put")`, raising `ValueError` on non-option symbols; `contract_multiplier(symbol: str) -> Decimal` (`Decimal("100")` for options else `Decimal("1")`). Every later task imports these from `app.assets`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_assets.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from app.assets import contract_multiplier, is_option_symbol, parse_occ


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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_assets.py -v`
Expected: FAIL — `ImportError: cannot import name 'contract_multiplier'`

- [ ] **Step 3: Implement** — replace `backend/app/assets.py` with:

```python
"""Single source of truth for asset-class routing.

Classification order everywhere: option -> crypto -> stock.
- Option: compact OCC symbol (ROOT + YYMMDD + C/P + strike*1000 zero-padded
  to 8 digits, e.g. "SPY260821C00625000"). No dash, so it can never collide
  with the crypto heuristic.
- Crypto: "-" in symbol (e.g. "BTC-USD"); stock tickers never contain "-".
- Stock: everything else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

_OCC_RE = re.compile(r"^[A-Z]{1,6}(\d{6})[CP]\d{8}$")


def is_crypto_symbol(symbol: str) -> bool:
    return "-" in symbol


def is_option_symbol(symbol: str) -> bool:
    m = _OCC_RE.match(symbol)
    if m is None:
        return False
    try:
        datetime.strptime(m.group(1), "%y%m%d")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class OccContract:
    underlying: str
    expiry: date
    right: str  # "call" | "put"
    strike: Decimal


def parse_occ(symbol: str) -> OccContract:
    if not is_option_symbol(symbol):
        raise ValueError(f"not an OCC option symbol: {symbol}")
    root_len = len(symbol) - 15
    expiry = datetime.strptime(symbol[root_len:root_len + 6], "%y%m%d").date()
    right = "call" if symbol[root_len + 6] == "C" else "put"
    strike = Decimal(symbol[root_len + 7:]) / Decimal("1000")
    return OccContract(underlying=symbol[:root_len], expiry=expiry,
                       right=right, strike=strike)


def contract_multiplier(symbol: str) -> Decimal:
    """100 for option contracts, 1 for everything else. The ONLY source of
    the x100 — engine, valuation, and adapters must use this, never a
    literal."""
    return Decimal("100") if is_option_symbol(symbol) else Decimal("1")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_assets.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/assets.py tests/test_assets.py && git commit -m "feat: OCC option symbol predicates and contract multiplier"
```

---

### Task 2: Quote bid/ask, `OptionChainRow`, and test fakes

**Files:**
- Modify: `backend/app/marketdata/base.py`
- Modify: `backend/tests/fakes.py`
- Test: `backend/tests/test_option_types.py`

**Interfaces:**
- Consumes: `Quote` from `app.marketdata.base` (existing frozen dataclass).
- Produces: `Quote` gains `bid: Decimal | None = None` and `ask: Decimal | None = None` (existing constructor sites unchanged); `OptionChainRow(symbol: str, strike: Decimal, right: str, bid, ask, last, open_interest, iv, delta, gamma, theta, vega — all Decimal | None)` in `app.marketdata.base`; `FakeMarketData.set_option_quote(symbol, bid=None, ask=None, last=None)` (clamps non-positive bid/ask to None; price = quantized mid when two-sided, else last); `FakeOptionsData` (subclass of `FakeMarketData`) with `set_expirations(underlying, dates)`, `get_expirations(underlying) -> list[date]`, `set_chain(underlying, expiry, calls, puts)`, `get_chain(underlying, expiry) -> (calls, puts)`, and `get_bars` that always raises `MarketDataError("bars not available for option contracts")`.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_option_types.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, OptionChainRow, Quote, UnknownSymbolError
from tests.fakes import FakeMarketData, FakeOptionsData


def test_quote_bid_ask_default_none():
    from app.timeutil import utcnow
    q = Quote(symbol="SPY", price=Decimal("100"), as_of=utcnow())
    assert q.bid is None and q.ask is None


def test_set_option_quote_two_sided_prices_at_mid():
    md = FakeMarketData()
    md.set_option_quote("SPY260821C00625000", bid="4.90", ask="5.10")
    q = md.get_quote("SPY260821C00625000")
    assert q.bid == Decimal("4.90") and q.ask == Decimal("5.10")
    assert q.price == Decimal("5.0000")


def test_set_option_quote_clamps_zero_bid_and_falls_back_to_last():
    md = FakeMarketData()
    md.set_option_quote("SPY260821C00625000", bid="0", ask="5.10", last="5.05")
    q = md.get_quote("SPY260821C00625000")
    assert q.bid is None
    assert q.ask == Decimal("5.10")
    assert q.price == Decimal("5.05")


def test_fake_options_data_chain_and_expirations():
    od = FakeOptionsData()
    row = OptionChainRow(symbol="SPY260821C00625000", strike=Decimal("625"),
                         right="call", bid=Decimal("4.9"), ask=Decimal("5.1"),
                         last=None, open_interest=Decimal("120"), iv=Decimal("0.17"),
                         delta=Decimal("0.55"), gamma=None, theta=Decimal("-0.12"),
                         vega=None)
    od.set_expirations("SPY", [date(2026, 8, 21)])
    od.set_chain("SPY", date(2026, 8, 21), calls=[row], puts=[])
    assert od.get_expirations("SPY") == [date(2026, 8, 21)]
    calls, puts = od.get_chain("SPY", date(2026, 8, 21))
    assert calls[0].strike == Decimal("625") and puts == []
    with pytest.raises(UnknownSymbolError):
        od.get_expirations("XXXX")
    with pytest.raises(MarketDataError):
        od.get_bars("SPY260821C00625000")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_option_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'OptionChainRow'`

- [ ] **Step 3: Implement.** In `backend/app/marketdata/base.py`, replace the `Quote` dataclass and add `OptionChainRow` after `Bar`:

```python
@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    as_of: datetime  # naive UTC
    # Options only (spread-crossing fills); None for stocks/crypto.
    bid: Decimal | None = None
    ask: Decimal | None = None


@dataclass(frozen=True)
class OptionChainRow:
    symbol: str
    strike: Decimal
    right: str  # "call" | "put"
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    open_interest: Decimal | None
    iv: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
```

(`Bar` and the imports stay as they are; `OptionChainRow` goes between `Bar` and `MarketDataProvider`.)

In `backend/tests/fakes.py`, add inside `FakeMarketData` (after `set_bars`):

```python
    def set_option_quote(self, symbol: str, bid=None, ask=None, last=None) -> None:
        bid_d = Decimal(str(bid)) if bid is not None else None
        ask_d = Decimal(str(ask)) if ask is not None else None
        if bid_d is not None and bid_d <= 0:
            bid_d = None  # zero/negative bid = no bid, matching the provider
        if ask_d is not None and ask_d <= 0:
            ask_d = None
        if bid_d is not None and ask_d is not None:
            price = ((bid_d + ask_d) / 2).quantize(Decimal("0.0001"))
        elif last is not None:
            price = Decimal(str(last))
        else:
            price = ask_d or bid_d or Decimal("0")
        self.quotes[symbol] = Quote(symbol=symbol, price=price, as_of=utcnow(),
                                    bid=bid_d, ask=ask_d)
```

and add at the end of `backend/tests/fakes.py` (before `Clock`):

```python
class FakeOptionsData(FakeMarketData):
    """Fake for the options data service: quotes plus chains/expirations."""

    def __init__(self):
        super().__init__()
        self.expirations: dict[str, list] = {}
        self.chains: dict[tuple, tuple[list, list]] = {}

    def set_expirations(self, underlying: str, dates: list) -> None:
        self.expirations[underlying] = dates

    def set_chain(self, underlying: str, expiry, calls: list, puts: list) -> None:
        self.chains[(underlying, expiry)] = (calls, puts)

    def get_expirations(self, underlying: str) -> list:
        if self.fail:
            raise MarketDataError("provider down")
        if underlying not in self.expirations:
            raise UnknownSymbolError(underlying)
        return self.expirations[underlying]

    def get_chain(self, underlying: str, expiry) -> tuple[list, list]:
        if self.fail:
            raise MarketDataError("provider down")
        if (underlying, expiry) not in self.chains:
            raise UnknownSymbolError(underlying)
        return self.chains[(underlying, expiry)]

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        raise MarketDataError("bars not available for option contracts")
```

- [ ] **Step 4: Run to verify pass, plus no regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/test_option_types.py tests/test_marketdata_service.py tests/test_providers.py -v`
Expected: all PASS (the `Quote` change is additive-with-defaults).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/marketdata/base.py tests/fakes.py tests/test_option_types.py && git commit -m "feat: Quote bid/ask, OptionChainRow, and options test fakes"
```

---

### Task 3: `AlpacaOptionsData` provider + `OptionsDataService` caches + config

**Files:**
- Create: `backend/app/marketdata/alpaca_options.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_options_provider.py`

**Interfaces:**
- Consumes: `is_option_symbol`, `parse_occ` (Task 1); `Quote`, `OptionChainRow`, `MarketDataError`, `UnknownSymbolError` (Task 2); `_parse_ts` from `app.marketdata.alpaca`.
- Produces: `AlpacaOptionsData(key_id, secret, feed="indicative", data_base="https://data.alpaca.markets", contracts_base="https://paper-api.alpaca.markets", data_transport=None, contracts_transport=None)` with `get_quote(symbol) -> Quote`, `get_bars(...)` (always raises), `get_contracts(underlying) -> list[dict]`, `get_chain_snapshots(underlying, expiry: date) -> dict[str, dict]`; `OptionsDataService(provider, quote_ttl_seconds=30, chain_ttl_seconds=30, contracts_ttl_seconds=900, now_fn=utcnow)` with `get_quote`, `get_bars`, `get_expirations(underlying) -> list[date]`, `get_chain(underlying, expiry) -> tuple[list[OptionChainRow], list[OptionChainRow]]`; settings `alpaca_options_feed: str = "indicative"` and `alpaca_contracts_base: str = "https://paper-api.alpaca.markets"`.
- Note on the spec's "single-contract quotes reuse the existing 30-second quote cache": the intended reading is same TTL and keying semantics, implemented as `OptionsDataService`'s own quote cache — the stock `MarketDataService` cache is an instance-internal dict and cannot be shared across services.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_options_provider.py`:

```python
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.marketdata.alpaca_options import AlpacaOptionsData, OptionsDataService
from app.marketdata.base import MarketDataError, UnknownSymbolError
from tests.fakes import Clock

OCC = "SPY260821C00625000"
SNAP = {
    "latestQuote": {"bp": 4.90, "ap": 5.10, "t": "2026-07-17T15:00:00Z"},
    "latestTrade": {"p": 5.05, "t": "2026-07-17T14:59:00Z"},
    "impliedVolatility": 0.172,
    "greeks": {"delta": 0.55, "gamma": 0.01, "theta": -0.12, "vega": 0.35, "rho": 0.05},
}


def provider_with(data_handler=None, contracts_handler=None, feed="indicative"):
    dt = httpx.MockTransport(data_handler) if data_handler else None
    ct = httpx.MockTransport(contracts_handler) if contracts_handler else None
    return AlpacaOptionsData("key", "secret", feed=feed,
                             data_transport=dt, contracts_transport=ct)


def test_quote_two_sided_is_mid_with_bid_ask():
    def handler(request):
        assert request.url.path == "/v1beta1/options/snapshots"
        assert request.url.params["symbols"] == OCC
        assert request.url.params["feed"] == "indicative"
        return httpx.Response(200, json={"snapshots": {OCC: SNAP}})

    q = provider_with(data_handler=handler).get_quote(OCC)
    assert q.bid == Decimal("4.9") and q.ask == Decimal("5.1")
    assert q.price == Decimal("5.0000")
    assert q.as_of.tzinfo is None


def test_quote_one_sided_falls_back_to_last():
    snap = {"latestQuote": {"bp": 0, "ap": 5.10}, "latestTrade": {"p": 5.05, "t": "2026-07-17T14:59:00Z"}}

    def handler(request):
        return httpx.Response(200, json={"snapshots": {OCC: snap}})

    q = provider_with(data_handler=handler).get_quote(OCC)
    assert q.bid is None and q.ask == Decimal("5.1")
    assert q.price == Decimal("5.05")


def test_quote_no_data_is_marketdataerror():
    def handler(request):
        return httpx.Response(200, json={"snapshots": {OCC: {"latestQuote": {"bp": 0, "ap": 0}}}})

    with pytest.raises(MarketDataError, match="no quote for contract"):
        provider_with(data_handler=handler).get_quote(OCC)


def test_quote_missing_contract_is_unknown_symbol():
    def handler(request):
        return httpx.Response(200, json={"snapshots": {}})

    with pytest.raises(UnknownSymbolError):
        provider_with(data_handler=handler).get_quote(OCC)


def test_422_is_marketdataerror_not_unknown_symbol():
    def handler(request):
        return httpx.Response(422, json={"message": "bad params"})

    with pytest.raises(MarketDataError) as exc:
        provider_with(data_handler=handler).get_quote(OCC)
    assert not isinstance(exc.value, UnknownSymbolError)


def test_404_is_unknown_symbol():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(UnknownSymbolError):
        provider_with(data_handler=handler).get_quote(OCC)


def test_get_bars_always_raises():
    with pytest.raises(MarketDataError, match="bars not available for option contracts"):
        provider_with().get_bars(OCC)


def test_500_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError) as exc:
        provider_with(data_handler=handler).get_quote(OCC)
    assert not isinstance(exc.value, UnknownSymbolError)


def test_contracts_sends_explicit_lte_and_paginates():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        assert request.url.path == "/v2/options/contracts"
        assert request.url.params["underlying_symbols"] == "SPY"
        assert request.url.params["limit"] == "10000"
        # Alpaca defaults lte to the upcoming weekend; we MUST override it.
        gte = date.fromisoformat(request.url.params["expiration_date_gte"])
        lte = date.fromisoformat(request.url.params["expiration_date_lte"])
        assert lte - gte >= timedelta(days=700)
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={
                "option_contracts": [
                    {"symbol": "SPY260821C00625000", "expiration_date": "2026-08-21",
                     "open_interest": "120"},
                ],
                "next_page_token": "tok2"})
        assert request.url.params["page_token"] == "tok2"
        return httpx.Response(200, json={
            "option_contracts": [
                {"symbol": "SPY260918C00630000", "expiration_date": "2026-09-18",
                 "open_interest": None},
            ],
            "next_page_token": None})

    rows = provider_with(contracts_handler=handler).get_contracts("SPY")
    assert len(rows) == 2 and len(calls) == 2


def test_chain_snapshots_paginates_and_sends_expiry():
    def handler(request):
        assert request.url.path == "/v1beta1/options/snapshots/SPY"
        assert request.url.params["expiration_date"] == "2026-08-21"
        assert request.url.params["feed"] == "indicative"
        assert request.url.params["limit"] == "1000"
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={"snapshots": {OCC: SNAP},
                                             "next_page_token": "n1"})
        return httpx.Response(200, json={
            "snapshots": {"SPY260821P00600000": SNAP}, "next_page_token": None})

    snaps = provider_with(data_handler=handler).get_chain_snapshots("SPY", date(2026, 8, 21))
    assert set(snaps) == {OCC, "SPY260821P00600000"}


class StubProvider:
    def __init__(self):
        self.quote_calls = 0
        self.contract_calls = 0
        self.chain_calls = 0

    def get_quote(self, symbol):
        self.quote_calls += 1
        from app.marketdata.base import Quote
        from app.timeutil import utcnow
        return Quote(symbol=symbol, price=Decimal("5"), as_of=utcnow(),
                     bid=Decimal("4.9"), ask=Decimal("5.1"))

    def get_bars(self, symbol, timeframe="1D", limit=200):
        raise MarketDataError("bars not available for option contracts")

    def get_contracts(self, underlying):
        self.contract_calls += 1
        return [
            {"symbol": "SPY260821C00625000", "expiration_date": "2026-08-21",
             "open_interest": "120"},
            {"symbol": "SPY260821P00600000", "expiration_date": "2026-08-21",
             "open_interest": "80"},
            {"symbol": "SPY260918C00630000", "expiration_date": "2026-09-18",
             "open_interest": None},
            {"symbol": "BADSYMBOL", "expiration_date": "2026-08-21",
             "open_interest": "5"},
        ]

    def get_chain_snapshots(self, underlying, expiry):
        self.chain_calls += 1
        return {  # 630 listed BEFORE 625 so the strike sort is exercised
            "SPY260821C00630000": {"latestQuote": {"bp": 3.0, "ap": 3.2}},
            "SPY260821C00625000": SNAP,
            "SPY260821P00600000": {"latestQuote": {"bp": 1.0, "ap": 1.2},
                                   "greeks": {"delta": -0.4}},
            "SPY7260821C00625000!": SNAP,  # adjusted/non-standard: filtered
        }


def test_service_expirations_sorted_distinct_and_cached():
    stub = StubProvider()
    clock = Clock(datetime(2026, 7, 17, 12, 0))
    svc = OptionsDataService(stub, now_fn=clock)
    assert svc.get_expirations("SPY") == [date(2026, 8, 21), date(2026, 9, 18)]
    svc.get_expirations("SPY")
    assert stub.contract_calls == 1  # 15-min cache
    clock.now += timedelta(seconds=901)
    svc.get_expirations("SPY")
    assert stub.contract_calls == 2


def test_service_expirations_empty_is_unknown_symbol():
    class Empty(StubProvider):
        def get_contracts(self, underlying):
            return []

    with pytest.raises(UnknownSymbolError):
        OptionsDataService(Empty()).get_expirations("XXXX")


def test_service_chain_merges_oi_filters_and_sorts():
    stub = StubProvider()
    svc = OptionsDataService(stub)
    calls, puts = svc.get_chain("SPY", date(2026, 8, 21))
    # strike-ascending despite the stub listing 630 first
    assert [r.symbol for r in calls] == ["SPY260821C00625000",
                                         "SPY260821C00630000"]
    assert calls[1].strike == Decimal("630") and calls[1].open_interest is None
    assert [r.symbol for r in puts] == ["SPY260821P00600000"]
    row = calls[0]
    assert row.strike == Decimal("625") and row.open_interest == Decimal("120")
    assert row.iv == Decimal("0.172") and row.theta == Decimal("-0.12")
    assert puts[0].delta == Decimal("-0.4") and puts[0].open_interest == Decimal("80")
    assert puts[0].last is None and puts[0].iv is None


def test_service_chain_and_quote_cached_30s():
    stub = StubProvider()
    clock = Clock(datetime(2026, 7, 17, 12, 0))
    svc = OptionsDataService(stub, now_fn=clock)
    svc.get_chain("SPY", date(2026, 8, 21))
    svc.get_chain("SPY", date(2026, 8, 21))
    assert stub.chain_calls == 1
    svc.get_quote(OCC)
    svc.get_quote(OCC)
    assert stub.quote_calls == 1
    clock.now += timedelta(seconds=31)
    svc.get_chain("SPY", date(2026, 8, 21))
    svc.get_quote(OCC)
    assert stub.chain_calls == 2 and stub.quote_calls == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.marketdata.alpaca_options'`

- [ ] **Step 3: Implement.** Add to `backend/app/config.py` after `alpaca_trading_base`:

```python
    alpaca_options_feed: str = "indicative"
    alpaca_contracts_base: str = "https://paper-api.alpaca.markets"
```

Create `backend/app/marketdata/alpaca_options.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_provider.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/marketdata/alpaca_options.py app/config.py tests/test_options_provider.py && git commit -m "feat: Alpaca options provider and caching options data service"
```

---

### Task 4: Engine — multiplier, ask reservation, expiry guard, commission override

**Files:**
- Modify: `backend/app/engine/engine.py`
- Test: `backend/tests/test_engine_options.py`

**Interfaces:**
- Consumes: `contract_multiplier`, `is_option_symbol`, `parse_occ` (Task 1); `Quote.ask` (Task 2).
- Produces: `TradingEngine.apply_fill(session, order, price, commission: Decimal | None = None)` (None ⇒ `account.commission`); buy reservation and fill cash both multiply by `contract_multiplier(order.symbol)`; market-buy reservation prices at `quote.ask` when present else `quote.price`; placement rejects option orders with reason `contract expired` when expiry < today (NY) or expiry == today and NY time ≥ 16:00. The guard lives in `place_order` only — the settlement job (Task 8) bypasses it by constructing orders directly.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_engine_options.py`:

```python
from datetime import datetime
from decimal import Decimal

from app.engine.engine import TradingEngine
from tests.factories import make_account
from tests.fakes import Clock, FakeMarketData

OCC = "SPY260821C00625000"       # expires 2026-08-21
EXPIRED = "SPY260630C00625000"   # expired 2026-06-30
ZERO_DTE = "SPY260701C00625000"  # expires 2026-07-01 (Clock's default day)

# Clock default = 2026-07-01 12:00 UTC = 08:00 NY (before the close).
AFTER_CLOSE = datetime(2026, 7, 1, 20, 30)  # 16:30 NY


def setup(session, cash="100000", commission="0", now=None):
    md = FakeMarketData()
    md.set_option_quote(OCC, bid="4.90", ask="5.10")
    engine = TradingEngine(md, now_fn=Clock(now))
    account = make_account(session, cash=cash, commission=commission)
    return md, engine, account


def place_buy(engine, session, account, symbol=OCC, qty="2", order_type="market",
              limit_price=None):
    return engine.place_order(session, account_id=account.id, symbol=symbol,
                              side="buy", order_type=order_type,
                              qty=Decimal(qty), limit_price=limit_price)


def test_market_buy_reserves_at_ask_times_100(session):
    md, engine, account = setup(session, commission="1")
    order = place_buy(engine, session, account)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("5.10") * 2 * 100 + 1  # 1021


def test_limit_buy_reserves_at_limit_times_100(session):
    md, engine, account = setup(session)
    order = place_buy(engine, session, account, qty="1", order_type="limit",
                      limit_price=Decimal("5"))
    assert order.reserved_cash == Decimal("500")


def test_insufficient_cash_check_uses_multiplier(session):
    md, engine, account = setup(session, cash="500")
    order = place_buy(engine, session, account, qty="1")  # ask 5.10 -> 510
    assert order.status == "rejected"
    assert "insufficient cash" in order.reject_reason


def test_fill_debits_cash_times_100_and_keeps_per_share_avg_cost(session):
    md, engine, account = setup(session, commission="1")
    order = place_buy(engine, session, account)
    engine.apply_fill(session, order, Decimal("5.10"))
    assert account.cash == Decimal("100000") - Decimal("1021")
    from sqlalchemy import select
    from app.models import Position
    pos = session.scalar(select(Position).where(Position.symbol == OCC))
    assert pos.qty == 2 and pos.avg_cost == Decimal("5.1000")


def test_sell_realized_pnl_times_100(session):
    md, engine, account = setup(session, commission="1")
    buy = place_buy(engine, session, account)
    engine.apply_fill(session, buy, Decimal("5.10"))
    sell = engine.place_order(session, account_id=account.id, symbol=OCC,
                              side="sell", order_type="market", qty=Decimal("2"))
    fill = engine.apply_fill(session, sell, Decimal("6"))
    # (6 - 5.10) * 2 * 100 - 1 commission
    assert fill.realized_pnl == Decimal("179.0000")
    assert account.cash == Decimal("100000") - Decimal("1021") + Decimal("1199")


def test_apply_fill_commission_override(session):
    md, engine, account = setup(session, commission="1")
    buy = place_buy(engine, session, account)
    engine.apply_fill(session, buy, Decimal("5.10"))
    sell = engine.place_order(session, account_id=account.id, symbol=OCC,
                              side="sell", order_type="market", qty=Decimal("2"))
    before = account.cash
    fill = engine.apply_fill(session, sell, Decimal("0"), commission=Decimal("0"))
    assert fill.commission == Decimal("0")
    assert account.cash == before  # $0 settlement moves cash by exactly $0


def test_fractional_contracts_rejected(session):
    md, engine, account = setup(session)
    order = place_buy(engine, session, account, qty="1.5")
    assert order.status == "rejected"
    assert order.reject_reason == "quantity must be a whole share count"


def test_expired_contract_rejected_before_quote_lookup(session):
    md, engine, account = setup(session)  # no quote set for EXPIRED
    order = place_buy(engine, session, account, symbol=EXPIRED, qty="1")
    assert order.status == "rejected"
    assert order.reject_reason == "contract expired"


def test_zero_dte_allowed_before_close(session):
    md, engine, account = setup(session)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = place_buy(engine, session, account, symbol=ZERO_DTE, qty="1")
    assert order.status == "pending"


def test_zero_dte_rejected_after_close(session):
    md, engine, account = setup(session, now=AFTER_CLOSE)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = place_buy(engine, session, account, symbol=ZERO_DTE, qty="1")
    assert order.status == "rejected"
    assert order.reject_reason == "contract expired"


def test_stock_orders_unchanged(session):
    md, engine, account = setup(session, commission="1")
    md.set_quote("SPY", "100")
    order = place_buy(engine, session, account, symbol="SPY", qty="5")
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("501")  # multiplier 1
    engine.apply_fill(session, order, Decimal("100"))
    assert account.cash == Decimal("100000") - Decimal("501")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_engine_options.py -v`
Expected: FAIL — reservation asserts wrong (no ×100), `contract expired` guard missing, `commission=` unexpected keyword.

- [ ] **Step 3: Implement.** In `backend/app/engine/engine.py`:

Replace the imports block at the top:

```python
from __future__ import annotations

from datetime import time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.assets import contract_multiplier, is_crypto_symbol, is_option_symbol, parse_occ
from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.models import Account, Fill, Order, Position
from app.timeutil import utcnow

NY_TZ = ZoneInfo("America/New_York")
```

In `place_order`, insert the expiry guard between the limit-price validation and the quote fetch (after the `if order_type == "limit" and ...: return self.reject_order(...)` block, before `try: quote = ...`):

```python
        if is_option_symbol(order.symbol):
            # User/API placement only; the settlement job constructs orders
            # directly and never runs this guard.
            now_ny = self.now_fn().replace(tzinfo=timezone.utc).astimezone(NY_TZ)
            expiry = parse_occ(order.symbol).expiry
            if expiry < now_ny.date() or (expiry == now_ny.date()
                                          and now_ny.time() >= time(16, 0)):
                return self.reject_order(session, order, "contract expired")
```

Replace the buy-reservation block:

```python
        if side == "buy":
            if order_type == "limit":
                est_price = limit_price
            elif quote.ask is not None:
                est_price = quote.ask  # options reserve at the ask (fill price)
            else:
                est_price = quote.price
            cost = (est_price * qty * contract_multiplier(order.symbol)
                    + account.commission)
            available = self.available_cash(session, account)
            if cost > available:
                return self.reject_order(
                    session, order,
                    f"insufficient cash: need {cost}, available {available}")
            order.reserved_cash = cost
```

Replace `apply_fill`:

```python
    def apply_fill(self, session, order: Order, price: Decimal,
                   commission: Decimal | None = None) -> Fill:
        if order.status != "pending":
            raise InvalidOrderState(f"cannot fill order in status {order.status}")
        account = session.get(Account, order.account_id)
        if commission is None:
            commission = account.commission
        mult = contract_multiplier(order.symbol)
        fill = Fill(order_id=order.id, price=price, qty=order.qty,
                    commission=commission, filled_at=self.now_fn())
        pos = self._get_or_create_position(session, order.account_id, order.symbol)
        if order.side == "buy":
            account.cash -= price * order.qty * mult + commission
            new_qty = pos.qty + order.qty
            pos.avg_cost = ((pos.avg_cost * pos.qty + price * order.qty) / new_qty
                            ).quantize(Decimal("0.0001"))
            pos.qty = new_qty
        else:
            pnl = ((price - pos.avg_cost) * order.qty * mult - commission
                   ).quantize(Decimal("0.0001"))
            fill.realized_pnl = pnl
            pos.realized_pnl += pnl
            pos.qty -= order.qty
            account.cash += price * order.qty * mult - commission
        order.status = "filled"
        session.add(fill)
        session.flush()
        return fill
```

(`avg_cost` stays per-share/per-unit-premium; only cash and P&L multiply.)

- [ ] **Step 4: Run to verify pass, plus engine regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/test_engine_options.py tests/test_engine_placement.py tests/test_engine_fills.py tests/test_engine_clock.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/engine/engine.py tests/test_engine_options.py && git commit -m "feat: engine contract multiplier, ask reservation, expiry guard, commission override"
```

---

### Task 5: Valuation multiplier

**Files:**
- Modify: `backend/app/engine/valuation.py`
- Test: `backend/tests/test_valuation.py`

**Interfaces:**
- Consumes: `contract_multiplier` (Task 1).
- Produces: `position_values` multiplies `market_value` AND `unrealized_pnl` by `contract_multiplier(pos.symbol)`; `account_equity`, `take_snapshots`, and the accounts API inherit. `last_price` and `avg_cost` stay per-share.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_valuation.py`:

```python
def test_option_positions_value_at_mid_times_100(session):
    from decimal import Decimal

    from app.engine.valuation import account_equity, position_values
    from app.models import Position
    from tests.factories import make_account
    from tests.fakes import FakeMarketData

    account = make_account(session, cash="10000")
    session.add(Position(account_id=account.id, symbol="SPY260821C00625000",
                         qty=Decimal("2"), avg_cost=Decimal("5"),
                         realized_pnl=Decimal("0")))
    session.flush()
    md = FakeMarketData()
    md.set_option_quote("SPY260821C00625000", bid="5.90", ask="6.10")  # mid 6
    values = position_values(session, account, lambda s: md)
    pv = values[0]
    assert pv.last_price == Decimal("6.0000")          # per-share mid
    assert pv.market_value == Decimal("1200.0000")     # 6 * 2 * 100
    assert pv.unrealized_pnl == Decimal("200.0000")    # (6-5) * 2 * 100
    assert account_equity(session, account, lambda s: md) == Decimal("11200.0000")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_valuation.py -v`
Expected: new test FAILS (`market_value` == 12, no ×100); existing tests pass.

- [ ] **Step 3: Implement.** In `backend/app/engine/valuation.py`, add the import and multiply. Import line (top of file, with the other `app.` imports):

```python
from app.assets import contract_multiplier
```

Replace the loop body in `position_values`:

```python
    for pos in positions:
        quote = market_data_for_symbol(pos.symbol).get_quote(pos.symbol)
        mult = contract_multiplier(pos.symbol)
        out.append(PositionValue(
            symbol=pos.symbol, qty=pos.qty, avg_cost=pos.avg_cost,
            last_price=quote.price, market_value=quote.price * pos.qty * mult,
            unrealized_pnl=(quote.price - pos.avg_cost) * pos.qty * mult,
            realized_pnl=pos.realized_pnl))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_valuation.py tests/test_replay_valuation.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/engine/valuation.py tests/test_valuation.py && git commit -m "feat: contract multiplier in position valuation"
```

---

### Task 6: `OptionsSimAdapter` — cross-the-spread fills

**Files:**
- Create: `backend/app/engine/options_sim_adapter.py`
- Test: `backend/tests/test_options_sim.py`

**Interfaces:**
- Consumes: `TradingEngine` incl. `apply_fill(..., commission=None)` (Task 4), `contract_multiplier`, `parse_occ` (Task 1), `Quote.bid/.ask` (Task 2), `ny_date` from `app.engine.valuation`.
- Produces: `OptionsSimAdapter(engine, market_data, calendar, now_fn=utcnow, owns_order=None)` with `place_order(session, **kwargs)`, `cancel_order(session, order_id)`, `process_pending(session, now=None)` — same contract as `SimAdapter` so Task 7 can wire it into jobs and routing.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_options_sim.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.engine.engine import TradingEngine
from app.engine.options_sim_adapter import OptionsSimAdapter
from app.models import Fill, Order
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData

OCC = "SPY260821C00625000"        # expires 2026-08-21
ZERO_DTE = "SPY260701C00625000"   # expires 2026-07-01 (Clock default day)


def setup(session, cash="100000", commission="0", open_=True):
    md = FakeMarketData()
    md.set_option_quote(OCC, bid="4.90", ask="5.10")
    cal = FakeCalendar(open_=open_)
    clock = Clock()
    engine = TradingEngine(md, now_fn=clock)
    adapter = OptionsSimAdapter(engine, md, cal, now_fn=clock)
    account = make_account(session, cash=cash, commission=commission)
    return md, cal, clock, adapter, account


def buy(adapter, session, account, symbol=OCC, qty="1", order_type="market",
        limit_price=None, tif="day"):
    return adapter.place_order(session, account_id=account.id, symbol=symbol,
                               side="buy", order_type=order_type,
                               qty=Decimal(qty), tif=tif, limit_price=limit_price)


def sell(adapter, session, account, symbol=OCC, qty="1", order_type="market",
         limit_price=None):
    return adapter.place_order(session, account_id=account.id, symbol=symbol,
                               side="sell", order_type=order_type,
                               qty=Decimal(qty), limit_price=limit_price)


def fill_price(session, order):
    return session.scalar(select(Fill.price).where(Fill.order_id == order.id))


def test_market_buy_fills_at_ask_when_open(session):
    md, cal, clock, adapter, account = setup(session)
    order = buy(adapter, session, account)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.10")
    assert account.cash == Decimal("100000") - Decimal("510")


def test_market_sell_fills_at_bid(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="2")
    order = sell(adapter, session, account, qty="1")
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("4.90")


def test_market_order_pends_while_closed_then_fills_at_open(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account)
    assert order.status == "pending"
    cal.open = True
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.10")


def test_limit_buy_fills_at_ask_once_crossed(session):
    md, cal, clock, adapter, account = setup(session)
    order = buy(adapter, session, account, order_type="limit",
                limit_price=Decimal("5.00"))
    assert order.status == "pending"  # ask 5.10 > limit
    md.set_option_quote(OCC, bid="4.80", ask="4.95")
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("4.95")  # ask, not limit


def test_limit_sell_fills_at_bid_once_crossed(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="1")
    order = sell(adapter, session, account, order_type="limit",
                 limit_price=Decimal("5.00"))
    assert order.status == "pending"  # bid 4.90 < limit
    md.set_option_quote(OCC, bid="5.20", ask="5.40")
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "filled"
    assert fill_price(session, order) == Decimal("5.20")


def test_buy_with_no_ask_stays_pending(session):
    md, cal, clock, adapter, account = setup(session)
    md.set_option_quote(OCC, bid="4.90", last="5.00")  # no ask
    order = buy(adapter, session, account)
    assert order.status == "pending"
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "pending"


def test_sell_with_zero_bid_stays_pending(session):
    md, cal, clock, adapter, account = setup(session)
    buy(adapter, session, account, qty="1")
    md.set_option_quote(OCC, bid="0", ask="5.10", last="5.00")
    order = sell(adapter, session, account, qty="1")
    assert order.status == "pending"
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "pending"


def test_at_fill_recheck_prices_at_ask_not_mid(session):
    # cash 500; queued while closed at ask 4.90 (reserve 490). Overnight the
    # spread widens to bid 4.40 / ask 5.60: mid 5.00 -> 500 would PASS the
    # recheck, but the fill is at ask 5.60 -> 560. Must reject, cash intact.
    md, cal, clock, adapter, account = setup(session, cash="500", open_=False)
    md.set_option_quote(OCC, bid="4.70", ask="4.90")  # reserve at ask = 490
    order = buy(adapter, session, account, tif="gtc")
    assert order.status == "pending" and order.reserved_cash == Decimal("490")
    md.set_option_quote(OCC, bid="4.40", ask="5.60")
    cal.open = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert "insufficient cash at fill" in order.reject_reason
    assert account.cash == Decimal("500")


def test_dead_contract_expires_instead_of_filling(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = buy(adapter, session, account, symbol=ZERO_DTE, tif="gtc")
    assert order.status == "pending"
    engine_available_before = account.cash  # reserved but not spent
    clock.now = datetime(2026, 7, 2, 14, 0)  # next day; quote still crossed
    cal.open = True
    adapter.process_pending(session)
    session.refresh(order)
    assert order.status == "expired"
    assert order.reject_reason == "contract expired"
    assert account.cash == engine_available_before
    assert session.scalar(select(Fill).where(Fill.order_id == order.id)) is None


def test_day_tif_expires_via_calendar_and_releases_reserved_cash(session):
    # No session.refresh here: the calendar stays closed, so process_pending
    # early-returns before its flush — refresh would revert the in-memory
    # expiry back to "pending" (same style as tests/test_sim_limit_expiry.py).
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account, tif="day")
    assert order.status == "pending"
    cal.expiry_at = datetime(2026, 7, 1, 20, 0)
    clock.now = datetime(2026, 7, 1, 21, 0)
    adapter.process_pending(session)
    assert order.status == "expired"
    assert adapter.engine.available_cash(session, account) == Decimal("100000")


def test_market_data_error_rejects_market_order(session):
    md, cal, clock, adapter, account = setup(session, open_=False)
    order = buy(adapter, session, account)
    md.fail = True
    cal.open = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_sim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.options_sim_adapter'`

- [ ] **Step 3: Implement** — create `backend/app/engine/options_sim_adapter.py`:

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.assets import contract_multiplier, parse_occ
from app.engine.engine import TradingEngine
from app.engine.valuation import ny_date
from app.marketdata.base import MarketDataError
from app.models import Account, Order
from app.timeutil import utcnow


class OptionsSimAdapter:
    """Simulated options execution: cross-the-spread fills.

    Market buys fill at the ask, market sells at the bid. Limit buys fill at
    the ask once ask <= limit; limit sells at the bid once bid >= limit.
    One-sided or zero-bid quotes never fabricate a fill — the order stays
    pending until the side exists. Dead contracts (expiry < today NY) are
    expired before any fill attempt, so a stale after-hours snapshot can
    never fill them even if the 16:05 settlement job missed a day.
    """

    def __init__(self, engine: TradingEngine, market_data, calendar,
                 now_fn=utcnow, owns_order=None):
        self.engine = engine
        self.market_data = market_data
        self.calendar = calendar
        self.now_fn = now_fn
        self.owns_order = owns_order or (lambda order: True)

    def place_order(self, session, **kwargs) -> Order:
        order = self.engine.place_order(session, **kwargs)
        if order.status != "pending":
            return order
        if order.order_type == "market" and self.calendar.is_open(self.now_fn()):
            self._fill_market(session, order)
        return order

    def cancel_order(self, session, order_id: int) -> Order:
        return self.engine.cancel_order(session, order_id)

    def process_pending(self, session, now: datetime | None = None) -> None:
        now = now or self.now_fn()
        today = ny_date(now)
        pending = session.scalars(
            select(Order).where(Order.status == "pending")).all()
        pending = [o for o in pending if self.owns_order(o)]

        for order in pending:
            if parse_occ(order.symbol).expiry < today:
                self.engine.expire_order(session, order)
                order.reject_reason = "contract expired"
            elif order.tif == "day" and now >= self.calendar.expiry_time(order.placed_at):
                self.engine.expire_order(session, order)

        if not self.calendar.is_open(now):
            return

        # Flush local expiries so refresh() below re-reads them instead of
        # clobbering them back to "pending" (same pattern as SimAdapter).
        session.flush()

        for order in pending:
            session.refresh(order)
            if order.status != "pending":
                continue
            if order.order_type == "market":
                self._fill_market(session, order)
            else:
                self._check_limit(session, order)

    def _fill_market(self, session, order: Order) -> None:
        try:
            quote = self.market_data.get_quote(order.symbol)
        except MarketDataError:
            self.engine.reject_order(session, order, "market data unavailable")
            return
        if order.side == "buy":
            if quote.ask is None:
                return  # no ask: stay pending, never fabricate a fill
            account = session.get(Account, order.account_id)
            # Recheck at the ACTUAL fill price (ask), never quote.price (mid):
            # checking at mid while debiting at ask lets cash go negative.
            cost = (quote.ask * order.qty * contract_multiplier(order.symbol)
                    + account.commission)
            spendable = (self.engine.available_cash(session, account)
                         + order.reserved_cash)
            if cost > spendable:
                self.engine.reject_order(
                    session, order,
                    f"insufficient cash at fill: need {cost}, available {spendable}")
                return
            self.engine.apply_fill(session, order, quote.ask)
        else:
            if quote.bid is None:
                return  # no bid: stay pending
            self.engine.apply_fill(session, order, quote.bid)

    def _check_limit(self, session, order: Order) -> None:
        try:
            quote = self.market_data.get_quote(order.symbol)
        except MarketDataError:
            return  # pending limit orders wait for the next successful check
        if order.side == "buy":
            if quote.ask is not None and quote.ask <= order.limit_price:
                self.engine.apply_fill(session, order, quote.ask)
        else:
            if quote.bid is not None and quote.bid >= order.limit_price:
                self.engine.apply_fill(session, order, quote.bid)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_sim.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/engine/options_sim_adapter.py tests/test_options_sim.py && git commit -m "feat: OptionsSimAdapter with cross-the-spread fills"
```

---

### Task 7: Routing — AppDeps fields, four routing copies, owns_order partition, jobs tick, conftest

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/jobs.py` (run_process_pending only)
- Modify: `backend/app/api/orders.py` (503 copy)
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_jobs.py` (fixture + one test)
- Test: `backend/tests/test_deps_routing.py`

**Interfaces:**
- Consumes: `OptionsSimAdapter` (Task 6), `AlpacaOptionsData`/`OptionsDataService` (Task 3), `is_option_symbol` (Task 1), `FakeOptionsData` (Task 2).
- Produces: `AppDeps` gains `options_market_data: object | None = None`, `options_engine: TradingEngine | None = None`, `options_execution: "OptionsSimAdapter | None" = None` (all default None so bare constructions keep working); option branch first in ALL FOUR routing copies; stock sim `owns_order` excludes options; `jobs.run_process_pending` ticks `deps.options_execution` when not None; conftest `client` exposes `c.options_fake_md` (a `FakeOptionsData`).

- [ ] **Step 1: Write the failing tests.** Append to `backend/tests/test_deps_routing.py`:

```python
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
```

Append to `backend/tests/test_jobs.py`:

```python
def test_run_process_pending_fills_queued_option_order(deps, session_factory):
    from sqlalchemy import select

    from app.models import Account, Fill, Order

    with session_factory() as s:
        account = s.scalar(select(Account))
        order = deps.options_execution.place_order(
            s, account_id=account.id, symbol="SPY260821C00625000",
            side="buy", order_type="market", qty=Decimal("1"), tif="gtc")
        s.commit()
        assert order.status == "pending"  # options calendar starts closed
        order_id = order.id

    deps.options_calendar_for_test.open = True
    run_process_pending(deps)

    with session_factory() as s:
        order = s.get(Order, order_id)
        assert order.status == "filled"
        fill = s.scalar(select(Fill).where(Fill.order_id == order_id))
        assert fill.price == Decimal("5.10")  # fills at the ask
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deps_routing.py tests/test_jobs.py -v`
Expected: FAIL — `AppDeps` has no `options_execution`; jobs fixture lacks the options stack.

- [ ] **Step 3: Implement.**

In `backend/app/main.py`:

Add imports:

```python
from app.assets import is_crypto_symbol, is_option_symbol
from app.engine.options_sim_adapter import OptionsSimAdapter
from app.marketdata.alpaca_options import AlpacaOptionsData, OptionsDataService
```

(replacing the existing `from app.assets import is_crypto_symbol` line).

Add fields to `AppDeps` (after `crypto_execution`, before `live_execution`):

```python
    options_market_data: object | None = None
    options_engine: TradingEngine | None = None
    options_execution: OptionsSimAdapter | None = None
```

Replace the two `AppDeps` routing methods:

```python
    def execution_for_symbol(self, symbol: str):
        if is_option_symbol(symbol):
            return self.options_execution
        return self.crypto_execution if is_crypto_symbol(symbol) else self.execution

    def market_data_for_symbol(self, symbol: str):
        if is_option_symbol(symbol):
            return self.options_market_data
        return self.crypto_market_data if is_crypto_symbol(symbol) else self.market_data
```

In `build_deps`, tighten the stock sim's `owns_order`:

```python
    execution = SimAdapter(engine, market_data, calendar,
                           owns_order=lambda o: o.account.mode == "paper"
                           and not is_crypto_symbol(o.symbol)
                           and not is_option_symbol(o.symbol))
```

After the crypto block, build the options pipeline unconditionally (empty keys degrade to `MarketDataError` at request time, which every consumer already handles):

```python
    options_market_data = OptionsDataService(AlpacaOptionsData(
        settings.alpaca_key_id, settings.alpaca_secret,
        feed=settings.alpaca_options_feed,
        contracts_base=settings.alpaca_contracts_base))
    options_engine = TradingEngine(options_market_data)
    options_execution = OptionsSimAdapter(
        options_engine, options_market_data, calendar,
        owns_order=lambda o: o.account.mode == "paper"
        and is_option_symbol(o.symbol))
```

Replace the two closures passed to `StrategyRunner`:

```python
    def execution_for_symbol(symbol: str):
        if is_option_symbol(symbol):
            return options_execution
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        if is_option_symbol(symbol):
            return options_market_data
        return crypto_market_data if is_crypto_symbol(symbol) else market_data
```

Add the three fields to the `AppDeps(...)` construction at the end of `build_deps`:

```python
                   options_market_data=options_market_data,
                   options_engine=options_engine,
                   options_execution=options_execution,
```

In `backend/app/jobs.py`, extend `run_process_pending`:

```python
def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        deps.crypto_execution.process_pending(session)
        if deps.options_execution is not None:
            deps.options_execution.process_pending(session)
        if deps.live_execution is not None:
            deps.live_execution.process_pending(session)
        session.commit()
```

In `backend/app/api/orders.py`, make the two `execution is None` messages mode-aware (both occurrences — `place_order` and `cancel_order`):

```python
    if execution is None:
        raise HTTPException(503, "live trading not configured"
                            if account.mode == "live"
                            else "options trading not configured")
```

(in `cancel_order` the account is `order.account`.)

In `backend/tests/conftest.py`, extend the `client` fixture. Import `FakeOptionsData`, `Clock`, and `OptionsSimAdapter`:

```python
from app.assets import is_crypto_symbol, is_option_symbol
from app.engine.options_sim_adapter import OptionsSimAdapter
from tests.fakes import Clock, FakeCalendar, FakeMarketData, FakeOptionsData
```

After the crypto block add (the pinned `Clock()` — 2026-07-01 — keeps the fixed
`SPY260821C00625000` contract permanently unexpired, so these tests don't rot
when the real date passes 2026-08-21):

```python
    options_fake_md = FakeOptionsData()
    options_fake_md.set_option_quote("SPY260821C00625000", bid="4.90", ask="5.10")
    options_fake_cal = FakeCalendar(open_=True)
    options_clock = Clock()
    options_engine = TradingEngine(options_fake_md, now_fn=options_clock)
    options_execution = OptionsSimAdapter(options_engine, options_fake_md,
                                          options_fake_cal, now_fn=options_clock,
                                          owns_order=lambda o: o.account.mode == "paper"
                                          and is_option_symbol(o.symbol))
```

Tighten the stock `execution`'s `owns_order` in the fixture to match `build_deps`:

```python
    execution = SimAdapter(engine, fake_md, fake_cal,
                           owns_order=lambda o: o.account.mode == "paper"
                           and not is_crypto_symbol(o.symbol)
                           and not is_option_symbol(o.symbol))
```

Add to the `AppDeps(...)` construction:

```python
                   options_market_data=options_fake_md,
                   options_engine=options_engine,
                   options_execution=options_execution,
```

and expose on the client:

```python
    c.options_fake_md = options_fake_md
    c.options_fake_cal = options_fake_cal
```

In `backend/tests/test_jobs.py`, extend the `deps` fixture the same way (after the crypto block):

```python
    options_md = FakeOptionsData()
    options_md.set_option_quote("SPY260821C00625000", bid="4.90", ask="5.10")
    options_cal = FakeCalendar(open_=False)
    options_clock = Clock()  # pinned 2026-07-01: contract never expires under test
    options_engine = TradingEngine(options_md, now_fn=options_clock)
    options_execution = OptionsSimAdapter(options_engine, options_md, options_cal,
                                          now_fn=options_clock,
                                          owns_order=lambda o: o.account.mode == "paper"
                                          and is_option_symbol(o.symbol))
```

with imports `from app.assets import is_crypto_symbol, is_option_symbol`, `from app.engine.options_sim_adapter import OptionsSimAdapter`, `from tests.fakes import Clock, FakeCalendar, FakeMarketData, FakeOptionsData`, and replace the fixture's final `return AppDeps(...)` statement with:

```python
    deps_obj = AppDeps(settings=Settings(), session_factory=session_factory,
                       market_data=md, calendar=cal, engine=engine,
                       execution=execution, runner=runner,
                       crypto_market_data=crypto_md, crypto_calendar=crypto_cal,
                       crypto_engine=crypto_engine, crypto_execution=crypto_execution,
                       options_market_data=options_md,
                       options_engine=options_engine,
                       options_execution=options_execution)
    deps_obj.options_calendar_for_test = options_cal
    return deps_obj
```

(Assign the extra attribute after construction — `AppDeps` is a plain dataclass, so attribute assignment works.)

- [ ] **Step 4: Run to verify pass, plus regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/test_deps_routing.py tests/test_jobs.py tests/test_api_accounts_orders.py tests/test_api_crypto.py tests/test_sim_market.py tests/test_sim_limit_expiry.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/main.py app/jobs.py app/api/orders.py tests/conftest.py tests/test_jobs.py tests/test_deps_routing.py && git commit -m "feat: options pipeline routing, owns_order partition, and process_pending tick"
```

---

### Task 8: Expiry settlement job

**Files:**
- Create: `backend/app/engine/options_expiry.py`
- Modify: `backend/app/jobs.py`
- Test: `backend/tests/test_options_expiry.py`, plus scheduler assert in `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: `apply_fill(..., commission=Decimal("0"))` (Task 4), `expire_order`, `is_option_symbol`/`parse_occ` (Task 1), `ny_date` from `app.engine.valuation`.
- Produces: `settle_expired_options(session, *, engine, stock_market_data, now=None)`; `jobs.run_option_expiry(deps)`; scheduler job id `option_expiry`, cron 16:05 America/New_York mon-fri. Settlement orders carry idempotency key `settle:{account_id}:{symbol}` and are created directly (NEVER via `place_order`).

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_options_expiry.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.engine.engine import TradingEngine
from app.engine.options_expiry import settle_expired_options
from app.models import Fill, Order, Position
from tests.factories import make_account
from tests.fakes import Clock, FakeMarketData

# Settlement runs after the close on 2026-07-01 (a Wednesday).
NOW = datetime(2026, 7, 1, 20, 5)  # 16:05 NY
ITM_CALL = "SPY260701C00600000"    # strike 600
ITM_PUT = "SPY260701P00650000"     # strike 650
OTM_CALL = "SPY260701C00650000"    # strike 650
LIVE_CONTRACT = "SPY260821C00600000"  # expires later


def setup(session, cash="10000", commission="0", spy_price="625"):
    md = FakeMarketData()
    if spy_price is not None:
        md.set_quote("SPY", spy_price)
    engine = TradingEngine(md, now_fn=Clock(NOW))
    account = make_account(session, cash=cash, commission=commission)
    return md, engine, account


def add_position(session, account, symbol, qty="2", avg_cost="5"):
    pos = Position(account_id=account.id, symbol=symbol, qty=Decimal(qty),
                   avg_cost=Decimal(avg_cost), realized_pnl=Decimal("0"))
    session.add(pos)
    session.flush()
    return pos


def settle(session, engine, md):
    settle_expired_options(session, engine=engine, stock_market_data=md, now=NOW)


def test_itm_call_settles_at_intrinsic(session):
    md, engine, account = setup(session)
    pos = add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000") + 25 * 2 * 100  # intrinsic 625-600
    order = session.scalar(select(Order).where(
        Order.idempotency_key == f"settle:{account.id}:{ITM_CALL}"))
    assert order.status == "filled" and order.side == "sell"
    fill = session.scalar(select(Fill).where(Fill.order_id == order.id))
    assert fill.price == Decimal("25")
    assert fill.commission == Decimal("0")
    assert fill.realized_pnl == Decimal("4000.0000")  # (25-5)*2*100


def test_itm_put_settles_at_intrinsic(session):
    md, engine, account = setup(session)
    add_position(session, account, ITM_PUT)
    settle(session, engine, md)
    assert account.cash == Decimal("10000") + 25 * 2 * 100  # 650-625


def test_otm_settles_at_zero_and_moves_no_cash(session):
    md, engine, account = setup(session, commission="1")
    pos = add_position(session, account, OTM_CALL)
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000")  # exactly zero cash movement
    fill = session.scalar(select(Fill).join(Order, Fill.order_id == Order.id)
                          .where(Order.symbol == OTM_CALL))
    assert fill.price == Decimal("0")
    assert fill.realized_pnl == Decimal("-1000.0000")  # (0-5)*2*100, no commission


def test_pending_sell_released_before_settlement(session):
    md, engine, account = setup(session)
    pos = add_position(session, account, ITM_CALL)
    gtc = Order(account_id=account.id, symbol=ITM_CALL, side="sell",
                order_type="limit", tif="gtc", qty=Decimal("2"),
                limit_price=Decimal("30"), placed_at=NOW)
    session.add(gtc)
    session.flush()
    settle(session, engine, md)
    session.refresh(gtc)
    assert gtc.status == "expired"
    assert gtc.reject_reason == "contract expired"
    assert pos.qty == 0  # settled in the same run despite the open sell


def test_pending_buy_releases_reserved_cash(session):
    md, engine, account = setup(session)
    dead_buy = Order(account_id=account.id, symbol=OTM_CALL, side="buy",
                     order_type="limit", tif="gtc", qty=Decimal("1"),
                     limit_price=Decimal("5"), reserved_cash=Decimal("500"),
                     placed_at=NOW)
    session.add(dead_buy)
    session.flush()
    settle(session, engine, md)
    session.refresh(dead_buy)
    assert dead_buy.status == "expired"
    assert engine.available_cash(session, account) == Decimal("10000")


def test_rerun_is_noop(session):
    md, engine, account = setup(session)
    add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    cash_after = account.cash
    settle(session, engine, md)
    assert account.cash == cash_after
    orders = session.scalars(select(Order).where(
        Order.symbol == ITM_CALL)).all()
    assert len(orders) == 1


def test_quote_failure_skips_then_later_run_settles(session):
    md, engine, account = setup(session, spy_price=None)  # no SPY quote yet
    pos = add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    assert pos.qty == 2  # skipped, will retry
    md.set_quote("SPY", "625")
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000") + Decimal("5000")


def test_unexpired_and_nonpaper_positions_untouched(session):
    md, engine, account = setup(session)
    live = make_account(session, name="live", mode="live")
    replay = make_account(session, name="replay:1:manual", mode="replay")
    add_position(session, account, LIVE_CONTRACT)      # not yet expired
    add_position(session, live, ITM_CALL)              # expired but live mode
    add_position(session, replay, ITM_CALL)            # expired but replay mode
    settle(session, engine, md)
    positions = session.scalars(select(Position)).all()
    assert all(p.qty == 2 for p in positions)
```

Append to `backend/tests/test_jobs.py`:

```python
def test_scheduler_registers_option_expiry_before_snapshots(deps):
    scheduler = build_scheduler(deps)
    job = scheduler.get_job("option_expiry")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "16" and fields["minute"] == "5"


def test_run_option_expiry_settles_expired_position(deps, session_factory):
    from sqlalchemy import select

    from app.jobs import run_option_expiry
    from app.models import Account, Position

    with session_factory() as s:
        account = s.scalar(select(Account))
        s.add(Position(account_id=account.id, symbol="SPY250620C00090000",
                       qty=Decimal("1"), avg_cost=Decimal("2"),
                       realized_pnl=Decimal("0")))
        cash_before = account.cash
        s.commit()

    run_option_expiry(deps)

    with session_factory() as s:
        account = s.scalar(select(Account))
        # SPY fake quote is 100, strike 90 -> intrinsic 10 * 1 * 100
        assert account.cash == cash_before + Decimal("1000")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_expiry.py tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.options_expiry'`; scheduler job missing.

- [ ] **Step 3: Implement.** Create `backend/app/engine/options_expiry.py`:

```python
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.assets import is_option_symbol, parse_occ
from app.engine.valuation import ny_date
from app.marketdata.base import MarketDataError
from app.models import Account, Order, Position
from app.timeutil import utcnow

log = logging.getLogger(__name__)


def settle_expired_options(session, *, engine, stock_market_data,
                           now: datetime | None = None) -> None:
    """Release dead orders, then cash-settle expired option positions.

    Paper accounts only (live settles at the broker; replay cannot hold
    options). NEVER goes through place_order: it would quote the dead
    contract, hit the expired-contract guard, and consume the settle: key on
    rejection — permanently poisoning settlement. Orders are constructed
    directly and filled via apply_fill with commission 0. Idempotent: a
    FILLED settle: order short-circuits re-runs; a quote failure skips the
    position and the next run (guard is expiry <= today) retries it.
    """
    now = now or utcnow()
    today = ny_date(now)
    accounts = session.scalars(
        select(Account).where(Account.mode == "paper")).all()
    for account in accounts:
        # 1) Release still-pending orders on dead contracts FIRST, so an
        #    open GTC sell can never block settling the position it covers,
        #    and dead buys release their reserved_cash.
        pending = session.scalars(select(Order).where(
            Order.account_id == account.id, Order.status == "pending")).all()
        for order in pending:
            if (is_option_symbol(order.symbol)
                    and parse_occ(order.symbol).expiry <= today):
                engine.expire_order(session, order)
                order.reject_reason = "contract expired"
        session.commit()

        # 2) Settle expired positions at intrinsic value of the underlying.
        positions = session.scalars(select(Position).where(
            Position.account_id == account.id)).all()
        for pos in positions:
            if pos.qty <= 0 or not is_option_symbol(pos.symbol):
                continue
            contract = parse_occ(pos.symbol)
            if contract.expiry > today:
                continue
            key = f"settle:{account.id}:{pos.symbol}"
            existing = session.scalar(select(Order).where(
                Order.account_id == account.id,
                Order.idempotency_key == key))
            if existing is not None:
                if existing.status != "filled":
                    log.error("settlement order %s in unexpected status %s; "
                              "skipping", key, existing.status)
                continue  # filled = already settled: the re-run no-op
            try:
                under = stock_market_data.get_quote(contract.underlying)
            except MarketDataError:
                log.warning("no quote for %s; retrying settlement of %s "
                            "next run", contract.underlying, pos.symbol)
                continue
            if contract.right == "call":
                intrinsic = max(Decimal("0"), under.price - contract.strike)
            else:
                intrinsic = max(Decimal("0"), contract.strike - under.price)
            order = Order(account_id=account.id, symbol=pos.symbol,
                          side="sell", order_type="market", tif="day",
                          qty=pos.qty, reserved_cash=Decimal("0"),
                          idempotency_key=key, placed_at=now)
            session.add(order)
            session.flush()
            engine.apply_fill(session, order, intrinsic,
                              commission=Decimal("0"))
            session.commit()  # per-position: nothing is ever half-settled
```

In `backend/app/jobs.py`, add the import and job:

```python
from app.engine.options_expiry import settle_expired_options
```

```python
def run_option_expiry(deps) -> None:
    with deps.session_factory() as session:
        settle_expired_options(session, engine=deps.engine,
                               stock_market_data=deps.market_data)
        session.commit()
```

and in `build_scheduler`, before the snapshots job:

```python
    scheduler.add_job(run_option_expiry,
                      CronTrigger(hour=16, minute=5, day_of_week="mon-fri",
                                  timezone=NY_TZ),
                      args=[deps], id="option_expiry")
```

(16:05 NY runs before the 16:10 snapshots, so expiry-day snapshots capture settled cash instead of quoting dead contracts.)

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_options_expiry.py tests/test_jobs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/engine/options_expiry.py app/jobs.py tests/test_options_expiry.py tests/test_jobs.py && git commit -m "feat: option expiry cash-settlement job"
```

---

### Task 9: API endpoints + live/strategy/replay fences

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/market.py`
- Modify: `backend/app/engine/alpaca_live_adapter.py`
- Modify: `backend/app/strategy/base.py`
- Modify: `backend/app/replay/service.py`
- Test: `backend/tests/test_api_options.py`

**Interfaces:**
- Consumes: `OptionsDataService.get_expirations/get_chain` (Task 3, via `deps.options_market_data` wired in Task 7), `OptionChainRow` (Task 2), `is_option_symbol` (Task 1).
- Produces: `GET /api/market/options/{underlying}/expirations` → `OptionExpirationsOut{underlying, expirations: list[date]}`; `GET /api/market/options/{underlying}/chain?expiry=YYYY-MM-DD` → `OptionChainOut{underlying, expiry, calls, puts}` with `OptionChainRowOut` rows; `QuoteOut` gains `bid`/`ask` (`Money | None`); live/strategy/replay fences with the exact copy strings from Global Constraints.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_api_options.py`:

```python
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

OCC = "SPY260821C00625000"


def chain_row(**over):
    from app.marketdata.base import OptionChainRow
    base = dict(symbol=OCC, strike=Decimal("625"), right="call",
                bid=Decimal("4.9"), ask=Decimal("5.1"), last=Decimal("5.05"),
                open_interest=Decimal("120"), iv=Decimal("0.172"),
                delta=Decimal("0.55"), gamma=Decimal("0.01"),
                theta=Decimal("-0.12"), vega=Decimal("0.35"))
    base.update(over)
    return OptionChainRow(**base)


def test_expirations_endpoint(client):
    client.options_fake_md.set_expirations("SPY", [date(2026, 8, 21), date(2026, 9, 18)])
    r = client.get("/api/market/options/SPY/expirations")
    assert r.status_code == 200
    assert r.json() == {"underlying": "SPY",
                        "expirations": ["2026-08-21", "2026-09-18"]}


def test_expirations_unknown_underlying_404(client):
    r = client.get("/api/market/options/XXXX/expirations")
    assert r.status_code == 404
    assert r.json()["detail"] == "no options listed for symbol"


def test_expirations_provider_down_503(client):
    client.options_fake_md.set_expirations("SPY", [date(2026, 8, 21)])
    client.options_fake_md.fail = True
    r = client.get("/api/market/options/SPY/expirations")
    assert r.status_code == 503
    client.options_fake_md.fail = False


def test_chain_endpoint(client):
    put = chain_row(symbol="SPY260821P00600000", strike=Decimal("600"),
                    right="put", delta=Decimal("-0.4"), last=None, iv=None)
    client.options_fake_md.set_chain("SPY", date(2026, 8, 21),
                                     calls=[chain_row()], puts=[put])
    r = client.get("/api/market/options/SPY/chain?expiry=2026-08-21")
    assert r.status_code == 200
    body = r.json()
    assert body["underlying"] == "SPY" and body["expiry"] == "2026-08-21"
    call = body["calls"][0]
    assert call["symbol"] == OCC
    assert call["strike"] == "625" and call["bid"] == "4.9" and call["ask"] == "5.1"
    assert call["open_interest"] == "120" and call["theta"] == "-0.12"
    assert body["puts"][0]["last"] is None and body["puts"][0]["iv"] is None


def test_quote_endpoint_returns_bid_ask_for_options(client):
    r = client.get(f"/api/market/quote/{OCC}")
    assert r.status_code == 200
    body = r.json()
    assert body["bid"] == "4.9" and body["ask"] == "5.1"
    assert body["price"] == "5"


def test_quote_endpoint_stock_has_null_bid_ask(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    body = r.json()
    assert body["bid"] is None and body["ask"] is None


def test_bars_endpoint_on_option_is_503_not_500(client):
    r = client.get(f"/api/market/bars/{OCC}")
    assert r.status_code == 503
    assert r.json()["detail"] == "market data unavailable"


def test_post_option_order_fills_at_ask_times_100(client):
    accounts = client.get("/api/accounts").json()
    account_id = accounts[0]["id"]
    cash_before = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    r = client.post(f"/api/accounts/{account_id}/orders", json={
        "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
    assert r.status_code == 201
    assert r.json()["status"] == "filled"
    cash_after = Decimal(client.get(f"/api/accounts/{account_id}").json()["cash"])
    assert cash_before - cash_after == Decimal("510")  # 5.10 ask * 1 * 100


def test_post_option_order_503_when_options_not_wired(client):
    deps = client.app.state.deps
    saved = deps.options_execution
    deps.options_execution = None
    try:
        accounts = client.get("/api/accounts").json()
        r = client.post(f"/api/accounts/{accounts[0]['id']}/orders", json={
            "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
        assert r.status_code == 503
        assert r.json()["detail"] == "options trading not configured"
    finally:
        deps.options_execution = saved


def test_live_adapter_rejects_options(session):
    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.engine.engine import TradingEngine
    from tests.factories import make_account
    from tests.fakes import FakeMarketData

    md = FakeMarketData()
    md.set_quote(OCC, "5.05")  # yfinance CAN resolve OCC tickers: fence must hit
    engine = TradingEngine(md)
    adapter = AlpacaLiveAdapter(
        engine, "https://example.invalid", "k", "s",
        transport=httpx.MockTransport(
            lambda request: pytest.fail("must never reach the broker")))
    account = make_account(session, name="live", mode="live")
    order = adapter.place_order(session, account_id=account.id, symbol=OCC,
                                side="buy", order_type="market", qty=Decimal("1"))
    assert order.status == "rejected"
    assert order.reject_reason == "options not supported on live"


def test_strategy_context_rejects_options_before_any_engine_call(session):
    from app.strategy.base import Context
    from tests.factories import make_account
    from sqlalchemy import select
    from app.models import Order

    account = make_account(session)
    ctx = Context(session, account,
                  execution_for_symbol=lambda s: pytest.fail("must not route"),
                  market_data_for_symbol=lambda s: pytest.fail("must not route"))
    with pytest.raises(ValueError, match="strategies cannot trade options"):
        ctx.buy(OCC, Decimal("1"))
    assert session.scalars(select(Order)).all() == []


def test_replay_creation_rejects_options_before_any_fetch(session):
    from app.replay.service import ReplayCreationError, create_session

    with pytest.raises(ReplayCreationError,
                       match="options are not supported in replay"):
        # sources=None proves the fence fires before any history fetch.
        create_session(session, None, symbols=["SPY", OCC],
                       start_date=date(2026, 1, 5), strategies=[],
                       known_strategies=set(),
                       starting_cash=Decimal("100000"))


def test_replay_create_endpoint_returns_400_for_option_symbols(client):
    deps = client.app.state.deps
    saved = deps.replay_sources
    deps.replay_sources = object()  # fence fires before sources are touched
    try:
        r = client.post("/api/replay/sessions", json={
            "symbols": ["SPY", OCC], "start_date": "2026-01-05"})
        assert r.status_code == 400
        assert r.json()["detail"] == "options are not supported in replay"
    finally:
        deps.replay_sources = saved


def test_replay_placement_rejects_option_symbols(client):
    from tests.factories import (make_replay_account, make_replay_bar,
                                 make_replay_session)

    deps = client.app.state.deps
    with deps.session_factory() as s:
        row = make_replay_session(s, symbols=("SPY",))
        make_replay_bar(s, row.id, "SPY", "2024-06-03")
        acct = make_replay_account(s, row.id)
        s.commit()
        acct_id = acct.id
    # Options can never be in a session universe, so the strict
    # ReplayMarketData placement guard rejects the contract as unknown.
    r = client.post(f"/api/accounts/{acct_id}/orders", json={
        "symbol": OCC, "side": "buy", "order_type": "market", "qty": "1"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"].startswith("unknown symbol")


def test_strategy_context_option_data_access(session):
    from decimal import Decimal as D

    from app.marketdata.base import MarketDataError
    from app.strategy.base import Context
    from tests.factories import make_account
    from tests.fakes import FakeOptionsData

    od = FakeOptionsData()
    od.set_option_quote(OCC, bid="4.90", ask="5.10")
    account = make_account(session)
    ctx = Context(session, account,
                  execution_for_symbol=lambda s: pytest.fail("data-only test"),
                  market_data_for_symbol=lambda s: od)
    q = ctx.get_quote(OCC)  # read-only quote access is permitted
    assert q.ask == D("5.1")
    with pytest.raises(MarketDataError, match="bars not available"):
        ctx.get_bars(OCC)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_options.py -v`
Expected: FAIL — 404s on the new routes, no `bid` in QuoteOut, fences missing.

- [ ] **Step 3: Implement.**

In `backend/app/api/schemas.py`, extend `QuoteOut` and add the options schemas after `BarOut`:

```python
class QuoteOut(BaseModel):
    symbol: str
    price: Money
    as_of: datetime
    bid: Money | None = None
    ask: Money | None = None


class OptionChainRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    strike: Money
    right: str
    bid: Money | None
    ask: Money | None
    last: Money | None
    open_interest: Money | None
    iv: Money | None
    delta: Money | None
    gamma: Money | None
    theta: Money | None
    vega: Money | None


class OptionChainOut(BaseModel):
    underlying: str
    expiry: date
    calls: list[OptionChainRowOut]
    puts: list[OptionChainRowOut]


class OptionExpirationsOut(BaseModel):
    underlying: str
    expirations: list[date]
```

In `backend/app/api/market.py`, pass bid/ask through the quote endpoint and add the two routes:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_deps, require_auth
from app.api.schemas import (BarOut, OptionChainOut, OptionExpirationsOut,
                             QuoteOut)
from app.marketdata.base import MarketDataError, UnknownSymbolError

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/market/quote/{symbol}", response_model=QuoteOut)
def quote(symbol: str, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        q = deps.market_data_for_symbol(symbol).get_quote(symbol)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of,
                    bid=q.bid, ask=q.ask)


@router.get("/market/bars/{symbol}", response_model=list[BarOut])
def bars(symbol: str, limit: int = 200, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        return deps.market_data_for_symbol(symbol).get_bars(symbol, "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")


@router.get("/market/options/{underlying}/expirations",
            response_model=OptionExpirationsOut)
def option_expirations(underlying: str, deps=Depends(get_deps)):
    underlying = underlying.upper()
    if deps.options_market_data is None:
        raise HTTPException(503, "options data not configured")
    try:
        dates = deps.options_market_data.get_expirations(underlying)
    except UnknownSymbolError:
        raise HTTPException(404, "no options listed for symbol")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return OptionExpirationsOut(underlying=underlying, expirations=dates)


@router.get("/market/options/{underlying}/chain", response_model=OptionChainOut)
def option_chain(underlying: str, expiry: date, deps=Depends(get_deps)):
    underlying = underlying.upper()
    if deps.options_market_data is None:
        raise HTTPException(503, "options data not configured")
    try:
        calls, puts = deps.options_market_data.get_chain(underlying, expiry)
    except UnknownSymbolError:
        raise HTTPException(404, "no options listed for symbol")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return OptionChainOut(underlying=underlying, expiry=expiry,
                          calls=calls, puts=puts)
```

In `backend/app/engine/alpaca_live_adapter.py`, change the assets import to `from app.assets import is_crypto_symbol, is_option_symbol` and add after the crypto rejection in `place_order`:

```python
        if is_option_symbol(order.symbol):
            return self.engine.reject_order(
                session, order, "options not supported on live")
```

In `backend/app/strategy/base.py`, add `from app.assets import is_option_symbol` to the imports and guard `_place` as its first statement:

```python
    def _place(self, side, symbol, qty, limit_price, tif) -> Order:
        if is_option_symbol(symbol):
            # Fenced BEFORE any engine call: no Order row, no reservation.
            raise ValueError("strategies cannot trade options")
        order = self._execution_for_symbol(symbol).place_order(
```

(the rest of `_place` is unchanged; the runner's existing per-strategy `except Exception` records the error.)

In `backend/app/replay/service.py`, add `is_option_symbol` to the existing `from app.assets import is_crypto_symbol` import and insert into `create_session` immediately after the second `if not symbols: raise ...` (the dedupe block), before any history fetch:

```python
    if any(is_option_symbol(s) for s in symbols):
        raise ReplayCreationError("options are not supported in replay")
```

- [ ] **Step 4: Run to verify pass, then the full suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api_options.py -v`
Expected: all PASS

Run: `cd backend && .venv/bin/python -m pytest`
Expected: entire suite green.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/api/schemas.py app/api/market.py app/engine/alpaca_live_adapter.py app/strategy/base.py app/replay/service.py tests/test_api_options.py && git commit -m "feat: options chain API and live/strategy/replay fences"
```

