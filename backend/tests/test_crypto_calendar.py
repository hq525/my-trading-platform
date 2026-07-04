from datetime import datetime

from app.engine.crypto_calendar import CryptoCalendar


def test_always_open():
    cal = CryptoCalendar()
    assert cal.is_open(datetime(2026, 7, 4, 3, 0)) is True   # a Saturday
    assert cal.is_open(datetime(2026, 12, 25, 12, 0)) is True  # Christmas


def test_always_a_trading_day():
    cal = CryptoCalendar()
    assert cal.is_trading_day(datetime(2026, 7, 4).date()) is True


def test_next_open_returns_input_unchanged():
    cal = CryptoCalendar()
    now = datetime(2026, 7, 4, 15, 30)
    assert cal.next_open(now) == now


def test_expiry_is_next_utc_midnight():
    cal = CryptoCalendar()
    assert cal.expiry_time(datetime(2026, 7, 4, 15, 30)) == datetime(2026, 7, 5, 0, 0)


def test_expiry_at_exact_midnight_gives_full_day():
    cal = CryptoCalendar()
    assert cal.expiry_time(datetime(2026, 7, 4, 0, 0)) == datetime(2026, 7, 5, 0, 0)
