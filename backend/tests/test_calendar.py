from datetime import date, datetime

import pytest

from app.engine.calendar import MarketCalendar


@pytest.fixture(scope="module")
def cal():
    return MarketCalendar()


def test_open_midday_wednesday(cal):
    assert cal.is_open(datetime(2026, 6, 24, 15, 0)) is True


def test_closed_after_hours(cal):
    assert cal.is_open(datetime(2026, 6, 24, 21, 0)) is False


def test_closed_weekend_and_holiday(cal):
    assert cal.is_open(datetime(2026, 6, 27, 15, 0)) is False  # Saturday
    assert cal.is_open(datetime(2026, 7, 3, 15, 0)) is False   # July 4 observed


def test_is_trading_day(cal):
    assert cal.is_trading_day(date(2026, 6, 24)) is True
    assert cal.is_trading_day(date(2026, 7, 3)) is False


def test_next_open_after_close(cal):
    assert cal.next_open(datetime(2026, 6, 24, 21, 0)) == datetime(2026, 6, 25, 13, 30)


def test_expiry_during_session_is_same_day_close(cal):
    assert cal.expiry_time(datetime(2026, 6, 24, 15, 0)) == datetime(2026, 6, 24, 20, 0)


def test_expiry_after_close_is_next_session_close(cal):
    assert cal.expiry_time(datetime(2026, 6, 24, 21, 30)) == datetime(2026, 6, 25, 20, 0)
