from __future__ import annotations

from datetime import time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.assets import contract_multiplier, is_crypto_symbol, is_option_symbol, parse_occ
from app.marketdata.base import MarketDataError, UnknownSymbolError
from app.models import Account, Fill, Order, Position
from app.timeutil import utcnow

NY_TZ = ZoneInfo("America/New_York")


class InvalidOrderState(Exception):
    pass


class TradingEngine:
    """Bookkeeping: validation, reservations, cancellation. Fill policy lives
    in the execution adapter (SimAdapter), which calls back into apply_fill."""

    def __init__(self, market_data, now_fn=utcnow):
        self.market_data = market_data
        self.now_fn = now_fn

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
                      placed_at=self.now_fn())
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

        if is_option_symbol(order.symbol):
            # User/API placement only; the settlement job constructs orders
            # directly and never runs this guard.
            now_ny = self.now_fn().replace(tzinfo=timezone.utc).astimezone(NY_TZ)
            expiry = parse_occ(order.symbol).expiry
            if expiry < now_ny.date() or (expiry == now_ny.date()
                                          and now_ny.time() >= time(16, 0)):
                return self.reject_order(session, order, "contract expired")

        try:
            quote = self.market_data.get_quote(order.symbol)
        except UnknownSymbolError:
            return self.reject_order(session, order, f"unknown symbol: {order.symbol}")
        except MarketDataError:
            return self.reject_order(session, order, "market data unavailable")

        if side == "buy":
            if order_type == "limit":
                est_price = limit_price
            elif quote.ask is not None:
                est_price = quote.ask  # options reserve at the ask (fill price)
            else:
                est_price = quote.price
            cost = (est_price * qty * contract_multiplier(order.symbol)
                    + account.commission)
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

    def apply_fill(self, session, order: Order, price: Decimal,
                   commission: Decimal | None = None) -> Fill:
        if order.status != "pending":
            raise InvalidOrderState(f"cannot fill order in status {order.status}")
        account = session.get(Account, order.account_id)
        if commission is None:
            commission = account.commission
        mult = contract_multiplier(order.symbol)
        fill = Fill(order_id=order.id, price=price, qty=order.qty,
                    commission=commission, filled_at=self.now_fn())
        pos = self._get_or_create_position(session, order.account_id, order.symbol)
        if order.side == "buy":
            account.cash -= price * order.qty * mult + commission
            new_qty = pos.qty + order.qty
            pos.avg_cost = ((pos.avg_cost * pos.qty + price * order.qty) / new_qty
                            ).quantize(Decimal("0.0001"))
            pos.qty = new_qty
        else:
            pnl = ((price - pos.avg_cost) * order.qty * mult - commission
                   ).quantize(Decimal("0.0001"))
            fill.realized_pnl = pnl
            pos.realized_pnl += pnl
            pos.qty -= order.qty
            account.cash += price * order.qty * mult - commission
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
