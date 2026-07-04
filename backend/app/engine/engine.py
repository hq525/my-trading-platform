from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.assets import is_crypto_symbol
from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.models import Account, Fill, Order, Position
from app.timeutil import utcnow


class InvalidOrderState(Exception):
    pass


class TradingEngine:
    """Bookkeeping: validation, reservations, cancellation. Fill policy lives
    in the execution adapter (SimAdapter), which calls back into apply_fill."""

    def __init__(self, market_data):
        self.market_data = market_data

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
                           qty=Decimal("0"), avg_cost=Decimal("0"), realized_pnl=Decimal("0"))
            session.add(pos)
            session.flush()
        return pos
