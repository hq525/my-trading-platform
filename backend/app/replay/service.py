from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select

from app.assets import is_crypto_symbol
from app.marketdata.base import Bar, MarketDataError, UnknownSymbolError
from app.models import (Account, EquitySnapshot, Fill, JournalNote, Order,
                        Position, ReplayBar, ReplaySession)
from app.timeutil import utcnow

STOCK_HISTORY_LIMIT = 520   # ~2 years of trading days (yfinance's own cap)
CRYPTO_HISTORY_LIMIT = 730  # 2 years of daily bars via Binance


@dataclass
class ReplaySources:
    """History fetchers for preload. The generic MarketDataService path is
    unsuitable here (Alpaca's window heuristic returns the OLDEST N bars),
    so replay fetches from these providers directly."""

    stock: object            # YFinanceData in production
    crypto_primary: object   # BinanceData
    crypto_fallback: object  # CoinbaseData (~300-day window)


class ReplayCreationError(Exception):
    pass


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def session_lock(session_id: int) -> threading.Lock:
    """Per-session lock serializing step and delete (single-process app)."""
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


def _fetch_history(sources: ReplaySources, symbol: str, today: date) -> list[Bar]:
    try:
        if is_crypto_symbol(symbol):
            try:
                bars = sources.crypto_primary.get_bars(symbol, "1D",
                                                       CRYPTO_HISTORY_LIMIT)
            except (MarketDataError, UnknownSymbolError):
                bars = sources.crypto_fallback.get_bars(symbol, "1D",
                                                        CRYPTO_HISTORY_LIMIT)
        else:
            bars = sources.stock.get_bars(symbol, "1D", STOCK_HISTORY_LIMIT)
    except UnknownSymbolError:
        raise ReplayCreationError(f"unknown symbol: {symbol}")
    except MarketDataError as e:
        raise ReplayCreationError(f"could not load history for {symbol}: {e}")
    bars = [b for b in bars if b.timestamp.date() < today]  # drop today's partial
    if not bars:
        raise ReplayCreationError(f"no history available for {symbol}")
    return bars


def create_session(db, sources: ReplaySources, *, symbols, start_date: date,
                   strategies, known_strategies, starting_cash,
                   name: str | None = None, today: date | None = None
                   ) -> ReplaySession:
    today = today or utcnow().date()
    if not symbols:
        raise ReplayCreationError("at least one symbol is required")
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if not symbols:
        raise ReplayCreationError("at least one symbol is required")
    unknown = [n for n in strategies if n not in known_strategies]
    if unknown:
        raise ReplayCreationError(f"unknown strategies: {', '.join(unknown)}")

    # All network I/O and validation BEFORE any DB write.
    history = {sym: _fetch_history(sources, sym, today) for sym in symbols}
    problems = [f"{sym} history starts {bars[0].timestamp.date()} "
                f"(through {bars[-1].timestamp.date()})"
                for sym, bars in history.items()
                if bars[0].timestamp.date() > start_date]
    if problems:
        raise ReplayCreationError(
            "insufficient coverage at start date: " + "; ".join(problems))
    all_dates = sorted({b.timestamp.date()
                        for bars in history.values() for b in bars})
    if start_date > all_dates[-1]:
        raise ReplayCreationError(
            f"start date is beyond available history (last bar {all_dates[-1]})")
    cursor = next(d for d in all_dates if d >= start_date)

    row = ReplaySession(
        name=name or f"{', '.join(symbols)} from {start_date}",
        symbols_json=json.dumps(symbols),
        strategies_json=json.dumps(list(strategies)),
        start_date=start_date, cursor_date=cursor, end_date=all_dates[-1],
        starting_cash=starting_cash)
    db.add(row)
    db.flush()
    db.add(Account(name=f"replay:{row.id}:manual", kind="manual", mode="replay",
                   cash=starting_cash, starting_cash=starting_cash,
                   replay_session_id=row.id))
    for sname in strategies:
        db.add(Account(name=f"replay:{row.id}:strategy:{sname}", kind="manual",
                       mode="replay", cash=starting_cash,
                       starting_cash=starting_cash, replay_session_id=row.id))
    for sym, bars in history.items():
        for b in bars:
            db.add(ReplayBar(session_id=row.id, symbol=sym,
                             date=b.timestamp.date(), open=b.open, high=b.high,
                             low=b.low, close=b.close, volume=b.volume))
    db.flush()
    return row


def delete_session(db, session_id: int) -> None:
    """One transaction; caller's session commit makes it atomic. Includes
    journal notes: SQLite here neither enforces FKs nor avoids rowid reuse,
    so an orphaned note would eventually reattach to an unrelated trade."""
    with session_lock(session_id):
        row = db.get(ReplaySession, session_id)
        if row is None:
            raise ValueError(f"no such replay session: {session_id}")
        account_ids = list(db.scalars(select(Account.id).where(
            Account.replay_session_id == session_id)))
        order_ids = list(db.scalars(select(Order.id).where(
            Order.account_id.in_(account_ids)))) if account_ids else []
        if order_ids:
            db.execute(delete(JournalNote).where(
                JournalNote.order_id.in_(order_ids)))
            db.execute(delete(Fill).where(Fill.order_id.in_(order_ids)))
            db.execute(delete(Order).where(Order.id.in_(order_ids)))
        if account_ids:
            db.execute(delete(Position).where(
                Position.account_id.in_(account_ids)))
            db.execute(delete(EquitySnapshot).where(
                EquitySnapshot.account_id.in_(account_ids)))
            db.execute(delete(Account).where(Account.id.in_(account_ids)))
        db.execute(delete(ReplayBar).where(ReplayBar.session_id == session_id))
        db.delete(row)
        db.flush()
