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
