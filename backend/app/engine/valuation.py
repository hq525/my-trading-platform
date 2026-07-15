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
    all_positions = session.scalars(select(Position).where(
        Position.account_id == account.id)).all()
    positions = [p for p in all_positions if p.qty > 0]
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
        if account.mode == "replay":
            continue  # replay snapshots are written by the stepper, virtual-dated
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
