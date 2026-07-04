# Crypto Support — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add crypto trading (Coinbase/Binance data, a 24/7 calendar, fractional quantities) to the existing FastAPI paper-trading backend, per the approved spec at `docs/superpowers/specs/2026-07-04-crypto-phase-2-design.md`.

**Architecture:** A second, independent pipeline (`CryptoCalendar` + `MarketDataService([CoinbaseData, BinanceData])` + a second `TradingEngine`/`SimAdapter`) is built from the exact same classes the stock pipeline already uses. Every operation — order placement, cancellation, quotes, valuation, strategy trading — routes to the matching pipeline via one rule: a "-" in the symbol means crypto. There is no account-level or strategy-level asset-class flag; any account can hold both stock and crypto positions.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, httpx (already a dependency — no new packages needed for the two new providers).

## Global Constraints

- Money is `Decimal`, never float (unchanged from Phase 1).
- **Quantities are `Decimal`.** Stock symbols (no "-") must be whole numbers; crypto symbols (contain "-") may have up to 8 decimal places.
- **Symbol-shape routing is the single mechanism, used everywhere:** `app.assets.is_crypto_symbol(symbol)` — a "-" in the symbol means crypto. This is implemented exactly once and imported wherever routing is needed; never reimplement the check inline, including in tests.
- No `Account.asset_class` field, no auto-created crypto-only account, no `Strategy.asset_class` attribute. Accounts and strategies are asset-class-agnostic containers.
- Datetimes are naive UTC everywhere (unchanged).
- Rejections are stored orders with `status="rejected"` + a human-readable `reject_reason`, never exceptions (unchanged).
- TDD every task: failing test → implement → pass → commit. Commit prefixes `feat:`/`fix:`/`test:`/`chore:`.
- The full suite must stay green after every task: `cd backend && uv run pytest -q`.

## File Structure

```
backend/
  app/
    assets.py                    NEW — is_crypto_symbol(symbol) -> bool
    models.py                    MODIFY — Order/Fill/Position.qty: int -> Decimal
    engine/
      engine.py                  MODIFY — qty coercion + whole/fractional validation
      crypto_calendar.py         NEW — CryptoCalendar (always open, UTC-midnight expiry)
      valuation.py               MODIFY — market_data_for_symbol lookup, drop trading-day gate
    marketdata/
      coinbase.py                NEW — CoinbaseData (primary crypto provider)
      binance.py                 NEW — BinanceData (fallback crypto provider)
    strategy/
      base.py                    MODIFY — Context routes per-call by symbol
      runner.py                  MODIFY — StrategyRunner takes routing callables
    api/
      schemas.py                 MODIFY — Qty type; OrderIn/OrderOut/PositionOut/TradeOut.qty
      orders.py                  MODIFY — route place/cancel via deps.execution_for_symbol
      accounts.py                MODIFY — route valuation via deps.market_data_for_symbol
      market.py                  MODIFY — route quote/bars via deps.market_data_for_symbol
    main.py                      MODIFY — AppDeps gains crypto_* fields + routing methods
    jobs.py                      MODIFY — run_process_pending runs both stacks
  tests/
    test_assets.py                NEW
    test_crypto_calendar.py       NEW
    test_crypto_providers.py      NEW
    test_deps_routing.py          NEW
    test_api_crypto.py            NEW
    test_engine_placement.py      MODIFY — append crypto/fractional-qty tests
    test_valuation.py             MODIFY — lookup-function signature, mixed-account test
    test_jobs.py                  MODIFY — deps fixture gains crypto stack
    test_strategy_runner.py       MODIFY — routing-callable constructor, mixed-symbol test
    conftest.py                   MODIFY — client fixture gains crypto stack
```

---

### Task 1: Quantities widen to Decimal; symbol-shape routing helper; engine validation

**Files:**
- Create: `backend/app/assets.py`
- Modify: `backend/app/models.py`, `backend/app/engine/engine.py`
- Test: `backend/tests/test_assets.py` (new), `backend/tests/test_engine_placement.py` (append)

**Interfaces:**
- Consumes: nothing new — this is the foundational task.
- Produces: `is_crypto_symbol(symbol: str) -> bool` (imported by every later task that routes). `TradingEngine.place_order`'s `qty` parameter now accepts `Decimal` (and coerces plain `int`/numeric-string input, so existing callers passing bare `int` keep working unchanged). `Order.qty`, `Fill.qty`, `Position.qty` are now `Decimal` columns. `TradingEngine.available_qty` returns `Decimal`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_assets.py`:

```python
from app.assets import is_crypto_symbol


def test_dash_means_crypto():
    assert is_crypto_symbol("BTC-USD") is True


def test_no_dash_means_stock():
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("SPY") is False
```

Append to `backend/tests/test_engine_placement.py` (existing file, existing tests untouched):

```python
def test_crypto_buy_allows_fractional_qty(engine, session, md):
    md.set_quote("BTC-USD", "65000")
    acct = make_account(session, cash="100000")
    order = engine.place_order(session, account_id=acct.id, symbol="BTC-USD",
                               side="buy", order_type="market",
                               qty=Decimal("0.005"))
    assert order.status == "pending"
    assert order.qty == Decimal("0.005")


def test_crypto_buy_rejects_over_precise_qty(engine, session, md):
    md.set_quote("BTC-USD", "65000")
    acct = make_account(session, cash="100000")
    order = engine.place_order(session, account_id=acct.id, symbol="BTC-USD",
                               side="buy", order_type="market",
                               qty=Decimal("0.123456789"))
    assert order.status == "rejected"
    assert order.reject_reason == "quantity precision exceeds 8 decimal places"


def test_stock_buy_rejects_fractional_qty(engine, session):
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market",
                               qty=Decimal("1.5"))
    assert order.status == "rejected"
    assert order.reject_reason == "quantity must be a whole share count"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_assets.py tests/test_engine_placement.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.assets'`.

- [ ] **Step 3: Implement `app/assets.py`**

```python
"""Single source of truth for stock-vs-crypto routing.

A "-" in a symbol means crypto (e.g. "BTC-USD"); no dash means a stock
ticker (stock tickers never contain "-"). Every place that needs to route
between the stock and crypto pipelines imports this function rather than
reimplementing the check.
"""


def is_crypto_symbol(symbol: str) -> bool:
    return "-" in symbol
```

- [ ] **Step 4: Widen qty columns in `backend/app/models.py`**

Change line 48 (`Order.qty`):
```python
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal)
```

Change line 66 (`Fill.qty`):
```python
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal)
```

Change line 81 (`Position.qty`):
```python
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
```

- [ ] **Step 5: Update `backend/app/engine/engine.py`**

Add the import at the top (alongside the existing imports):
```python
from app.assets import is_crypto_symbol
```

Replace `place_order`'s signature and body (the whole method) with:

```python
    def place_order(self, session, *, account_id: int, symbol: str, side: str,
                    order_type: str, qty: Decimal, tif: str = "day",
                    limit_price: Decimal | None = None,
                    idempotency_key: str | None = None) -> Order:
        if idempotency_key is not None:
            existing = session.scalar(select(Order).where(
                Order.account_id == account_id,
                Order.idempotency_key == idempotency_key))
            if existing is not None:
                return existing

        account = session.get(Account, account_id)
        if account is None:
            raise ValueError(f"no such account: {account_id}")

        qty = qty if isinstance(qty, Decimal) else Decimal(str(qty))

        order = Order(account_id=account_id, symbol=symbol.upper(), side=side,
                      order_type=order_type, tif=tif, qty=qty,
                      limit_price=limit_price, idempotency_key=idempotency_key,
                      placed_at=utcnow())
        session.add(order)
        session.flush()

        if side not in ("buy", "sell") or order_type not in ("market", "limit") \
                or tif not in ("day", "gtc"):
            return self.reject_order(session, order, "invalid order parameters")
        if qty <= 0:
            return self.reject_order(session, order, "quantity must be positive")
        if is_crypto_symbol(order.symbol):
            if qty != qty.quantize(Decimal("0.00000001")):
                return self.reject_order(
                    session, order, "quantity precision exceeds 8 decimal places")
        else:
            if qty != qty.to_integral_value():
                return self.reject_order(
                    session, order, "quantity must be a whole share count")
        if order_type == "limit" and (limit_price is None or limit_price <= 0):
            return self.reject_order(session, order, "limit price required")

        try:
            quote = self.market_data.get_quote(order.symbol)
        except UnknownSymbolError:
            return self.reject_order(session, order, f"unknown symbol: {order.symbol}")
        except MarketDataError:
            return self.reject_order(session, order, "market data unavailable")

        if side == "buy":
            est_price = limit_price if order_type == "limit" else quote.price
            cost = est_price * qty + account.commission
            available = self.available_cash(session, account)
            if cost > available:
                return self.reject_order(
                    session, order,
                    f"insufficient cash: need {cost}, available {available}")
            order.reserved_cash = cost
        else:
            if qty > self.available_qty(session, account, order.symbol,
                                        exclude_order_id=order.id):
                return self.reject_order(session, order, "insufficient shares")

        return order
```

(Note: the `qty` coercion means existing callers that pass a bare `int` — e.g. the shipped `SmaCross` strategy's `ctx.buy("SPY", qty)` where `qty` is a Python `int` — keep working unchanged. Real callers going forward, API and `Context`, pass `Decimal`.)

Replace `available_qty`'s body (return type and the two `0`/`sum` defaults):

```python
    def available_qty(self, session, account: Account, symbol: str,
                      exclude_order_id: int | None = None) -> Decimal:
        pos = session.scalar(select(Position).where(
            Position.account_id == account.id, Position.symbol == symbol))
        held = pos.qty if pos is not None else Decimal("0")
        stmt = select(Order.qty).where(
            Order.account_id == account.id,
            Order.symbol == symbol,
            Order.status == "pending",
            Order.side == "sell")
        if exclude_order_id is not None:
            stmt = stmt.where(Order.id != exclude_order_id)
        pending_sells = session.scalars(stmt).all()
        return held - sum(pending_sells, Decimal("0"))
```

Replace `_get_or_create_position`'s `Position(...)` construction line (`qty=0` → `qty=Decimal("0")`):

```python
            pos = Position(account_id=account_id, symbol=symbol,
                           qty=Decimal("0"), avg_cost=Decimal("0"), realized_pnl=Decimal("0"))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_assets.py tests/test_engine_placement.py -q`
Expected: `2 + 15 passed` (2 new assets tests, existing 12 engine-placement tests plus 3 new ones).

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (the wider `Decimal` qty type is transparent to every other existing test, since `Decimal("10") == 10` holds in Python and every other caller either passes `Decimal` already or a bare `int` that the new coercion accepts).

- [ ] **Step 8: Commit**

```bash
git add app/assets.py app/models.py app/engine/engine.py tests/test_assets.py tests/test_engine_placement.py
git commit -m "feat: widen order/position/fill quantities to Decimal with symbol-aware validation"
```

---

### Task 2: CryptoCalendar

**Files:**
- Create: `backend/app/engine/crypto_calendar.py`
- Test: `backend/tests/test_crypto_calendar.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CryptoCalendar` implementing the same 4-method interface as `MarketCalendar` (`is_open`, `is_trading_day`, `next_open`, `expiry_time`) — always open, day orders expire at the next UTC midnight strictly after `placed_at`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_crypto_calendar.py`:

```python
from datetime import datetime

from app.engine.crypto_calendar import CryptoCalendar


def test_always_open():
    cal = CryptoCalendar()
    assert cal.is_open(datetime(2026, 7, 4, 3, 0)) is True   # a Saturday
    assert cal.is_open(datetime(2026, 12, 25, 12, 0)) is True  # Christmas


def test_always_a_trading_day():
    cal = CryptoCalendar()
    assert cal.is_trading_day(datetime(2026, 7, 4).date()) is True


def test_next_open_returns_input_unchanged():
    cal = CryptoCalendar()
    now = datetime(2026, 7, 4, 15, 30)
    assert cal.next_open(now) == now


def test_expiry_is_next_utc_midnight():
    cal = CryptoCalendar()
    assert cal.expiry_time(datetime(2026, 7, 4, 15, 30)) == datetime(2026, 7, 5, 0, 0)


def test_expiry_at_exact_midnight_gives_full_day():
    cal = CryptoCalendar()
    assert cal.expiry_time(datetime(2026, 7, 4, 0, 0)) == datetime(2026, 7, 5, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_crypto_calendar.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.crypto_calendar'`.

- [ ] **Step 3: Implement**

`backend/app/engine/crypto_calendar.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timedelta


class CryptoCalendar:
    """Crypto markets never close. All datetimes naive UTC."""

    def is_open(self, at: datetime) -> bool:
        return True

    def is_trading_day(self, d: date) -> bool:
        return True

    def next_open(self, after: datetime) -> datetime:
        return after

    def expiry_time(self, placed_at: datetime) -> datetime:
        next_midnight = datetime(placed_at.year, placed_at.month, placed_at.day) \
            + timedelta(days=1)
        return next_midnight
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_crypto_calendar.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add app/engine/crypto_calendar.py tests/test_crypto_calendar.py
git commit -m "feat: CryptoCalendar for the always-open crypto pipeline"
```

---

### Task 3: Coinbase and Binance market data providers

**Files:**
- Create: `backend/app/marketdata/coinbase.py`, `backend/app/marketdata/binance.py`
- Test: `backend/tests/test_crypto_providers.py`

**Interfaces:**
- Consumes: `Quote`, `Bar`, `MarketDataError`, `UnknownSymbolError` (from `app.marketdata.base`, unchanged from Phase 1).
- Produces: `CoinbaseData()` and `BinanceData()`, both satisfying the `MarketDataProvider` protocol (`name`, `get_quote`, `get_bars`), tested via `httpx.MockTransport` — no live network calls. Task 4 wires both into a `MarketDataService([CoinbaseData(), BinanceData()])` crypto stack.

**Implementation note:** the exact Coinbase/Binance response field names below reflect each API's current public documentation at the time of writing. If a live smoke-test later shows a field name has changed, update the parsing accordingly — the tests here are offline fixtures, not live-call guarantees.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_crypto_providers.py`:

```python
from decimal import Decimal

import httpx
import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.binance import BinanceData
from app.marketdata.coinbase import CoinbaseData


def coinbase_with(handler):
    return CoinbaseData(transport=httpx.MockTransport(handler))


def test_coinbase_quote_parses_price_and_time():
    def handler(request):
        assert request.url.path == "/products/BTC-USD/ticker"
        return httpx.Response(200, json={
            "trade_id": 123, "price": "65432.10", "size": "0.01",
            "time": "2026-07-04T12:00:00.123456Z",
        })

    q = coinbase_with(handler).get_quote("BTC-USD")
    assert q.price == Decimal("65432.10")
    assert q.as_of.year == 2026 and q.as_of.tzinfo is None


def test_coinbase_unknown_symbol():
    def handler(request):
        return httpx.Response(404, json={"message": "NotFound"})

    with pytest.raises(UnknownSymbolError):
        coinbase_with(handler).get_quote("XXX-USD")


def test_coinbase_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        coinbase_with(handler).get_quote("BTC-USD")


def test_coinbase_bars_reversed_to_oldest_first():
    def handler(request):
        assert request.url.path == "/products/BTC-USD/candles"
        assert request.url.params["granularity"] == "86400"
        # Coinbase returns candles newest-first.
        return httpx.Response(200, json=[
            [1751500800, 100.0, 105.0, 101.0, 104.0, 10.0],  # newer
            [1751414400, 95.0, 99.0, 96.0, 98.0, 20.0],       # older
        ])

    bars = coinbase_with(handler).get_bars("BTC-USD", "1D", 2)
    assert bars[0].close == Decimal("98.0")    # oldest first
    assert bars[-1].close == Decimal("104.0")  # newest last


def binance_with(handler):
    return BinanceData(transport=httpx.MockTransport(handler))


def test_binance_quote_translates_symbol_and_parses_price():
    def handler(request):
        assert request.url.path == "/api/v3/ticker/price"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "65430.50"})

    q = binance_with(handler).get_quote("BTC-USD")
    assert q.price == Decimal("65430.50")
    assert q.as_of.tzinfo is None


def test_binance_unknown_symbol():
    def handler(request):
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with pytest.raises(UnknownSymbolError):
        binance_with(handler).get_quote("XXX-USD")


def test_binance_other_400_is_marketdataerror():
    def handler(request):
        return httpx.Response(400, json={"code": -1100, "msg": "Illegal characters."})

    with pytest.raises(MarketDataError):
        binance_with(handler).get_quote("BTC-USD")


def test_binance_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        binance_with(handler).get_quote("BTC-USD")


def test_binance_bars_oldest_first():
    def handler(request):
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1d"
        return httpx.Response(200, json=[
            [1751414400000, "95.0", "99.0", "96.0", "98.0", "20.0", 1751500799999],
            [1751500800000, "100.0", "105.0", "101.0", "104.0", "10.5", 1751587199999],
        ])

    bars = binance_with(handler).get_bars("BTC-USD", "1D", 2)
    assert bars[0].close == Decimal("98.0")
    assert bars[-1].close == Decimal("104.0")
    assert bars[-1].volume == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_crypto_providers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.marketdata.coinbase'`.

- [ ] **Step 3: Implement**

`backend/app/marketdata/coinbase.py`:

```python
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
```

`backend/app/marketdata/binance.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_crypto_providers.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add app/marketdata/coinbase.py app/marketdata/binance.py tests/test_crypto_providers.py
git commit -m "feat: Coinbase and Binance crypto market data providers"
```

---

### Task 4: Crypto pipeline wiring in AppDeps/build_deps

**Files:**
- Modify: `backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/test_jobs.py`
- Test: `backend/tests/test_deps_routing.py` (new)

**Interfaces:**
- Consumes: `is_crypto_symbol` (Task 1), `CryptoCalendar` (Task 2), `CoinbaseData`/`BinanceData` (Task 3).
- Produces: `AppDeps` gains fields `crypto_market_data`, `crypto_calendar`, `crypto_engine`, `crypto_execution`, plus two methods: `execution_for_symbol(self, symbol: str) -> SimAdapter` and `market_data_for_symbol(self, symbol: str)`. `build_deps()` constructs a full crypto stack. This task does **not** yet change how `StrategyRunner` is constructed (that's Task 8) — it only adds the new `AppDeps` fields and updates the two existing test fixtures that construct `AppDeps` directly so the suite stays green.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_deps_routing.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_deps_routing.py -q`
Expected: FAIL — `AttributeError: 'AppDeps' object has no attribute 'crypto_execution'` (the `client` fixture doesn't build one yet).

- [ ] **Step 3: Update `backend/app/main.py`**

Add imports (alongside the existing ones):
```python
from app.assets import is_crypto_symbol
from app.engine.crypto_calendar import CryptoCalendar
from app.marketdata.binance import BinanceData
from app.marketdata.coinbase import CoinbaseData
```

Replace the `AppDeps` dataclass with:

```python
@dataclass
class AppDeps:
    settings: Settings
    session_factory: object
    market_data: object
    calendar: object
    engine: TradingEngine
    execution: SimAdapter
    runner: StrategyRunner
    crypto_market_data: object
    crypto_calendar: object
    crypto_engine: TradingEngine
    crypto_execution: SimAdapter

    def execution_for_symbol(self, symbol: str) -> SimAdapter:
        return self.crypto_execution if is_crypto_symbol(symbol) else self.execution

    def market_data_for_symbol(self, symbol: str):
        return self.crypto_market_data if is_crypto_symbol(symbol) else self.market_data
```

In `build_deps`, insert the crypto stack construction right after the existing stock `execution = SimAdapter(engine, market_data, calendar)` line, and update the final `AppDeps(...)` construction:

```python
def build_deps(settings: Settings | None = None, market_data=None,
               calendar=None) -> AppDeps:
    settings = settings or Settings()
    db_engine = make_engine(f"sqlite:///{settings.db_path}")
    init_db(db_engine)
    session_factory = make_session_factory(db_engine)
    if market_data is None:
        providers = []
        if settings.alpaca_key_id:
            providers.append(AlpacaData(settings.alpaca_key_id, settings.alpaca_secret))
        providers.append(YFinanceData())
        market_data = MarketDataService(providers)
    calendar = calendar or MarketCalendar()
    engine = TradingEngine(market_data)
    execution = SimAdapter(engine, market_data, calendar)

    crypto_calendar = CryptoCalendar()
    crypto_market_data = MarketDataService([CoinbaseData(), BinanceData()])
    crypto_engine = TradingEngine(crypto_market_data)
    crypto_execution = SimAdapter(crypto_engine, crypto_market_data, crypto_calendar)

    runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution,
                            market_data, calendar, settings.starting_cash)
    return AppDeps(settings=settings, session_factory=session_factory,
                   market_data=market_data, calendar=calendar, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_market_data, crypto_calendar=crypto_calendar,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)
```

(The `runner = StrategyRunner(...)` line is intentionally left in its Phase-1 form here — Task 8 changes it.)

- [ ] **Step 4: Update `backend/tests/conftest.py`**

Replace the `client` fixture body with:

```python
@pytest.fixture
def client(session_factory, tmp_path):
    fake_md = FakeMarketData()
    fake_md.set_quote("SPY", "100")
    fake_cal = FakeCalendar(open_=True)
    engine = TradingEngine(fake_md)
    execution = SimAdapter(engine, fake_md, fake_cal)

    crypto_fake_md = FakeMarketData()
    crypto_fake_md.set_quote("BTC-USD", "65000")
    crypto_fake_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_fake_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_fake_md, crypto_fake_cal)

    settings = Settings(password="pw", secret_key="test-secret")
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    runner = StrategyRunner(Path(strategies_dir), session_factory, execution,
                            fake_md, fake_cal, settings.starting_cash)
    deps = AppDeps(settings=settings, session_factory=session_factory,
                   market_data=fake_md, calendar=fake_cal, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_fake_md, crypto_calendar=crypto_fake_cal,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.fake_md = fake_md
    c.fake_cal = fake_cal
    c.crypto_fake_md = crypto_fake_md
    c.crypto_fake_cal = crypto_fake_cal
    return c
```

- [ ] **Step 5: Update `backend/tests/test_jobs.py`**

Replace the `deps` fixture body with:

```python
@pytest.fixture
def deps(session_factory, tmp_path):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal)

    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    crypto_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, crypto_cal)

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    runner = StrategyRunner(Path(strategies_dir), session_factory, execution,
                            md, cal, Decimal("100000"))
    with session_factory() as s:
        make_account(s)
        s.commit()
    return AppDeps(settings=Settings(), session_factory=session_factory,
                   market_data=md, calendar=cal, engine=engine,
                   execution=execution, runner=runner,
                   crypto_market_data=crypto_md, crypto_calendar=crypto_cal,
                   crypto_engine=crypto_engine, crypto_execution=crypto_execution)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_deps_routing.py -q`
Expected: `4 passed`

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass (existing tests are unaffected — `run_process_pending`/`run_snapshots` still only touch the stock stack at this point, the new crypto fields just exist unused until Tasks 7-8).

- [ ] **Step 8: Commit**

```bash
git add app/main.py tests/conftest.py tests/test_jobs.py tests/test_deps_routing.py
git commit -m "feat: wire a second crypto pipeline into AppDeps with symbol-based routing"
```

---

### Task 5: Valuation, API routing, and scheduler jobs go per-symbol

This task is one atomic unit rather than three, because `valuation.py`'s
signature change is only meaningful once every one of its callers
(`accounts.py`'s `account_detail`, `jobs.py`'s `run_snapshots`) is updated in
the same breath — splitting them would leave the suite red between commits.

**Files:**
- Modify: `backend/app/engine/valuation.py`, `backend/app/api/schemas.py`, `backend/app/api/orders.py`, `backend/app/api/accounts.py`, `backend/app/api/market.py`, `backend/app/jobs.py`
- Test: `backend/tests/test_valuation.py` (replace), `backend/tests/test_api_crypto.py` (new), `backend/tests/test_jobs.py` (append)

**Interfaces:**
- Consumes: `is_crypto_symbol` (Task 1), `deps.execution_for_symbol`/`deps.market_data_for_symbol` (Task 4).
- Produces: `PositionValue.qty: Decimal` (was `int`). `position_values(session, account, market_data_for_symbol)` and `account_equity(session, account, market_data_for_symbol)` now take a **lookup function** `(symbol: str) -> MarketDataService`-like object instead of a single service, calling it per-position. `take_snapshots(session, market_data_for_symbol, now=None)` **drops the `calendar` parameter entirely** — snapshots are taken every day the job runs, for every account, with no trading-day gate (a mixed account's crypto positions can move on any day). `Qty` Pydantic type (Decimal-as-trimmed-string, same serialization as `Money`); `OrderIn.qty: Decimal`; `OrderOut.qty`/`PositionOut.qty`/`TradeOut.qty: Qty`. Order placement/cancellation, account valuation, quote/bar lookups, and the scheduler all route to the correct pipeline by symbol shape.

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_valuation.py` entirely with:

```python
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.assets import is_crypto_symbol
from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity, ny_date, position_values, take_snapshots
from app.models import EquitySnapshot
from tests.factories import make_account
from tests.fakes import FakeMarketData


@pytest.fixture
def md():
    f = FakeMarketData()
    f.set_quote("SPY", "100")
    return f


@pytest.fixture
def engine(md):
    return TradingEngine(md)


def open_position(engine, session, acct, symbol="SPY", qty=10, price="100"):
    order = engine.place_order(session, account_id=acct.id, symbol=symbol,
                               side="buy", order_type="market", qty=qty)
    engine.apply_fill(session, order, Decimal(price))


def test_ny_date_converts_from_utc():
    # 01:00 UTC on July 3 is still July 2 in New York (EDT, UTC-4).
    assert ny_date(datetime(2026, 7, 3, 1, 0)) == date(2026, 7, 2)


def test_position_values_and_unrealized(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    [pv] = position_values(session, acct, lambda s: md)
    assert pv.market_value == Decimal("1100")
    assert pv.unrealized_pnl == Decimal("100")


def test_account_equity(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    assert account_equity(session, acct, lambda s: md) == Decimal("100100")  # 99000 + 1100


def test_take_snapshots_writes_one_row_per_account(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 2, 20, 10))
    snap = session.query(EquitySnapshot).one()
    assert snap.date == date(2026, 7, 2)
    assert snap.equity == Decimal("100000")  # 99000 cash + 1000 position


def test_take_snapshots_same_day_updates_not_duplicates(engine, session, md):
    acct = make_account(session)
    now = datetime(2026, 7, 2, 20, 10)
    take_snapshots(session, lambda s: md, now=now)
    take_snapshots(session, lambda s: md, now=now)
    assert session.query(EquitySnapshot).count() == 1


def test_take_snapshots_skips_account_on_data_outage(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    md.fail = True
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 2, 20, 10))
    assert session.query(EquitySnapshot).count() == 0


def test_take_snapshots_runs_every_day_regardless_of_stock_calendar(engine, session, md):
    # Phase 1 skipped snapshots on non-trading days; Phase 2 removes that gate
    # because a mixed account's crypto positions can move on any day.
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, lambda s: md, now=datetime(2026, 7, 4, 20, 10))  # a Saturday
    assert session.query(EquitySnapshot).count() == 1


def test_position_values_mixed_account_routes_by_symbol(engine, session, md):
    md.set_quote("BTC-USD", "60000")  # needed so the engine can open the position at all
    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")  # different price proves routing picks this one
    acct = make_account(session)
    open_position(engine, session, acct, symbol="SPY", qty=10, price="100")
    open_position(engine, session, acct, symbol="BTC-USD", qty=Decimal("0.01"), price="60000")

    def market_data_for_symbol(symbol):
        return crypto_md if is_crypto_symbol(symbol) else md

    values = {pv.symbol: pv for pv in position_values(session, acct, market_data_for_symbol)}
    assert values["SPY"].last_price == Decimal("100")
    assert values["BTC-USD"].last_price == Decimal("65000")
    equity = account_equity(session, acct, market_data_for_symbol)
    assert equity == Decimal("100050")  # 98400 cash + 1000 SPY + 650 BTC-USD
```

(The existing `test_take_snapshots_skips_non_trading_day` test is intentionally removed — the behavior it tested is deliberately removed per the approved spec.)

`backend/tests/test_api_crypto.py` (new file):

```python
def place_crypto(client, body=None):
    payload = {"symbol": "BTC-USD", "side": "buy", "order_type": "market",
              "qty": "0.01"}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_crypto_market_order_fills_via_crypto_stack(client):
    r = place_crypto(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "BTC-USD"
    assert body["qty"] == "0.01"


def test_crypto_order_rejects_fractional_qty_precision(client):
    r = place_crypto(client, {"qty": "0.123456789"})
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "quantity precision exceeds 8 decimal places"


def test_stock_order_still_rejects_fractional_qty(client):
    r = client.post("/api/accounts/1/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1.5"})
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "quantity must be a whole share count"


def test_cancel_crypto_order_routes_to_crypto_stack(client):
    client.crypto_fake_cal.open = False
    order = place_crypto(client).json()
    assert order["status"] == "pending"
    r = client.post(f"/api/orders/{order['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_account_detail_blends_stock_and_crypto_positions(client):
    client.post("/api/accounts/1/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10})
    place_crypto(client)
    detail = client.get("/api/accounts/1").json()
    symbols = {p["symbol"] for p in detail["positions"]}
    assert symbols == {"SPY", "BTC-USD"}


def test_quote_endpoint_routes_crypto_symbol(client):
    r = client.get("/api/market/quote/BTC-USD")
    assert r.status_code == 200
    assert r.json()["price"] == "65000"


def test_quote_endpoint_still_routes_stock_symbol(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    assert r.json()["price"] == "100"
```

Append to `backend/tests/test_jobs.py`:

```python
def test_run_process_pending_fills_queued_crypto_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.crypto_calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.crypto_execution.place_order(
            s, account_id=acct.id, symbol="BTC-USD", side="buy",
            order_type="market", qty=Decimal("0.01"))
        s.commit()
        order_id = order.id
    deps.crypto_calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_valuation.py tests/test_api_crypto.py tests/test_jobs.py -q`
Expected: FAIL — `position_values() takes 2 positional arguments but 3 were given` style errors in `test_valuation.py` (the old functions take a single `market_data` object, not a lookup function); connection/routing errors in `test_api_crypto.py` (crypto orders aren't routed yet); the new crypto job test fails in `test_jobs.py`.

- [ ] **Step 3: Implement `valuation.py`**

Replace `backend/app/engine/valuation.py` entirely with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.marketdata.base import MarketDataError
from app.models import Account, EquitySnapshot, Position
from app.timeutil import utcnow

NY_TZ = ZoneInfo("America/New_York")


def ny_date(dt_utc: datetime) -> date:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(NY_TZ).date()


@dataclass(frozen=True)
class PositionValue:
    symbol: str
    qty: Decimal
    avg_cost: Decimal
    last_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal


def position_values(session, account: Account, market_data_for_symbol) -> list[PositionValue]:
    out = []
    positions = session.scalars(select(Position).where(
        Position.account_id == account.id, Position.qty > 0)).all()
    for pos in positions:
        quote = market_data_for_symbol(pos.symbol).get_quote(pos.symbol)
        out.append(PositionValue(
            symbol=pos.symbol, qty=pos.qty, avg_cost=pos.avg_cost,
            last_price=quote.price, market_value=quote.price * pos.qty,
            unrealized_pnl=(quote.price - pos.avg_cost) * pos.qty,
            realized_pnl=pos.realized_pnl))
    return out


def account_equity(session, account: Account, market_data_for_symbol) -> Decimal:
    values = position_values(session, account, market_data_for_symbol)
    return account.cash + sum((pv.market_value for pv in values), Decimal("0"))


def take_snapshots(session, market_data_for_symbol, now: datetime | None = None) -> None:
    now = now or utcnow()
    d = ny_date(now)
    for account in session.scalars(select(Account)).all():
        try:
            equity = account_equity(session, account, market_data_for_symbol)
        except MarketDataError:
            continue  # skip this account today rather than record a wrong number
        snap = session.scalar(select(EquitySnapshot).where(
            EquitySnapshot.account_id == account.id, EquitySnapshot.date == d))
        if snap is None:
            session.add(EquitySnapshot(account_id=account.id, date=d,
                                       equity=equity, cash=account.cash))
        else:
            snap.equity = equity
            snap.cash = account.cash
```

- [ ] **Step 4: Run the `valuation.py` tests in isolation**

Run: `cd backend && uv run pytest tests/test_valuation.py -q`
Expected: `9 passed`

- [ ] **Step 5: Update `backend/app/api/schemas.py`**

Add the `Qty` type right after the existing `Money` type definition:

```python
Qty = Annotated[Decimal, PlainSerializer(_serialize_money, return_type=str, when_used="json")]
```

Change `OrderIn.qty` from `int` to:
```python
    qty: Decimal
```

Change `OrderOut.qty` from `int` to:
```python
    qty: Qty
```

Change `PositionOut.qty` from `int` to:
```python
    qty: Qty
```

Change `TradeOut.qty` from `int` to:
```python
    qty: Qty
```

- [ ] **Step 6: Update `backend/app/api/orders.py`**

Replace `place_order`'s body:

```python
@router.post("/accounts/{account_id}/orders", response_model=OrderOut,
             status_code=201)
def place_order(account_id: int, body: OrderIn, session=Depends(get_session),
                deps=Depends(get_deps)):
    if session.get(Account, account_id) is None:
        raise HTTPException(404, "no such account")
    execution = deps.execution_for_symbol(body.symbol)
    return execution.place_order(
        session, account_id=account_id, symbol=body.symbol, side=body.side,
        order_type=body.order_type, qty=body.qty, tif=body.tif,
        limit_price=body.limit_price, idempotency_key=body.idempotency_key)
```

Replace `cancel_order`'s body:

```python
@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, session=Depends(get_session),
                 deps=Depends(get_deps)):
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "no such order")
    execution = deps.execution_for_symbol(order.symbol)
    try:
        return execution.cancel_order(session, order_id)
    except ValueError:
        raise HTTPException(404, "no such order")
    except InvalidOrderState as e:
        raise HTTPException(409, str(e))
```

- [ ] **Step 7: Update `backend/app/api/accounts.py`**

Replace `account_detail`'s body:

```python
@router.get("/accounts/{account_id}", response_model=AccountDetailOut)
def account_detail(account_id: int, session=Depends(get_session),
                   deps=Depends(get_deps)):
    account = _account_or_404(session, account_id)
    try:
        values = position_values(session, account, deps.market_data_for_symbol)
        equity = account_equity(session, account, deps.market_data_for_symbol)
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return AccountDetailOut(
        id=account.id, name=account.name, kind=account.kind,
        cash=account.cash, starting_cash=account.starting_cash, equity=equity,
        positions=[PositionOut(**vars(pv)) for pv in values])
```

- [ ] **Step 8: Update `backend/app/api/market.py`**

Replace both route bodies:

```python
@router.get("/market/quote/{symbol}", response_model=QuoteOut)
def quote(symbol: str, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        q = deps.market_data_for_symbol(symbol).get_quote(symbol)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of)


@router.get("/market/bars/{symbol}", response_model=list[BarOut])
def bars(symbol: str, limit: int = 200, deps=Depends(get_deps)):
    symbol = symbol.upper()
    try:
        return deps.market_data_for_symbol(symbol).get_bars(symbol, "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
```

- [ ] **Step 9: Update `backend/app/jobs.py`**

Replace `run_process_pending` and `run_snapshots`:

```python
def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        deps.crypto_execution.process_pending(session)
        session.commit()


def run_snapshots(deps) -> None:
    with deps.session_factory() as session:
        take_snapshots(session, deps.market_data_for_symbol)
        session.commit()
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_crypto.py tests/test_jobs.py -q`
Expected: `7 passed` (`test_api_crypto.py`) and `4 passed` (`test_jobs.py`, existing 3 plus the new crypto one).

- [ ] **Step 11: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass, including the pre-existing `test_api_accounts_orders.py` and `test_api_market_journal_strategies.py` suites unchanged (qty crosses the API as JSON numbers or strings either way — Pydantic's `Decimal` field accepts both — and no existing test asserts an exact `qty` value in a JSON response body).

- [ ] **Step 12: Commit**

```bash
git add app/engine/valuation.py app/api/schemas.py app/api/orders.py app/api/accounts.py app/api/market.py app/jobs.py tests/test_valuation.py tests/test_api_crypto.py tests/test_jobs.py
git commit -m "feat: route valuation, orders/accounts/market API, and scheduler jobs by symbol shape"
```

---

### Task 6: Strategy runner and Context route per-call by symbol

**Files:**
- Modify: `backend/app/strategy/base.py`, `backend/app/strategy/runner.py`, `backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/test_jobs.py`, `backend/tests/test_strategy_runner.py`

**Interfaces:**
- Consumes: `is_crypto_symbol` (Task 1).
- Produces: `StrategyRunner.__init__(strategies_dir, session_factory, execution_for_symbol, market_data_for_symbol, starting_cash)` — takes routing **callables** instead of a single `execution`/`market_data`/`calendar` triple (the `calendar` parameter is removed entirely: the `daily_after_close` trading-day gate is dropped for the same reason `take_snapshots`' gate was dropped — a strategy might trade crypto, which has no "trading day" concept). `Context` now also takes `execution_for_symbol`/`market_data_for_symbol` and routes every `buy`/`sell`/`cancel`/`get_quote`/`get_bars` call by the symbol argument, so one strategy can trade both a stock and a crypto pair in the same run.

- [ ] **Step 1: Write the failing tests**

Replace `backend/tests/test_strategy_runner.py` entirely with:

```python
from decimal import Decimal
from pathlib import Path

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.models import Account, Order, StrategyRun, StrategyState
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData

GOOD_STRATEGY = '''
from decimal import Decimal
from app.strategy.base import Strategy

class BuyOne(Strategy):
    def run(self, ctx):
        ctx.buy("SPY", qty=1)
'''

BAD_STRATEGY = '''
from app.strategy.base import Strategy

class Exploder(Strategy):
    def run(self, ctx):
        raise RuntimeError("boom")
'''


def _stock_only(execution=None, market_data=None):
    def execution_for_symbol(symbol):
        return execution

    def market_data_for_symbol(symbol):
        return market_data

    return execution_for_symbol, market_data_for_symbol


@pytest.fixture
def runner(tmp_path, session_factory):
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    (tmp_path / "exploder.py").write_text(BAD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    execution_for_symbol, market_data_for_symbol = _stock_only(execution, md)
    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
    r.discover()
    r.sync_accounts()
    return r


def enable(session_factory, name):
    with session_factory() as s:
        state = s.query(StrategyState).filter_by(name=name).one()
        state.enabled = True
        s.commit()


def test_discovery_finds_strategies(runner):
    assert set(runner.strategies) == {"BuyOne", "Exploder"}


def test_accounts_created_disabled_by_default(runner, session_factory):
    with session_factory() as s:
        acct = s.query(Account).filter_by(name="strategy:BuyOne").one()
        assert acct.kind == "strategy"
        assert acct.cash == Decimal("100000")
        assert s.query(StrategyState).filter_by(name="BuyOne").one().enabled is False


def test_disabled_strategy_does_not_run(runner, session_factory):
    assert runner.run_strategy("BuyOne") is None
    with session_factory() as s:
        assert s.query(StrategyRun).count() == 0


def test_enabled_strategy_places_order_in_own_account(runner, session_factory):
    enable(session_factory, "BuyOne")
    run = runner.run_strategy("BuyOne")
    assert run.status == "ok"
    assert run.detail == "orders placed: 1"
    with session_factory() as s:
        order = s.query(Order).one()
        acct = s.get(Account, order.account_id)
        assert acct.name == "strategy:BuyOne"
        assert order.status == "filled"


def test_error_is_contained_and_recorded(runner, session_factory):
    enable(session_factory, "Exploder")
    run = runner.run_strategy("Exploder")
    assert run.status == "error"
    assert "boom" in run.detail


def test_sync_accounts_is_idempotent(runner, session_factory):
    runner.sync_accounts()
    with session_factory() as s:
        assert s.query(Account).filter_by(name="strategy:BuyOne").count() == 1


def test_broken_strategy_file_is_skipped(tmp_path, session_factory):
    (tmp_path / "broken.py").write_text("def broken(:\n")
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    execution_for_symbol, market_data_for_symbol = _stock_only(execution, md)
    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
    r.discover()
    assert set(r.strategies) == {"BuyOne"}


class _GoodSchedule:
    schedule = "daily_after_close"


class _BadSchedule:
    schedule = "not a cron"


def test_invalid_cron_schedule_is_skipped(runner):
    runner.strategies = {"Good": _GoodSchedule, "Bad": _BadSchedule}
    scheduler = BackgroundScheduler()
    runner.register_jobs(scheduler)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "strategy:Good" in job_ids
    assert "strategy:Bad" not in job_ids


def test_strategy_can_trade_both_stock_and_crypto_symbols(tmp_path, session_factory):
    mixed_strategy = '''
from decimal import Decimal
from app.strategy.base import Strategy

class MixedTrader(Strategy):
    def run(self, ctx):
        ctx.buy("SPY", qty=1)
        ctx.buy("BTC-USD", qty=Decimal("0.01"))
'''
    (tmp_path / "mixed.py").write_text(mixed_strategy)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, FakeCalendar(open_=True))

    def execution_for_symbol(symbol):
        return crypto_execution if "-" in symbol else execution

    def market_data_for_symbol(symbol):
        return crypto_md if "-" in symbol else md

    r = StrategyRunner(Path(tmp_path), session_factory, execution_for_symbol,
                       market_data_for_symbol, Decimal("100000"))
    r.discover()
    r.sync_accounts()
    with session_factory() as s:
        state = s.query(StrategyState).filter_by(name="MixedTrader").one()
        state.enabled = True
        s.commit()
    run = r.run_strategy("MixedTrader")
    assert run.status == "ok"
    assert run.detail == "orders placed: 2"
    with session_factory() as s:
        symbols = {o.symbol for o in s.query(Order).all()}
        assert symbols == {"SPY", "BTC-USD"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_strategy_runner.py -q`
Expected: FAIL — `TypeError: StrategyRunner.__init__() takes 6 positional arguments but 5 were given` (constructor signature hasn't changed yet).

- [ ] **Step 3: Update `backend/app/strategy/base.py`**

Replace `Context` entirely (the `Strategy` class above it is unchanged):

```python
class Context:
    """Exactly the capabilities a manual trader has via the UI — nothing more,
    so strategies stay portable to live trading. Routes to the stock or
    crypto pipeline per-call based on the traded symbol, so one strategy can
    trade both in the same run."""

    def __init__(self, session, account, execution_for_symbol, market_data_for_symbol):
        self._session = session
        self._account = account
        self._execution_for_symbol = execution_for_symbol
        self._market_data_for_symbol = market_data_for_symbol
        self.placed: list[int] = []

    def get_quote(self, symbol: str):
        return self._market_data_for_symbol(symbol).get_quote(symbol)

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        return self._market_data_for_symbol(symbol).get_bars(symbol, timeframe, limit)

    @property
    def cash(self) -> Decimal:
        return self._account.cash

    def positions(self) -> list[Position]:
        return list(self._session.scalars(select(Position).where(
            Position.account_id == self._account.id, Position.qty > 0)))

    def orders(self, status: str | None = None) -> list[Order]:
        stmt = select(Order).where(Order.account_id == self._account.id)
        if status is not None:
            stmt = stmt.where(Order.status == status)
        return list(self._session.scalars(stmt))

    def buy(self, symbol: str, qty: Decimal, limit_price: Decimal | None = None,
            tif: str = "day") -> Order:
        return self._place("buy", symbol, qty, limit_price, tif)

    def sell(self, symbol: str, qty: Decimal, limit_price: Decimal | None = None,
             tif: str = "day") -> Order:
        return self._place("sell", symbol, qty, limit_price, tif)

    def cancel(self, order_id: int) -> Order:
        order_row = self._session.get(Order, order_id)
        symbol = order_row.symbol if order_row is not None else ""
        order = self._execution_for_symbol(symbol).cancel_order(self._session, order_id)
        self._session.commit()
        return order

    def _place(self, side, symbol, qty, limit_price, tif) -> Order:
        order = self._execution_for_symbol(symbol).place_order(
            self._session, account_id=self._account.id, symbol=symbol,
            side=side, order_type="limit" if limit_price is not None else "market",
            qty=qty, tif=tif, limit_price=limit_price)
        self._session.commit()  # each order commits: survives a later crash
        self.placed.append(order.id)
        return order
```

- [ ] **Step 4: Update `backend/app/strategy/runner.py`**

Remove the `from app.engine.valuation import ny_date` import (no longer used).

Replace the whole `StrategyRunner` class:

```python
class StrategyRunner:
    def __init__(self, strategies_dir: Path, session_factory, execution_for_symbol,
                 market_data_for_symbol, starting_cash: Decimal):
        self.strategies_dir = strategies_dir
        self.session_factory = session_factory
        self.execution_for_symbol = execution_for_symbol
        self.market_data_for_symbol = market_data_for_symbol
        self.starting_cash = starting_cash
        self.strategies: dict[str, type[Strategy]] = {}

    def discover(self) -> None:
        for path in sorted(self.strategies_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(
                f"user_strategies_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                log.exception("skipping strategy file %s: import failed", path.name)
                continue
            for obj in vars(module).values():
                if (isinstance(obj, type) and issubclass(obj, Strategy)
                        and obj is not Strategy):
                    self.strategies[obj.strategy_name()] = obj

    def sync_accounts(self) -> None:
        with self.session_factory() as session:
            for name in self.strategies:
                acct_name = f"strategy:{name}"
                if session.scalar(select(Account).where(
                        Account.name == acct_name)) is None:
                    session.add(Account(name=acct_name, kind="strategy",
                                        cash=self.starting_cash,
                                        starting_cash=self.starting_cash))
                if session.scalar(select(StrategyState).where(
                        StrategyState.name == name)) is None:
                    session.add(StrategyState(name=name, enabled=False))
            session.commit()

    def run_strategy(self, name: str) -> StrategyRun | None:
        cls = self.strategies[name]
        with self.session_factory() as session:
            state = session.scalar(select(StrategyState).where(
                StrategyState.name == name))
            if state is None or not state.enabled:
                return None
            account = session.scalar(select(Account).where(
                Account.name == f"strategy:{name}"))
            run = StrategyRun(strategy_name=name, started_at=utcnow())
            ctx = Context(session, account, self.execution_for_symbol,
                         self.market_data_for_symbol)
            try:
                cls().run(ctx)
                run.detail = f"orders placed: {len(ctx.placed)}"
            except Exception:
                session.rollback()  # discards uncommitted partial state only;
                # orders already placed were committed by Context and survive
                run.status = "error"
                run.detail = traceback.format_exc()[-2000:]
            run.finished_at = utcnow()
            session.add(run)
            session.commit()
            return run

    def register_jobs(self, scheduler) -> None:
        for name, cls in self.strategies.items():
            try:
                trigger = (CronTrigger(day_of_week="mon-fri", hour=16, minute=5,
                                       timezone=NY_TZ)
                           if cls.schedule == "daily_after_close"
                           else CronTrigger.from_crontab(cls.schedule, timezone=NY_TZ))
            except ValueError:
                log.exception("skipping strategy %s: invalid schedule %r", name, cls.schedule)
                continue
            scheduler.add_job(self.run_strategy, trigger, args=[name],
                              id=f"strategy:{name}", replace_existing=True)
```

- [ ] **Step 5: Update `backend/app/main.py`**

In `build_deps`, replace the `runner = StrategyRunner(...)` line with routing closures built from the already-constructed pipeline objects:

```python
    def execution_for_symbol(symbol: str) -> SimAdapter:
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_market_data if is_crypto_symbol(symbol) else market_data

    runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution_for_symbol,
                            market_data_for_symbol, settings.starting_cash)
```

(This replaces the old `runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution, market_data, calendar, settings.starting_cash)` line. These local closures are separate from — but behave identically to — `AppDeps`'s `execution_for_symbol`/`market_data_for_symbol` methods; they exist here because `runner` is constructed *before* the final `AppDeps` object, so it can't depend on it.)

- [ ] **Step 6: Update `backend/tests/conftest.py`**

Add the import:
```python
from app.assets import is_crypto_symbol
```

Replace the `runner = StrategyRunner(...)` line in the `client` fixture:

```python
    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_fake_md if is_crypto_symbol(symbol) else fake_md

    runner = StrategyRunner(Path(strategies_dir), session_factory, execution_for_symbol,
                            market_data_for_symbol, settings.starting_cash)
```

- [ ] **Step 7: Update `backend/tests/test_jobs.py`**

Add the import:
```python
from app.assets import is_crypto_symbol
```

Replace the `runner = StrategyRunner(...)` line in the `deps` fixture:

```python
    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_md if is_crypto_symbol(symbol) else md

    runner = StrategyRunner(Path(strategies_dir), session_factory, execution_for_symbol,
                            market_data_for_symbol, Decimal("100000"))
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_strategy_runner.py -q`
Expected: `9 passed`

- [ ] **Step 9: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add app/strategy/base.py app/strategy/runner.py app/main.py tests/conftest.py tests/test_jobs.py tests/test_strategy_runner.py
git commit -m "feat: strategies route trades by symbol, so one strategy can trade stock and crypto"
```

---

## Verification Sweep (after all tasks)

- `cd backend && uv run pytest -q` — full suite green.
- Spec coverage check against `docs/superpowers/specs/2026-07-04-crypto-phase-2-design.md`: fractional qty + validation ✓, symbol-shape routing (single helper, used everywhere) ✓, CryptoCalendar ✓, Coinbase+Binance providers ✓, mixed-account valuation (no trading-day gate) ✓, order/cancel/quote/bars API routing ✓, scheduler runs both pipelines ✓, strategies route per-call by symbol (no `Strategy.asset_class`) ✓.
- Boot the server (`uv run uvicorn --factory app.main:create_app --port 8000`) and confirm via `/docs`: placing a `BTC-USD` market order on the `manual` account fills immediately (crypto market is always "open"), and `GET /api/accounts/1` shows both a stock and a crypto position blended into one equity number.
- Frontend crypto plan (types, qty lib, table grouping/tagging, order ticket) is a separate plan, written after this one lands.
