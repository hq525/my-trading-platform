from __future__ import annotations

from app.engine.engine import TradingEngine
from app.models import Account, Order, ReplaySession
from app.replay.market_data import ReplayMarketData, virtual_now


class ReplayExecution:
    """Adapter-shaped dispatcher for replay accounts. Builds a per-call
    TradingEngine bound to the account's session bars and virtual clock —
    validation and reservation happen at the current bar's close, and
    nothing ever fills at placement (fills live in stepper.step_session)."""

    def _session_row(self, db, account_id: int) -> ReplaySession:
        account = db.get(Account, account_id)
        return db.get(ReplaySession, account.replay_session_id)

    def _engine(self, db, session_row: ReplaySession) -> TradingEngine:
        md = ReplayMarketData(db, session_row)
        return TradingEngine(md, now_fn=lambda: virtual_now(session_row.cursor_date))

    def place_order(self, db, *, account_id: int, **kwargs) -> Order:
        session_row = self._session_row(db, account_id)
        return self._engine(db, session_row).place_order(
            db, account_id=account_id, **kwargs)

    def cancel_order(self, db, order_id: int) -> Order:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        session_row = self._session_row(db, order.account_id)
        return self._engine(db, session_row).cancel_order(db, order_id)
