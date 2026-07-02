# Paper Trading Platform — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI backend of the paper trading platform: trading engine (accounts, orders, simulated fills, positions, P&L), market data with fallback, strategy runner, scheduler, and REST API — per the approved spec at `docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md`.

**Architecture:** Own trading engine with a pluggable `ExecutionAdapter`. `TradingEngine` does bookkeeping (validation, reservations, fills, positions); `SimAdapter` decides when/at what price simulated fills happen. Market data flows through `MarketDataService` (Alpaca primary, yfinance fallback, 30s quote cache). APScheduler drives limit-order checks, day-order expiry, equity snapshots, and strategy runs. Everything external (market data, calendar, clock) is injected so tests run offline and deterministic.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (typed ORM), SQLite, APScheduler 3.x, exchange_calendars (XNYS), httpx, yfinance, itsdangerous, pytest, uv.

**Scope note:** This plan is backend-only. The Next.js frontend and Docker Compose deployment are a separate follow-up plan, written after this backend exists.

## Global Constraints

- All commands run from `backend/` unless stated otherwise. Environment via `uv` (`uv sync`, `uv run pytest -q`).
- **Money is `Decimal`, never float.** Stored as TEXT via the `SqliteDecimal` type; summed in Python (never SQL `SUM`, which coerces TEXT to float); serialized to JSON as strings via the `Money` annotated type.
- Quantities are `int` whole shares. Long-only. USD only. Order types: market/limit; TIF: day/GTC.
- **Datetimes are naive UTC** everywhere in code and DB (`app.timeutil.utcnow()`). NY-local dates via `app.engine.valuation.ny_date()`. Market sessions via `exchange_calendars` XNYS only.
- Rejections are stored orders with `status="rejected"` and a human-readable `reject_reason` — not exceptions. Exceptions are for programmer errors (`ValueError`) and invalid state transitions (`InvalidOrderState`).
- TDD every task: write failing test → run to see it fail → implement minimally → run to see it pass → commit.
- Commit messages: `feat:`/`test:`/`chore:` prefixes, imperative mood.

## File Structure

```
backend/
  pyproject.toml                 project metadata + deps + pytest config
  app/
    __init__.py
    config.py                    Settings (env-prefixed PT_)
    timeutil.py                  utcnow() — naive UTC
    db.py                        Base, engine/session factories, init_db
    models.py                    all ORM models + SqliteDecimal
    marketdata/
      __init__.py
      base.py                    Quote, Bar, errors, provider Protocol
      service.py                 MarketDataService (fallback + cache)
      alpaca.py                  AlpacaData provider
      yfinance_provider.py       YFinanceData provider
    engine/
      __init__.py
      calendar.py                MarketCalendar (XNYS wrapper)
      engine.py                  TradingEngine (bookkeeping)
      sim_adapter.py             SimAdapter (fill policy)
      valuation.py               position values, equity, snapshots, ny_date
    strategy/
      __init__.py
      base.py                    Strategy, Context
      runner.py                  StrategyRunner (discovery, accounts, runs, jobs)
    api/
      __init__.py
      deps.py                    get_deps, get_session, require_auth
      schemas.py                 Pydantic models, Money type
      auth.py                    login/logout
      accounts.py                account list/detail/snapshots
      orders.py                  place/list/cancel + journal note upsert
      market.py                  quote/bars
      journal.py                 trades list + stats
      strategies.py              strategy list/toggle/runs
    jobs.py                      scheduler job functions + build_scheduler
    main.py                      AppDeps, build_deps, create_app
  strategies/
    sma_cross.py                 example strategy
  tests/
    conftest.py                  session fixtures (+ API client fixture from Task 11)
    fakes.py                     FakeMarketData, FakeCalendar, Clock
    factories.py                 make_account
    test_models.py
    test_calendar.py
    test_marketdata_service.py
    test_providers.py
    test_engine_placement.py
    test_engine_fills.py
    test_sim_market.py
    test_sim_limit_expiry.py
    test_valuation.py
    test_strategy_runner.py
    test_api_auth.py
    test_api_accounts_orders.py
    test_api_market_journal_strategies.py
    test_jobs.py
```

---

### Task 1: Project scaffold, database layer, and ORM models

**Files:**
- Create: `backend/pyproject.toml`, `.gitignore` (repo root), `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/timeutil.py`, `backend/app/db.py`, `backend/app/models.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Base`, `make_engine(db_url) -> Engine`, `make_session_factory(engine) -> sessionmaker`, `init_db(engine)`, `utcnow() -> datetime` (naive UTC), `Settings` (fields: `db_path: str`, `password: str`, `secret_key: str`, `alpaca_key_id: str`, `alpaca_secret: str`, `starting_cash: Decimal`, env prefix `PT_`), and ORM models `Account`, `Order`, `Fill`, `Position`, `JournalNote`, `EquitySnapshot`, `StrategyState`, `StrategyRun` with the exact columns shown below. Test fixtures `session_factory`, `session`.

- [ ] **Step 1: Create the project scaffold**

`backend/pyproject.toml`:

```toml
[project]
name = "paper-trading-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "apscheduler>=3.10,<4",
    "exchange-calendars>=4.5",
    "httpx>=0.27",
    "yfinance>=0.2.40",
    "itsdangerous>=2.2",
]

[dependency-groups]
dev = ["pytest>=8.2"]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

`.gitignore` at the **repo root**:

```
.venv/
__pycache__/
*.pyc
*.db
.env
.pytest_cache/
node_modules/
```

Create empty `backend/app/__init__.py`.

- [ ] **Step 2: Install dependencies**

Run: `cd backend && uv sync`
Expected: resolves and installs without error, creates `backend/.venv`.

- [ ] **Step 3: Write the failing tests**

`backend/tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, make_session_factory


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s
```

`backend/tests/test_models.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Order


def test_account_cash_round_trips_as_decimal(session):
    session.add(Account(name="manual", cash=Decimal("100000.50"),
                        starting_cash=Decimal("100000.50")))
    session.commit()
    session.expire_all()
    acct = session.query(Account).one()
    assert acct.cash == Decimal("100000.50")
    assert isinstance(acct.cash, Decimal)


def test_account_names_are_unique(session):
    session.add(Account(name="manual", cash=Decimal("1"), starting_cash=Decimal("1")))
    session.commit()
    session.add(Account(name="manual", cash=Decimal("1"), starting_cash=Decimal("1")))
    with pytest.raises(IntegrityError):
        session.commit()


def test_order_defaults(session):
    acct = Account(name="a", cash=Decimal("1000"), starting_cash=Decimal("1000"))
    session.add(acct)
    session.flush()
    session.add(Order(account_id=acct.id, symbol="SPY", side="buy",
                      order_type="market", qty=10))
    session.commit()
    session.expire_all()
    o = session.query(Order).one()
    assert o.status == "pending"
    assert o.tif == "day"
    assert o.reserved_cash == Decimal("0")
    assert o.limit_price is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'` (or `app.models`).

- [ ] **Step 5: Implement**

`backend/app/timeutil.py`:

```python
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current time as naive UTC — the convention for all stored datetimes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

`backend/app/config.py`:

```python
from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PT_", env_file=".env")

    db_path: str = "paper_trading.db"
    password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    alpaca_key_id: str = ""
    alpaca_secret: str = ""
    starting_cash: Decimal = Decimal("100000")
```

`backend/app/db.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_url: str):
    return create_engine(db_url, connect_args={"check_same_thread": False})


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
```

`backend/app/models.py`:

```python
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint, types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow


class SqliteDecimal(types.TypeDecorator):
    """Store Decimal as TEXT so SQLite never coerces money to float."""

    impl = types.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String, default="manual")  # manual | strategy
    cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    starting_cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    commission: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # buy | sell
    order_type: Mapped[str] = mapped_column(String)  # market | limit
    tif: Mapped[str] = mapped_column(String, default="day")  # day | gtc
    qty: Mapped[int]
    limit_price: Mapped[Decimal | None] = mapped_column(SqliteDecimal, default=None)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending | filled | cancelled | rejected | expired
    reject_reason: Mapped[str | None] = mapped_column(String, default=None)
    reserved_cash: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    idempotency_key: Mapped[str | None] = mapped_column(String, default=None)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)

    account: Mapped[Account] = relationship()


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    price: Mapped[Decimal] = mapped_column(SqliteDecimal)
    qty: Mapped[int]
    commission: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    realized_pnl: Mapped[Decimal | None] = mapped_column(SqliteDecimal, default=None)  # sells only
    filled_at: Mapped[datetime] = mapped_column(default=utcnow)

    order: Mapped[Order] = relationship()


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    symbol: Mapped[str] = mapped_column(String)
    qty: Mapped[int] = mapped_column(default=0)
    avg_cost: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))


class JournalNote(Base):
    __tablename__ = "journal_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[date] = mapped_column(Date)
    equity: Mapped[Decimal] = mapped_column(SqliteDecimal)
    cash: Mapped[Decimal] = mapped_column(SqliteDecimal)


class StrategyState(Base):
    __tablename__ = "strategy_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    enabled: Mapped[bool] = mapped_column(default=False)


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String, default="ok")  # ok | error
    detail: Mapped[str] = mapped_column(Text, default="")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: backend scaffold, db layer, and ORM models"
```

### Task 2: Market calendar

**Files:**
- Create: `backend/app/engine/__init__.py` (empty), `backend/app/engine/calendar.py`
- Test: `backend/tests/test_calendar.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `MarketCalendar` with `is_open(at: datetime) -> bool`, `is_trading_day(d: date) -> bool`, `next_open(after: datetime) -> datetime`, `expiry_time(placed_at: datetime) -> datetime`. All datetimes naive UTC. Later tasks depend on exactly these four method names (the fakes mirror them).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_calendar.py` (June 2026: NYSE summer hours are 13:30–20:00 UTC; 2026-07-03 is the observed July-4 holiday):

```python
from datetime import date, datetime

import pytest

from app.engine.calendar import MarketCalendar


@pytest.fixture(scope="module")
def cal():
    return MarketCalendar()


def test_open_midday_wednesday(cal):
    assert cal.is_open(datetime(2026, 6, 24, 15, 0)) is True


def test_closed_after_hours(cal):
    assert cal.is_open(datetime(2026, 6, 24, 21, 0)) is False


def test_closed_weekend_and_holiday(cal):
    assert cal.is_open(datetime(2026, 6, 27, 15, 0)) is False  # Saturday
    assert cal.is_open(datetime(2026, 7, 3, 15, 0)) is False   # July 4 observed


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2026, 6, 24)) is True
    assert cal.is_trading_day(date(2026, 7, 3)) is False


def test_next_open_after_close(cal):
    assert cal.next_open(datetime(2026, 6, 24, 21, 0)) == datetime(2026, 6, 25, 13, 30)


def test_expiry_during_session_is_same_day_close(cal):
    assert cal.expiry_time(datetime(2026, 6, 24, 15, 0)) == datetime(2026, 6, 24, 20, 0)


def test_expiry_after_close_is_next_session_close(cal):
    assert cal.expiry_time(datetime(2026, 6, 24, 21, 30)) == datetime(2026, 6, 25, 20, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_calendar.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine'`.

- [ ] **Step 3: Implement**

`backend/app/engine/calendar.py`:

```python
from __future__ import annotations

from datetime import date, datetime

import exchange_calendars as xcals
import pandas as pd


class MarketCalendar:
    """NYSE trading hours via exchange_calendars. All datetimes naive UTC."""

    def __init__(self) -> None:
        self._cal = xcals.get_calendar("XNYS")

    @staticmethod
    def _ts(at: datetime) -> pd.Timestamp:
        return pd.Timestamp(at, tz="UTC")

    @staticmethod
    def _naive(ts: pd.Timestamp) -> datetime:
        return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()

    def is_open(self, at: datetime) -> bool:
        return bool(self._cal.is_open_on_minute(self._ts(at)))

    def is_trading_day(self, d: date) -> bool:
        return bool(self._cal.is_session(pd.Timestamp(d)))

    def next_open(self, after: datetime) -> datetime:
        return self._naive(self._cal.next_open(self._ts(after)))

    def expiry_time(self, placed_at: datetime) -> datetime:
        """Close of the session in which an order placed at placed_at is active.

        Placed while open -> that session's close. Placed while closed -> the
        close of the next session (where the order first becomes active).
        """
        ts = self._ts(placed_at)
        if self._cal.is_open_on_minute(ts):
            return self._naive(self._cal.next_close(ts))
        return self._naive(self._cal.next_close(self._cal.next_open(ts)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_calendar.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/engine tests/test_calendar.py
git commit -m "feat: NYSE market calendar wrapper"
```

---

### Task 3: Market data core — types, service with fallback and cache, test fakes

**Files:**
- Create: `backend/app/marketdata/__init__.py` (empty), `backend/app/marketdata/base.py`, `backend/app/marketdata/service.py`
- Test: `backend/tests/fakes.py`, `backend/tests/test_marketdata_service.py`

**Interfaces:**
- Consumes: `utcnow` (Task 1).
- Produces: `Quote(symbol, price, as_of)`, `Bar(timestamp, open, high, low, close, volume)` (frozen dataclasses, Decimal prices), `MarketDataError`, `UnknownSymbolError(MarketDataError)`, `MarketDataProvider` Protocol (`name: str`, `get_quote(symbol) -> Quote`, `get_bars(symbol, timeframe="1D", limit=200) -> list[Bar]`), `MarketDataService(providers, quote_ttl_seconds=30, now_fn=utcnow)` exposing the same two methods. Test doubles: `FakeMarketData` (with `set_quote(symbol, price)`, `set_bars(symbol, closes)`, `fail: bool`), `FakeCalendar(open_=True, trading_day=True)` (attrs `next_open_at`, `expiry_at`), `Clock` (callable, settable `.now`). Every later task uses these fakes.

- [ ] **Step 1: Write the failing tests**

`backend/tests/fakes.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


class FakeMarketData:
    """Stands in for both a provider and the whole MarketDataService."""

    name = "fake"

    def __init__(self):
        self.quotes: dict[str, Quote] = {}
        self.bars: dict[str, list[Bar]] = {}
        self.fail = False

    def set_quote(self, symbol: str, price) -> None:
        self.quotes[symbol] = Quote(symbol=symbol, price=Decimal(str(price)), as_of=utcnow())

    def set_bars(self, symbol: str, closes: list) -> None:
        self.bars[symbol] = [
            Bar(timestamp=datetime(2026, 1, 1) + timedelta(days=i),
                open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)),
                close=Decimal(str(c)), volume=1000)
            for i, c in enumerate(closes)
        ]

    def get_quote(self, symbol: str) -> Quote:
        if self.fail:
            raise MarketDataError("provider down")
        if symbol not in self.quotes:
            raise UnknownSymbolError(symbol)
        return self.quotes[symbol]

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if self.fail:
            raise MarketDataError("provider down")
        if symbol not in self.bars:
            raise UnknownSymbolError(symbol)
        return self.bars[symbol][-limit:]


class FakeCalendar:
    def __init__(self, open_: bool = True, trading_day: bool = True):
        self.open = open_
        self.trading_day = trading_day
        self.next_open_at = datetime(2026, 7, 6, 13, 30)
        self.expiry_at = datetime(2026, 7, 6, 20, 0)

    def is_open(self, at):
        return self.open

    def is_trading_day(self, d):
        return self.trading_day

    def next_open(self, after):
        return self.next_open_at

    def expiry_time(self, placed_at):
        return self.expiry_at


class Clock:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 7, 1, 12, 0)

    def __call__(self) -> datetime:
        return self.now
```

`backend/tests/test_marketdata_service.py`:

```python
from datetime import timedelta
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.service import MarketDataService
from tests.fakes import Clock, FakeMarketData


def test_returns_quote_from_primary():
    primary = FakeMarketData()
    primary.set_quote("SPY", "500.10")
    svc = MarketDataService([primary])
    assert svc.get_quote("SPY").price == Decimal("500.10")


def test_falls_back_when_primary_down():
    primary, fallback = FakeMarketData(), FakeMarketData()
    primary.fail = True
    fallback.set_quote("SPY", "501")
    svc = MarketDataService([primary, fallback])
    assert svc.get_quote("SPY").price == Decimal("501")


def test_all_providers_down_raises():
    p = FakeMarketData()
    p.fail = True
    with pytest.raises(MarketDataError):
        MarketDataService([p]).get_quote("SPY")


def test_unknown_symbol_does_not_fall_back():
    primary, fallback = FakeMarketData(), FakeMarketData()
    fallback.set_quote("XXXX", "1")
    with pytest.raises(UnknownSymbolError):
        MarketDataService([primary, fallback]).get_quote("XXXX")


def test_quote_cached_within_ttl_then_refreshed():
    clock = Clock()
    p = FakeMarketData()
    p.set_quote("SPY", "500")
    svc = MarketDataService([p], quote_ttl_seconds=30, now_fn=clock)
    assert svc.get_quote("SPY").price == Decimal("500")
    p.set_quote("SPY", "510")
    assert svc.get_quote("SPY").price == Decimal("500")  # cached
    clock.now += timedelta(seconds=31)
    assert svc.get_quote("SPY").price == Decimal("510")  # expired


def test_get_bars_falls_back():
    primary, fallback = FakeMarketData(), FakeMarketData()
    primary.fail = True
    fallback.set_bars("SPY", ["1", "2", "3"])
    bars = MarketDataService([primary, fallback]).get_bars("SPY", "1D", 2)
    assert [b.close for b in bars] == [Decimal("2"), Decimal("3")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_marketdata_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.marketdata'`.

- [ ] **Step 3: Implement**

`backend/app/marketdata/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class MarketDataError(Exception):
    pass


class UnknownSymbolError(MarketDataError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    as_of: datetime  # naive UTC


@dataclass(frozen=True)
class Bar:
    timestamp: datetime  # naive UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> Quote: ...

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]: ...
```

`backend/app/marketdata/service.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_marketdata_service.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/marketdata tests/fakes.py tests/test_marketdata_service.py
git commit -m "feat: market data types and service with fallback and quote cache"
```

---

### Task 4: Real providers — Alpaca and yfinance

**Files:**
- Create: `backend/app/marketdata/alpaca.py`, `backend/app/marketdata/yfinance_provider.py`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: `Quote`, `Bar`, `MarketDataError`, `UnknownSymbolError` (Task 3), `utcnow` (Task 1).
- Produces: `AlpacaData(key_id, secret, transport=None)` and `YFinanceData(ticker_factory=None)`, both satisfying the `MarketDataProvider` protocol. Task 14's `build_deps` constructs these.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_providers.py`:

```python
from decimal import Decimal

import httpx
import pandas as pd
import pytest

from app.marketdata.alpaca import AlpacaData
from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.marketdata.yfinance_provider import YFinanceData


def alpaca_with(handler):
    return AlpacaData("key", "secret", transport=httpx.MockTransport(handler))


def test_alpaca_quote_parses_price_and_time():
    def handler(request):
        assert request.url.path == "/v2/stocks/AAPL/trades/latest"
        assert request.headers["APCA-API-KEY-ID"] == "key"
        return httpx.Response(200, json={
            "symbol": "AAPL",
            "trade": {"p": 189.34, "t": "2026-07-02T19:59:59.123456789Z"},
        })

    q = alpaca_with(handler).get_quote("AAPL")
    assert q.price == Decimal("189.34")
    assert q.as_of.year == 2026 and q.as_of.tzinfo is None


def test_alpaca_unknown_symbol():
    def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    with pytest.raises(UnknownSymbolError):
        alpaca_with(handler).get_quote("XXXX")


def test_alpaca_server_error_is_marketdataerror():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(MarketDataError):
        alpaca_with(handler).get_quote("AAPL")


def test_alpaca_bars_parse():
    def handler(request):
        assert request.url.path == "/v2/stocks/SPY/bars"
        return httpx.Response(200, json={"bars": [
            {"t": "2026-06-30T04:00:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100},
            {"t": "2026-07-01T04:00:00Z", "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 200},
        ]})

    bars = alpaca_with(handler).get_bars("SPY", "1D", 2)
    assert bars[-1].close == Decimal("2.0")
    assert bars[-1].volume == 200


class StubQuoteTicker:
    def __init__(self, price):
        self.fast_info = {"last_price": price}


class StubBarsTicker:
    def history(self, period, interval, auto_adjust):
        idx = pd.date_range("2026-06-01", periods=3, freq="B", tz="America/New_York")
        return pd.DataFrame(
            {"Open": [1.0, 2.0, 3.0], "High": [1.1, 2.1, 3.1],
             "Low": [0.9, 1.9, 2.9], "Close": [1.5, 2.5, 3.5],
             "Volume": [100, 200, 300]},
            index=idx,
        )


def test_yfinance_quote():
    provider = YFinanceData(ticker_factory=lambda s: StubQuoteTicker(123.45))
    assert provider.get_quote("SPY").price == Decimal("123.45")


def test_yfinance_missing_price_is_unknown_symbol():
    provider = YFinanceData(ticker_factory=lambda s: StubQuoteTicker(None))
    with pytest.raises(UnknownSymbolError):
        provider.get_quote("XXXX")


def test_yfinance_bars():
    provider = YFinanceData(ticker_factory=lambda s: StubBarsTicker())
    bars = provider.get_bars("SPY", "1D", 2)
    assert len(bars) == 2
    assert bars[-1].close == Decimal("3.5")
    assert bars[-1].timestamp.tzinfo is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.marketdata.alpaca'`.

- [ ] **Step 3: Implement**

`backend/app/marketdata/alpaca.py`:

```python
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
```

`backend/app/marketdata/yfinance_provider.py`:

```python
from __future__ import annotations

from decimal import Decimal

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.timeutil import utcnow


class YFinanceData:
    """Keyless fallback provider via yfinance."""

    name = "yfinance"

    def __init__(self, ticker_factory=None):
        if ticker_factory is None:
            import yfinance as yf

            ticker_factory = yf.Ticker
        self._ticker = ticker_factory

    def get_quote(self, symbol: str) -> Quote:
        try:
            price = self._ticker(symbol).fast_info["last_price"]
        except UnknownSymbolError:
            raise
        except Exception as e:
            raise MarketDataError(f"yfinance: {e}") from e
        if price is None:
            raise UnknownSymbolError(symbol)
        return Quote(symbol=symbol, price=Decimal(str(price)), as_of=utcnow())

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        try:
            df = self._ticker(symbol).history(period="2y", interval="1d", auto_adjust=False)
        except Exception as e:
            raise MarketDataError(f"yfinance: {e}") from e
        if df.empty:
            raise UnknownSymbolError(symbol)
        bars = []
        for idx, row in df.tail(limit).iterrows():
            ts = idx.tz_convert("UTC").tz_localize(None) if idx.tzinfo else idx
            bars.append(Bar(
                timestamp=ts.to_pydatetime(),
                open=Decimal(str(row["Open"])), high=Decimal(str(row["High"])),
                low=Decimal(str(row["Low"])), close=Decimal(str(row["Close"])),
                volume=int(row["Volume"]),
            ))
        return bars
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_providers.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/marketdata/alpaca.py app/marketdata/yfinance_provider.py tests/test_providers.py
git commit -m "feat: Alpaca and yfinance market data providers"
```

### Task 5: Engine — order placement, validation, reservations, idempotency, cancel

**Files:**
- Create: `backend/app/engine/engine.py`
- Test: `backend/tests/factories.py`, `backend/tests/test_engine_placement.py`

**Interfaces:**
- Consumes: models (Task 1), `MarketDataError`/`UnknownSymbolError` (Task 3), `FakeMarketData` (Task 3).
- Produces: `InvalidOrderState(Exception)`; `TradingEngine(market_data)` with:
  - `place_order(session, *, account_id: int, symbol: str, side: str, order_type: str, qty: int, tif: str = "day", limit_price: Decimal | None = None, idempotency_key: str | None = None) -> Order` — returns a persisted Order, `status` either `"pending"` or `"rejected"`.
  - `cancel_order(session, order_id: int) -> Order` — raises `ValueError` (missing) / `InvalidOrderState` (not pending).
  - `reject_order(session, order, reason) -> Order`, `expire_order(session, order) -> Order`
  - `available_cash(session, account) -> Decimal`, `available_qty(session, account, symbol) -> int`
  - (Task 6 adds `apply_fill`.)
  Test helper `make_account(session, name="manual", cash="100000", commission="0") -> Account`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/factories.py`:

```python
from decimal import Decimal

from app.models import Account


def make_account(session, name="manual", cash="100000", commission="0"):
    acct = Account(name=name, cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal(commission))
    session.add(acct)
    session.flush()
    return acct
```

`backend/tests/test_engine_placement.py`:

```python
from decimal import Decimal

import pytest

from app.engine.engine import InvalidOrderState, TradingEngine
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


def place(engine, session, acct, **kw):
    args = dict(account_id=acct.id, symbol="SPY", side="buy",
                order_type="market", qty=10)
    args.update(kw)
    return engine.place_order(session, **args)


def test_market_buy_is_pending_with_reservation(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("1000")


def test_insufficient_cash_rejected(engine, session):
    acct = make_account(session, cash="500")
    order = place(engine, session, acct)
    assert order.status == "rejected"
    assert order.reject_reason.startswith("insufficient cash")


def test_reservations_count_against_available_cash(engine, session):
    acct = make_account(session, cash="100000")
    assert place(engine, session, acct, qty=600).status == "pending"   # reserves 60000
    assert place(engine, session, acct, qty=600).status == "rejected"  # only 40000 left


def test_unknown_symbol_rejected(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, symbol="XXXX")
    assert order.status == "rejected"
    assert order.reject_reason == "unknown symbol: XXXX"


def test_market_data_down_rejected(engine, session, md):
    acct = make_account(session)
    md.fail = True
    order = place(engine, session, acct)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"


def test_nonpositive_qty_rejected(engine, session):
    acct = make_account(session)
    assert place(engine, session, acct, qty=0).status == "rejected"


def test_limit_requires_price(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, order_type="limit")
    assert order.status == "rejected"
    assert order.reject_reason == "limit price required"


def test_limit_buy_reserves_at_limit_price(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, order_type="limit",
                  limit_price=Decimal("95"), qty=10)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("950")


def test_sell_without_shares_rejected(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct, side="sell")
    assert order.status == "rejected"
    assert order.reject_reason == "insufficient shares"


def test_idempotency_key_returns_same_order(engine, session):
    acct = make_account(session)
    a = place(engine, session, acct, idempotency_key="abc")
    b = place(engine, session, acct, idempotency_key="abc")
    assert a.id == b.id


def test_cancel_pending_releases_reservation(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    cancelled = engine.cancel_order(session, order.id)
    assert cancelled.status == "cancelled"
    assert engine.available_cash(session, acct) == Decimal("100000")


def test_cancel_non_pending_raises(engine, session):
    acct = make_account(session)
    order = place(engine, session, acct)
    engine.cancel_order(session, order.id)
    with pytest.raises(InvalidOrderState):
        engine.cancel_order(session, order.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine_placement.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.engine'`.

- [ ] **Step 3: Implement**

`backend/app/engine/engine.py`:

```python
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.models import Account, Order, Position
from app.timeutil import utcnow


class InvalidOrderState(Exception):
    pass


class TradingEngine:
    """Bookkeeping: validation, reservations, cancellation. Fill policy lives
    in the execution adapter (SimAdapter), which calls back into apply_fill."""

    def __init__(self, market_data):
        self.market_data = market_data

    def place_order(self, session, *, account_id: int, symbol: str, side: str,
                    order_type: str, qty: int, tif: str = "day",
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
            if qty > self.available_qty(session, account, order.symbol):
                return self.reject_order(session, order, "insufficient shares")

        return order

    def cancel_order(self, session, order_id: int) -> Order:
        order = session.get(Order, order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        if order.status != "pending":
            raise InvalidOrderState(f"cannot cancel order in status {order.status}")
        order.status = "cancelled"
        return order

    def reject_order(self, session, order: Order, reason: str) -> Order:
        order.status = "rejected"
        order.reject_reason = reason
        return order

    def expire_order(self, session, order: Order) -> Order:
        order.status = "expired"
        return order

    def available_cash(self, session, account: Account) -> Decimal:
        # Sum in Python: SQLite SUM over TEXT-stored decimals coerces to float.
        reserved = session.scalars(select(Order.reserved_cash).where(
            Order.account_id == account.id,
            Order.status == "pending",
            Order.side == "buy")).all()
        return account.cash - sum(reserved, Decimal("0"))

    def available_qty(self, session, account: Account, symbol: str) -> int:
        pos = session.scalar(select(Position).where(
            Position.account_id == account.id, Position.symbol == symbol))
        held = pos.qty if pos is not None else 0
        pending_sells = session.scalars(select(Order.qty).where(
            Order.account_id == account.id,
            Order.symbol == symbol,
            Order.status == "pending",
            Order.side == "sell")).all()
        return held - sum(pending_sells)
```

Note: `available_cash` counts only **pending** buy orders, so a cancelled/rejected/filled order's reservation is released implicitly — no zeroing needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_placement.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add app/engine/engine.py tests/factories.py tests/test_engine_placement.py
git commit -m "feat: order placement with validation, reservations, idempotency"
```

---

### Task 6: Engine — fills, positions, realized P&L

**Files:**
- Modify: `backend/app/engine/engine.py` (add `apply_fill` and `_get_or_create_position`)
- Test: `backend/tests/test_engine_fills.py`

**Interfaces:**
- Consumes: `TradingEngine` (Task 5), models (Task 1).
- Produces: `TradingEngine.apply_fill(session, order: Order, price: Decimal) -> Fill` — fills the whole order at `price`, updates cash/position, sets `Fill.realized_pnl` on sells, marks the order `"filled"`. Raises `InvalidOrderState` if the order isn't pending. `SimAdapter` (Tasks 7–8) calls this.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_engine_fills.py`:

```python
from decimal import Decimal

import pytest

from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Fill, Position
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


def buy(engine, session, acct, qty, price):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=qty)
    return engine.apply_fill(session, order, Decimal(price))


def sell(engine, session, acct, qty, price):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="sell", order_type="market", qty=qty)
    return engine.apply_fill(session, order, Decimal(price))


def test_buy_fill_updates_cash_and_position(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    assert acct.cash == Decimal("99000")
    pos = session.query(Position).one()
    assert pos.qty == 10
    assert pos.avg_cost == Decimal("100.0000")


def test_avg_cost_is_weighted(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    buy(engine, session, acct, 10, "110")
    pos = session.query(Position).one()
    assert pos.qty == 20
    assert pos.avg_cost == Decimal("105.0000")


def test_sell_realizes_pnl(engine, session):
    acct = make_account(session)
    buy(engine, session, acct, 10, "100")
    fill = sell(engine, session, acct, 5, "120")
    pos = session.query(Position).one()
    assert fill.realized_pnl == Decimal("100.0000")
    assert pos.realized_pnl == Decimal("100.0000")
    assert pos.qty == 5
    assert acct.cash == Decimal("99600")  # 99000 + 600


def test_commission_charged_on_both_sides(engine, session):
    acct = make_account(session, commission="1")
    buy(engine, session, acct, 10, "100")
    assert acct.cash == Decimal("98999")  # -1000 - 1
    fill = sell(engine, session, acct, 10, "110")
    assert fill.realized_pnl == Decimal("99.0000")  # 100 - 1
    assert acct.cash == Decimal("100098")  # 98999 + 1100 - 1


def test_fill_marks_order_filled_and_creates_row(engine, session):
    acct = make_account(session)
    fill = buy(engine, session, acct, 10, "100")
    assert fill.order.status == "filled"
    assert session.query(Fill).count() == 1


def test_cannot_fill_non_pending(engine, session):
    acct = make_account(session)
    fill = buy(engine, session, acct, 10, "100")
    with pytest.raises(InvalidOrderState):
        engine.apply_fill(session, fill.order, Decimal("100"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine_fills.py -q`
Expected: FAIL — `AttributeError: 'TradingEngine' object has no attribute 'apply_fill'`.

- [ ] **Step 3: Implement**

Add to `backend/app/engine/engine.py` (inside `TradingEngine`; also add `Fill` to the models import):

```python
    def apply_fill(self, session, order: Order, price: Decimal) -> Fill:
        if order.status != "pending":
            raise InvalidOrderState(f"cannot fill order in status {order.status}")
        account = session.get(Account, order.account_id)
        commission = account.commission
        fill = Fill(order_id=order.id, price=price, qty=order.qty,
                    commission=commission, filled_at=utcnow())
        pos = self._get_or_create_position(session, order.account_id, order.symbol)
        if order.side == "buy":
            account.cash -= price * order.qty + commission
            new_qty = pos.qty + order.qty
            pos.avg_cost = ((pos.avg_cost * pos.qty + price * order.qty) / new_qty
                            ).quantize(Decimal("0.0001"))
            pos.qty = new_qty
        else:
            pnl = ((price - pos.avg_cost) * order.qty - commission
                   ).quantize(Decimal("0.0001"))
            fill.realized_pnl = pnl
            pos.realized_pnl += pnl
            pos.qty -= order.qty
            account.cash += price * order.qty - commission
        order.status = "filled"
        session.add(fill)
        session.flush()
        return fill

    def _get_or_create_position(self, session, account_id: int, symbol: str) -> Position:
        pos = session.scalar(select(Position).where(
            Position.account_id == account_id, Position.symbol == symbol))
        if pos is None:
            pos = Position(account_id=account_id, symbol=symbol,
                           qty=0, avg_cost=Decimal("0"), realized_pnl=Decimal("0"))
            session.add(pos)
            session.flush()
        return pos
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_fills.py tests/test_engine_placement.py -q`
Expected: `18 passed` (new tests plus Task 5's still green)

- [ ] **Step 5: Commit**

```bash
git add app/engine/engine.py tests/test_engine_fills.py
git commit -m "feat: fills update cash, positions, and realized P&L"
```

### Task 7: SimAdapter — market orders (immediate fill and after-hours queueing)

**Files:**
- Create: `backend/app/engine/sim_adapter.py`
- Test: `backend/tests/test_sim_market.py`

**Interfaces:**
- Consumes: `TradingEngine` incl. `apply_fill` (Tasks 5–6), `FakeCalendar`, `FakeMarketData`, `Clock` (Task 3).
- Produces: `SimAdapter(engine, market_data, calendar, now_fn=utcnow)` with:
  - `place_order(session, **kwargs) -> Order` — same keyword signature as `TradingEngine.place_order`; this is the entry point the API and strategies use (the `ExecutionAdapter` interface).
  - `cancel_order(session, order_id) -> Order` — delegates to the engine.
  - `process_pending(session, now: datetime | None = None) -> None` — scheduler entry point (expiry + queued market fills here; limit checks added in Task 8).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_sim_market.py`:

```python
from datetime import datetime
from decimal import Decimal

import pytest

from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData


@pytest.fixture
def setup(session):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    clock = Clock(datetime(2026, 7, 2, 15, 0))
    engine = TradingEngine(md)
    adapter = SimAdapter(engine, md, cal, now_fn=clock)
    return md, cal, clock, adapter


def place_market(adapter, session, acct):
    return adapter.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=10)


def test_market_order_fills_immediately_when_open(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    assert order.status == "filled"
    assert acct.cash == Decimal("99000")


def test_market_order_queues_when_closed(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    assert order.status == "pending"


def test_queued_market_order_fills_on_next_open(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    cal.open = True
    md.set_quote("SPY", "102")  # next session's opening price
    adapter.process_pending(session)
    assert order.status == "filled"
    assert acct.cash == Decimal("98980")


def test_no_quote_at_fill_time_rejects_market_order(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    cal.open = True
    md.fail = True
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "market data unavailable"


def test_process_pending_does_nothing_while_closed(setup, session):
    md, cal, clock, adapter = setup
    cal.open = False
    acct = make_account(session)
    order = place_market(adapter, session, acct)
    adapter.process_pending(session)
    assert order.status == "pending"


def test_rejected_placement_passes_through(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session, cash="10")
    order = place_market(adapter, session, acct)
    assert order.status == "rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sim_market.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.sim_adapter'`.

- [ ] **Step 3: Implement**

`backend/app/engine/sim_adapter.py`:

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.engine.engine import TradingEngine
from app.marketdata.base import MarketDataError
from app.models import Order
from app.timeutil import utcnow


class SimAdapter:
    """Simulated execution: fill policy appropriate for swing trading.

    Market order while open  -> fill now at latest quote.
    Market order while closed -> queue; fill at first quote after next open
                                 (approximates the opening price).
    Limit order              -> checked periodically by process_pending (Task 8).
    """

    def __init__(self, engine: TradingEngine, market_data, calendar, now_fn=utcnow):
        self.engine = engine
        self.market_data = market_data
        self.calendar = calendar
        self.now_fn = now_fn

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
        pending = session.scalars(
            select(Order).where(Order.status == "pending")).all()

        for order in pending:
            if order.tif == "day" and now >= self.calendar.expiry_time(order.placed_at):
                self.engine.expire_order(session, order)

        if not self.calendar.is_open(now):
            return

        for order in pending:
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
            # Spec: reject rather than fill at a stale/unknown price.
            self.engine.reject_order(session, order, "market data unavailable")
            return
        self.engine.apply_fill(session, order, quote.price)

    def _check_limit(self, session, order: Order) -> None:
        try:
            quote = self.market_data.get_quote(order.symbol)
        except MarketDataError:
            return  # spec: pending limit orders wait for the next successful check
        crossed = (quote.price <= order.limit_price if order.side == "buy"
                   else quote.price >= order.limit_price)
        if crossed:
            self.engine.apply_fill(session, order, order.limit_price)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sim_market.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/engine/sim_adapter.py tests/test_sim_market.py
git commit -m "feat: SimAdapter market order fills with after-hours queueing"
```

---

### Task 8: SimAdapter — limit orders and day-order expiry

**Files:**
- Modify: `backend/app/engine/sim_adapter.py` (already contains `_check_limit` from Task 7 — this task verifies limit/expiry behavior with tests; fix the implementation if any test disagrees)
- Test: `backend/tests/test_sim_limit_expiry.py`

**Interfaces:**
- Consumes: `SimAdapter` (Task 7), fakes (Task 3).
- Produces: verified limit-fill and expiry semantics: buy fills when quote ≤ limit, sell when quote ≥ limit, fill price = limit price; day orders expire once `now >= calendar.expiry_time(placed_at)`; GTC orders persist; outages leave limit orders pending.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_sim_limit_expiry.py`:

```python
from datetime import datetime
from decimal import Decimal

import pytest

from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData


@pytest.fixture
def setup(session):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    clock = Clock(datetime(2026, 7, 2, 15, 0))
    engine = TradingEngine(md)
    adapter = SimAdapter(engine, md, cal, now_fn=clock)
    return md, cal, clock, adapter


def place_limit(adapter, session, acct, side="buy", limit="95", tif="day", qty=10):
    return adapter.place_order(session, account_id=acct.id, symbol="SPY",
                               side=side, order_type="limit", qty=qty,
                               tif=tif, limit_price=Decimal(limit))


def test_buy_limit_waits_above_limit(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, limit="95")
    adapter.process_pending(session)
    assert order.status == "pending"


def test_buy_limit_fills_at_limit_price_when_crossed(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, limit="95")
    md.set_quote("SPY", "94")
    adapter.process_pending(session)
    assert order.status == "filled"
    assert order.account.cash == Decimal("99050")  # filled at 95, not 94


def test_sell_limit_fills_when_crossed(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    buy = adapter.place_order(session, account_id=acct.id, symbol="SPY",
                              side="buy", order_type="market", qty=10)
    assert buy.status == "filled"
    order = place_limit(adapter, session, acct, side="sell", limit="105")
    md.set_quote("SPY", "110")
    adapter.process_pending(session)
    assert order.status == "filled"
    assert acct.cash == Decimal("100050")  # 99000 + 10*105


def test_day_order_expires_after_session_close(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, tif="day")
    cal.expiry_at = datetime(2026, 7, 2, 20, 0)
    adapter.process_pending(session, now=datetime(2026, 7, 2, 20, 1))
    assert order.status == "expired"


def test_gtc_order_survives_expiry_sweep(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct, tif="gtc")
    adapter.process_pending(session, now=datetime(2026, 7, 10, 20, 1))
    assert order.status == "pending"


def test_outage_leaves_limit_order_pending(setup, session):
    md, cal, clock, adapter = setup
    acct = make_account(session)
    order = place_limit(adapter, session, acct)
    md.fail = True
    adapter.process_pending(session)
    assert order.status == "pending"
```

- [ ] **Step 2: Run tests to verify current behavior**

Run: `uv run pytest tests/test_sim_limit_expiry.py -q`
Expected: `6 passed` if Task 7's `_check_limit` and expiry sweep are correct — otherwise fix `sim_adapter.py` until green. (These tests are the acceptance gate for limit/expiry semantics; do not weaken them to fit the code.)

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sim_limit_expiry.py app/engine/sim_adapter.py
git commit -m "test: limit order fills and day-order expiry semantics"
```

---

### Task 9: Valuation and equity snapshots

**Files:**
- Create: `backend/app/engine/valuation.py`
- Test: `backend/tests/test_valuation.py`

**Interfaces:**
- Consumes: models (Task 1), market data errors (Task 3), engine fixtures (Tasks 5–6), `FakeCalendar` (Task 3).
- Produces: `ny_date(dt_utc: datetime) -> date`; `PositionValue` dataclass (`symbol, qty, avg_cost, last_price, market_value, unrealized_pnl, realized_pnl`); `position_values(session, account, market_data) -> list[PositionValue]`; `account_equity(session, account, market_data) -> Decimal`; `take_snapshots(session, market_data, calendar, now=None) -> None`. Tasks 12–14 use all of these.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_valuation.py`:

```python
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity, ny_date, position_values, take_snapshots
from app.models import EquitySnapshot
from tests.factories import make_account
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def md():
    f = FakeMarketData()
    f.set_quote("SPY", "100")
    return f


@pytest.fixture
def engine(md):
    return TradingEngine(md)


def open_position(engine, session, acct, qty=10, price="100"):
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=qty)
    engine.apply_fill(session, order, Decimal(price))


def test_ny_date_converts_from_utc():
    # 01:00 UTC on July 3 is still July 2 in New York (EDT, UTC-4).
    assert ny_date(datetime(2026, 7, 3, 1, 0)) == date(2026, 7, 2)


def test_position_values_and_unrealized(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    [pv] = position_values(session, acct, md)
    assert pv.market_value == Decimal("1100")
    assert pv.unrealized_pnl == Decimal("100")


def test_account_equity(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct, qty=10, price="100")
    md.set_quote("SPY", "110")
    assert account_equity(session, acct, md) == Decimal("100100")  # 99000 + 1100


def test_take_snapshots_writes_one_row_per_account(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    take_snapshots(session, md, FakeCalendar(), now=datetime(2026, 7, 2, 20, 10))
    snap = session.query(EquitySnapshot).one()
    assert snap.date == date(2026, 7, 2)
    assert snap.equity == Decimal("100000")  # 99000 cash + 1000 position


def test_take_snapshots_same_day_updates_not_duplicates(engine, session, md):
    acct = make_account(session)
    now = datetime(2026, 7, 2, 20, 10)
    take_snapshots(session, md, FakeCalendar(), now=now)
    take_snapshots(session, md, FakeCalendar(), now=now)
    assert session.query(EquitySnapshot).count() == 1


def test_take_snapshots_skips_non_trading_day(session, md):
    make_account(session)
    take_snapshots(session, md, FakeCalendar(trading_day=False),
                   now=datetime(2026, 7, 3, 20, 10))
    assert session.query(EquitySnapshot).count() == 0


def test_take_snapshots_skips_account_on_data_outage(engine, session, md):
    acct = make_account(session)
    open_position(engine, session, acct)
    md.fail = True
    take_snapshots(session, md, FakeCalendar(), now=datetime(2026, 7, 2, 20, 10))
    assert session.query(EquitySnapshot).count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_valuation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.valuation'`.

- [ ] **Step 3: Implement**

`backend/app/engine/valuation.py`:

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
    qty: int
    avg_cost: Decimal
    last_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal


def position_values(session, account: Account, market_data) -> list[PositionValue]:
    out = []
    positions = session.scalars(select(Position).where(
        Position.account_id == account.id, Position.qty > 0)).all()
    for pos in positions:
        quote = market_data.get_quote(pos.symbol)
        out.append(PositionValue(
            symbol=pos.symbol, qty=pos.qty, avg_cost=pos.avg_cost,
            last_price=quote.price, market_value=quote.price * pos.qty,
            unrealized_pnl=(quote.price - pos.avg_cost) * pos.qty,
            realized_pnl=pos.realized_pnl))
    return out


def account_equity(session, account: Account, market_data) -> Decimal:
    values = position_values(session, account, market_data)
    return account.cash + sum((pv.market_value for pv in values), Decimal("0"))


def take_snapshots(session, market_data, calendar, now: datetime | None = None) -> None:
    now = now or utcnow()
    d = ny_date(now)
    if not calendar.is_trading_day(d):
        return
    for account in session.scalars(select(Account)).all():
        try:
            equity = account_equity(session, account, market_data)
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_valuation.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/engine/valuation.py tests/test_valuation.py
git commit -m "feat: position valuation, account equity, and daily snapshots"
```

### Task 10: Strategy framework, runner, and example strategy

**Files:**
- Create: `backend/app/strategy/__init__.py` (empty), `backend/app/strategy/base.py`, `backend/app/strategy/runner.py`, `backend/strategies/sma_cross.py`
- Test: `backend/tests/test_strategy_runner.py`

**Interfaces:**
- Consumes: `SimAdapter` (Task 7), models (Task 1), `ny_date` (Task 9), fakes (Task 3).
- Produces:
  - `Strategy` base class: class attrs `name: str | None = None`, `schedule: str = "daily_after_close"` (or a 5-field cron string, NY time); method `run(self, ctx)`; classmethod `strategy_name() -> str`.
  - `Context` with `get_quote(symbol)`, `get_bars(symbol, timeframe="1D", limit=200)`, property `cash`, `positions() -> list[Position]`, `orders(status=None) -> list[Order]`, `buy(symbol, qty, limit_price=None, tif="day") -> Order`, `sell(...) -> Order`, `cancel(order_id) -> Order`, attr `placed: list[int]` (order ids placed this run). `buy`/`sell`/`cancel` commit immediately, so work done before a later crash survives.
  - `StrategyRunner(strategies_dir: Path, session_factory, execution, market_data, calendar, starting_cash: Decimal)` with `discover()`, `sync_accounts()`, `run_strategy(name) -> StrategyRun | None`, `register_jobs(scheduler)`, and dict attr `strategies: dict[str, type[Strategy]]`. Strategy accounts are named `strategy:<Name>`, `kind="strategy"`, disabled by default.
  - Example `SmaCross` strategy (20/50-day SMA cross on SPY).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_strategy_runner.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest

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


@pytest.fixture
def runner(tmp_path, session_factory):
    (tmp_path / "buy_one.py").write_text(GOOD_STRATEGY)
    (tmp_path / "exploder.py").write_text(BAD_STRATEGY)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, FakeCalendar(open_=True))
    r = StrategyRunner(Path(tmp_path), session_factory, execution, md,
                       FakeCalendar(), Decimal("100000"))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategy_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy'`.

- [ ] **Step 3: Implement**

`backend/app/strategy/base.py`:

```python
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Order, Position


class Strategy:
    """Subclass in backend/strategies/*.py. The runner gives each strategy its
    own account; run() is called on `schedule` with a Context bound to it."""

    name: str | None = None
    schedule: str = "daily_after_close"  # or a 5-field cron string (NY time)

    def run(self, ctx: "Context") -> None:
        raise NotImplementedError

    @classmethod
    def strategy_name(cls) -> str:
        return cls.name or cls.__name__


class Context:
    """Exactly the capabilities a manual trader has via the UI — nothing more,
    so strategies stay portable to live trading."""

    def __init__(self, session, account, execution, market_data):
        self._session = session
        self._account = account
        self._execution = execution
        self._md = market_data
        self.placed: list[int] = []

    def get_quote(self, symbol: str):
        return self._md.get_quote(symbol)

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        return self._md.get_bars(symbol, timeframe, limit)

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

    def buy(self, symbol: str, qty: int, limit_price: Decimal | None = None,
            tif: str = "day") -> Order:
        return self._place("buy", symbol, qty, limit_price, tif)

    def sell(self, symbol: str, qty: int, limit_price: Decimal | None = None,
             tif: str = "day") -> Order:
        return self._place("sell", symbol, qty, limit_price, tif)

    def cancel(self, order_id: int) -> Order:
        order = self._execution.cancel_order(self._session, order_id)
        self._session.commit()
        return order

    def _place(self, side, symbol, qty, limit_price, tif) -> Order:
        order = self._execution.place_order(
            self._session, account_id=self._account.id, symbol=symbol,
            side=side, order_type="limit" if limit_price is not None else "market",
            qty=qty, tif=tif, limit_price=limit_price)
        self._session.commit()  # each order commits: survives a later crash
        self.placed.append(order.id)
        return order
```

`backend/app/strategy/runner.py`:

```python
from __future__ import annotations

import importlib.util
import traceback
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.engine.valuation import ny_date
from app.models import Account, StrategyRun, StrategyState
from app.strategy.base import Context, Strategy
from app.timeutil import utcnow

NY_TZ = ZoneInfo("America/New_York")


class StrategyRunner:
    def __init__(self, strategies_dir: Path, session_factory, execution,
                 market_data, calendar, starting_cash: Decimal):
        self.strategies_dir = strategies_dir
        self.session_factory = session_factory
        self.execution = execution
        self.market_data = market_data
        self.calendar = calendar
        self.starting_cash = starting_cash
        self.strategies: dict[str, type[Strategy]] = {}

    def discover(self) -> None:
        for path in sorted(self.strategies_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(
                f"user_strategies_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
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
            if (cls.schedule == "daily_after_close"
                    and not self.calendar.is_trading_day(ny_date(utcnow()))):
                return None
            account = session.scalar(select(Account).where(
                Account.name == f"strategy:{name}"))
            run = StrategyRun(strategy_name=name, started_at=utcnow())
            ctx = Context(session, account, self.execution, self.market_data)
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
            trigger = (CronTrigger(day_of_week="mon-fri", hour=16, minute=5,
                                   timezone=NY_TZ)
                       if cls.schedule == "daily_after_close"
                       else CronTrigger.from_crontab(cls.schedule, timezone=NY_TZ))
            scheduler.add_job(self.run_strategy, trigger, args=[name],
                              id=f"strategy:{name}", replace_existing=True)
```

`backend/strategies/sma_cross.py` (the shipped example):

```python
from decimal import Decimal

from app.strategy.base import Strategy


class SmaCross(Strategy):
    """Hold SPY while its 20-day SMA is above its 50-day SMA."""

    schedule = "daily_after_close"

    def run(self, ctx):
        bars = ctx.get_bars("SPY", "1D", 60)
        closes = [b.close for b in bars]
        if len(closes) < 50:
            return
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        held = {p.symbol: p for p in ctx.positions()}.get("SPY")
        if sma20 > sma50 and held is None:
            price = ctx.get_quote("SPY").price
            qty = int((ctx.cash * Decimal("0.95")) / price)
            if qty > 0:
                ctx.buy("SPY", qty)  # market order after close -> fills at next open
        elif sma20 < sma50 and held is not None:
            ctx.sell("SPY", held.qty)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_runner.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add app/strategy strategies tests/test_strategy_runner.py
git commit -m "feat: strategy framework, runner with error containment, SMA example"
```

---

### Task 11: App factory and auth

**Files:**
- Create: `backend/app/api/__init__.py` (empty), `backend/app/api/deps.py`, `backend/app/api/schemas.py`, `backend/app/api/auth.py`, `backend/app/main.py`
- Modify: `backend/tests/conftest.py` (add the `client` fixture)
- Test: `backend/tests/test_api_auth.py`

**Interfaces:**
- Consumes: everything built so far.
- Produces:
  - `AppDeps` dataclass: fields `settings, session_factory, market_data, calendar, engine, execution, runner`.
  - `build_deps(settings=None, market_data=None, calendar=None) -> AppDeps` (real providers/DB).
  - `create_app(deps=None, start_scheduler=True) -> FastAPI` — ensures the `manual` account exists, runs `runner.discover()` + `runner.sync_accounts()`, mounts routers under `/api`, stores deps at `app.state.deps`. (Scheduler wiring lands in Task 14; until then `create_app` accepts and ignores `start_scheduler`.)
  - Auth: `POST /api/login {password}` sets `pt_session` cookie (itsdangerous-signed, 30-day max age); `require_auth` FastAPI dependency; `GET /api/health` unauthenticated.
  - `schemas.Money` — `Annotated[Decimal, PlainSerializer(str, ...)]`: **all money fields in responses are JSON strings.**
  - Test fixture `client` — TestClient on a fully-faked app, already logged in; `client.fake_md` and `client.fake_cal` expose the fakes for later API tests.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_api_auth.py`:

```python
def test_health_is_open(client):
    import httpx

    fresh = httpx.Client(transport=httpx.ASGITransport(app=client.app),
                         base_url="http://test")
    assert fresh.get("/api/health").status_code == 200


def test_protected_route_requires_login(client):
    import httpx

    fresh = httpx.Client(transport=httpx.ASGITransport(app=client.app),
                         base_url="http://test")
    assert fresh.get("/api/accounts").status_code == 401


def test_wrong_password_rejected(client):
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_login_then_access(client):
    # the fixture already logged in
    assert client.get("/api/accounts").status_code == 200
```

Add to `backend/tests/conftest.py`:

```python
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.main import AppDeps, create_app
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def client(session_factory, tmp_path):
    fake_md = FakeMarketData()
    fake_md.set_quote("SPY", "100")
    fake_cal = FakeCalendar(open_=True)
    engine = TradingEngine(fake_md)
    execution = SimAdapter(engine, fake_md, fake_cal)
    settings = Settings(password="pw", secret_key="test-secret")
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    runner = StrategyRunner(Path(strategies_dir), session_factory, execution,
                            fake_md, fake_cal, settings.starting_cash)
    deps = AppDeps(settings=settings, session_factory=session_factory,
                   market_data=fake_md, calendar=fake_cal, engine=engine,
                   execution=execution, runner=runner)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.fake_md = fake_md
    c.fake_cal = fake_cal
    return c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Implement**

`backend/app/api/schemas.py` (started here, extended in Tasks 12–13):

```python
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

# All money crosses the API as strings — no float rounding in transit.
Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]


class LoginIn(BaseModel):
    password: str
```

`backend/app/api/deps.py`:

```python
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "pt_session"
SESSION_MAX_AGE = 30 * 86400


def serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="pt-session")


def get_deps(request: Request):
    return request.app.state.deps


def get_session(deps=Depends(get_deps)):
    with deps.session_factory() as session:
        yield session
        session.commit()


def require_auth(request: Request, deps=Depends(get_deps)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not authenticated")
    try:
        serializer(deps.settings.secret_key).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "invalid session")
```

`backend/app/api/auth.py`:

```python
import hmac

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import SESSION_COOKIE, SESSION_MAX_AGE, get_deps, serializer
from app.api.schemas import LoginIn

router = APIRouter()


@router.post("/login")
def login(body: LoginIn, response: Response, deps=Depends(get_deps)):
    if not hmac.compare_digest(body.password, deps.settings.password):
        raise HTTPException(401, "wrong password")
    token = serializer(deps.settings.secret_key).dumps({"u": "owner"})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_MAX_AGE)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}
```

`backend/app/main.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import auth
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.engine.calendar import MarketCalendar
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.marketdata.alpaca import AlpacaData
from app.marketdata.service import MarketDataService
from app.marketdata.yfinance_provider import YFinanceData
from app.models import Account
from app.strategy.runner import StrategyRunner

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"


@dataclass
class AppDeps:
    settings: Settings
    session_factory: object
    market_data: object
    calendar: object
    engine: TradingEngine
    execution: SimAdapter
    runner: StrategyRunner


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
    runner = StrategyRunner(STRATEGIES_DIR, session_factory, execution,
                            market_data, calendar, settings.starting_cash)
    return AppDeps(settings=settings, session_factory=session_factory,
                   market_data=market_data, calendar=calendar, engine=engine,
                   execution=execution, runner=runner)


def create_app(deps: AppDeps | None = None, start_scheduler: bool = True) -> FastAPI:
    deps = deps or build_deps()

    with deps.session_factory() as session:
        if session.scalar(select(Account).where(Account.name == "manual")) is None:
            session.add(Account(name="manual", kind="manual",
                                cash=deps.settings.starting_cash,
                                starting_cash=deps.settings.starting_cash))
            session.commit()
    deps.runner.discover()
    deps.runner.sync_accounts()

    app = FastAPI(title="Paper Trading Platform")
    app.state.deps = deps
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.include_router(auth.router, prefix="/api")
    return app
```

(`start_scheduler` is accepted but unused until Task 14 wires the scheduler in.)

Also add a placeholder protected route so the auth tests can exercise `require_auth` — create `backend/app/api/accounts.py`:

```python
from fastapi import APIRouter, Depends

from app.api.deps import get_session, require_auth
from app.models import Account
from sqlalchemy import select

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/accounts")
def list_accounts(session=Depends(get_session)):
    return [{"id": a.id, "name": a.name} for a in session.scalars(select(Account))]
```

and include it in `main.py` after the auth router:

```python
from app.api import accounts

    app.include_router(accounts.router, prefix="/api")
```

(Task 12 replaces this stub's response with full schemas.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_auth.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/api app/main.py tests/conftest.py tests/test_api_auth.py
git commit -m "feat: app factory, password auth with signed session cookie"
```

### Task 12: Accounts and orders API

**Files:**
- Modify: `backend/app/api/schemas.py`, `backend/app/api/accounts.py` (replace Task 11 stub)
- Create: `backend/app/api/orders.py`
- Modify: `backend/app/main.py` (include orders router)
- Test: `backend/tests/test_api_accounts_orders.py`

**Interfaces:**
- Consumes: `client` fixture (Task 11), valuation functions (Task 9), `SimAdapter.place_order` (Task 7), `InvalidOrderState` (Task 5).
- Produces endpoints:
  - `GET /api/accounts` → `list[AccountOut]`
  - `GET /api/accounts/{id}` → `AccountDetailOut` (503 if market data down)
  - `GET /api/accounts/{id}/snapshots` → `list[SnapshotOut]`
  - `POST /api/accounts/{id}/orders` (201) → `OrderOut`; body `OrderIn`
  - `GET /api/accounts/{id}/orders?status=` → `list[OrderOut]`
  - `POST /api/orders/{id}/cancel` → `OrderOut` (404 missing, 409 not pending)
  - `PUT /api/orders/{id}/note` body `NoteIn` → `{"ok": true}` (404 if order missing)
  Schemas added: `OrderIn`, `OrderOut`, `AccountOut`, `PositionOut`, `AccountDetailOut`, `SnapshotOut`, `NoteIn`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_api_accounts_orders.py`:

```python
def place(client, body=None):
    payload = {"symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_place_market_order_fills(client):
    r = place(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "filled"
    assert body["symbol"] == "SPY"


def test_rejected_order_reports_reason(client):
    r = place(client, {"qty": 10_000_000})
    assert r.status_code == 201
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"].startswith("insufficient cash")


def test_idempotency_key_returns_same_order(client):
    a = place(client, {"idempotency_key": "k1"}).json()
    b = place(client, {"idempotency_key": "k1"}).json()
    assert a["id"] == b["id"]


def test_list_orders_filters_by_status(client):
    place(client)
    client.fake_cal.open = False
    place(client)  # queues -> pending
    filled = client.get("/api/accounts/1/orders?status=filled").json()
    pending = client.get("/api/accounts/1/orders?status=pending").json()
    assert len(filled) == 1 and len(pending) == 1


def test_cancel_pending_order(client):
    client.fake_cal.open = False
    order = place(client).json()
    r = client.post(f"/api/orders/{order['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_filled_order_is_409(client):
    order = place(client).json()
    assert client.post(f"/api/orders/{order['id']}/cancel").status_code == 409


def test_account_detail_shows_positions_and_equity(client):
    place(client)
    client.fake_md.set_quote("SPY", "110")
    detail = client.get("/api/accounts/1").json()
    assert detail["name"] == "manual"
    assert detail["cash"] == "99000"
    assert detail["equity"] == "100100"
    [pos] = detail["positions"]
    assert pos["symbol"] == "SPY"
    assert pos["unrealized_pnl"] == "100"


def test_account_detail_503_when_data_down(client):
    place(client)
    client.fake_md.fail = True
    assert client.get("/api/accounts/1").status_code == 503


def test_note_upsert(client):
    order = place(client).json()
    r = client.put(f"/api/orders/{order['id']}/note", json={"text": "breakout entry"})
    assert r.status_code == 200
    assert client.put(f"/api/orders/{order['id']}/note",
                      json={"text": "revised"}).status_code == 200


def test_snapshots_endpoint_empty_initially(client):
    assert client.get("/api/accounts/1/snapshots").json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_accounts_orders.py -q`
Expected: FAIL — 404/405 errors (routes don't exist yet).

- [ ] **Step 3: Implement**

Add to `backend/app/api/schemas.py`:

```python
class OrderIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    qty: int
    tif: Literal["day", "gtc"] = "day"
    limit_price: Decimal | None = None
    idempotency_key: str | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    symbol: str
    side: str
    order_type: str
    tif: str
    qty: int
    limit_price: Money | None
    status: str
    reject_reason: str | None
    placed_at: datetime


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    cash: Money
    starting_cash: Money


class PositionOut(BaseModel):
    symbol: str
    qty: int
    avg_cost: Money
    last_price: Money
    market_value: Money
    unrealized_pnl: Money
    realized_pnl: Money


class AccountDetailOut(AccountOut):
    equity: Money
    positions: list[PositionOut]


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: str
    equity: Money
    cash: Money


class NoteIn(BaseModel):
    text: str
```

Note: `SnapshotOut.date` is `str` — convert with `str(snap.date)` when building responses.

Replace `backend/app/api/accounts.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import AccountDetailOut, AccountOut, PositionOut, SnapshotOut
from app.engine.valuation import account_equity, position_values
from app.marketdata.base import MarketDataError
from app.models import Account, EquitySnapshot

router = APIRouter(dependencies=[Depends(require_auth)])


def _account_or_404(session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "no such account")
    return account


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(session=Depends(get_session)):
    return session.scalars(select(Account)).all()


@router.get("/accounts/{account_id}", response_model=AccountDetailOut)
def account_detail(account_id: int, session=Depends(get_session),
                   deps=Depends(get_deps)):
    account = _account_or_404(session, account_id)
    try:
        values = position_values(session, account, deps.market_data)
        equity = account_equity(session, account, deps.market_data)
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return AccountDetailOut(
        id=account.id, name=account.name, kind=account.kind,
        cash=account.cash, starting_cash=account.starting_cash, equity=equity,
        positions=[PositionOut(**vars(pv)) for pv in values])


@router.get("/accounts/{account_id}/snapshots", response_model=list[SnapshotOut])
def snapshots(account_id: int, session=Depends(get_session)):
    _account_or_404(session, account_id)
    rows = session.scalars(select(EquitySnapshot)
                           .where(EquitySnapshot.account_id == account_id)
                           .order_by(EquitySnapshot.date)).all()
    return [SnapshotOut(date=str(r.date), equity=r.equity, cash=r.cash)
            for r in rows]
```

(`PositionOut(**vars(pv))` works because `PositionValue` is a plain dataclass with matching field names.)

Create `backend/app/api/orders.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import NoteIn, OrderIn, OrderOut
from app.engine.engine import InvalidOrderState
from app.models import Account, JournalNote, Order

router = APIRouter(dependencies=[Depends(require_auth)])


@router.post("/accounts/{account_id}/orders", response_model=OrderOut,
             status_code=201)
def place_order(account_id: int, body: OrderIn, session=Depends(get_session),
                deps=Depends(get_deps)):
    if session.get(Account, account_id) is None:
        raise HTTPException(404, "no such account")
    return deps.execution.place_order(
        session, account_id=account_id, symbol=body.symbol, side=body.side,
        order_type=body.order_type, qty=body.qty, tif=body.tif,
        limit_price=body.limit_price, idempotency_key=body.idempotency_key)


@router.get("/accounts/{account_id}/orders", response_model=list[OrderOut])
def list_orders(account_id: int, status: str | None = None,
                session=Depends(get_session)):
    stmt = (select(Order).where(Order.account_id == account_id)
            .order_by(Order.placed_at.desc()))
    if status is not None:
        stmt = stmt.where(Order.status == status)
    return session.scalars(stmt).all()


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, session=Depends(get_session),
                 deps=Depends(get_deps)):
    try:
        return deps.execution.cancel_order(session, order_id)
    except ValueError:
        raise HTTPException(404, "no such order")
    except InvalidOrderState as e:
        raise HTTPException(409, str(e))


@router.put("/orders/{order_id}/note")
def upsert_note(order_id: int, body: NoteIn, session=Depends(get_session)):
    if session.get(Order, order_id) is None:
        raise HTTPException(404, "no such order")
    note = session.scalar(select(JournalNote).where(
        JournalNote.order_id == order_id))
    if note is None:
        session.add(JournalNote(order_id=order_id, text=body.text))
    else:
        note.text = body.text
    return {"ok": True}
```

In `backend/app/main.py`, import and include the orders router:

```python
from app.api import accounts, auth, orders

    app.include_router(accounts.router, prefix="/api")
    app.include_router(orders.router, prefix="/api")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_accounts_orders.py tests/test_api_auth.py -q`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add app/api app/main.py tests/test_api_accounts_orders.py
git commit -m "feat: accounts and orders REST API"
```

---

### Task 13: Market, journal, and strategies API

**Files:**
- Modify: `backend/app/api/schemas.py`
- Create: `backend/app/api/market.py`, `backend/app/api/journal.py`, `backend/app/api/strategies.py`
- Modify: `backend/app/main.py` (include the three routers)
- Test: `backend/tests/test_api_market_journal_strategies.py`

**Interfaces:**
- Consumes: `client` fixture, market data service errors, `Fill.realized_pnl` (Task 6), `StrategyRunner`/`StrategyState`/`StrategyRun` (Task 10).
- Produces endpoints:
  - `GET /api/market/quote/{symbol}` → `QuoteOut` (404 unknown, 503 down)
  - `GET /api/market/bars/{symbol}?limit=200` → `list[BarOut]`
  - `GET /api/journal?account_id=1` → `list[TradeOut]` (fills newest-first with notes)
  - `GET /api/journal/stats?account_id=1` → `StatsOut`
  - `GET /api/strategies` → `list[StrategyOut]`; `POST /api/strategies/{name}/toggle` → `StrategyOut`; `GET /api/strategies/{name}/runs` → `list[RunOut]`
  Schemas added: `QuoteOut`, `BarOut`, `TradeOut`, `StatsOut`, `StrategyOut`, `RunOut`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_api_market_journal_strategies.py`:

```python
def place(client, body=None):
    payload = {"symbol": "SPY", "side": "buy", "order_type": "market", "qty": 10}
    if body:
        payload.update(body)
    return client.post("/api/accounts/1/orders", json=payload)


def test_quote_endpoint(client):
    r = client.get("/api/market/quote/SPY")
    assert r.status_code == 200
    assert r.json()["price"] == "100"
    assert "as_of" in r.json()


def test_quote_unknown_symbol_404(client):
    assert client.get("/api/market/quote/XXXX").status_code == 404


def test_quote_outage_503(client):
    client.fake_md.fail = True
    assert client.get("/api/market/quote/SPY").status_code == 503


def test_bars_endpoint(client):
    client.fake_md.set_bars("SPY", ["1", "2", "3"])
    bars = client.get("/api/market/bars/SPY?limit=2").json()
    assert len(bars) == 2
    assert bars[-1]["close"] == "3"


def test_journal_lists_trades_with_notes(client):
    order = place(client).json()
    client.put(f"/api/orders/{order['id']}/note", json={"text": "entry note"})
    place(client, {"side": "sell", "qty": 5})
    trades = client.get("/api/journal?account_id=1").json()
    assert len(trades) == 2
    sell, buy = trades  # newest first
    assert sell["side"] == "sell"
    assert sell["realized_pnl"] == "0.0000"
    assert buy["note"] == "entry note"


def test_journal_stats(client):
    place(client)                                   # buy 10 @ 100
    place(client, {"side": "sell", "qty": 5})       # realized 0 (neither win nor loss)
    client.fake_md.set_quote("SPY", "120")
    place(client, {"side": "sell", "qty": 5})       # realized +100 -> win
    stats = client.get("/api/journal/stats?account_id=1").json()
    assert stats["closed_trades"] == 2
    assert stats["wins"] == 1


def test_strategies_list_and_toggle(client):
    # the client fixture's strategies dir is empty -> empty list
    assert client.get("/api/strategies").json() == []
    assert client.post("/api/strategies/Nope/toggle").status_code == 404


def test_strategy_toggle_and_runs(client, tmp_path):
    # register a strategy directly on the app's runner
    from app.strategy.base import Strategy

    class Manual(Strategy):
        def run(self, ctx):
            pass

    runner = client.app.state.deps.runner
    runner.strategies["Manual"] = Manual
    runner.sync_accounts()

    [s] = client.get("/api/strategies").json()
    assert s["name"] == "Manual" and s["enabled"] is False
    toggled = client.post("/api/strategies/Manual/toggle").json()
    assert toggled["enabled"] is True
    assert client.get("/api/strategies/Manual/runs").json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_market_journal_strategies.py -q`
Expected: FAIL — 404s (routes don't exist yet).

- [ ] **Step 3: Implement**

Add to `backend/app/api/schemas.py`:

```python
class QuoteOut(BaseModel):
    symbol: str
    price: Money
    as_of: datetime


class BarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: int


class TradeOut(BaseModel):
    order_id: int
    symbol: str
    side: str
    qty: int
    price: Money
    commission: Money
    realized_pnl: Money | None
    filled_at: datetime
    note: str | None


class StatsOut(BaseModel):
    closed_trades: int
    wins: int
    win_rate: float | None
    avg_gain: Money | None
    avg_loss: Money | None


class StrategyOut(BaseModel):
    name: str
    schedule: str
    enabled: bool
    account_id: int


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_name: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    detail: str
```

Create `backend/app/api/market.py`:

```python
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_deps, require_auth
from app.api.schemas import BarOut, QuoteOut
from app.marketdata.base import MarketDataError, UnknownSymbolError

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/market/quote/{symbol}", response_model=QuoteOut)
def quote(symbol: str, deps=Depends(get_deps)):
    try:
        q = deps.market_data.get_quote(symbol.upper())
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol.upper()}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of)


@router.get("/market/bars/{symbol}", response_model=list[BarOut])
def bars(symbol: str, limit: int = 200, deps=Depends(get_deps)):
    try:
        return deps.market_data.get_bars(symbol.upper(), "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, f"unknown symbol: {symbol.upper()}")
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
```

Create `backend/app/api/journal.py`:

```python
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_session, require_auth
from app.api.schemas import StatsOut, TradeOut
from app.models import Fill, JournalNote, Order

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/journal", response_model=list[TradeOut])
def journal(account_id: int, session=Depends(get_session)):
    fills = session.scalars(
        select(Fill).join(Order).where(Order.account_id == account_id)
        .order_by(Fill.filled_at.desc(), Fill.id.desc())).all()
    notes = {n.order_id: n.text for n in session.scalars(select(JournalNote))}
    return [TradeOut(order_id=f.order_id, symbol=f.order.symbol,
                     side=f.order.side, qty=f.qty, price=f.price,
                     commission=f.commission, realized_pnl=f.realized_pnl,
                     filled_at=f.filled_at, note=notes.get(f.order_id))
            for f in fills]


@router.get("/journal/stats", response_model=StatsOut)
def stats(account_id: int, session=Depends(get_session)):
    realized = [f.realized_pnl for f in session.scalars(
        select(Fill).join(Order).where(
            Order.account_id == account_id,
            Fill.realized_pnl.is_not(None))).all()]
    if not realized:
        return StatsOut(closed_trades=0, wins=0, win_rate=None,
                        avg_gain=None, avg_loss=None)
    gains = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]
    return StatsOut(
        closed_trades=len(realized),
        wins=len(gains),
        win_rate=len(gains) / len(realized),
        avg_gain=(sum(gains, Decimal("0")) / len(gains)).quantize(Decimal("0.01"))
        if gains else None,
        avg_loss=(sum(losses, Decimal("0")) / len(losses)).quantize(Decimal("0.01"))
        if losses else None)
```

Create `backend/app/api/strategies.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import RunOut, StrategyOut
from app.models import Account, StrategyRun, StrategyState

router = APIRouter(dependencies=[Depends(require_auth)])


def _strategy_out(session, deps, name: str) -> StrategyOut:
    cls = deps.runner.strategies[name]
    state = session.scalar(select(StrategyState).where(StrategyState.name == name))
    account = session.scalar(select(Account).where(
        Account.name == f"strategy:{name}"))
    return StrategyOut(name=name, schedule=cls.schedule,
                       enabled=bool(state and state.enabled),
                       account_id=account.id)


@router.get("/strategies", response_model=list[StrategyOut])
def list_strategies(session=Depends(get_session), deps=Depends(get_deps)):
    return [_strategy_out(session, deps, name)
            for name in sorted(deps.runner.strategies)]


@router.post("/strategies/{name}/toggle", response_model=StrategyOut)
def toggle(name: str, session=Depends(get_session), deps=Depends(get_deps)):
    if name not in deps.runner.strategies:
        raise HTTPException(404, f"no such strategy: {name}")
    state = session.scalar(select(StrategyState).where(StrategyState.name == name))
    state.enabled = not state.enabled
    session.flush()
    return _strategy_out(session, deps, name)


@router.get("/strategies/{name}/runs", response_model=list[RunOut])
def runs(name: str, limit: int = 20, session=Depends(get_session),
         deps=Depends(get_deps)):
    if name not in deps.runner.strategies:
        raise HTTPException(404, f"no such strategy: {name}")
    return session.scalars(
        select(StrategyRun).where(StrategyRun.strategy_name == name)
        .order_by(StrategyRun.started_at.desc()).limit(limit)).all()
```

In `backend/app/main.py`, include the routers:

```python
from app.api import accounts, auth, journal, market, orders, strategies

    app.include_router(market.router, prefix="/api")
    app.include_router(journal.router, prefix="/api")
    app.include_router(strategies.router, prefix="/api")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add app/api app/main.py tests/test_api_market_journal_strategies.py
git commit -m "feat: market, journal, and strategies REST API"
```

---

### Task 14: Scheduler wiring, entrypoint, and README

**Files:**
- Create: `backend/app/jobs.py`, `backend/.env.example`, `README.md` (repo root)
- Modify: `backend/app/main.py` (lifespan starts/stops the scheduler)
- Test: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: everything.
- Produces: `run_process_pending(deps)`, `run_snapshots(deps)`, `build_scheduler(deps) -> BackgroundScheduler` (jobs: `process_pending` every 2 minutes; `snapshots` cron 16:10 NY mon–fri; one job per strategy via `runner.register_jobs`). `create_app(deps, start_scheduler=True)` now starts/stops the scheduler via FastAPI lifespan. Server runs with `uv run uvicorn --factory app.main:create_app`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_jobs.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.jobs import build_scheduler, run_process_pending, run_snapshots
from app.main import AppDeps
from app.models import EquitySnapshot
from app.strategy.runner import StrategyRunner
from tests.factories import make_account
from tests.fakes import FakeCalendar, FakeMarketData


@pytest.fixture
def deps(session_factory, tmp_path):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal)
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    runner = StrategyRunner(Path(strategies_dir), session_factory, execution,
                            md, cal, Decimal("100000"))
    with session_factory() as s:
        make_account(s)
        s.commit()
    return AppDeps(settings=Settings(), session_factory=session_factory,
                   market_data=md, calendar=cal, engine=engine,
                   execution=execution, runner=runner)


def test_run_process_pending_fills_queued_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.execution.place_order(
            s, account_id=acct.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    deps.calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_run_snapshots_writes_rows(deps):
    run_snapshots(deps)
    with deps.session_factory() as s:
        assert s.query(EquitySnapshot).count() == 1


def test_build_scheduler_registers_jobs(deps):
    sched = build_scheduler(deps)
    ids = {job.id for job in sched.get_jobs()}
    assert {"process_pending", "snapshots"} <= ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.jobs'`.

- [ ] **Step 3: Implement**

`backend/app/jobs.py`:

```python
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.engine.valuation import take_snapshots

log = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")


def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        session.commit()


def run_snapshots(deps) -> None:
    with deps.session_factory() as session:
        take_snapshots(session, deps.market_data, deps.calendar)
        session.commit()


def build_scheduler(deps) -> BackgroundScheduler:
    # APScheduler logs and swallows job exceptions, so one bad run
    # never kills the scheduler (spec: error containment).
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_process_pending, "interval", minutes=2, args=[deps],
                      id="process_pending")
    scheduler.add_job(run_snapshots,
                      CronTrigger(day_of_week="mon-fri", hour=16, minute=10,
                                  timezone=NY_TZ),
                      args=[deps], id="snapshots")
    deps.runner.register_jobs(scheduler)
    return scheduler
```

In `backend/app/main.py`, wire the scheduler into a lifespan (replace the `create_app` body's `app = FastAPI(...)` line and add the import):

```python
from contextlib import asynccontextmanager

from app.jobs import build_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if start_scheduler:
            scheduler = build_scheduler(deps)
            scheduler.start()
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Paper Trading Platform", lifespan=lifespan)
```

`backend/.env.example`:

```
PT_PASSWORD=pick-a-password
PT_SECRET_KEY=generate-a-long-random-string
# Optional but recommended (free): https://alpaca.markets -> paper/data API keys
PT_ALPACA_KEY_ID=
PT_ALPACA_SECRET=
# PT_DB_PATH=paper_trading.db
# PT_STARTING_CASH=100000
```

`README.md` (repo root):

```markdown
# my-trading-platform

Personal paper-trading platform for practicing swing trading of US stocks/ETFs.
Spec: docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md

## Backend quickstart

    cd backend
    uv sync
    cp .env.example .env   # then edit PT_PASSWORD and PT_SECRET_KEY
    uv run uvicorn --factory app.main:create_app --port 8000

API at http://localhost:8000/api (docs at /docs). Log in via POST /api/login.
Without Alpaca keys it falls back to yfinance automatically.

## Tests

    cd backend
    uv run pytest -q

## Strategies

Drop a Python file in backend/strategies/ subclassing
app.strategy.base.Strategy, restart, then enable it via POST
/api/strategies/{name}/toggle. Each strategy trades its own account.
```

- [ ] **Step 4: Run the full suite and boot the server**

Run: `uv run pytest -q`
Expected: full suite passes.

Run: `PT_PASSWORD=pw PT_SECRET_KEY=s uv run uvicorn --factory app.main:create_app --port 8000 &` then `curl -s localhost:8000/api/health`; expect `{"ok":true}`; then stop the server.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: scheduler wiring, server entrypoint, README"
```

---

## Verification sweep (after all tasks)

- `cd backend && uv run pytest -q` — everything green.
- Boot the server with real providers (no Alpaca keys needed — yfinance fallback), log in, place a market order for 1 share of SPY via `/docs`, confirm it fills (market open) or queues (closed), and check `GET /api/accounts/1`.
- Spec coverage check against `docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md`: engine ✓ fills ✓ positions/P&L ✓ reservations ✓ idempotency ✓ calendar ✓ data fallback+cache+staleness ✓ snapshots ✓ journal+stats ✓ strategies ✓ scheduler ✓ auth ✓. Frontend + Docker Compose: separate follow-up plan.





