from __future__ import annotations

from datetime import date, datetime, timedelta


class CryptoCalendar:
    """Crypto markets never close. All datetimes naive UTC."""

    def is_open(self, at: datetime) -> bool:
        return True

    def is_trading_day(self, d: date) -> bool:
        return True

    def next_open(self, after: datetime) -> datetime:
        return after

    def expiry_time(self, placed_at: datetime) -> datetime:
        next_midnight = datetime(placed_at.year, placed_at.month, placed_at.day) \
            + timedelta(days=1)
        return next_midnight
