from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from app.marketdata.base import Bar, MarketDataError, Quote, UnknownSymbolError
from app.models import ReplayBar


def virtual_now(d: date) -> datetime:
    """Replay's fixed daily timestamp (naive UTC). A convention only —
    replay never consults trading calendars."""
    return datetime(d.year, d.month, d.day, 21, 0)


class ReplayMarketData:
    """Quotes/bars from a session's frozen bars, never past the cursor.

    strict=True (placement): a symbol whose coverage has ended raises
    MarketDataError so the engine rejects new orders with the standard
    reason. strict=False (valuation, UI quote): always serves the latest
    close <= cursor, so positions in coverage-ended symbols stay valued.
    """

    name = "replay"

    def __init__(self, db, session_row, strict: bool = True):
        self._db = db
        self._session = session_row
        self.strict = strict

    def _latest_bar(self, symbol: str) -> ReplayBar | None:
        return self._db.scalar(
            select(ReplayBar)
            .where(ReplayBar.session_id == self._session.id,
                   ReplayBar.symbol == symbol,
                   ReplayBar.date <= self._session.cursor_date)
            .order_by(ReplayBar.date.desc())
            .limit(1))

    def get_quote(self, symbol: str) -> Quote:
        if symbol not in self._session.symbols:
            raise UnknownSymbolError(symbol)
        bar = self._latest_bar(symbol)
        if bar is None:
            raise UnknownSymbolError(symbol)
        if self.strict and bar.date < self._session.cursor_date:
            last = self._db.scalar(
                select(func.max(ReplayBar.date)).where(
                    ReplayBar.session_id == self._session.id,
                    ReplayBar.symbol == symbol))
            if last is not None and last < self._session.cursor_date:
                raise MarketDataError(f"no {symbol} data after {last}")
        return Quote(symbol=symbol, price=bar.close, as_of=virtual_now(bar.date))

    def get_bars(self, symbol: str, timeframe: str = "1D", limit: int = 200) -> list[Bar]:
        if timeframe != "1D":
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if symbol not in self._session.symbols:
            raise UnknownSymbolError(symbol)
        rows = self._db.scalars(
            select(ReplayBar)
            .where(ReplayBar.session_id == self._session.id,
                   ReplayBar.symbol == symbol,
                   ReplayBar.date <= self._session.cursor_date)
            .order_by(ReplayBar.date.desc())
            .limit(limit)).all()
        return [Bar(timestamp=datetime(r.date.year, r.date.month, r.date.day),
                    open=r.open, high=r.high, low=r.low, close=r.close,
                    volume=r.volume)
                for r in reversed(rows)]
