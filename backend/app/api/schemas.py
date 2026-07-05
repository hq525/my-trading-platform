from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

# All money crosses the API as strings — no float rounding in transit.
def _serialize_money(d: Decimal) -> str:
    # format(d, "f") forces fixed-point notation, avoiding the scientific
    # notation str(Decimal) switches to below 1e-6 — reachable for 8dp
    # crypto quantities even though it never was for 4dp money.
    s = format(d, "f")
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s

Money = Annotated[Decimal, PlainSerializer(_serialize_money, return_type=str, when_used="json")]
Qty = Annotated[Decimal, PlainSerializer(_serialize_money, return_type=str, when_used="json")]


class LoginIn(BaseModel):
    password: str


class OrderIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    qty: Decimal
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
    qty: Qty
    limit_price: Money | None
    status: str
    reject_reason: str | None
    placed_at: datetime


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


class PositionOut(BaseModel):
    symbol: str
    qty: Qty
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
    qty: Qty
    price: Money
    commission: Money
    realized_pnl: Money | None
    filled_at: datetime
    note: str | None
    account_mode: str


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
