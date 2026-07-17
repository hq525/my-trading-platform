from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.assets import contract_multiplier, parse_occ
from app.engine.engine import TradingEngine
from app.engine.valuation import ny_date
from app.marketdata.base import MarketDataError
from app.models import Account, Order
from app.timeutil import utcnow


class OptionsSimAdapter:
    """Simulated options execution: cross-the-spread fills.

    Market buys fill at the ask, market sells at the bid. Limit buys fill at
    the ask once ask <= limit; limit sells at the bid once bid >= limit.
    One-sided or zero-bid quotes never fabricate a fill — the order stays
    pending until the side exists. Dead contracts (expiry < today NY) are
    expired before any fill attempt, so a stale after-hours snapshot can
    never fill them even if the 16:05 settlement job missed a day.
    """

    def __init__(self, engine: TradingEngine, market_data, calendar,
                 now_fn=utcnow, owns_order=None):
        self.engine = engine
        self.market_data = market_data
        self.calendar = calendar
        self.now_fn = now_fn
        self.owns_order = owns_order or (lambda order: True)

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
        today = ny_date(now)
        pending = session.scalars(
            select(Order).where(Order.status == "pending")).all()
        pending = [o for o in pending if self.owns_order(o)]

        for order in pending:
            if parse_occ(order.symbol).expiry < today:
                self.engine.expire_order(session, order)
                order.reject_reason = "contract expired"
            elif order.tif == "day" and now >= self.calendar.expiry_time(order.placed_at):
                self.engine.expire_order(session, order)

        if not self.calendar.is_open(now):
            return

        # Flush local expiries so refresh() below re-reads them instead of
        # clobbering them back to "pending" (same pattern as SimAdapter).
        session.flush()

        for order in pending:
            session.refresh(order)
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
            self.engine.reject_order(session, order, "market data unavailable")
            return
        if order.side == "buy":
            if quote.ask is None:
                return  # no ask: stay pending, never fabricate a fill
            account = session.get(Account, order.account_id)
            # Recheck at the ACTUAL fill price (ask), never quote.price (mid):
            # checking at mid while debiting at ask lets cash go negative.
            cost = (quote.ask * order.qty * contract_multiplier(order.symbol)
                    + account.commission)
            spendable = (self.engine.available_cash(session, account)
                         + order.reserved_cash)
            if cost > spendable:
                self.engine.reject_order(
                    session, order,
                    f"insufficient cash at fill: need {cost}, available {spendable}")
                return
            self.engine.apply_fill(session, order, quote.ask)
        else:
            if quote.bid is None:
                return  # no bid: stay pending
            self.engine.apply_fill(session, order, quote.bid)

    def _check_limit(self, session, order: Order) -> None:
        try:
            quote = self.market_data.get_quote(order.symbol)
        except MarketDataError:
            return  # pending limit orders wait for the next successful check
        if order.side == "buy":
            if quote.ask is not None and quote.ask <= order.limit_price:
                self.engine.apply_fill(session, order, quote.ask)
        else:
            if quote.bid is not None and quote.bid >= order.limit_price:
                self.engine.apply_fill(session, order, quote.bid)
