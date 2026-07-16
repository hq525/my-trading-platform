from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.assets import is_option_symbol, parse_occ
from app.engine.valuation import ny_date
from app.marketdata.base import MarketDataError
from app.models import Account, Order, Position
from app.timeutil import utcnow

log = logging.getLogger(__name__)


def settle_expired_options(session, *, engine, stock_market_data,
                           now: datetime | None = None) -> None:
    """Release dead orders, then cash-settle expired option positions.

    Paper accounts only (live settles at the broker; replay cannot hold
    options). NEVER goes through place_order: it would quote the dead
    contract, hit the expired-contract guard, and consume the settle: key on
    rejection — permanently poisoning settlement. Orders are constructed
    directly and filled via apply_fill with commission 0. Idempotent: a
    FILLED settle: order short-circuits re-runs; a quote failure skips the
    position and the next run (guard is expiry <= today) retries it.
    """
    now = now or utcnow()
    today = ny_date(now)
    accounts = session.scalars(
        select(Account).where(Account.mode == "paper")).all()
    for account in accounts:
        # 1) Release still-pending orders on dead contracts FIRST, so an
        #    open GTC sell can never block settling the position it covers,
        #    and dead buys release their reserved_cash.
        pending = session.scalars(select(Order).where(
            Order.account_id == account.id, Order.status == "pending")).all()
        for order in pending:
            if (is_option_symbol(order.symbol)
                    and parse_occ(order.symbol).expiry <= today):
                engine.expire_order(session, order)
                order.reject_reason = "contract expired"
        session.commit()

        # 2) Settle expired positions at intrinsic value of the underlying.
        positions = session.scalars(select(Position).where(
            Position.account_id == account.id)).all()
        for pos in positions:
            if pos.qty <= 0 or not is_option_symbol(pos.symbol):
                continue
            contract = parse_occ(pos.symbol)
            if contract.expiry > today:
                continue
            key = f"settle:{account.id}:{pos.symbol}"
            existing = session.scalar(select(Order).where(
                Order.account_id == account.id,
                Order.idempotency_key == key))
            if existing is not None:
                if existing.status != "filled":
                    log.error("settlement order %s in unexpected status %s; "
                              "skipping", key, existing.status)
                continue  # filled = already settled: the re-run no-op
            try:
                under = stock_market_data.get_quote(contract.underlying)
            except MarketDataError:
                log.warning("no quote for %s; retrying settlement of %s "
                            "next run", contract.underlying, pos.symbol)
                continue
            if contract.right == "call":
                intrinsic = max(Decimal("0"), under.price - contract.strike)
            else:
                intrinsic = max(Decimal("0"), contract.strike - under.price)
            order = Order(account_id=account.id, symbol=pos.symbol,
                          side="sell", order_type="market", tif="day",
                          qty=pos.qty, reserved_cash=Decimal("0"),
                          idempotency_key=key, placed_at=now)
            session.add(order)
            session.flush()
            engine.apply_fill(session, order, intrinsic,
                              commission=Decimal("0"))
            session.commit()  # per-position: nothing is ever half-settled
