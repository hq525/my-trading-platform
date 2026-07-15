from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select

from app.engine.engine import TradingEngine
from app.engine.valuation import account_equity
from app.models import Account, EquitySnapshot, Order, ReplayBar, ReplaySession
from app.replay.market_data import ReplayMarketData, virtual_now
from app.replay.service import session_lock
from app.strategy.base import Context


@dataclass
class StepResult:
    cursor_date: date
    fills: list[dict] = field(default_factory=list)
    expired: list[int] = field(default_factory=list)
    cancelled_at_exhaustion: list[int] = field(default_factory=list)
    strategy_errors: dict[str, str] = field(default_factory=dict)
    exhausted: bool = False


def step_session(db, deps, session_id: int, steps: int = 1) -> StepResult:
    with session_lock(session_id):
        row = db.get(ReplaySession, session_id)
        if row is None:
            raise ValueError(f"no such replay session: {session_id}")
        result = StepResult(cursor_date=row.cursor_date, exhausted=row.exhausted)
        for _ in range(steps):
            if row.cursor_date >= row.end_date:
                break
            _advance_one(db, deps, row, result)
        if row.cursor_date >= row.end_date:
            _cancel_all_pending(db, row, result)
            db.commit()
        result.cursor_date = row.cursor_date
        result.exhausted = row.exhausted
        return result


def _advance_one(db, deps, row: ReplaySession, result: StepResult) -> None:
    next_date = db.scalar(select(func.min(ReplayBar.date)).where(
        ReplayBar.session_id == row.id, ReplayBar.date > row.cursor_date))
    row.cursor_date = next_date
    engine = TradingEngine(ReplayMarketData(db, row),
                           now_fn=lambda: virtual_now(next_date))
    bars = {b.symbol: b for b in db.scalars(select(ReplayBar).where(
        ReplayBar.session_id == row.id, ReplayBar.date == next_date))}
    last_dates = dict(db.execute(
        select(ReplayBar.symbol, func.max(ReplayBar.date))
        .where(ReplayBar.session_id == row.id)
        .group_by(ReplayBar.symbol)).all())
    pending = db.scalars(select(Order).join(Account).where(
        Order.status == "pending",
        Account.replay_session_id == row.id)).all()
    db.flush()
    for order in pending:
        db.refresh(order)  # a concurrent cancel must win (SimAdapter guard)
        if order.status != "pending":
            continue
        bar = bars.get(order.symbol)
        if bar is not None:
            _try_fill(db, engine, order, bar, result)
        if order.status != "pending":
            continue
        if last_dates.get(order.symbol) and last_dates[order.symbol] < next_date:
            engine.expire_order(db, order)   # coverage ended for this symbol
            result.expired.append(order.id)
        elif order.tif == "day" and bar is not None:
            engine.expire_order(db, order)   # day = exactly one bar
            result.expired.append(order.id)
    _write_snapshots(db, row, next_date)
    db.commit()  # cursor + fills + expiries + snapshots land atomically
    _run_strategies(db, deps, row, result)


def _try_fill(db, engine: TradingEngine, order: Order, bar: ReplayBar,
              result: StepResult) -> None:
    if order.order_type == "market":
        price = bar.open
        if order.side == "buy":
            account = db.get(Account, order.account_id)
            cost = price * order.qty + account.commission
            spendable = (engine.available_cash(db, account)
                         + order.reserved_cash)
            if cost > spendable:
                engine.reject_order(
                    db, order,
                    f"insufficient cash at fill: need {cost}, "
                    f"available {spendable}")
                return
    else:
        price = None
        if order.side == "buy":
            if bar.open <= order.limit_price:
                price = bar.open          # gap-through: the better price
            elif bar.low <= order.limit_price:
                price = order.limit_price
        else:
            if bar.open >= order.limit_price:
                price = bar.open
            elif bar.high >= order.limit_price:
                price = order.limit_price
        if price is None:
            return
    fill = engine.apply_fill(db, order, price)
    result.fills.append({"order_id": order.id, "symbol": order.symbol,
                         "side": order.side, "qty": fill.qty,
                         "price": fill.price})


def _write_snapshots(db, row: ReplaySession, d: date) -> None:
    md = ReplayMarketData(db, row, strict=False)
    for account in db.scalars(select(Account).where(
            Account.replay_session_id == row.id)):
        equity = account_equity(db, account, lambda s: md)
        db.add(EquitySnapshot(account_id=account.id, date=d,
                              equity=equity, cash=account.cash))


def _run_strategies(db, deps, row: ReplaySession, result: StepResult) -> None:
    """After the atomic commit: strategy orders are placed against a durable
    cursor, so a crashed/re-entered step can never fill them against the bar
    whose close they saw."""
    if not row.strategies:
        return
    md = ReplayMarketData(db, row)
    for name in row.strategies:
        cls = deps.runner.strategies.get(name)
        if cls is None:
            result.strategy_errors[name] = (
                "strategy not found (removed since session creation?)")
            continue
        account = db.scalar(select(Account).where(
            Account.replay_session_id == row.id,
            Account.name == f"replay:{row.id}:strategy:{name}"))
        ctx = Context(db, account,
                      lambda symbol: deps.replay_execution,
                      lambda symbol: md)
        try:
            cls().run(ctx)
        except Exception:
            db.rollback()  # discard partial uncommitted state only
            result.strategy_errors[name] = traceback.format_exc()[-2000:]


def _cancel_all_pending(db, row: ReplaySession, result: StepResult) -> None:
    pending = db.scalars(select(Order).join(Account).where(
        Order.status == "pending",
        Account.replay_session_id == row.id)).all()
    for order in pending:
        order.status = "cancelled"
        result.cancelled_at_exhaustion.append(order.id)
