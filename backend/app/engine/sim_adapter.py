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
