from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Order, Position


class Strategy:
    """Subclass in backend/strategies/*.py. The runner gives each strategy its
    own account; run() is called on `schedule` with a Context bound to it."""

    name: str | None = None
    schedule: str = "daily_after_close"  # or a 5-field cron string (NY time)

    def run(self, ctx: "Context") -> None:
        raise NotImplementedError

    @classmethod
    def strategy_name(cls) -> str:
        return cls.name or cls.__name__


class Context:
    """Exactly the capabilities a manual trader has via the UI — nothing more,
    so strategies stay portable to live trading. Routes to the stock or
    crypto pipeline per-call based on the traded symbol, so one strategy can
    trade both in the same run."""

    def __init__(self, session, account, execution_for_symbol, market_data_for_symbol):
        self._session = session
        self._account = account
        self._execution_for_symbol = execution_for_symbol
        self._market_data_for_symbol = market_data_for_symbol
        self.placed: list[int] = []

    def get_quote(self, symbol: str):
        return self._market_data_for_symbol(symbol).get_quote(symbol)

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200):
        return self._market_data_for_symbol(symbol).get_bars(symbol, timeframe, limit)

    @property
    def cash(self) -> Decimal:
        return self._account.cash

    def positions(self) -> list[Position]:
        all_positions = self._session.scalars(select(Position).where(
            Position.account_id == self._account.id))
        return [p for p in all_positions if p.qty > 0]

    def orders(self, status: str | None = None) -> list[Order]:
        stmt = select(Order).where(Order.account_id == self._account.id)
        if status is not None:
            stmt = stmt.where(Order.status == status)
        return list(self._session.scalars(stmt))

    def buy(self, symbol: str, qty: Decimal, limit_price: Decimal | None = None,
            tif: str = "day") -> Order:
        return self._place("buy", symbol, qty, limit_price, tif)

    def sell(self, symbol: str, qty: Decimal, limit_price: Decimal | None = None,
             tif: str = "day") -> Order:
        return self._place("sell", symbol, qty, limit_price, tif)

    def cancel(self, order_id: int) -> Order:
        order_row = self._session.get(Order, order_id)
        symbol = order_row.symbol if order_row is not None else ""
        order = self._execution_for_symbol(symbol).cancel_order(self._session, order_id)
        self._session.commit()
        return order

    def _place(self, side, symbol, qty, limit_price, tif) -> Order:
        order = self._execution_for_symbol(symbol).place_order(
            self._session, account_id=self._account.id, symbol=symbol,
            side=side, order_type="limit" if limit_price is not None else "market",
            qty=qty, tif=tif, limit_price=limit_price)
        self._session.commit()  # each order commits: survives a later crash
        self.placed.append(order.id)
        return order
