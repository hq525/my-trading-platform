from __future__ import annotations

from datetime import date, datetime

import exchange_calendars as xcals
import pandas as pd


class MarketCalendar:
    """NYSE trading hours via exchange_calendars. All datetimes naive UTC."""

    def __init__(self) -> None:
        self._cal = xcals.get_calendar("XNYS")

    @staticmethod
    def _ts(at: datetime) -> pd.Timestamp:
        return pd.Timestamp(at, tz="UTC")

    @staticmethod
    def _naive(ts: pd.Timestamp) -> datetime:
        return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()

    def is_open(self, at: datetime) -> bool:
        return bool(self._cal.is_open_on_minute(self._ts(at)))

    def is_trading_day(self, d: date) -> bool:
        return bool(self._cal.is_session(pd.Timestamp(d)))

    def next_open(self, after: datetime) -> datetime:
        return self._naive(self._cal.next_open(self._ts(after)))

    def expiry_time(self, placed_at: datetime) -> datetime:
        """Close of the session in which an order placed at placed_at is active.

        Placed while open -> that session's close. Placed while closed -> the
        close of the next session (where the order first becomes active).
        """
        ts = self._ts(placed_at)
        if self._cal.is_open_on_minute(ts):
            return self._naive(self._cal.next_close(ts))
        return self._naive(self._cal.next_close(self._cal.next_open(ts)))
