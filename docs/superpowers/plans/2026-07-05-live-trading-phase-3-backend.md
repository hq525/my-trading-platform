# Live Trading (Phase 3) Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live trading through Alpaca's brokerage API (paper-trading endpoint for now) behind the existing `ExecutionAdapter` interface, mirroring Alpaca's fills/cancels/expiries/rejections into the existing local ledger.

**Architecture:** A new `AlpacaLiveAdapter` implements the same `place_order`/`cancel_order`/`process_pending` interface as `SimAdapter`, but submits orders to Alpaca and mirrors Alpaca's decisions back via the existing 2-minute poll job. A sync job overwrites the live account's cash with Alpaca's figure and flags (never heals) position drift. Routing gains an account dimension: live-mode accounts → live adapter; everything else keeps Phase 2's symbol-shape routing. Spec: `docs/superpowers/specs/2026-07-05-live-trading-phase-3-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, httpx (+ `httpx.MockTransport` for tests), APScheduler, pytest.

## Global Constraints

- Money and quantity math uses `Decimal` everywhere — never floats. Convert API strings with `Decimal(value)` (they are already strings) or `Decimal(str(x))` for anything else.
- Decimal columns are stored as TEXT (`SqliteDecimal`): never compare them in SQL `WHERE` clauses — filter in Python (Phase 2's lexicographic-comparison bug).
- All broker HTTP is mocked with `httpx.MockTransport`; the suite stays deterministic and offline.
- Live trading is enabled iff `settings.alpaca_trading_key_id` is non-empty. When disabled, behavior is byte-for-byte today's.
- Exact strings (verbatim, tested):
  - reject reason `"crypto not supported in live trading yet"`
  - reject reason prefixes `"broker rejected: "` and `"broker unreachable: "`
  - live account name `"live"`; account modes `"paper"` / `"live"`
  - settings env vars `PT_ALPACA_TRADING_KEY_ID`, `PT_ALPACA_TRADING_SECRET`, `PT_ALPACA_TRADING_BASE` (default `"https://paper-api.alpaca.markets"`)
- Position drift found by sync is recorded in `Account.sync_detail`, never auto-healed.
- `StrategyRunner` and `backend/strategies/` are untouched — strategies stay paper-only.
- Every task ends with the full suite green: `cd backend && uv run pytest -q`. Run all commands from `backend/`.

## File Structure

| File | Responsibility |
|---|---|
| `app/config.py` | + 3 trading settings |
| `app/models.py` | + `Account.mode`/`last_synced_at`/`sync_detail`, `Order.broker_order_id` |
| `app/db.py` | + additive SQLite column migration in `init_db` |
| `app/engine/alpaca_live_adapter.py` (new) | submit/cancel/mirror/sync against Alpaca |
| `app/engine/sim_adapter.py` | `owns_symbol` → `owns_order` (account-mode-aware ownership) |
| `app/main.py` | `AppDeps.live_execution`, `execution_for(account, symbol)`, wiring, startup live account + first sync |
| `app/jobs.py` | live orders in `run_process_pending`; new `run_live_sync` every 10 min |
| `app/api/schemas.py` | `AccountOut` + mode/sync fields; `TradeOut.account_mode` |
| `app/api/orders.py` | route via `execution_for`; `BrokerError` → 502; missing adapter → 503 |
| `app/api/accounts.py` | pass new fields into `AccountDetailOut` |
| `app/api/journal.py` | emit `account_mode` |
| `tests/live_fixtures.py` (new) | shared deps builder for live app/API tests |

---

### Task 1: Trading settings, live columns, and SQLite column migration

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_live_migration.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings.alpaca_trading_key_id: str`, `Settings.alpaca_trading_secret: str`, `Settings.alpaca_trading_base: str`; `Account.mode: str` (default `"paper"`), `Account.last_synced_at: datetime | None`, `Account.sync_detail: str | None`; `Order.broker_order_id: str | None`. `init_db` adds these columns to pre-existing databases.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_live_migration.py`:

```python
import sqlite3

from app.config import Settings
from app.db import init_db, make_engine
from app.models import Account, Order
from tests.factories import make_account


def test_trading_settings_default_to_disabled():
    s = Settings(_env_file=None)
    assert s.alpaca_trading_key_id == ""
    assert s.alpaca_trading_secret == ""
    assert s.alpaca_trading_base == "https://paper-api.alpaca.markets"


def test_new_accounts_default_to_paper_mode(session):
    acct = make_account(session)
    assert acct.mode == "paper"
    assert acct.last_synced_at is None
    assert acct.sync_detail is None


def test_new_orders_have_no_broker_order_id(session):
    acct = make_account(session)
    order = Order(account_id=acct.id, symbol="SPY", side="buy",
                  order_type="market", qty=1)
    session.add(order)
    session.flush()
    assert order.broker_order_id is None


def test_init_db_adds_live_columns_to_existing_database(tmp_path):
    # A database created before Phase 3 lacks the new columns; init_db
    # (create_all skips existing tables) must ALTER them in.
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE accounts (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, "
        "kind VARCHAR, cash VARCHAR, starting_cash VARCHAR, commission VARCHAR, "
        "created_at DATETIME)")
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id INTEGER, "
        "symbol VARCHAR, side VARCHAR, order_type VARCHAR, tif VARCHAR, "
        "qty VARCHAR, limit_price VARCHAR, status VARCHAR, reject_reason VARCHAR, "
        "reserved_cash VARCHAR, idempotency_key VARCHAR, placed_at DATETIME)")
    conn.execute("INSERT INTO accounts (name, kind, cash, starting_cash, commission) "
                 "VALUES ('manual', 'manual', '100000', '100000', '0')")
    conn.commit()
    conn.close()

    engine = make_engine(f"sqlite:///{db}")
    init_db(engine)

    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT mode, last_synced_at, sync_detail FROM accounts").fetchone()
        assert row == ("paper", None, None)
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(orders)")}
        assert "broker_order_id" in cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_live_migration.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'alpaca_trading_key_id'`, `'Account' object has no attribute 'mode'`, etc.

- [ ] **Step 3: Implement**

In `backend/app/config.py`, add three fields after `alpaca_secret`:

```python
    alpaca_key_id: str = ""
    alpaca_secret: str = ""
    alpaca_trading_key_id: str = ""
    alpaca_trading_secret: str = ""
    alpaca_trading_base: str = "https://paper-api.alpaca.markets"
```

In `backend/app/models.py`, `Account` — add `mode` after `kind`, and the sync fields after `created_at`:

```python
    kind: Mapped[str] = mapped_column(String, default="manual")  # manual | strategy
    mode: Mapped[str] = mapped_column(String, default="paper")  # paper | live
    cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    starting_cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    commission: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Live-account sync (spec: Alpaca is the source of truth for live cash).
    last_synced_at: Mapped[datetime | None] = mapped_column(default=None)
    sync_detail: Mapped[str | None] = mapped_column(String, default=None)
```

In `backend/app/models.py`, `Order` — add after `idempotency_key`:

```python
    idempotency_key: Mapped[str | None] = mapped_column(String, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String, default=None)
```

In `backend/app/db.py`, replace `init_db` with:

```python
# Columns added after a table first shipped; create_all() will not alter
# existing tables, so init_db adds them by hand. (table, column, DDL type).
_NEW_COLUMNS = [
    ("accounts", "mode", "VARCHAR DEFAULT 'paper'"),
    ("accounts", "last_synced_at", "DATETIME"),
    ("accounts", "sync_detail", "VARCHAR"),
    ("orders", "broker_order_id", "VARCHAR"),
]


def init_db(engine) -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, ddl in _NEW_COLUMNS:
            cols = {row[1] for row in
                    conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
```

In `backend/.env.example`, append:

```
# Live trading (Phase 3). Paper-trading endpoint by default — real broker
# mechanics, virtual money. Leave the keys empty to run paper-only.
PT_ALPACA_TRADING_KEY_ID=
PT_ALPACA_TRADING_SECRET=
# PT_ALPACA_TRADING_BASE=https://paper-api.alpaca.markets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_live_migration.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass (the new columns are additive with defaults; nothing existing changes).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/models.py backend/app/db.py backend/.env.example backend/tests/test_live_migration.py
git commit -m "feat: trading settings, account mode/sync columns, and additive SQLite migration"
```

---

### Task 2: AlpacaLiveAdapter — submit and cancel

**Files:**
- Create: `backend/app/engine/alpaca_live_adapter.py`
- Modify: `backend/tests/factories.py`
- Test: `backend/tests/test_live_adapter.py` (new)

**Interfaces:**
- Consumes: `TradingEngine.place_order/reject_order/cancel_order` (`app/engine/engine.py`), `is_crypto_symbol` (`app/assets.py`), `Order.broker_order_id` (Task 1).
- Produces: `AlpacaLiveAdapter(engine, base_url, key_id, secret, transport=None, now_fn=utcnow)` with `place_order(session, **kwargs) -> Order` and `cancel_order(session, order_id) -> Order`; exception `BrokerError`. Task 3 adds `process_pending`/`sync_account` to the same class; Task 5 wires it; Task 6 maps `BrokerError` to HTTP 502.

**Behavior (from spec):**
- `place_order`: local engine validation first (creates the row, reserves cash). If pending: crypto symbols are rejected with the exact reason `"crypto not supported in live trading yet"` (reachable because yfinance can resolve `BTC-USD`, so engine validation may pass). Otherwise `POST /v2/orders` with `client_order_id=str(order.id)` (idempotent resubmits). 200/201 → store `broker_order_id`, stay pending. Alpaca error → reject `"broker rejected: <message>"`. Network error → reject `"broker unreachable: <error>"`. A rejection releases the reservation automatically (only `pending` buys count against available cash).
- `cancel_order`: unknown id → `ValueError`; non-pending → `InvalidOrderState` (matching `TradingEngine.cancel_order` so the API layer's handling is uniform); pending without `broker_order_id` (defensive — submit-rejected orders can't stay pending) → local `engine.cancel_order`; otherwise `DELETE /v2/orders/{broker_order_id}` and **return the order still pending** — the poll mirrors Alpaca's final state because a cancel can race a fill. DELETE network failure → raise `BrokerError`.

- [ ] **Step 1: Update the account factory**

In `backend/tests/factories.py`, replace `make_account`:

```python
def make_account(session, name="manual", cash="100000", commission="0",
                 mode="paper"):
    acct = Account(name=name, cash=Decimal(cash), starting_cash=Decimal(cash),
                   commission=Decimal(commission), mode=mode)
    session.add(acct)
    session.flush()
    return acct
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_live_adapter.py`:

```python
import json
from decimal import Decimal

import httpx
import pytest

from app.engine.alpaca_live_adapter import AlpacaLiveAdapter, BrokerError
from app.engine.engine import InvalidOrderState, TradingEngine
from tests.factories import make_account
from tests.fakes import FakeMarketData


def make_adapter(handler, extra_quotes=None):
    md = FakeMarketData()
    md.set_quote("AAPL", "180")
    for sym, price in (extra_quotes or {}).items():
        md.set_quote(sym, price)
    return AlpacaLiveAdapter(TradingEngine(md), "https://paper-api.test",
                             "key", "secret",
                             transport=httpx.MockTransport(handler))


@pytest.fixture
def live_account(session):
    acct = make_account(session, name="live", mode="live")
    session.commit()
    return acct


def test_place_order_submits_and_stores_broker_id(session, live_account):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["APCA-API-KEY-ID"]
        return httpx.Response(200, json={"id": "broker-1", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "pending"
    assert order.broker_order_id == "broker-1"
    assert seen["path"] == "/v2/orders"
    assert seen["key"] == "key"
    assert seen["body"]["symbol"] == "AAPL"
    assert seen["body"]["qty"] == "10"
    assert seen["body"]["side"] == "buy"
    assert seen["body"]["type"] == "market"
    assert seen["body"]["time_in_force"] == "day"
    assert seen["body"]["client_order_id"] == str(order.id)
    assert "limit_price" not in seen["body"]


def test_place_limit_order_includes_limit_price(session, live_account):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "broker-2", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, tif="gtc", limit_price=Decimal("175.50"))
    assert order.status == "pending"
    assert seen["body"]["limit_price"] == "175.50"
    assert seen["body"]["time_in_force"] == "gtc"


def test_local_validation_rejects_before_any_submit(session):
    poor = make_account(session, name="live", cash="100", mode="live")
    session.commit()

    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("submitted to broker despite local rejection")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=poor.id, symbol="AAPL",
                                side="buy", order_type="market", qty=10)
    assert order.status == "rejected"
    assert "insufficient cash" in order.reject_reason
    assert order.broker_order_id is None


def test_crypto_symbol_rejected_without_submit(session, live_account):
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("submitted crypto order to stock broker")

    # yfinance can resolve BTC-USD, so engine validation may pass; the
    # adapter's own guard must still reject it.
    adapter = make_adapter(handler, extra_quotes={"BTC-USD": "65000"})
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="BTC-USD", side="buy",
                                order_type="market", qty=Decimal("0.5"))
    assert order.status == "rejected"
    assert order.reject_reason == "crypto not supported in live trading yet"


def test_broker_rejection_rejects_locally_and_releases_reservation(
        session, live_account):
    def handler(request):
        return httpx.Response(
            403, json={"code": 40310000, "message": "insufficient buying power"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "rejected"
    assert order.reject_reason == "broker rejected: insufficient buying power"
    assert adapter.engine.available_cash(session, live_account) == \
        Decimal("100000")


def test_network_failure_rejects_locally(session, live_account):
    def handler(request):
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    assert order.status == "rejected"
    assert order.reject_reason.startswith("broker unreachable:")


def test_cancel_sends_delete_and_leaves_order_pending(session, live_account):
    deletes = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "broker-3",
                                             "status": "accepted"})
        deletes.append(request.url.path)
        return httpx.Response(204)

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    result = adapter.cancel_order(session, order.id)
    assert deletes == ["/v2/orders/broker-3"]
    # Not finalized locally: a cancel can race a fill; the poll mirrors
    # Alpaca's final answer.
    assert result.status == "pending"


def test_cancel_network_failure_raises_broker_error(session, live_account):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "broker-4",
                                             "status": "accepted"})
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    with pytest.raises(BrokerError):
        adapter.cancel_order(session, order.id)


def test_cancel_unknown_order_raises_value_error(session, live_account):
    adapter = make_adapter(lambda request: httpx.Response(204))
    with pytest.raises(ValueError):
        adapter.cancel_order(session, 999)


def test_cancel_non_pending_order_raises_invalid_state(session, live_account):
    def handler(request):
        return httpx.Response(200, json={"id": "broker-5", "status": "accepted"})

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy", order_type="limit",
                                qty=5, limit_price=Decimal("150"))
    order.status = "filled"
    with pytest.raises(InvalidOrderState):
        adapter.cancel_order(session, order.id)


def test_cancel_without_broker_id_cancels_locally(session, live_account):
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no broker order to cancel")

    adapter = make_adapter(handler)
    # Created directly via the engine: pending but never submitted.
    order = adapter.engine.place_order(
        session, account_id=live_account.id, symbol="AAPL", side="buy",
        order_type="limit", qty=5, limit_price=Decimal("150"))
    result = adapter.cancel_order(session, order.id)
    assert result.status == "cancelled"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_live_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.alpaca_live_adapter'`.

- [ ] **Step 4: Implement the adapter**

Create `backend/app/engine/alpaca_live_adapter.py`:

```python
from __future__ import annotations

import httpx
from sqlalchemy import select

from app.assets import is_crypto_symbol
from app.engine.engine import InvalidOrderState, TradingEngine
from app.models import Account, Order
from app.timeutil import utcnow


class BrokerError(Exception):
    """The broker API could not be reached or gave an unusable answer."""


class AlpacaLiveAdapter:
    """Live execution via Alpaca's brokerage API (paper endpoint by default).

    Alpaca decides fills; this adapter mirrors them into the local ledger.
    Local engine validation and cash reservation still run first so the
    books stay balanced by construction, and a periodic sync overwrites
    local cash with Alpaca's figure (Alpaca is the source of truth).
    """

    def __init__(self, engine: TradingEngine, base_url: str, key_id: str,
                 secret: str, transport: httpx.BaseTransport | None = None,
                 now_fn=utcnow):
        self.engine = engine
        self.now_fn = now_fn
        self._client = httpx.Client(
            base_url=base_url,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
            timeout=10,
            transport=transport,
        )

    def place_order(self, session, **kwargs) -> Order:
        order = self.engine.place_order(session, **kwargs)
        if order.status != "pending":
            return order
        if is_crypto_symbol(order.symbol):
            return self.engine.reject_order(
                session, order, "crypto not supported in live trading yet")
        body = {"symbol": order.symbol, "qty": str(order.qty),
                "side": order.side, "type": order.order_type,
                "time_in_force": order.tif,
                "client_order_id": str(order.id)}
        if order.order_type == "limit":
            body["limit_price"] = str(order.limit_price)
        try:
            r = self._client.post("/v2/orders", json=body)
        except httpx.HTTPError as e:
            return self.engine.reject_order(
                session, order, f"broker unreachable: {e}")
        if r.status_code not in (200, 201):
            return self.engine.reject_order(
                session, order, f"broker rejected: {self._error_message(r)}")
        order.broker_order_id = r.json()["id"]
        return order

    def cancel_order(self, session, order_id: int) -> Order:
        order = session.get(Order, order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        if order.status != "pending":
            raise InvalidOrderState(
                f"cannot cancel order in status {order.status}")
        if order.broker_order_id is None:
            # Defensive: a pending order that never reached the broker.
            return self.engine.cancel_order(session, order_id)
        try:
            self._client.delete(f"/v2/orders/{order.broker_order_id}")
        except httpx.HTTPError as e:
            raise BrokerError(f"broker unreachable: {e}") from e
        # Regardless of the DELETE response (204 accepted, 422 already
        # terminal, 404 unknown): a cancel can race a fill, so the next
        # poll mirrors Alpaca's final state instead of guessing here.
        return order

    @staticmethod
    def _error_message(r: httpx.Response) -> str:
        try:
            return r.json().get("message") or f"HTTP {r.status_code}"
        except ValueError:
            return f"HTTP {r.status_code}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_live_adapter.py -v`
Expected: 11 passed.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/engine/alpaca_live_adapter.py backend/tests/test_live_adapter.py backend/tests/factories.py
git commit -m "feat: AlpacaLiveAdapter submits and cancels orders against the broker API"
```

---

### Task 3: AlpacaLiveAdapter — fill mirroring and account sync

**Files:**
- Modify: `backend/app/engine/alpaca_live_adapter.py`
- Test: `backend/tests/test_live_adapter.py` (append)

**Interfaces:**
- Consumes: Task 2's class; `TradingEngine.apply_fill/cancel_order/expire_order/reject_order`; `Account.mode/last_synced_at/sync_detail`, `Position`.
- Produces: `process_pending(session, now=None) -> None` (mirrors every pending live-mode order) and `sync_account(session) -> None` (cash overwrite + position-drift detection) on `AlpacaLiveAdapter`. Task 5's jobs call both.

**Behavior (from spec):**
- `process_pending`: pending orders of live-mode accounts only (`join(Account)` on mode — a String column, safe in SQL). Skip any without `broker_order_id`. `GET /v2/orders/{broker_order_id}`; map `filled` → `apply_fill` at `Decimal(filled_avg_price)`, `canceled` → local cancel, `expired` → expire, `rejected` → reject with `"broker rejected: <reason or 'unspecified'>"`. Any other status (or any HTTP/network error) → leave pending for the next cycle.
- `sync_account`: find the live account (none → no-op). `GET /v2/account` and `GET /v2/positions`; any failure → return with nothing changed (staleness shows via `last_synced_at` age). Success → `cash = Decimal(account_json["cash"])`; compare local positions (qty > 0, filtered in Python) against remote by symbol; mismatches → `sync_detail = "SYM: local X, alpaca Y; ..."` (sorted by symbol), match → `sync_detail = None`; set `last_synced_at = self.now_fn()`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_live_adapter.py`:

```python
def _accepting_post(request):
    return httpx.Response(200, json={"id": "broker-9", "status": "accepted"})


def place_pending(session, account, poll_response_json=None,
                  poll_error=False):
    """Adapter whose POST accepts and whose GET returns the given order json."""
    def handler(request):
        if request.method == "POST" and request.url.path == "/v2/orders":
            return _accepting_post(request)
        if poll_error:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=poll_response_json)

    adapter = make_adapter(handler)
    order = adapter.place_order(session, account_id=account.id, symbol="AAPL",
                                side="buy", order_type="market", qty=10)
    assert order.status == "pending"
    return adapter, order


def test_poll_mirrors_fill_at_alpaca_average_price(session, live_account):
    adapter, order = place_pending(
        session, live_account,
        {"status": "filled", "filled_avg_price": "179.55"})
    adapter.process_pending(session)
    session.flush()
    assert order.status == "filled"
    assert live_account.cash == Decimal("100000") - Decimal("179.55") * 10


def test_poll_mirrors_cancellation(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "canceled"})
    adapter.process_pending(session)
    assert order.status == "cancelled"


def test_poll_mirrors_expiry(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "expired"})
    adapter.process_pending(session)
    assert order.status == "expired"


def test_poll_mirrors_rejection_with_reason(session, live_account):
    adapter, order = place_pending(session, live_account, {"status": "rejected"})
    adapter.process_pending(session)
    assert order.status == "rejected"
    assert order.reject_reason == "broker rejected: unspecified"


def test_poll_leaves_nonterminal_statuses_pending(session, live_account):
    adapter, order = place_pending(
        session, live_account,
        {"status": "partially_filled", "filled_avg_price": "179.00"})
    adapter.process_pending(session)
    assert order.status == "pending"


def test_poll_network_failure_keeps_order_pending(session, live_account):
    adapter, order = place_pending(session, live_account, poll_error=True)
    adapter.process_pending(session)
    assert order.status == "pending"


def test_poll_ignores_paper_orders(session, live_account):
    paper = make_account(session, name="paper-acct")
    session.commit()
    polled = []

    def handler(request):
        if request.method == "POST":
            return _accepting_post(request)
        polled.append(request.url.path)
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "1"})

    adapter = make_adapter(handler)
    live_order = adapter.place_order(session, account_id=live_account.id,
                                     symbol="AAPL", side="buy",
                                     order_type="market", qty=1)
    paper_order = adapter.engine.place_order(
        session, account_id=paper.id, symbol="AAPL", side="buy",
        order_type="limit", qty=1, limit_price=Decimal("100"))
    adapter.process_pending(session)
    assert polled == [f"/v2/orders/{live_order.broker_order_id}"]
    assert paper_order.status == "pending"


def sync_adapter(account_json, positions_json, fail=False):
    def handler(request):
        if fail:
            raise httpx.ConnectError("down")
        if request.url.path == "/v2/account":
            return httpx.Response(200, json=account_json)
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=positions_json)
        if request.method == "POST":
            return _accepting_post(request)
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "180"})

    return make_adapter(handler)


def test_sync_overwrites_cash_and_stamps_time(session, live_account):
    adapter = sync_adapter({"cash": "98765.43"}, [])
    adapter.sync_account(session)
    assert live_account.cash == Decimal("98765.43")
    assert live_account.last_synced_at is not None
    assert live_account.sync_detail is None


def test_sync_detects_position_mismatch(session, live_account):
    adapter = sync_adapter({"cash": "98204.50"},
                           [{"symbol": "AAPL", "qty": "12"}])
    # Establish a local position of 10 AAPL via a mirrored fill.
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    adapter.process_pending(session)
    assert order.status == "filled"
    adapter.sync_account(session)
    assert live_account.sync_detail == "AAPL: local 10, alpaca 12"


def test_sync_match_clears_previous_mismatch(session, live_account):
    adapter = sync_adapter({"cash": "98204.50"},
                           [{"symbol": "AAPL", "qty": "10"}])
    order = adapter.place_order(session, account_id=live_account.id,
                                symbol="AAPL", side="buy",
                                order_type="market", qty=10)
    adapter.process_pending(session)
    assert order.status == "filled"
    live_account.sync_detail = "stale mismatch"
    adapter.sync_account(session)
    assert live_account.sync_detail is None


def test_sync_failure_changes_nothing(session, live_account):
    adapter = sync_adapter({}, [], fail=True)
    adapter.sync_account(session)
    assert live_account.cash == Decimal("100000")
    assert live_account.last_synced_at is None


def test_sync_without_live_account_is_a_noop(session):
    make_account(session, name="paper-only")
    session.commit()
    adapter = sync_adapter({"cash": "1"}, [])
    adapter.sync_account(session)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_live_adapter.py -v`
Expected: the Task 2 tests still pass; the new ones FAIL with `AttributeError: 'AlpacaLiveAdapter' object has no attribute 'process_pending'`.

- [ ] **Step 3: Implement**

Add to `AlpacaLiveAdapter` (imports `select`, `Account`, `Decimal` — extend the import block at the top of the file with `from decimal import Decimal` and `from app.models import Account, Order, Position`):

```python
    def process_pending(self, session, now=None) -> None:
        pending = session.scalars(
            select(Order).join(Account).where(
                Order.status == "pending", Account.mode == "live")).all()
        for order in pending:
            if order.broker_order_id is None:
                continue  # never reached the broker; nothing to mirror
            try:
                r = self._client.get(f"/v2/orders/{order.broker_order_id}")
            except httpx.HTTPError:
                continue  # wait for the next cycle
            if r.status_code != 200:
                continue
            data = r.json()
            status = data["status"]
            if status == "filled":
                self.engine.apply_fill(session, order,
                                       Decimal(data["filled_avg_price"]))
            elif status == "canceled":
                self.engine.cancel_order(session, order.id)
            elif status == "expired":
                self.engine.expire_order(session, order)
            elif status == "rejected":
                reason = data.get("reason") or "unspecified"
                self.engine.reject_order(session, order,
                                         f"broker rejected: {reason}")
            # anything else (new, accepted, partially_filled, ...) waits

    def sync_account(self, session) -> None:
        account = session.scalar(select(Account).where(Account.mode == "live"))
        if account is None:
            return
        try:
            acct_r = self._client.get("/v2/account")
            pos_r = self._client.get("/v2/positions")
        except httpx.HTTPError:
            return  # keep last-known values; last_synced_at ages visibly
        if acct_r.status_code != 200 or pos_r.status_code != 200:
            return
        account.cash = Decimal(acct_r.json()["cash"])
        remote = {p["symbol"]: Decimal(p["qty"]) for p in pos_r.json()}
        local_rows = session.scalars(select(Position).where(
            Position.account_id == account.id)).all()
        # qty is TEXT in SQLite: compare in Python, never in SQL.
        local = {p.symbol: p.qty for p in local_rows if p.qty > 0}
        diffs = [f"{s}: local {local.get(s, Decimal('0'))}, "
                 f"alpaca {remote.get(s, Decimal('0'))}"
                 for s in sorted(set(local) | set(remote))
                 if local.get(s, Decimal("0")) != remote.get(s, Decimal("0"))]
        account.sync_detail = "; ".join(diffs) if diffs else None
        account.last_synced_at = self.now_fn()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_live_adapter.py -v`
Expected: 23 passed.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/engine/alpaca_live_adapter.py backend/tests/test_live_adapter.py
git commit -m "feat: mirror Alpaca fills and sync live account cash and positions"
```

---

### Task 4: SimAdapter ownership goes order-based

**Files:**
- Modify: `backend/app/engine/sim_adapter.py`
- Modify: `backend/app/main.py` (the two `SimAdapter(...)` constructions in `build_deps`)
- Modify: `backend/tests/conftest.py` (the two in the `client` fixture)
- Modify: `backend/tests/test_jobs.py` (the two in the `deps` fixture + new test)

**Interfaces:**
- Consumes: `Account.mode` (Task 1), `Order.account` relationship (existing).
- Produces: `SimAdapter(engine, market_data, calendar, now_fn=utcnow, owns_order=None)` — the predicate now takes an `Order` (was `owns_symbol` taking a symbol string). Task 5's wiring passes the mode-aware predicates below.

**Why:** three adapters will share one `orders` table. The Phase 2 `owns_symbol` predicate partitions by symbol shape but cannot exclude live-mode orders — a live AAPL order looks identical to a paper one by symbol. The predicate must see the order (and through it, the account's mode).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_jobs.py`:

```python
def test_sim_adapters_never_touch_live_orders(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    with deps.session_factory() as s:
        live = make_account(s, name="live", mode="live")
        # Pending live order created via the engine (as if submitted to the
        # broker); the sim adapters must not fill or expire it.
        order = deps.engine.place_order(
            s, account_id=live.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_jobs.py::test_sim_adapters_never_touch_live_orders -v`
Expected: FAIL — the stock `SimAdapter` fills the live order (`status == "filled"`), because `owns_symbol` cannot see the account mode.

- [ ] **Step 3: Rename the predicate in SimAdapter**

In `backend/app/engine/sim_adapter.py`, change `__init__` and the filter line:

```python
    def __init__(self, engine: TradingEngine, market_data, calendar, now_fn=utcnow,
                 owns_order=None):
        self.engine = engine
        self.market_data = market_data
        self.calendar = calendar
        self.now_fn = now_fn
        # Three adapters (stock + crypto sims, live) share one `orders`
        # table; each must only touch orders it owns — partitioned by the
        # account's mode and the symbol's shape — or it will steal and
        # mis-price another pipeline's pending orders. Defaults to "owns
        # everything" so single-pipeline callers/tests are unaffected.
        self.owns_order = owns_order or (lambda order: True)
```

and in `process_pending`:

```python
        pending = [o for o in pending if self.owns_order(o)]
```

- [ ] **Step 4: Update the three call sites**

In `backend/app/main.py` (`build_deps`):

```python
    execution = SimAdapter(engine, market_data, calendar,
                           owns_order=lambda o: o.account.mode != "live"
                           and not is_crypto_symbol(o.symbol))
    ...
    crypto_execution = SimAdapter(crypto_engine, crypto_market_data, crypto_calendar,
                                  owns_order=lambda o: o.account.mode != "live"
                                  and is_crypto_symbol(o.symbol))
```

In `backend/tests/conftest.py` (`client` fixture) and `backend/tests/test_jobs.py` (`deps` fixture), apply the same two replacements:

```python
    execution = SimAdapter(engine, fake_md, fake_cal,
                           owns_order=lambda o: o.account.mode != "live"
                           and not is_crypto_symbol(o.symbol))
    ...
    crypto_execution = SimAdapter(crypto_engine, crypto_fake_md, crypto_fake_cal,
                                  owns_order=lambda o: o.account.mode != "live"
                                  and is_crypto_symbol(o.symbol))
```

(In `test_jobs.py` the variables are `md`/`cal`/`crypto_md`/`crypto_cal` — keep those names.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_jobs.py -v`
Expected: all pass, including the new ownership test.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass — the five single-pipeline `SimAdapter(...)` constructions (`test_sim_market.py`, `test_sim_limit_expiry.py`, `test_strategy_runner.py`) rely on the default predicate and are untouched.

- [ ] **Step 7: Commit**

```bash
git add backend/app/engine/sim_adapter.py backend/app/main.py backend/tests/conftest.py backend/tests/test_jobs.py
git commit -m "feat: adapters own orders by account mode, not just symbol shape"
```

---

### Task 5: Wiring — AppDeps, startup live account, scheduler jobs

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/jobs.py`
- Create: `backend/tests/live_fixtures.py`
- Test: `backend/tests/test_jobs.py` (append), `backend/tests/test_deps_routing.py` (append), `backend/tests/test_live_startup.py` (new)

**Interfaces:**
- Consumes: `AlpacaLiveAdapter` (Tasks 2–3), `owns_order` predicates (Task 4), trading settings (Task 1).
- Produces: `AppDeps.live_execution: object | None = None` (last dataclass field); `AppDeps.execution_for(account, symbol)` — live-mode account → `live_execution`, else Phase 2 symbol routing; `jobs.run_live_sync(deps)`; scheduler job id `"live_sync"` (interval, 10 minutes, only when enabled); startup creation of the `"live"` account + one synchronous sync. Task 6's API routes call `execution_for`. Test helper `tests/live_fixtures.py: make_live_deps(session_factory, tmp_path, live_handler=None)` and `default_live_handler`.

- [ ] **Step 1: Create the shared test fixture module**

Create `backend/tests/live_fixtures.py` — an `AppDeps` builder mirroring `conftest.py`'s `client` fixture, plus an optional live stack:

```python
from decimal import Decimal
from pathlib import Path

import httpx

from app.assets import is_crypto_symbol
from app.config import Settings
from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
from app.engine.engine import TradingEngine
from app.engine.sim_adapter import SimAdapter
from app.main import AppDeps
from app.strategy.runner import StrategyRunner
from tests.fakes import FakeCalendar, FakeMarketData


def default_live_handler(request):
    path = request.url.path
    if path == "/v2/account":
        return httpx.Response(200, json={"cash": "50000"})
    if path == "/v2/positions":
        return httpx.Response(200, json=[])
    if request.method == "POST" and path == "/v2/orders":
        return httpx.Response(200, json={"id": "b-live-1", "status": "accepted"})
    if request.method == "DELETE":
        return httpx.Response(204)
    if request.method == "GET" and path.startswith("/v2/orders/"):
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "100"})
    return httpx.Response(404)


def make_live_deps(session_factory, tmp_path, live_handler=None):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    # yfinance can resolve crypto tickers, so the stock service may too;
    # this exercises the live adapter's own crypto guard.
    md.set_quote("BTC-USD", "65000")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal,
                           owns_order=lambda o: o.account.mode != "live"
                           and not is_crypto_symbol(o.symbol))

    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    crypto_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, crypto_cal,
                                  owns_order=lambda o: o.account.mode != "live"
                                  and is_crypto_symbol(o.symbol))

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir(exist_ok=True)

    def execution_for_symbol(symbol: str):
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        return crypto_md if is_crypto_symbol(symbol) else md

    runner = StrategyRunner(Path(strategies_dir), session_factory,
                            execution_for_symbol, market_data_for_symbol,
                            Decimal("100000"))
    live_execution = None
    if live_handler is not None:
        live_execution = AlpacaLiveAdapter(
            engine, "https://paper-api.test", "key", "secret",
            transport=httpx.MockTransport(live_handler))
    return AppDeps(settings=Settings(password="pw", secret_key="test-secret"),
                   session_factory=session_factory, market_data=md,
                   calendar=cal, engine=engine, execution=execution,
                   runner=runner, crypto_market_data=crypto_md,
                   crypto_calendar=crypto_cal, crypto_engine=crypto_engine,
                   crypto_execution=crypto_execution,
                   live_execution=live_execution)
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_live_startup.py`:

```python
from decimal import Decimal

from sqlalchemy import select

from app.main import create_app
from app.models import Account
from tests.live_fixtures import default_live_handler, make_live_deps


def test_startup_creates_and_syncs_live_account(session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path, default_live_handler)
    create_app(deps, start_scheduler=False)
    with session_factory() as s:
        live = s.scalar(select(Account).where(Account.mode == "live"))
        assert live is not None
        assert live.name == "live"
        assert live.cash == Decimal("50000")  # synced, not the placeholder 0
        assert live.last_synced_at is not None


def test_startup_without_live_adapter_creates_no_live_account(
        session_factory, tmp_path):
    deps = make_live_deps(session_factory, tmp_path, live_handler=None)
    create_app(deps, start_scheduler=False)
    with session_factory() as s:
        assert s.scalar(select(Account).where(Account.mode == "live")) is None


def test_startup_live_account_is_idempotent(session_factory, tmp_path):
    create_app(make_live_deps(session_factory, tmp_path, default_live_handler),
               start_scheduler=False)
    create_app(make_live_deps(session_factory, tmp_path, default_live_handler),
               start_scheduler=False)
    with session_factory() as s:
        rows = s.scalars(select(Account).where(Account.mode == "live")).all()
        assert len(rows) == 1
```

Append to `backend/tests/test_deps_routing.py`:

```python
from types import SimpleNamespace

from app.main import AppDeps


def _bare_deps(live_execution):
    return AppDeps(settings=None, session_factory=None, market_data="stock-md",
                   calendar=None, engine=None, execution="stock-exec",
                   runner=None, crypto_market_data="crypto-md",
                   crypto_calendar=None, crypto_engine=None,
                   crypto_execution="crypto-exec",
                   live_execution=live_execution)


def test_execution_for_routes_live_account_to_live_adapter():
    deps = _bare_deps("live-exec")
    assert deps.execution_for(SimpleNamespace(mode="live"), "AAPL") == "live-exec"


def test_execution_for_routes_paper_account_by_symbol_shape():
    deps = _bare_deps(None)
    paper = SimpleNamespace(mode="paper")
    assert deps.execution_for(paper, "AAPL") == "stock-exec"
    assert deps.execution_for(paper, "BTC-USD") == "crypto-exec"
```

Append to `backend/tests/test_jobs.py`:

```python
def test_run_process_pending_mirrors_live_fill(deps):
    import httpx

    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.models import Order

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "b-7", "status": "accepted"})
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "101"})

    deps.live_execution = AlpacaLiveAdapter(
        deps.engine, "https://paper-api.test", "k", "s",
        transport=httpx.MockTransport(handler))
    with deps.session_factory() as s:
        live = make_account(s, name="live", mode="live")
        order = deps.live_execution.place_order(
            s, account_id=live.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_run_live_sync_updates_cash(deps):
    import httpx
    from decimal import Decimal
    from sqlalchemy import select

    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.jobs import run_live_sync
    from app.models import Account

    def handler(request):
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"cash": "42000"})
        return httpx.Response(200, json=[])

    deps.live_execution = AlpacaLiveAdapter(
        deps.engine, "https://paper-api.test", "k", "s",
        transport=httpx.MockTransport(handler))
    with deps.session_factory() as s:
        make_account(s, name="live", mode="live")
        s.commit()
    run_live_sync(deps)
    with deps.session_factory() as s:
        live = s.scalar(select(Account).where(Account.mode == "live"))
        assert live.cash == Decimal("42000")


def test_build_scheduler_registers_live_sync_only_when_enabled(deps):
    ids = {j.id for j in build_scheduler(deps).get_jobs()}
    assert "live_sync" not in ids

    deps.live_execution = object()
    ids = {j.id for j in build_scheduler(deps).get_jobs()}
    assert "live_sync" in ids
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_live_startup.py tests/test_deps_routing.py tests/test_jobs.py -v`
Expected: FAIL — `AppDeps` has no `live_execution` field (`TypeError: unexpected keyword argument`), no `execution_for`, no `run_live_sync`.

- [ ] **Step 4: Implement**

In `backend/app/main.py`:

Add imports:

```python
from decimal import Decimal

from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
```

Extend `AppDeps` (new field LAST — it has a default; add the method after `market_data_for_symbol`):

```python
    crypto_execution: SimAdapter
    live_execution: AlpacaLiveAdapter | None = None

    def execution_for(self, account, symbol: str):
        if account.mode == "live":
            return self.live_execution
        return self.execution_for_symbol(symbol)
```

In `build_deps`, after `crypto_execution` is built:

```python
    live_execution = None
    if settings.alpaca_trading_key_id:
        live_execution = AlpacaLiveAdapter(
            engine, settings.alpaca_trading_base,
            settings.alpaca_trading_key_id, settings.alpaca_trading_secret)
```

(the live adapter reuses the stock `engine` — validation quotes come from the stock market-data service, and bookkeeping is identical) and pass `live_execution=live_execution` to the returned `AppDeps`.

In `create_app`, after the `manual` account block and before `deps.runner.discover()`:

```python
    if deps.live_execution is not None:
        with deps.session_factory() as session:
            if session.scalar(select(Account).where(Account.mode == "live")) is None:
                # Cash placeholder 0: the sync below immediately replaces it
                # with Alpaca's real figure, so the UI never sees it.
                session.add(Account(name="live", kind="manual", mode="live",
                                    cash=Decimal("0"),
                                    starting_cash=Decimal("0")))
                session.commit()
            deps.live_execution.sync_account(session)
            session.commit()
```

In `backend/app/jobs.py`:

```python
def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        deps.crypto_execution.process_pending(session)
        if deps.live_execution is not None:
            deps.live_execution.process_pending(session)
        session.commit()


def run_live_sync(deps) -> None:
    with deps.session_factory() as session:
        deps.live_execution.sync_account(session)
        session.commit()
```

and in `build_scheduler`, before `deps.runner.register_jobs(scheduler)`:

```python
    if deps.live_execution is not None:
        scheduler.add_job(run_live_sync, "interval", minutes=10, args=[deps],
                          id="live_sync")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_live_startup.py tests/test_deps_routing.py tests/test_jobs.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass (`live_execution` defaults to `None`, so `conftest.py`'s `AppDeps(...)` call is unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/app/jobs.py backend/tests/live_fixtures.py backend/tests/test_live_startup.py backend/tests/test_deps_routing.py backend/tests/test_jobs.py
git commit -m "feat: wire live adapter into deps, startup account creation, and scheduler"
```

---

### Task 6: API — mode-aware routing and new response fields

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/orders.py`
- Modify: `backend/app/api/accounts.py`
- Modify: `backend/app/api/journal.py`
- Test: `backend/tests/test_api_live.py` (new)

**Interfaces:**
- Consumes: `AppDeps.execution_for` (Task 5), `BrokerError` (Task 2), `Account.mode/last_synced_at/sync_detail` (Task 1), `tests/live_fixtures.py` (Task 5).
- Produces: `AccountOut` gains `mode: str`, `last_synced_at: datetime | None`, `sync_detail: str | None`; `TradeOut` gains `account_mode: str`. Order placement/cancellation route by account mode. `BrokerError` → HTTP 502; live account present but adapter unconfigured → HTTP 503. The Phase 3 frontend plan consumes these fields.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_api_live.py`:

```python
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.live_fixtures import default_live_handler, make_live_deps


def make_client(session_factory, tmp_path, live_handler=default_live_handler):
    deps = make_live_deps(session_factory, tmp_path, live_handler)
    app = create_app(deps, start_scheduler=False)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    c.deps = deps
    return c


@pytest.fixture
def live_client(session_factory, tmp_path):
    return make_client(session_factory, tmp_path)


def account_by_name(client, name):
    accounts = client.get("/api/accounts").json()
    return next(a for a in accounts if a["name"] == name)


def test_accounts_expose_mode_and_sync_fields(live_client):
    live = account_by_name(live_client, "live")
    assert live["mode"] == "live"
    assert live["cash"] == "50000"
    assert live["last_synced_at"] is not None
    assert live["sync_detail"] is None
    manual = account_by_name(live_client, "manual")
    assert manual["mode"] == "paper"
    assert manual["last_synced_at"] is None


def test_account_detail_includes_mode(live_client):
    live = account_by_name(live_client, "live")
    detail = live_client.get(f"/api/accounts/{live['id']}").json()
    assert detail["mode"] == "live"
    assert detail["sync_detail"] is None


def test_live_order_stays_pending_at_placement(live_client):
    live = account_by_name(live_client, "live")
    r = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "10"})
    assert r.status_code == 201
    body = r.json()
    # Live orders are never filled at placement — Alpaca decides via the poll.
    assert body["status"] == "pending"


def test_paper_order_still_fills_immediately(live_client):
    manual = account_by_name(live_client, "manual")
    r = live_client.post(f"/api/accounts/{manual['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "10"})
    assert r.status_code == 201
    assert r.json()["status"] == "filled"


def test_crypto_on_live_account_is_rejected(live_client):
    live = account_by_name(live_client, "live")
    r = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "BTC-USD", "side": "buy", "order_type": "market",
        "qty": "0.5"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "crypto not supported in live trading yet"


def test_cancel_live_order_returns_pending_until_poll(live_client):
    live = account_by_name(live_client, "live")
    placed = live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "limit", "qty": "5",
        "limit_price": "90"}).json()
    r = live_client.post(f"/api/orders/{placed['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_cancel_when_broker_unreachable_returns_502(session_factory, tmp_path):
    def handler(request):
        if request.method == "DELETE":
            raise httpx.ConnectError("down")
        return default_live_handler(request)

    client = make_client(session_factory, tmp_path, handler)
    live = account_by_name(client, "live")
    placed = client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "limit", "qty": "5",
        "limit_price": "90"}).json()
    r = client.post(f"/api/orders/{placed['id']}/cancel")
    assert r.status_code == 502


def test_journal_trades_carry_account_mode(live_client):
    from app.jobs import run_process_pending

    manual = account_by_name(live_client, "manual")
    live = account_by_name(live_client, "live")
    live_client.post(f"/api/accounts/{manual['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1"})
    live_client.post(f"/api/accounts/{live['id']}/orders", json={
        "symbol": "SPY", "side": "buy", "order_type": "market", "qty": "1"})
    run_process_pending(live_client.deps)  # poll mirrors the live fill

    paper_trades = live_client.get(
        f"/api/journal?account_id={manual['id']}").json()
    assert paper_trades[0]["account_mode"] == "paper"
    live_trades = live_client.get(
        f"/api/journal?account_id={live['id']}").json()
    assert live_trades[0]["account_mode"] == "live"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_api_live.py -v`
Expected: FAIL — `mode` missing from account responses (`KeyError`), live order routed to the stock SimAdapter (fills immediately instead of staying pending), `account_mode` missing from journal.

- [ ] **Step 3: Implement**

In `backend/app/api/schemas.py`:

```python
class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    mode: str
    cash: Money
    starting_cash: Money
    last_synced_at: datetime | None
    sync_detail: str | None
```

and add `account_mode: str` to `TradeOut` after `note`:

```python
    note: str | None
    account_mode: str
```

In `backend/app/api/orders.py`, replace the two routing lines and add the error mapping (imports: `from app.engine.alpaca_live_adapter import BrokerError`):

```python
@router.post("/accounts/{account_id}/orders", response_model=OrderOut,
             status_code=201)
def place_order(account_id: int, body: OrderIn, session=Depends(get_session),
                deps=Depends(get_deps)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "no such account")
    execution = deps.execution_for(account, body.symbol)
    if execution is None:
        # Live account exists but trading keys were removed from the env.
        raise HTTPException(503, "live trading not configured")
    return execution.place_order(
        session, account_id=account_id, symbol=body.symbol, side=body.side,
        order_type=body.order_type, qty=body.qty, tif=body.tif,
        limit_price=body.limit_price, idempotency_key=body.idempotency_key)
```

```python
@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, session=Depends(get_session),
                 deps=Depends(get_deps)):
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "no such order")
    execution = deps.execution_for(order.account, order.symbol)
    if execution is None:
        raise HTTPException(503, "live trading not configured")
    try:
        return execution.cancel_order(session, order_id)
    except ValueError:
        raise HTTPException(404, "no such order")
    except InvalidOrderState as e:
        raise HTTPException(409, str(e))
    except BrokerError as e:
        raise HTTPException(502, str(e))
```

In `backend/app/api/accounts.py`, `account_detail`'s constructor call gains the new fields:

```python
    return AccountDetailOut(
        id=account.id, name=account.name, kind=account.kind, mode=account.mode,
        cash=account.cash, starting_cash=account.starting_cash,
        last_synced_at=account.last_synced_at, sync_detail=account.sync_detail,
        equity=equity,
        positions=[PositionOut(**vars(pv)) for pv in values])
```

In `backend/app/api/journal.py`, the `TradeOut` construction gains:

```python
    return [TradeOut(order_id=f.order_id, symbol=f.order.symbol,
                     side=f.order.side, qty=f.qty, price=f.price,
                     commission=f.commission, realized_pnl=f.realized_pnl,
                     filled_at=f.filled_at, note=notes.get(f.order_id),
                     account_mode=f.order.account.mode)
            for f in fills]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_live.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass. `test_api_accounts_orders.py` and `test_api_market_journal_strategies.py` exercise these routes with paper accounts — they must stay green with zero changes to their files (the new response fields are additive; paper routing behavior is identical).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/orders.py backend/app/api/accounts.py backend/app/api/journal.py backend/tests/test_api_live.py
git commit -m "feat: route orders by account mode and expose mode/sync fields in the API"
```
