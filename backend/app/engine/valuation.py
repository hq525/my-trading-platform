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
