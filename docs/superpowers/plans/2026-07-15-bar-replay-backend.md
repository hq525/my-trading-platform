# Bar Replay Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic bar-replay sessions — preloaded per-session daily bars, a virtual clock, next-bar fills, strategies running per step — fully fenced from the paper and live pipelines.

**Architecture:** A new `backend/app/replay/` package: `ReplayMarketData` serves quotes/bars from frozen per-session `ReplayBar` rows (never past the cursor); `ReplayExecution` places/cancels via per-call `TradingEngine`s bound to the session's virtual clock (`TradingEngine` gains `now_fn`); `step_session` advances the cursor, fills against the new bar (market at open, gap-aware limit fills), expires day orders, writes virtual-dated snapshots atomically, then runs the session's strategies. Isolation: `mode="replay"` accounts, tightened `owns_order` predicates, `take_snapshots` skip, and a replay valuation branch in account detail. Spec: `docs/superpowers/specs/2026-07-15-bar-replay-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, pytest (deterministic, offline via fakes).

## Global Constraints

- Money/qty math uses `Decimal` everywhere — never floats.
- `SqliteDecimal` columns are TEXT: never compare them in SQL WHERE — filter in Python. (`ReplayBar.date` is a `Date` column — SQL comparison is fine; ISO text ordering is chronological.)
- All tests deterministic and offline; provider HTTP never reached (fakes / `httpx.MockTransport`).
- Exact values (verbatim, tested): account mode `"replay"`; account names `replay:{session_id}:manual` and `replay:{session_id}:strategy:{Name}`; virtual now = cursor date **21:00 UTC**; preload limits `STOCK_HISTORY_LIMIT = 520`, `CRYPTO_HISTORY_LIMIT = 730`; step `steps` query param bounds 1–250; reject reasons come from the engine's existing copy (`unknown symbol: X`, `market data unavailable`, `insufficient cash at fill: need X, available Y`).
- Fill semantics (spec): nothing fills at placement; market fills at next bar **open** (with the insufficient-cash-at-fill rejection); limit buy fills at `open` if `open <= limit`, else at `limit` if `low <= limit`; sells mirrored; `day` = exactly one bar (first step where the symbol HAD a bar); coverage-end auto-expiry; cancel-all at exhaustion.
- Step pipeline order is load-bearing: cursor + fills + expiries + snapshots **commit atomically before strategies run**.
- The session's frozen strategy list is authoritative; global `StrategyState.enabled` is ignored; `runner.run_strategy` is not reused.
- Both sim `owns_order` predicates tighten from `mode != "live"` to `mode == "paper"`; `take_snapshots` skips `mode == "replay"`.
- `StrategyRunner` discovery/scheduling untouched.
- Every task ends green: `cd backend && uv run pytest -q` (180 passing at branch start). Run all commands from `backend/`.

## File Structure

| File | Responsibility |
|---|---|
| `app/engine/engine.py` | + `now_fn` ctor param (stamps `placed_at`/`filled_at`) |
| `app/models.py` | + `ReplaySession`, `ReplayBar`, `Account.replay_session_id` |
| `app/db.py` | + `replay_session_id` in `_NEW_COLUMNS` |
| `app/replay/market_data.py` (new) | `virtual_now`, `ReplayMarketData` (strict/valuation modes) |
| `app/replay/service.py` (new) | `ReplaySources`, preload, `create_session`, `delete_session`, `session_lock` |
| `app/replay/execution.py` (new) | `ReplayExecution` (place/cancel via per-call engines) |
| `app/replay/stepper.py` (new) | `step_session` pipeline |
| `app/main.py` | wiring: `replay_execution`, `replay_sources`, `execution_for` branch, tightened predicates |
| `app/engine/valuation.py` | `take_snapshots` skips replay |
| `app/api/accounts.py` | replay valuation branch |
| `app/api/replay.py` (new) + `app/api/schemas.py` | router + response models |
| `tests/factories.py` | + replay factories |

---

### Task 1: TradingEngine gets an injectable clock

**Files:**
- Modify: `backend/app/engine/engine.py`
- Test: `backend/tests/test_engine_clock.py` (new)

**Interfaces:**
- Produces: `TradingEngine(market_data, now_fn=utcnow)`; `placed_at`/`filled_at` come from `self.now_fn()`. Every later replay task constructs engines with a virtual clock; all existing call sites are unaffected (default preserved).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_engine_clock.py`:

```python
from datetime import datetime
from decimal import Decimal

from app.engine.engine import TradingEngine
from app.timeutil import utcnow
from tests.factories import make_account
from tests.fakes import FakeMarketData


def _engine(now_fn=None):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    return TradingEngine(md, now_fn=now_fn) if now_fn else TradingEngine(md)


def test_engine_stamps_orders_and_fills_with_injected_clock(session):
    virtual = datetime(2024, 6, 3, 21, 0)
    engine = _engine(now_fn=lambda: virtual)
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
    assert order.placed_at == virtual
    fill = engine.apply_fill(session, order, Decimal("100"))
    assert fill.filled_at == virtual


def test_engine_defaults_to_wall_clock(session):
    engine = _engine()
    acct = make_account(session)
    order = engine.place_order(session, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
    assert abs((utcnow() - order.placed_at).total_seconds()) < 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_engine_clock.py -v`
Expected: 1 failed (`TypeError: TradingEngine.__init__() got an unexpected keyword argument 'now_fn'`), 1 passed (`test_engine_defaults_to_wall_clock` already holds today).

- [ ] **Step 3: Implement**

In `backend/app/engine/engine.py`:

```python
    def __init__(self, market_data, now_fn=utcnow):
        self.market_data = market_data
        self.now_fn = now_fn
```

In `place_order`, the `Order(...)` construction changes `placed_at=utcnow()` → `placed_at=self.now_fn()`. In `apply_fill`, the `Fill(...)` construction changes `filled_at=utcnow()` → `filled_at=self.now_fn()`. (These are the only two `utcnow()` calls in the file.)

- [ ] **Step 4: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_engine_clock.py -v && uv run pytest -q`
Expected: 2 passed; full suite all pass (default behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/engine.py backend/tests/test_engine_clock.py
git commit -m "feat: injectable clock for order and fill timestamps"
```

---

### Task 2: ReplaySession/ReplayBar models and migration

**Files:**
- Modify: `backend/app/models.py`, `backend/app/db.py`
- Modify: `backend/tests/factories.py`
- Test: `backend/tests/test_replay_models.py` (new)

**Interfaces:**
- Produces: `ReplaySession` (fields below; `.symbols`/`.strategies` JSON-list properties; `.exhausted` property), `ReplayBar`, `Account.replay_session_id: int | None`. Factories `make_replay_session`, `make_replay_bar`, `make_replay_account` for every later test.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_replay_models.py`:

```python
import sqlite3
from datetime import date
from decimal import Decimal

from app.db import init_db, make_engine
from tests.factories import make_account, make_replay_account, make_replay_bar, make_replay_session


def test_replay_session_properties(session):
    row = make_replay_session(session, symbols=("SPY", "BTC-USD"),
                              strategies=("SmaCross",),
                              start="2024-06-03", end="2024-06-28")
    assert row.symbols == ["SPY", "BTC-USD"]
    assert row.strategies == ["SmaCross"]
    assert row.exhausted is False
    row.cursor_date = date(2024, 6, 28)
    assert row.exhausted is True


def test_replay_bar_and_account_link(session):
    row = make_replay_session(session)
    bar = make_replay_bar(session, row.id, "SPY", "2024-06-03",
                          open_="100", high="102", low="99", close="101")
    acct = make_replay_account(session, row.id)
    assert bar.close == Decimal("101")
    assert acct.mode == "replay"
    assert acct.replay_session_id == row.id
    assert acct.name == f"replay:{row.id}:manual"


def test_regular_accounts_have_no_replay_session(session):
    acct = make_account(session)
    assert acct.replay_session_id is None


def test_init_db_adds_replay_session_id_to_existing_database(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, "
        "kind VARCHAR, cash VARCHAR, starting_cash VARCHAR, commission VARCHAR, "
        "created_at DATETIME, mode VARCHAR DEFAULT 'paper', "
        "last_synced_at DATETIME, sync_detail VARCHAR)")
    conn.commit()
    conn.close()
    engine = make_engine(f"sqlite:///{db}")
    init_db(engine)
    with engine.connect() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(accounts)")}
        assert "replay_session_id" in cols
        tables = {r[0] for r in c.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"replay_sessions", "replay_bars"} <= tables
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_replay_session'`.

- [ ] **Step 3: Implement models**

In `backend/app/models.py` — add `import json` and `Text` is already imported; the `Account.mode` comment becomes `# paper | live | replay`; add after `sync_detail`:

```python
    replay_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("replay_sessions.id"), default=None)
```

Append at the end of the file:

```python
class ReplaySession(Base):
    __tablename__ = "replay_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    symbols_json: Mapped[str] = mapped_column(Text)      # JSON list of symbols
    strategies_json: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    start_date: Mapped[date] = mapped_column(Date)
    cursor_date: Mapped[date] = mapped_column(Date)      # latest visible bar
    end_date: Mapped[date] = mapped_column(Date)         # max bar date at creation
    starting_cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def symbols(self) -> list[str]:
        return json.loads(self.symbols_json)

    @property
    def strategies(self) -> list[str]:
        return json.loads(self.strategies_json)

    @property
    def exhausted(self) -> bool:
        return self.cursor_date >= self.end_date


class ReplayBar(Base):
    __tablename__ = "replay_bars"
    __table_args__ = (UniqueConstraint("session_id", "symbol", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("replay_sessions.id"))
    symbol: Mapped[str] = mapped_column(String)
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal] = mapped_column(SqliteDecimal)
    high: Mapped[Decimal] = mapped_column(SqliteDecimal)
    low: Mapped[Decimal] = mapped_column(SqliteDecimal)
    close: Mapped[Decimal] = mapped_column(SqliteDecimal)
    volume: Mapped[int] = mapped_column(default=0)
```

In `backend/app/db.py`, append to `_NEW_COLUMNS`:

```python
    ("accounts", "replay_session_id", "INTEGER"),
```

- [ ] **Step 4: Implement factories**

Append to `backend/tests/factories.py` (add `import json` and `from datetime import date` at the top; extend the existing imports from `app.models`):

```python
def make_replay_session(session, symbols=("SPY",), start="2024-06-03",
                        cursor=None, end="2024-06-28", strategies=(),
                        starting_cash="100000", name="test session"):
    from app.models import ReplaySession
    row = ReplaySession(name=name, symbols_json=json.dumps(list(symbols)),
                        strategies_json=json.dumps(list(strategies)),
                        start_date=date.fromisoformat(start),
                        cursor_date=date.fromisoformat(cursor or start),
                        end_date=date.fromisoformat(end),
                        starting_cash=Decimal(starting_cash))
    session.add(row)
    session.flush()
    return row


def make_replay_bar(session, session_id, symbol, day, open_="100", high=None,
                    low=None, close=None, volume=1000):
    from app.models import ReplayBar
    bar = ReplayBar(session_id=session_id, symbol=symbol,
                    date=date.fromisoformat(day),
                    open=Decimal(open_), high=Decimal(high or open_),
                    low=Decimal(low or open_), close=Decimal(close or open_),
                    volume=volume)
    session.add(bar)
    session.flush()
    return bar


def make_replay_account(session, session_id, role="manual", cash="100000"):
    suffix = "manual" if role == "manual" else f"strategy:{role}"
    acct = Account(name=f"replay:{session_id}:{suffix}", kind="manual",
                   mode="replay", cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal("0"), replay_session_id=session_id)
    session.add(acct)
    session.flush()
    return acct
```

- [ ] **Step 5: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_models.py -v && uv run pytest -q`
Expected: 4 passed; full suite all pass (columns/tables are additive).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/db.py backend/tests/factories.py backend/tests/test_replay_models.py
git commit -m "feat: replay session and bar models with additive account link"
```

---

### Task 3: ReplayMarketData — cursor-bounded quotes and bars

**Files:**
- Create: `backend/app/replay/__init__.py` (empty), `backend/app/replay/market_data.py`
- Test: `backend/tests/test_replay_market_data.py` (new)

**Interfaces:**
- Consumes: `ReplaySession`/`ReplayBar` (Task 2).
- Produces: `virtual_now(d: date) -> datetime` (21:00 UTC naive); `ReplayMarketData(db, session_row, strict=True)` with `get_quote(symbol) -> Quote` and `get_bars(symbol, timeframe="1D", limit=200) -> list[Bar]`. `strict=True` = placement guard (coverage-ended symbols raise `MarketDataError`); `strict=False` = valuation/UI view (always serves the last close ≤ cursor). Out-of-universe symbols raise `UnknownSymbolError` in both modes.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_replay_market_data.py`:

```python
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.replay.market_data import ReplayMarketData, virtual_now
from tests.factories import make_replay_bar, make_replay_session


@pytest.fixture
def stock_session(session):
    row = make_replay_session(session, symbols=("SPY",),
                              start="2024-06-03", end="2024-06-06")
    make_replay_bar(session, row.id, "SPY", "2024-06-03", close="100")
    make_replay_bar(session, row.id, "SPY", "2024-06-04", close="101")
    make_replay_bar(session, row.id, "SPY", "2024-06-05", close="102")
    make_replay_bar(session, row.id, "SPY", "2024-06-06", close="103")
    return row


def test_virtual_now_convention():
    assert virtual_now(date(2024, 6, 3)) == datetime(2024, 6, 3, 21, 0)


def test_quote_is_latest_close_at_or_before_cursor(session, stock_session):
    stock_session.cursor_date = date(2024, 6, 4)
    md = ReplayMarketData(session, stock_session)
    q = md.get_quote("SPY")
    assert q.price == Decimal("101")
    assert q.as_of == datetime(2024, 6, 4, 21, 0)


def test_quote_never_sees_past_cursor(session, stock_session):
    md = ReplayMarketData(session, stock_session)  # cursor at start: 06-03
    assert md.get_quote("SPY").price == Decimal("100")


def test_out_of_universe_symbol_is_unknown(session, stock_session):
    md = ReplayMarketData(session, stock_session)
    with pytest.raises(UnknownSymbolError):
        md.get_quote("AAPL")
    with pytest.raises(UnknownSymbolError):
        md.get_bars("AAPL")


def test_stale_quote_served_over_weekend_gap(session):
    row = make_replay_session(session, symbols=("SPY", "BTC-USD"),
                              start="2024-06-07", end="2024-06-10",
                              cursor="2024-06-08")
    make_replay_bar(session, row.id, "SPY", "2024-06-07", close="100")
    make_replay_bar(session, row.id, "SPY", "2024-06-10", close="105")
    make_replay_bar(session, row.id, "BTC-USD", "2024-06-08", close="65000")
    md = ReplayMarketData(session, row)
    q = md.get_quote("SPY")  # Saturday cursor; future SPY bars exist
    assert q.price == Decimal("100")
    assert q.as_of == datetime(2024, 6, 7, 21, 0)


def test_strict_mode_rejects_coverage_ended_symbol(session):
    row = make_replay_session(session, symbols=("SPY", "XYZ"),
                              start="2024-06-03", end="2024-06-05",
                              cursor="2024-06-05")
    make_replay_bar(session, row.id, "SPY", "2024-06-05", close="100")
    make_replay_bar(session, row.id, "XYZ", "2024-06-03", close="50")
    with pytest.raises(MarketDataError):
        ReplayMarketData(session, row).get_quote("XYZ")
    # valuation view still serves the last close
    q = ReplayMarketData(session, row, strict=False).get_quote("XYZ")
    assert q.price == Decimal("50")


def test_get_bars_bounded_by_cursor_and_limit(session, stock_session):
    stock_session.cursor_date = date(2024, 6, 5)
    md = ReplayMarketData(session, stock_session)
    bars = md.get_bars("SPY")
    assert [b.close for b in bars] == [Decimal("100"), Decimal("101"), Decimal("102")]
    assert bars[0].timestamp == datetime(2024, 6, 3)
    assert [b.close for b in md.get_bars("SPY", limit=2)] == [Decimal("101"), Decimal("102")]
    with pytest.raises(ValueError):
        md.get_bars("SPY", timeframe="1m")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_market_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.replay'`.

- [ ] **Step 3: Implement**

Create empty `backend/app/replay/__init__.py`, then `backend/app/replay/market_data.py`:

```python
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.models import ReplayBar


def virtual_now(d: date) -> datetime:
    """Replay's fixed daily timestamp (naive UTC). A convention only —
    replay never consults trading calendars."""
    return datetime(d.year, d.month, d.day, 21, 0)


class ReplayMarketData:
    """Quotes/bars from a session's frozen bars, never past the cursor.

    strict=True (placement): a symbol whose coverage has ended raises
    MarketDataError so the engine rejects new orders with the standard
    reason. strict=False (valuation, UI quote): always serves the latest
    close <= cursor, so positions in coverage-ended symbols stay valued.
    """

    name = "replay"

    def __init__(self, db, session_row, strict: bool = True):
        self._db = db
        self._session = session_row
        self.strict = strict

    def _latest_bar(self, symbol: str) -> ReplayBar | None:
        return self._db.scalar(
            select(ReplayBar)
            .where(ReplayBar.session_id == self._session.id,
                   ReplayBar.symbol == symbol,
                   ReplayBar.date <= self._session.cursor_date)
            .order_by(ReplayBar.date.desc())
            .limit(1))

    def get_quote(self, symbol: str) -> Quote:
        if symbol not in self._session.symbols:
            raise UnknownSymbolError(symbol)
        bar = self._latest_bar(symbol)
        if bar is None:
            raise UnknownSymbolError(symbol)
        if self.strict and bar.date < self._session.cursor_date:
            last = self._db.scalar(
                select(func.max(ReplayBar.date)).where(
                    ReplayBar.session_id == self._session.id,
                    ReplayBar.symbol == symbol))
            if last is not None and last < self._session.cursor_date:
                raise MarketDataError(f"no {symbol} data after {last}")
        return Quote(symbol=symbol, price=bar.close, as_of=virtual_now(bar.date))

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if symbol not in self._session.symbols:
            raise UnknownSymbolError(symbol)
        rows = self._db.scalars(
            select(ReplayBar)
            .where(ReplayBar.session_id == self._session.id,
                   ReplayBar.symbol == symbol,
                   ReplayBar.date <= self._session.cursor_date)
            .order_by(ReplayBar.date.desc())
            .limit(limit)).all()
        return [Bar(timestamp=datetime(r.date.year, r.date.month, r.date.day),
                    open=r.open, high=r.high, low=r.low, close=r.close,
                    volume=r.volume)
                for r in reversed(rows)]
```

- [ ] **Step 4: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_market_data.py -v && uv run pytest -q`
Expected: 7 passed; full suite all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/replay/__init__.py backend/app/replay/market_data.py backend/tests/test_replay_market_data.py
git commit -m "feat: cursor-bounded replay market data with placement and valuation modes"
```

---

### Task 4: Preload, session creation, deletion, and locks

**Files:**
- Create: `backend/app/replay/service.py`
- Test: `backend/tests/test_replay_service.py` (new)

**Interfaces:**
- Consumes: models/factories (Task 2), `FakeMarketData` (existing — its `get_bars` honors `limit` and raises `UnknownSymbolError`/`MarketDataError`).
- Produces: `ReplaySources(stock, crypto_primary, crypto_fallback)`; `ReplayCreationError`; `create_session(db, sources, *, symbols, start_date, strategies, known_strategies, starting_cash, name=None, today=None) -> ReplaySession`; `delete_session(db, session_id)`; `session_lock(session_id) -> threading.Lock`; constants `STOCK_HISTORY_LIMIT = 520`, `CRYPTO_HISTORY_LIMIT = 730`. Tasks 5–9 use all of these.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_replay_service.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Account, EquitySnapshot, Fill, JournalNote, Order, Position, ReplayBar, ReplaySession
from app.replay.service import (ReplayCreationError, ReplaySources,
                                create_session, delete_session, session_lock)
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.fakes import FakeMarketData

TODAY = date(2024, 7, 1)


def make_sources(symbol_days: dict[str, list[tuple[str, str]]]):
    """symbol -> [(iso_date, close)] built directly as Bar objects."""
    from datetime import datetime

    from app.marketdata.base import Bar
    md = FakeMarketData()
    for sym, days in symbol_days.items():
        md.bars[sym] = [Bar(timestamp=datetime.fromisoformat(d),
                            open=Decimal(c), high=Decimal(c), low=Decimal(c),
                            close=Decimal(c), volume=1000)
                        for d, c in days]
    return ReplaySources(stock=md, crypto_primary=md, crypto_fallback=md)


def test_create_session_loads_bars_accounts_and_cursor(session):
    sources = make_sources({"SPY": [("2024-06-03", "100"), ("2024-06-04", "101"),
                                    ("2024-06-05", "102")]})
    row = create_session(session, sources, symbols=["spy"],
                         start_date=date(2024, 6, 4), strategies=["SmaCross"],
                         known_strategies={"SmaCross"},
                         starting_cash=Decimal("50000"), today=TODAY)
    assert row.symbols == ["SPY"]  # upper-cased
    assert row.cursor_date == date(2024, 6, 4)
    assert row.end_date == date(2024, 6, 5)
    assert row.name == "SPY from 2024-06-04"
    accounts = session.scalars(select(Account).where(
        Account.replay_session_id == row.id)).all()
    assert {a.name for a in accounts} == {
        f"replay:{row.id}:manual", f"replay:{row.id}:strategy:SmaCross"}
    assert all(a.mode == "replay" and a.cash == Decimal("50000") for a in accounts)
    assert session.scalars(select(ReplayBar).where(
        ReplayBar.session_id == row.id)).all().__len__() == 3


def test_create_session_drops_todays_partial_bar(session):
    sources = make_sources({"SPY": [("2024-06-28", "100"), ("2024-07-01", "101")]})
    row = create_session(session, sources, symbols=["SPY"],
                         start_date=date(2024, 6, 28), strategies=[],
                         known_strategies=set(), starting_cash=Decimal("1000"),
                         today=TODAY)
    dates = [b.date for b in session.scalars(select(ReplayBar))]
    assert date(2024, 7, 1) not in dates
    assert row.end_date == date(2024, 6, 28)


def test_create_session_requires_coverage_at_start(session):
    sources = make_sources({"SPY": [("2024-06-10", "100"), ("2024-06-11", "101")]})
    with pytest.raises(ReplayCreationError, match="history starts"):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=[],
                       known_strategies=set(), starting_cash=Decimal("1000"),
                       today=TODAY)


def test_create_session_validates_inputs(session):
    sources = make_sources({"SPY": [("2024-06-03", "100")]})
    with pytest.raises(ReplayCreationError, match="at least one symbol"):
        create_session(session, sources, symbols=[], start_date=date(2024, 6, 3),
                       strategies=[], known_strategies=set(),
                       starting_cash=Decimal("1000"), today=TODAY)
    with pytest.raises(ReplayCreationError, match="unknown strategies: Nope"):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=["Nope"],
                       known_strategies={"SmaCross"},
                       starting_cash=Decimal("1000"), today=TODAY)


def test_create_session_provider_failure_writes_nothing(session):
    md = FakeMarketData()
    md.fail = True
    sources = ReplaySources(stock=md, crypto_primary=md, crypto_fallback=md)
    with pytest.raises(ReplayCreationError):
        create_session(session, sources, symbols=["SPY"],
                       start_date=date(2024, 6, 3), strategies=[],
                       known_strategies=set(), starting_cash=Decimal("1000"),
                       today=TODAY)
    assert session.scalars(select(ReplaySession)).all() == []
    assert session.scalars(select(Account)).all() == []


def test_crypto_uses_fallback_when_primary_fails(session):
    from datetime import datetime

    from app.marketdata.base import Bar
    primary = FakeMarketData()
    primary.fail = True
    fallback = FakeMarketData()
    fallback.bars["BTC-USD"] = [
        Bar(timestamp=datetime(2024, 6, 3), open=Decimal("65000"),
            high=Decimal("65000"), low=Decimal("65000"), close=Decimal("65000"),
            volume=1)]
    sources = ReplaySources(stock=FakeMarketData(), crypto_primary=primary,
                            crypto_fallback=fallback)
    row = create_session(session, sources, symbols=["BTC-USD"],
                         start_date=date(2024, 6, 3), strategies=[],
                         known_strategies=set(), starting_cash=Decimal("1000"),
                         today=TODAY)
    assert row.end_date == date(2024, 6, 3)


def test_delete_session_cascades_everything_including_notes(session):
    row = make_replay_session(session)
    acct = make_replay_account(session, row.id)
    make_replay_bar(session, row.id, "SPY", "2024-06-03")
    order = Order(account_id=acct.id, symbol="SPY", side="buy",
                  order_type="market", qty=Decimal("1"), status="filled")
    session.add(order)
    session.flush()
    session.add(Fill(order_id=order.id, price=Decimal("100"), qty=Decimal("1")))
    session.add(JournalNote(order_id=order.id, text="replay note"))
    session.add(Position(account_id=acct.id, symbol="SPY", qty=Decimal("1"),
                         avg_cost=Decimal("100"), realized_pnl=Decimal("0")))
    session.add(EquitySnapshot(account_id=acct.id, date=date(2024, 6, 3),
                               equity=Decimal("1"), cash=Decimal("1")))
    session.flush()
    delete_session(session, row.id)
    for model in (ReplaySession, ReplayBar, Order, Fill, JournalNote,
                  Position, EquitySnapshot):
        assert session.scalars(select(model)).all() == []
    assert session.scalars(select(Account)).all() == []


def test_session_lock_is_stable_per_session():
    assert session_lock(1) is session_lock(1)
    assert session_lock(1) is not session_lock(2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.replay.service'`.

- [ ] **Step 3: Implement**

Create `backend/app/replay/service.py`:

```python
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select

from app.assets import is_crypto_symbol
from app.marketdata.base import Bar, MarketDataError, UnknownSymbolError
from app.models import (Account, EquitySnapshot, Fill, JournalNote, Order,
                        Position, ReplayBar, ReplaySession)
from app.timeutil import utcnow

STOCK_HISTORY_LIMIT = 520   # ~2 years of trading days (yfinance's own cap)
CRYPTO_HISTORY_LIMIT = 730  # 2 years of daily bars via Binance


@dataclass
class ReplaySources:
    """History fetchers for preload. The generic MarketDataService path is
    unsuitable here (Alpaca's window heuristic returns the OLDEST N bars),
    so replay fetches from these providers directly."""

    stock: object            # YFinanceData in production
    crypto_primary: object   # BinanceData
    crypto_fallback: object  # CoinbaseData (~300-day window)


class ReplayCreationError(Exception):
    pass


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def session_lock(session_id: int) -> threading.Lock:
    """Per-session lock serializing step and delete (single-process app)."""
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def _fetch_history(sources: ReplaySources, symbol: str, today: date) -> list[Bar]:
    try:
        if is_crypto_symbol(symbol):
            try:
                bars = sources.crypto_primary.get_bars(symbol, "1D",
                                                       CRYPTO_HISTORY_LIMIT)
            except (MarketDataError, UnknownSymbolError):
                bars = sources.crypto_fallback.get_bars(symbol, "1D",
                                                        CRYPTO_HISTORY_LIMIT)
        else:
            bars = sources.stock.get_bars(symbol, "1D", STOCK_HISTORY_LIMIT)
    except UnknownSymbolError:
        raise ReplayCreationError(f"unknown symbol: {symbol}")
    except MarketDataError as e:
        raise ReplayCreationError(f"could not load history for {symbol}: {e}")
    bars = [b for b in bars if b.timestamp.date() < today]  # drop today's partial
    if not bars:
        raise ReplayCreationError(f"no history available for {symbol}")
    return bars


def create_session(db, sources: ReplaySources, *, symbols, start_date: date,
                   strategies, known_strategies, starting_cash,
                   name: str | None = None, today: date | None = None
                   ) -> ReplaySession:
    today = today or utcnow().date()
    if not symbols:
        raise ReplayCreationError("at least one symbol is required")
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if not symbols:
        raise ReplayCreationError("at least one symbol is required")
    unknown = [n for n in strategies if n not in known_strategies]
    if unknown:
        raise ReplayCreationError(f"unknown strategies: {', '.join(unknown)}")

    # All network I/O and validation BEFORE any DB write.
    history = {sym: _fetch_history(sources, sym, today) for sym in symbols}
    problems = [f"{sym} history starts {bars[0].timestamp.date()} "
                f"(through {bars[-1].timestamp.date()})"
                for sym, bars in history.items()
                if bars[0].timestamp.date() > start_date]
    if problems:
        raise ReplayCreationError(
            "insufficient coverage at start date: " + "; ".join(problems))
    all_dates = sorted({b.timestamp.date()
                        for bars in history.values() for b in bars})
    if start_date > all_dates[-1]:
        raise ReplayCreationError(
            f"start date is beyond available history (last bar {all_dates[-1]})")
    cursor = next(d for d in all_dates if d >= start_date)

    row = ReplaySession(
        name=name or f"{', '.join(symbols)} from {start_date}",
        symbols_json=json.dumps(symbols),
        strategies_json=json.dumps(list(strategies)),
        start_date=start_date, cursor_date=cursor, end_date=all_dates[-1],
        starting_cash=starting_cash)
    db.add(row)
    db.flush()
    db.add(Account(name=f"replay:{row.id}:manual", kind="manual", mode="replay",
                   cash=starting_cash, starting_cash=starting_cash,
                   replay_session_id=row.id))
    for sname in strategies:
        db.add(Account(name=f"replay:{row.id}:strategy:{sname}", kind="manual",
                       mode="replay", cash=starting_cash,
                       starting_cash=starting_cash, replay_session_id=row.id))
    for sym, bars in history.items():
        for b in bars:
            db.add(ReplayBar(session_id=row.id, symbol=sym,
                             date=b.timestamp.date(), open=b.open, high=b.high,
                             low=b.low, close=b.close, volume=b.volume))
    db.flush()
    return row


def delete_session(db, session_id: int) -> None:
    """One transaction; caller's session commit makes it atomic. Includes
    journal notes: SQLite here neither enforces FKs nor avoids rowid reuse,
    so an orphaned note would eventually reattach to an unrelated trade."""
    with session_lock(session_id):
        row = db.get(ReplaySession, session_id)
        if row is None:
            raise ValueError(f"no such replay session: {session_id}")
        account_ids = list(db.scalars(select(Account.id).where(
            Account.replay_session_id == session_id)))
        order_ids = list(db.scalars(select(Order.id).where(
            Order.account_id.in_(account_ids)))) if account_ids else []
        if order_ids:
            db.execute(delete(JournalNote).where(
                JournalNote.order_id.in_(order_ids)))
            db.execute(delete(Fill).where(Fill.order_id.in_(order_ids)))
            db.execute(delete(Order).where(Order.id.in_(order_ids)))
        if account_ids:
            db.execute(delete(Position).where(
                Position.account_id.in_(account_ids)))
            db.execute(delete(EquitySnapshot).where(
                EquitySnapshot.account_id.in_(account_ids)))
            db.execute(delete(Account).where(Account.id.in_(account_ids)))
        db.execute(delete(ReplayBar).where(ReplayBar.session_id == session_id))
        db.delete(row)
        db.flush()
```

- [ ] **Step 4: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_service.py -v && uv run pytest -q`
Expected: 8 passed; full suite all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/replay/service.py backend/tests/test_replay_service.py
git commit -m "feat: replay session preload, creation, cascade delete, and locks"
```

---

### Task 5: ReplayExecution, wiring, and isolation fences

**Files:**
- Create: `backend/app/replay/execution.py`
- Modify: `backend/app/main.py`, `backend/app/engine/valuation.py`
- Modify (predicates only): `backend/tests/conftest.py`, `backend/tests/live_fixtures.py`, `backend/tests/test_jobs.py`
- Test: `backend/tests/test_replay_isolation.py` (new)

**Interfaces:**
- Consumes: `ReplayMarketData`/`virtual_now` (Task 3), `session_lock`/`ReplaySources` (Task 4), `TradingEngine.now_fn` (Task 1).
- Produces: `ReplayExecution()` with `place_order(db, *, account_id, **kwargs) -> Order` and `cancel_order(db, order_id) -> Order`; `AppDeps.replay_execution: ReplayExecution` (default-constructed) and `AppDeps.replay_sources: ReplaySources | None = None`; `execution_for` routes `mode == "replay"` → `replay_execution`; both sim `owns_order` predicates are `o.account.mode == "paper" and (not) is_crypto_symbol(o.symbol)`; `take_snapshots` skips `mode == "replay"`. Tasks 6/9 depend on all of these.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_replay_isolation.py`:

```python
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.engine.valuation import take_snapshots
from app.models import EquitySnapshot, Order
from app.replay.execution import ReplayExecution
from tests.factories import (make_account, make_replay_account,
                             make_replay_bar, make_replay_session)
from tests.fakes import FakeMarketData
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)


def make_stock_session(session, closes=(("2024-06-03", "100"),
                                        ("2024-06-04", "101"))):
    row = make_replay_session(session, symbols=("SPY",),
                              start=closes[0][0], end=closes[-1][0])
    for day, close in closes:
        make_replay_bar(session, row.id, "SPY", day, open_=close, close=close)
    acct = make_replay_account(session, row.id)
    return row, acct


def test_replay_placement_validates_and_stays_pending(session):
    row, acct = make_stock_session(session)
    execution = ReplayExecution()
    order = execution.place_order(session, account_id=acct.id, symbol="SPY",
                                  side="buy", order_type="market", qty=10)
    assert order.status == "pending"
    assert order.placed_at.date() == date(2024, 6, 3)  # virtual, not wall clock
    rejected = execution.place_order(session, account_id=acct.id, symbol="AAPL",
                                     side="buy", order_type="market", qty=1)
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "unknown symbol: AAPL"


def test_replay_cancel_releases_reservation(session):
    row, acct = make_stock_session(session)
    execution = ReplayExecution()
    order = execution.place_order(session, account_id=acct.id, symbol="SPY",
                                  side="buy", order_type="limit", qty=10,
                                  limit_price=Decimal("90"))
    cancelled = execution.cancel_order(session, order.id)
    assert cancelled.status == "cancelled"


def test_sim_adapters_never_touch_replay_orders(deps):
    from app.jobs import run_process_pending
    with deps.session_factory() as s:
        row, acct = make_stock_session(s)
        order = ReplayExecution().place_order(
            s, account_id=acct.id, symbol="SPY", side="buy",
            order_type="market", qty=1)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "pending"


def test_take_snapshots_skips_replay_accounts(session):
    make_account(session)  # paper account, snapshotted
    row, replay_acct = make_stock_session(session)
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    take_snapshots(session, lambda s: md)
    session.flush()
    snaps = session.scalars(select(EquitySnapshot)).all()
    assert {s.account_id for s in snaps} == {1}
    assert replay_acct.id not in {s.account_id for s in snaps}


def test_execution_for_routes_replay_accounts():
    from types import SimpleNamespace

    from app.main import AppDeps
    deps = AppDeps(settings=None, session_factory=None, market_data="md",
                   calendar=None, engine=None, execution="stock-exec",
                   runner=None, crypto_market_data="cmd", crypto_calendar=None,
                   crypto_engine=None, crypto_execution="crypto-exec")
    assert isinstance(deps.execution_for(SimpleNamespace(mode="replay"), "SPY"),
                      ReplayExecution)
    assert deps.execution_for(SimpleNamespace(mode="paper"), "SPY") == "stock-exec"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_isolation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.replay.execution'`.

- [ ] **Step 3: Implement ReplayExecution**

Create `backend/app/replay/execution.py`:

```python
from __future__ import annotations

from app.engine.engine import TradingEngine
from app.models import Account, Order, ReplaySession
from app.replay.market_data import ReplayMarketData, virtual_now


class ReplayExecution:
    """Adapter-shaped dispatcher for replay accounts. Builds a per-call
    TradingEngine bound to the account's session bars and virtual clock —
    validation and reservation happen at the current bar's close, and
    nothing ever fills at placement (fills live in stepper.step_session)."""

    def _session_row(self, db, account_id: int) -> ReplaySession:
        account = db.get(Account, account_id)
        return db.get(ReplaySession, account.replay_session_id)

    def _engine(self, db, session_row: ReplaySession) -> TradingEngine:
        md = ReplayMarketData(db, session_row)
        return TradingEngine(md, now_fn=lambda: virtual_now(session_row.cursor_date))

    def place_order(self, db, *, account_id: int, **kwargs) -> Order:
        session_row = self._session_row(db, account_id)
        return self._engine(db, session_row).place_order(
            db, account_id=account_id, **kwargs)

    def cancel_order(self, db, order_id: int) -> Order:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        session_row = self._session_row(db, order.account_id)
        return self._engine(db, session_row).cancel_order(db, order_id)
```

- [ ] **Step 4: Wire AppDeps and fences**

In `backend/app/main.py`:

Extend the existing `from dataclasses import dataclass` line to `from dataclasses import dataclass, field`, and add:

```python
from app.replay.execution import ReplayExecution
from app.replay.service import ReplaySources
```

`AppDeps` gains two fields after `live_execution` and a branch in `execution_for`:

```python
    live_execution: AlpacaLiveAdapter | None = None
    replay_execution: ReplayExecution = field(default_factory=ReplayExecution)
    replay_sources: ReplaySources | None = None

    def execution_for(self, account, symbol: str):
        if account.mode == "replay":
            return self.replay_execution
        if account.mode == "live":
            return self.live_execution
        return self.execution_for_symbol(symbol)
```

In `build_deps`, both `owns_order` predicates tighten (replace `o.account.mode != "live"` with `o.account.mode == "paper"` in the two `SimAdapter(...)` constructions), and after `live_execution` is built:

```python
    replay_sources = ReplaySources(stock=YFinanceData(),
                                   crypto_primary=BinanceData(),
                                   crypto_fallback=CoinbaseData())
```

with `replay_sources=replay_sources` added to the returned `AppDeps(...)`.

In `backend/app/engine/valuation.py`, `take_snapshots`'s loop gains a skip as its first line:

```python
    for account in session.scalars(select(Account)).all():
        if account.mode == "replay":
            continue  # replay snapshots are written by the stepper, virtual-dated
```

In `backend/tests/conftest.py`, `backend/tests/live_fixtures.py`, and `backend/tests/test_jobs.py`, replace every `owns_order=lambda o: o.account.mode != "live" and ...` with `owns_order=lambda o: o.account.mode == "paper" and ...` (two `SimAdapter(...)` constructions per file; keep each file's variable names).

- [ ] **Step 5: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_isolation.py -v && uv run pytest -q`
Expected: 5 passed; full suite all pass (predicate change affects only replay-mode orders, which no existing test creates; `replay_execution` default-constructs so no `AppDeps(...)` call site changes).

- [ ] **Step 6: Commit**

```bash
git add backend/app/replay/execution.py backend/app/main.py backend/app/engine/valuation.py backend/tests/conftest.py backend/tests/live_fixtures.py backend/tests/test_jobs.py backend/tests/test_replay_isolation.py
git commit -m "feat: replay execution routing and isolation fences"
```

---

### Task 6: The step pipeline — fills, expiry, snapshots, exhaustion

**Files:**
- Create: `backend/app/replay/stepper.py`
- Test: `backend/tests/test_replay_stepper.py` (new)

**Interfaces:**
- Consumes: everything above; `Context` (`app/strategy/base.py`), `account_equity` (`app/engine/valuation.py`).
- Produces: `step_session(db, deps, session_id, steps=1) -> StepResult` where `StepResult` is a dataclass `{cursor_date: date, fills: list[dict], expired: list[int], cancelled_at_exhaustion: list[int], strategy_errors: dict[str, str], exhausted: bool}` and each fill dict is `{"order_id", "symbol", "side", "qty", "price"}`. Task 9's router serializes this.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_replay_stepper.py`:

```python
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import EquitySnapshot, Order
from app.replay.execution import ReplayExecution
from app.replay.stepper import step_session
from tests.factories import (make_replay_account, make_replay_bar,
                             make_replay_session)
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)

EXEC = ReplayExecution()


def build(db, bars, symbols=("SPY",), strategies=(), cash="100000"):
    """bars: {symbol: [(day, open, high, low, close), ...]}"""
    days = sorted({d for rows in bars.values() for d, *_ in rows})
    row = make_replay_session(db, symbols=symbols, strategies=strategies,
                              start=days[0], end=days[-1],
                              starting_cash=cash)
    for sym, rows in bars.items():
        for day, o, h, lo, c in rows:
            make_replay_bar(db, row.id, sym, day, open_=o, high=h, low=lo, close=c)
    acct = make_replay_account(db, row.id, cash=cash)
    return row, acct


def test_market_order_fills_at_next_bar_open(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "104", "106", "103", "105")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=10)
        db.commit()
        result = step_session(db, deps, row.id)
        assert result.cursor_date == date(2024, 6, 4)
        assert result.fills == [{"order_id": order.id, "symbol": "SPY",
                                 "side": "buy", "qty": Decimal("10"),
                                 "price": Decimal("104")}]
        db.refresh(acct)
        assert acct.cash == Decimal("100000") - Decimal("104") * 10


def test_market_buy_rejected_when_open_gaps_beyond_cash(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "150", "150", "150", "150")]}, cash="1000")
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=9)
        db.commit()
        step_session(db, deps, row.id)
        db.refresh(order)
        assert order.status == "rejected"
        assert order.reject_reason.startswith("insufficient cash at fill")


def test_limit_fills_gap_aware(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "95", "99", "94", "98"),
            ("2024-06-05", "97", "103", "96", "102")]})
        # gap-through: open 95 < limit 98 -> fills at the BETTER price (open)
        gap = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                               order_type="limit", qty=1,
                               limit_price=Decimal("98"))
        # touch: open 95 > limit 94.5? no -> low 94 <= 94.5 -> fills AT limit
        touch = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                                 order_type="limit", qty=1,
                                 limit_price=Decimal("94.5"))
        # no touch: low 94 > limit 90 -> stays pending
        miss = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                                order_type="limit", qty=1,
                                limit_price=Decimal("90"), tif="gtc")
        db.commit()
        result = step_session(db, deps, row.id)
        prices = {f["order_id"]: f["price"] for f in result.fills}
        assert prices[gap.id] == Decimal("95")
        assert prices[touch.id] == Decimal("94.5")
        db.refresh(miss)
        assert miss.status == "pending"  # gtc persists


def test_sell_limit_gap_up_fills_at_open(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "115", "116", "114", "115")]})
        buy = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
        db.commit()
        step_session(db, deps, row.id)  # buy fills at 06-04 open 100
        sell = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                side="sell", order_type="limit", qty=1,
                                limit_price=Decimal("105"))
        db.commit()
        result = step_session(db, deps, row.id)
        assert result.fills[0]["price"] == Decimal("115")  # open, not limit


def test_day_order_lives_exactly_one_bar_and_skips_gap_days(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {
            "SPY": [("2024-06-07", "100", "100", "100", "100"),
                    ("2024-06-10", "100", "100", "99", "100")],
            "BTC-USD": [("2024-06-07", "1", "1", "1", "1"),
                        ("2024-06-08", "1", "1", "1", "1"),
                        ("2024-06-09", "1", "1", "1", "1"),
                        ("2024-06-10", "1", "1", "1", "1")]},
            symbols=("SPY", "BTC-USD"))
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("90"))  # never touches
        db.commit()
        r1 = step_session(db, deps, row.id)   # -> 06-08, crypto only
        assert r1.cursor_date == date(2024, 6, 8)
        db.refresh(order)
        assert order.status == "pending"      # SPY had no bar: order sleeps
        r2 = step_session(db, deps, row.id)   # -> 06-09, still crypto only
        db.refresh(order)
        assert order.status == "pending"
        r3 = step_session(db, deps, row.id)   # -> 06-10, SPY bar, no touch
        db.refresh(order)
        assert order.status == "expired"
        assert order.id in r3.expired


def test_coverage_end_expires_pending_orders(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {
            "SPY": [("2024-06-03", "100", "100", "100", "100"),
                    ("2024-06-04", "100", "100", "100", "100"),
                    ("2024-06-05", "100", "100", "100", "100")],
            "XYZ": [("2024-06-03", "50", "50", "50", "50")]},
            symbols=("SPY", "XYZ"))
        order = EXEC.place_order(db, account_id=acct.id, symbol="XYZ",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("40"), tif="gtc")
        db.commit()
        r = step_session(db, deps, row.id)    # -> 06-04; XYZ coverage over
        db.refresh(order)
        assert order.status == "expired"
        assert order.id in r.expired


def test_snapshots_written_per_step_with_virtual_dates(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "101"),
            ("2024-06-05", "100", "100", "100", "102")]})
        step_session(db, deps, row.id, steps=2)
        snaps = db.scalars(select(EquitySnapshot).where(
            EquitySnapshot.account_id == acct.id).order_by(
            EquitySnapshot.date)).all()
        assert [s.date for s in snaps] == [date(2024, 6, 4), date(2024, 6, 5)]
        assert all(s.equity == Decimal("100000") for s in snaps)  # no positions


def test_exhaustion_cancels_pending_and_resteps_are_noops(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("90"), tif="gtc")
        db.commit()
        r = step_session(db, deps, row.id)
        assert r.exhausted is True
        assert order.id in r.cancelled_at_exhaustion
        db.refresh(order)
        assert order.status == "cancelled"
        snaps_before = db.scalars(select(EquitySnapshot)).all()
        r2 = step_session(db, deps, row.id)   # no-op, no writes
        assert r2.exhausted is True and r2.fills == []
        assert db.scalars(select(EquitySnapshot)).all().__len__() == len(snaps_before)


def test_concurrent_steps_serialize_without_corruption(deps):
    import threading

    from app.models import ReplaySession
    with deps.session_factory() as db:
        row, _ = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "100", "100", "100", "100")]})
        db.commit()
        sid = row.id
    errors = []

    def one_step():
        try:
            with deps.session_factory() as db2:
                step_session(db2, deps, sid)
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=one_step) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with deps.session_factory() as db:
        assert db.get(ReplaySession, sid).cursor_date == date(2024, 6, 5)
        snaps = db.scalars(select(EquitySnapshot)).all()
        assert sorted(s.date for s in snaps) == [date(2024, 6, 4), date(2024, 6, 5)]


def test_cancelled_order_is_never_filled_by_a_step(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "100", "100", "100", "100")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=1)
        db.commit()
    # cancel through a different DB session, as a concurrent request would
    with deps.session_factory() as other:
        EXEC.cancel_order(other, order.id)
        other.commit()
    with deps.session_factory() as db:
        result = step_session(db, deps, row.id)
        assert result.fills == []
        assert db.get(Order, order.id).status == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_stepper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.replay.stepper'`.

- [ ] **Step 3: Implement**

Create `backend/app/replay/stepper.py`:

```python
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select

from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity
from app.models import Account, EquitySnapshot, Order, ReplayBar, ReplaySession
from app.replay.market_data import ReplayMarketData, virtual_now
from app.replay.service import session_lock
from app.strategy.base import Context


@dataclass
class StepResult:
    cursor_date: date
    fills: list[dict] = field(default_factory=list)
    expired: list[int] = field(default_factory=list)
    cancelled_at_exhaustion: list[int] = field(default_factory=list)
    strategy_errors: dict[str, str] = field(default_factory=dict)
    exhausted: bool = False


def step_session(db, deps, session_id: int, steps: int = 1) -> StepResult:
    with session_lock(session_id):
        row = db.get(ReplaySession, session_id)
        if row is None:
            raise ValueError(f"no such replay session: {session_id}")
        result = StepResult(cursor_date=row.cursor_date, exhausted=row.exhausted)
        for _ in range(steps):
            if row.cursor_date >= row.end_date:
                break
            _advance_one(db, deps, row, result)
        if row.cursor_date >= row.end_date:
            _cancel_all_pending(db, row, result)
            db.commit()
        result.cursor_date = row.cursor_date
        result.exhausted = row.exhausted
        return result


def _advance_one(db, deps, row: ReplaySession, result: StepResult) -> None:
    next_date = db.scalar(select(func.min(ReplayBar.date)).where(
        ReplayBar.session_id == row.id, ReplayBar.date > row.cursor_date))
    row.cursor_date = next_date
    engine = TradingEngine(ReplayMarketData(db, row),
                           now_fn=lambda: virtual_now(next_date))
    bars = {b.symbol: b for b in db.scalars(select(ReplayBar).where(
        ReplayBar.session_id == row.id, ReplayBar.date == next_date))}
    last_dates = dict(db.execute(
        select(ReplayBar.symbol, func.max(ReplayBar.date))
        .where(ReplayBar.session_id == row.id)
        .group_by(ReplayBar.symbol)).all())
    pending = db.scalars(select(Order).join(Account).where(
        Order.status == "pending",
        Account.replay_session_id == row.id)).all()
    db.flush()
    for order in pending:
        db.refresh(order)  # a concurrent cancel must win (SimAdapter guard)
        if order.status != "pending":
            continue
        bar = bars.get(order.symbol)
        if bar is not None:
            _try_fill(db, engine, order, bar, result)
        if order.status != "pending":
            continue
        if last_dates.get(order.symbol) and last_dates[order.symbol] < next_date:
            engine.expire_order(db, order)   # coverage ended for this symbol
            result.expired.append(order.id)
        elif order.tif == "day" and bar is not None:
            engine.expire_order(db, order)   # day = exactly one bar
            result.expired.append(order.id)
    _write_snapshots(db, row, next_date)
    db.commit()  # cursor + fills + expiries + snapshots land atomically
    _run_strategies(db, deps, row, result)


def _try_fill(db, engine: TradingEngine, order: Order, bar: ReplayBar,
              result: StepResult) -> None:
    if order.order_type == "market":
        price = bar.open
        if order.side == "buy":
            account = db.get(Account, order.account_id)
            cost = price * order.qty + account.commission
            spendable = (engine.available_cash(db, account)
                         + order.reserved_cash)
            if cost > spendable:
                engine.reject_order(
                    db, order,
                    f"insufficient cash at fill: need {cost}, "
                    f"available {spendable}")
                return
    else:
        price = None
        if order.side == "buy":
            if bar.open <= order.limit_price:
                price = bar.open          # gap-through: the better price
            elif bar.low <= order.limit_price:
                price = order.limit_price
        else:
            if bar.open >= order.limit_price:
                price = bar.open
            elif bar.high >= order.limit_price:
                price = order.limit_price
        if price is None:
            return
    fill = engine.apply_fill(db, order, price)
    result.fills.append({"order_id": order.id, "symbol": order.symbol,
                         "side": order.side, "qty": fill.qty,
                         "price": fill.price})


def _write_snapshots(db, row: ReplaySession, d: date) -> None:
    md = ReplayMarketData(db, row, strict=False)
    for account in db.scalars(select(Account).where(
            Account.replay_session_id == row.id)):
        equity = account_equity(db, account, lambda s: md)
        db.add(EquitySnapshot(account_id=account.id, date=d,
                              equity=equity, cash=account.cash))


def _run_strategies(db, deps, row: ReplaySession, result: StepResult) -> None:
    """After the atomic commit: strategy orders are placed against a durable
    cursor, so a crashed/re-entered step can never fill them against the bar
    whose close they saw."""
    if not row.strategies:
        return
    md = ReplayMarketData(db, row)
    for name in row.strategies:
        cls = deps.runner.strategies.get(name)
        if cls is None:
            result.strategy_errors[name] = (
                "strategy not found (removed since session creation?)")
            continue
        account = db.scalar(select(Account).where(
            Account.replay_session_id == row.id,
            Account.name == f"replay:{row.id}:strategy:{name}"))
        ctx = Context(db, account,
                      lambda symbol: deps.replay_execution,
                      lambda symbol: md)
        try:
            cls().run(ctx)
        except Exception:
            db.rollback()  # discard partial uncommitted state only
            result.strategy_errors[name] = traceback.format_exc()[-2000:]


def _cancel_all_pending(db, row: ReplaySession, result: StepResult) -> None:
    pending = db.scalars(select(Order).join(Account).where(
        Order.status == "pending",
        Account.replay_session_id == row.id)).all()
    for order in pending:
        order.status = "cancelled"
        result.cancelled_at_exhaustion.append(order.id)
```

- [ ] **Step 4: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_stepper.py -v && uv run pytest -q`
Expected: 10 passed; full suite all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/replay/stepper.py backend/tests/test_replay_stepper.py
git commit -m "feat: replay step pipeline with next-bar fills, expiry, and virtual snapshots"
```

---

### Task 7: Strategies inside the step — behavioral tests

**Files:**
- Test: `backend/tests/test_replay_strategies.py` (new; the implementation shipped in Task 6 — this task proves its contract and fixes anything it flushes out)

**Interfaces:**
- Consumes: `step_session` (Task 6), `deps.runner.strategies` registry, `make_replay_*` factories.

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_replay_strategies.py`:

```python
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Order
from app.replay.stepper import step_session
from app.strategy.base import Strategy
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)


class BuyOneSpy(Strategy):
    def run(self, ctx):
        if not ctx.positions() and not ctx.orders(status="pending"):
            ctx.buy("SPY", qty=1)


class Exploder(Strategy):
    def run(self, ctx):
        raise RuntimeError("boom")


class TradesOutsideUniverse(Strategy):
    def run(self, ctx):
        ctx.get_quote("TSLA")


def build(db, strategies):
    row = make_replay_session(db, symbols=("SPY",), strategies=strategies,
                              start="2024-06-03", end="2024-06-06")
    for day, close in (("2024-06-03", "100"), ("2024-06-04", "101"),
                       ("2024-06-05", "102"), ("2024-06-06", "103")):
        make_replay_bar(db, row.id, "SPY", day, open_=close, close=close)
    make_replay_account(db, row.id)
    for name in strategies:
        make_replay_account(db, row.id, role=name)
    return row


def test_strategy_orders_fill_on_the_following_bar(deps):
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy}
    with deps.session_factory() as db:
        row = build(db, ("BuyOneSpy",))
        r1 = step_session(db, deps, row.id)   # -> 06-04; strategy places order
        assert r1.fills == []                 # nothing fills the step it's placed
        acct = db.scalar(select(Account).where(
            Account.name == f"replay:{row.id}:strategy:BuyOneSpy"))
        order = db.scalar(select(Order).where(Order.account_id == acct.id))
        assert order.status == "pending"
        assert order.placed_at == datetime(2024, 6, 4, 21, 0)
        r2 = step_session(db, deps, row.id)   # -> 06-05; fills at open 102
        assert r2.fills[0]["order_id"] == order.id
        assert r2.fills[0]["price"] == Decimal("102")


def test_strategy_errors_are_contained_per_strategy(deps):
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy, "Exploder": Exploder}
    with deps.session_factory() as db:
        row = build(db, ("BuyOneSpy", "Exploder"))
        r = step_session(db, deps, row.id)
        assert "boom" in r.strategy_errors["Exploder"]
        assert "BuyOneSpy" not in r.strategy_errors
        assert r.cursor_date == date(2024, 6, 4)  # step itself succeeded


def test_missing_strategy_class_is_an_error_entry_not_a_500(deps):
    deps.runner.strategies = {}
    with deps.session_factory() as db:
        row = build(db, ("Ghost",))
        r = step_session(db, deps, row.id)
        assert "not found" in r.strategy_errors["Ghost"]


def test_out_of_universe_strategy_symbol_surfaces_as_error(deps):
    deps.runner.strategies = {"TradesOutsideUniverse": TradesOutsideUniverse}
    with deps.session_factory() as db:
        row = build(db, ("TradesOutsideUniverse",))
        r = step_session(db, deps, row.id)
        assert "TSLA" in r.strategy_errors["TradesOutsideUniverse"]


def test_global_enabled_toggle_is_ignored(deps):
    from app.models import StrategyState
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy}
    with deps.session_factory() as db:
        db.add(StrategyState(name="BuyOneSpy", enabled=False))
        row = build(db, ("BuyOneSpy",))
        step_session(db, deps, row.id)
        acct = db.scalar(select(Account).where(
            Account.name == f"replay:{row.id}:strategy:BuyOneSpy"))
        assert db.scalar(select(Order).where(
            Order.account_id == acct.id)) is not None  # ran despite disabled
```

- [ ] **Step 2: Run the tests**

Run: `cd backend && uv run pytest tests/test_replay_strategies.py -v`
Expected: 5 passed (Task 6 implemented the behavior). If any fail, fix `stepper.py` within this task.

- [ ] **Step 3: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_replay_strategies.py
git commit -m "test: strategies in replay run per bar, contained, ignoring the global toggle"
```

---

### Task 8: Replay valuation branch in account detail

**Files:**
- Modify: `backend/app/api/accounts.py`
- Test: `backend/tests/test_replay_valuation.py` (new)

**Interfaces:**
- Consumes: `ReplayMarketData(strict=False)` (Task 3), `Account.replay_session_id`.
- Produces: `GET /api/accounts/{id}` values replay accounts from session bars ≤ cursor — never live providers, never a 503 from provider outage. The frontend plan's positions/equity views depend on this.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_replay_valuation.py`:

```python
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Position
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.live_fixtures import make_live_deps


def test_account_detail_values_replay_positions_from_session_bars(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    app = create_app(deps, start_scheduler=False)
    client = TestClient(app)
    client.post("/api/login", json={"password": "pw"})

    with session_factory() as db:
        row = make_replay_session(db, symbols=("SPY",), start="2024-06-03",
                                  cursor="2024-06-04", end="2024-06-05")
        make_replay_bar(db, row.id, "SPY", "2024-06-03", close="100")
        make_replay_bar(db, row.id, "SPY", "2024-06-04", close="120")
        acct = make_replay_account(db, row.id, cash="99880")
        db.add(Position(account_id=acct.id, symbol="SPY", qty=Decimal("1"),
                        avg_cost=Decimal("120"), realized_pnl=Decimal("0")))
        db.commit()
        acct_id = acct.id

    # The paper stack's fake quote for SPY is "100" (live world); the replay
    # branch must value at the session bar close 120 instead.
    detail = client.get(f"/api/accounts/{acct_id}").json()
    assert detail["positions"][0]["last_price"] == "120"
    assert detail["equity"] == "100000"  # 99880 cash + 120 market value


def test_account_detail_replay_branch_survives_dead_symbols(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    app = create_app(deps, start_scheduler=False)
    client = TestClient(app)
    client.post("/api/login", json={"password": "pw"})

    with session_factory() as db:
        row = make_replay_session(db, symbols=("XYZ",), start="2024-06-03",
                                  cursor="2024-06-05", end="2024-06-05")
        make_replay_bar(db, row.id, "XYZ", "2024-06-03", close="50")
        make_replay_bar(db, row.id, "XYZ", "2024-06-05", close="55")
        acct = make_replay_account(db, row.id)
        db.add(Position(account_id=acct.id, symbol="XYZ", qty=Decimal("2"),
                        avg_cost=Decimal("50"), realized_pnl=Decimal("0")))
        db.commit()
        # simulate mid-session coverage end: delete the 06-05 bar
        from sqlalchemy import delete

        from app.models import ReplayBar
        db.execute(delete(ReplayBar).where(ReplayBar.date == row.end_date))
        db.commit()
        acct_id = acct.id

    detail = client.get(f"/api/accounts/{acct_id}").json()
    assert detail["positions"][0]["last_price"] == "50"  # last available close
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_replay_valuation.py -v`
Expected: FAIL — the first asserts `"100"` (live fake quote) instead of `"120"`; second may 404/503.

- [ ] **Step 3: Implement**

In `backend/app/api/accounts.py`, add imports:

```python
from app.models import Account, EquitySnapshot, ReplaySession
from app.replay.market_data import ReplayMarketData
```

and in `account_detail`, replace the lookup used for valuation:

```python
    account = _account_or_404(session, account_id)
    if account.mode == "replay":
        session_row = session.get(ReplaySession, account.replay_session_id)
        replay_md = ReplayMarketData(session, session_row, strict=False)
        lookup = lambda symbol: replay_md  # noqa: E731
    else:
        lookup = deps.market_data_for_symbol
    try:
        values = position_values(session, account, lookup)
        equity = account_equity(session, account, lookup)
    except MarketDataError:
        raise HTTPException(503, "market data unavailable")
```

(the rest of the function is unchanged).

- [ ] **Step 4: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_replay_valuation.py -v && uv run pytest -q`
Expected: 2 passed; full suite all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/accounts.py backend/tests/test_replay_valuation.py
git commit -m "feat: value replay accounts from session bars, never live quotes"
```

---

### Task 9: The /api/replay router

**Files:**
- Create: `backend/app/api/replay.py`
- Modify: `backend/app/api/schemas.py`, `backend/app/main.py` (router registration)
- Test: `backend/tests/test_api_replay.py` (new)

**Interfaces:**
- Consumes: everything above.
- Produces: the endpoints from the spec. Schemas: `ReplaySessionCreateIn`, `ReplayAccountOut(id, name, role)`, `CoverageOut(symbol, first_date, last_date)`, `ReplaySessionOut(id, name, symbols, start_date, cursor_date, end_date, exhausted, created_at)`, `ReplaySessionDetailOut(… + accounts + coverage)`, `StepFillOut(order_id, symbol, side, qty, price)`, `StepResultOut(cursor_date, fills, expired, cancelled_at_exhaustion, strategy_errors, exhausted)`. The frontend plan consumes these.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_api_replay.py`:

```python
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.marketdata.base import Bar
from app.replay.service import ReplaySources
from tests.fakes import FakeMarketData
from tests.live_fixtures import make_live_deps


def bars_for(days):
    return [Bar(timestamp=datetime.fromisoformat(d), open=Decimal(o),
                high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=1000)
            for d, o, h, l, c in days]


@pytest.fixture
def client(session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path)
    md = FakeMarketData()
    md.bars["SPY"] = bars_for([
        ("2024-06-03", "100", "100", "100", "100"),
        ("2024-06-04", "104", "106", "103", "105"),
        ("2024-06-05", "105", "107", "104", "106")])
    deps.replay_sources = ReplaySources(stock=md, crypto_primary=md,
                                        crypto_fallback=md)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.deps = deps
    return c


def create(client, **overrides):
    body = {"symbols": ["SPY"], "start_date": "2024-06-03", "strategies": []}
    body.update(overrides)
    return client.post("/api/replay/sessions", json=body)


def test_create_list_and_detail(client):
    r = create(client, name="my run")
    assert r.status_code == 201
    detail = r.json()
    assert detail["name"] == "my run"
    assert detail["symbols"] == ["SPY"]
    assert detail["cursor_date"] == "2024-06-03"
    assert detail["end_date"] == "2024-06-05"
    assert detail["exhausted"] is False
    assert detail["coverage"] == [{"symbol": "SPY", "first_date": "2024-06-03",
                                   "last_date": "2024-06-05"}]
    assert [a["role"] for a in detail["accounts"]] == ["manual"]

    sessions = client.get("/api/replay/sessions").json()
    assert len(sessions) == 1
    assert client.get(f"/api/replay/sessions/{detail['id']}").status_code == 200
    assert client.get("/api/replay/sessions/999").status_code == 404


def test_create_validation_errors(client):
    assert create(client, symbols=[]).status_code == 400
    assert create(client, start_date="2020-01-01").status_code == 400
    assert create(client, strategies=["Ghost"]).status_code == 400


def test_place_step_and_quote_flow(client):
    session_id = create(client).json()["id"]
    detail = client.get(f"/api/replay/sessions/{session_id}").json()
    manual_id = detail["accounts"][0]["id"]

    q = client.get(f"/api/replay/sessions/{session_id}/quote/SPY").json()
    assert q["price"] == "100"

    placed = client.post(f"/api/accounts/{manual_id}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market",
        "qty": "1"}).json()
    assert placed["status"] == "pending"   # nothing fills at placement

    r = client.post(f"/api/replay/sessions/{session_id}/step").json()
    assert r["cursor_date"] == "2024-06-04"
    assert r["fills"] == [{"order_id": placed["id"], "symbol": "SPY",
                           "side": "buy", "qty": "1", "price": "104"}]

    bars = client.get(
        f"/api/replay/sessions/{session_id}/bars/SPY").json()
    assert len(bars) == 2                  # bars <= cursor only
    assert bars[-1]["close"] == "105"

    r2 = client.post(
        f"/api/replay/sessions/{session_id}/step?steps=250").json()
    assert r2["exhausted"] is True

    assert client.post(
        f"/api/replay/sessions/{session_id}/step?steps=0").status_code == 422


def test_delete_session(client):
    session_id = create(client).json()["id"]
    assert client.delete(
        f"/api/replay/sessions/{session_id}").status_code == 200
    assert client.get(
        f"/api/replay/sessions/{session_id}").status_code == 404
    assert client.delete("/api/replay/sessions/999").status_code == 404


def test_quote_unknown_symbol_404(client):
    session_id = create(client).json()["id"]
    assert client.get(
        f"/api/replay/sessions/{session_id}/quote/AAPL").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api_replay.py -v`
Expected: FAIL — 404s on `/api/replay/...` (router doesn't exist).

- [ ] **Step 3: Add schemas**

Append to `backend/app/api/schemas.py` (it already imports `datetime`; add `from datetime import date` to its imports):

```python
class ReplaySessionCreateIn(BaseModel):
    symbols: list[str]
    start_date: date
    strategies: list[str] = []
    starting_cash: Decimal | None = None
    name: str | None = None


class ReplayAccountOut(BaseModel):
    id: int
    name: str
    role: str  # "manual" or the strategy name


class CoverageOut(BaseModel):
    symbol: str
    first_date: date
    last_date: date


class ReplaySessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    symbols: list[str]
    start_date: date
    cursor_date: date
    end_date: date
    exhausted: bool
    created_at: datetime


class ReplaySessionDetailOut(ReplaySessionOut):
    accounts: list[ReplayAccountOut]
    coverage: list[CoverageOut]


class StepFillOut(BaseModel):
    order_id: int
    symbol: str
    side: str
    qty: Qty
    price: Money


class StepResultOut(BaseModel):
    cursor_date: date
    fills: list[StepFillOut]
    expired: list[int]
    cancelled_at_exhaustion: list[int]
    strategy_errors: dict[str, str]
    exhausted: bool
```

- [ ] **Step 4: Implement the router**

Create `backend/app/api/replay.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import get_deps, get_session, require_auth
from app.api.schemas import (BarOut, CoverageOut, QuoteOut, ReplayAccountOut,
                             ReplaySessionCreateIn, ReplaySessionDetailOut,
                             ReplaySessionOut, StepResultOut)
from app.marketdata.base import UnknownSymbolError
from app.models import Account, ReplayBar, ReplaySession
from app.replay.market_data import ReplayMarketData
from app.replay.service import ReplayCreationError, create_session, delete_session
from app.replay.stepper import step_session

router = APIRouter(prefix="/replay", dependencies=[Depends(require_auth)])


def _session_or_404(session, session_id: int) -> ReplaySession:
    row = session.get(ReplaySession, session_id)
    if row is None:
        raise HTTPException(404, "no such replay session")
    return row


def _role(row: ReplaySession, account: Account) -> str:
    prefix = f"replay:{row.id}:strategy:"
    return (account.name[len(prefix):] if account.name.startswith(prefix)
            else "manual")


def _detail(session, row: ReplaySession) -> ReplaySessionDetailOut:
    accounts = session.scalars(select(Account).where(
        Account.replay_session_id == row.id).order_by(Account.id)).all()
    coverage = session.execute(
        select(ReplayBar.symbol, func.min(ReplayBar.date),
               func.max(ReplayBar.date))
        .where(ReplayBar.session_id == row.id)
        .group_by(ReplayBar.symbol).order_by(ReplayBar.symbol)).all()
    return ReplaySessionDetailOut(
        id=row.id, name=row.name, symbols=row.symbols,
        start_date=row.start_date, cursor_date=row.cursor_date,
        end_date=row.end_date, exhausted=row.exhausted,
        created_at=row.created_at,
        accounts=[ReplayAccountOut(id=a.id, name=a.name, role=_role(row, a))
                  for a in accounts],
        coverage=[CoverageOut(symbol=s, first_date=lo, last_date=hi)
                  for s, lo, hi in coverage])


@router.post("/sessions", response_model=ReplaySessionDetailOut, status_code=201)
def create(body: ReplaySessionCreateIn, session=Depends(get_session),
           deps=Depends(get_deps)):
    if deps.replay_sources is None:
        raise HTTPException(503, "replay sources not configured")
    try:
        row = create_session(
            session, deps.replay_sources, symbols=body.symbols,
            start_date=body.start_date, strategies=body.strategies,
            known_strategies=set(deps.runner.strategies),
            starting_cash=body.starting_cash or deps.settings.starting_cash,
            name=body.name)
    except ReplayCreationError as e:
        raise HTTPException(400, str(e))
    return _detail(session, row)


@router.get("/sessions", response_model=list[ReplaySessionOut])
def list_sessions(session=Depends(get_session)):
    return session.scalars(
        select(ReplaySession).order_by(ReplaySession.id.desc())).all()


@router.get("/sessions/{session_id}", response_model=ReplaySessionDetailOut)
def session_detail(session_id: int, session=Depends(get_session)):
    return _detail(session, _session_or_404(session, session_id))


@router.post("/sessions/{session_id}/step", response_model=StepResultOut)
def step(session_id: int, steps: int = Query(1, ge=1, le=250),
         session=Depends(get_session), deps=Depends(get_deps)):
    _session_or_404(session, session_id)
    result = step_session(session, deps, session_id, steps=steps)
    return StepResultOut(
        cursor_date=result.cursor_date, fills=result.fills,
        expired=result.expired,
        cancelled_at_exhaustion=result.cancelled_at_exhaustion,
        strategy_errors=result.strategy_errors, exhausted=result.exhausted)


@router.delete("/sessions/{session_id}")
def delete(session_id: int, session=Depends(get_session)):
    _session_or_404(session, session_id)
    delete_session(session, session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/bars/{symbol}",
            response_model=list[BarOut])
def bars(session_id: int, symbol: str, limit: int = Query(520, ge=1, le=1000),
         session=Depends(get_session)):
    row = _session_or_404(session, session_id)
    try:
        return ReplayMarketData(session, row, strict=False).get_bars(
            symbol.upper(), "1D", limit)
    except UnknownSymbolError:
        raise HTTPException(404, "symbol not in this session")


@router.get("/sessions/{session_id}/quote/{symbol}", response_model=QuoteOut)
def quote(session_id: int, symbol: str, session=Depends(get_session)):
    row = _session_or_404(session, session_id)
    try:
        q = ReplayMarketData(session, row, strict=False).get_quote(symbol.upper())
    except UnknownSymbolError:
        raise HTTPException(404, "symbol not in this session")
    return QuoteOut(symbol=q.symbol, price=q.price, as_of=q.as_of)
```

In `backend/app/main.py`: extend the API import line to include `replay` (`from app.api import accounts, auth, journal, market, orders, replay, strategies`) and register it with the others:

```python
    app.include_router(replay.router, prefix="/api")
```

- [ ] **Step 5: Run tests, then the full suite**

Run: `cd backend && uv run pytest tests/test_api_replay.py -v && uv run pytest -q`
Expected: 5 passed; full suite all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/replay.py backend/app/api/schemas.py backend/app/main.py backend/tests/test_api_replay.py
git commit -m "feat: replay sessions API with step, bars, quote, and cascade delete"
```
