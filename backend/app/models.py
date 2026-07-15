from __future__ import annotations

import json
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
    mode: Mapped[str] = mapped_column(String, default="paper")  # paper | live | replay
    cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    starting_cash: Mapped[Decimal] = mapped_column(SqliteDecimal)
    commission: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Live-account sync (spec: Alpaca is the source of truth for live cash).
    last_synced_at: Mapped[datetime | None] = mapped_column(default=None)
    sync_detail: Mapped[str | None] = mapped_column(String, default=None)
    replay_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("replay_sessions.id"), default=None)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # buy | sell
    order_type: Mapped[str] = mapped_column(String)  # market | limit
    tif: Mapped[str] = mapped_column(String, default="day")  # day | gtc
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal)
    limit_price: Mapped[Decimal | None] = mapped_column(SqliteDecimal, default=None)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending | filled | cancelled | rejected | expired
    reject_reason: Mapped[str | None] = mapped_column(String, default=None)
    reserved_cash: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
    idempotency_key: Mapped[str | None] = mapped_column(String, default=None)
    broker_order_id: Mapped[str | None] = mapped_column(String, default=None)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)

    account: Mapped[Account] = relationship()


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    price: Mapped[Decimal] = mapped_column(SqliteDecimal)
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal)
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
    qty: Mapped[Decimal] = mapped_column(SqliteDecimal, default=Decimal("0"))
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
