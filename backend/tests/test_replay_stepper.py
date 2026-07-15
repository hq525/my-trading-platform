from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import EquitySnapshot, Order
from app.replay.execution import ReplayExecution
from app.replay.stepper import step_session
from tests.factories import (make_replay_account, make_replay_bar,
                             make_replay_session)
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)

EXEC = ReplayExecution()


def build(db, bars, symbols=("SPY",), strategies=(), cash="100000"):
    """bars: {symbol: [(day, open, high, low, close), ...]}"""
    days = sorted({d for rows in bars.values() for d, *_ in rows})
    row = make_replay_session(db, symbols=symbols, strategies=strategies,
                              start=days[0], end=days[-1],
                              starting_cash=cash)
    for sym, rows in bars.items():
        for day, o, h, lo, c in rows:
            make_replay_bar(db, row.id, sym, day, open_=o, high=h, low=lo, close=c)
    acct = make_replay_account(db, row.id, cash=cash)
    return row, acct


def test_market_order_fills_at_next_bar_open(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "104", "106", "103", "105")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=10)
        db.commit()
        result = step_session(db, deps, row.id)
        assert result.cursor_date == date(2024, 6, 4)
        assert result.fills == [{"order_id": order.id, "symbol": "SPY",
                                 "side": "buy", "qty": Decimal("10"),
                                 "price": Decimal("104")}]
        db.refresh(acct)
        assert acct.cash == Decimal("100000") - Decimal("104") * 10


def test_market_buy_rejected_when_open_gaps_beyond_cash(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "150", "150", "150", "150")]}, cash="1000")
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=9)
        db.commit()
        step_session(db, deps, row.id)
        db.refresh(order)
        assert order.status == "rejected"
        assert order.reject_reason.startswith("insufficient cash at fill")


def test_limit_fills_gap_aware(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "95", "99", "94", "98"),
            ("2024-06-05", "97", "103", "96", "102")]})
        # gap-through: open 95 < limit 98 -> fills at the BETTER price (open)
        gap = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                               order_type="limit", qty=1,
                               limit_price=Decimal("98"))
        # touch: open 95 > limit 94.5? no -> low 94 <= 94.5 -> fills AT limit
        touch = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                                 order_type="limit", qty=1,
                                 limit_price=Decimal("94.5"))
        # no touch: low 94 > limit 90 -> stays pending
        miss = EXEC.place_order(db, account_id=acct.id, symbol="SPY", side="buy",
                                order_type="limit", qty=1,
                                limit_price=Decimal("90"), tif="gtc")
        db.commit()
        result = step_session(db, deps, row.id)
        prices = {f["order_id"]: f["price"] for f in result.fills}
        assert prices[gap.id] == Decimal("95")
        assert prices[touch.id] == Decimal("94.5")
        db.refresh(miss)
        assert miss.status == "pending"  # gtc persists


def test_sell_limit_gap_up_fills_at_open(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "115", "116", "114", "115")]})
        buy = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                               side="buy", order_type="market", qty=1)
        db.commit()
        step_session(db, deps, row.id)  # buy fills at 06-04 open 100
        sell = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                side="sell", order_type="limit", qty=1,
                                limit_price=Decimal("105"))
        db.commit()
        result = step_session(db, deps, row.id)
        assert result.fills[0]["price"] == Decimal("115")  # open, not limit


def test_day_order_lives_exactly_one_bar_and_skips_gap_days(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {
            "SPY": [("2024-06-07", "100", "100", "100", "100"),
                    ("2024-06-10", "100", "100", "99", "100")],
            "BTC-USD": [("2024-06-07", "1", "1", "1", "1"),
                        ("2024-06-08", "1", "1", "1", "1"),
                        ("2024-06-09", "1", "1", "1", "1"),
                        ("2024-06-10", "1", "1", "1", "1")]},
            symbols=("SPY", "BTC-USD"))
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("90"))  # never touches
        db.commit()
        r1 = step_session(db, deps, row.id)   # -> 06-08, crypto only
        assert r1.cursor_date == date(2024, 6, 8)
        db.refresh(order)
        assert order.status == "pending"      # SPY had no bar: order sleeps
        r2 = step_session(db, deps, row.id)   # -> 06-09, still crypto only
        db.refresh(order)
        assert order.status == "pending"
        r3 = step_session(db, deps, row.id)   # -> 06-10, SPY bar, no touch
        db.refresh(order)
        assert order.status == "expired"
        assert order.id in r3.expired


def test_coverage_end_expires_pending_orders(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {
            "SPY": [("2024-06-03", "100", "100", "100", "100"),
                    ("2024-06-04", "100", "100", "100", "100"),
                    ("2024-06-05", "100", "100", "100", "100")],
            "XYZ": [("2024-06-03", "50", "50", "50", "50")]},
            symbols=("SPY", "XYZ"))
        order = EXEC.place_order(db, account_id=acct.id, symbol="XYZ",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("40"), tif="gtc")
        db.commit()
        r = step_session(db, deps, row.id)    # -> 06-04; XYZ coverage over
        db.refresh(order)
        assert order.status == "expired"
        assert order.id in r.expired


def test_snapshots_written_per_step_with_virtual_dates(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "101"),
            ("2024-06-05", "100", "100", "100", "102")]})
        step_session(db, deps, row.id, steps=2)
        snaps = db.scalars(select(EquitySnapshot).where(
            EquitySnapshot.account_id == acct.id).order_by(
            EquitySnapshot.date)).all()
        assert [s.date for s in snaps] == [date(2024, 6, 4), date(2024, 6, 5)]
        assert all(s.equity == Decimal("100000") for s in snaps)  # no positions


def test_exhaustion_cancels_pending_and_resteps_are_noops(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="limit", qty=1,
                                 limit_price=Decimal("90"), tif="gtc")
        db.commit()
        r = step_session(db, deps, row.id)
        assert r.exhausted is True
        assert order.id in r.cancelled_at_exhaustion
        db.refresh(order)
        assert order.status == "cancelled"
        snaps_before = db.scalars(select(EquitySnapshot)).all()
        r2 = step_session(db, deps, row.id)   # no-op, no writes
        assert r2.exhausted is True and r2.fills == []
        assert db.scalars(select(EquitySnapshot)).all().__len__() == len(snaps_before)


def test_concurrent_steps_serialize_without_corruption(deps):
    import threading

    from app.models import ReplaySession
    with deps.session_factory() as db:
        row, _ = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "100", "100", "100", "100")]})
        db.commit()
        sid = row.id
    errors = []

    def one_step():
        try:
            with deps.session_factory() as db2:
                step_session(db2, deps, sid)
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=one_step) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with deps.session_factory() as db:
        assert db.get(ReplaySession, sid).cursor_date == date(2024, 6, 5)
        snaps = db.scalars(select(EquitySnapshot)).all()
        assert sorted(s.date for s in snaps) == [date(2024, 6, 4), date(2024, 6, 5)]


def test_cancelled_order_is_never_filled_by_a_step(deps):
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100"),
            ("2024-06-05", "100", "100", "100", "100")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=1)
        db.commit()
    # cancel through a different DB session, as a concurrent request would
    with deps.session_factory() as other:
        EXEC.cancel_order(other, order.id)
        other.commit()
    with deps.session_factory() as db:
        result = step_session(db, deps, row.id)
        assert result.fills == []
        assert db.get(Order, order.id).status == "cancelled"


def test_refresh_guard_mechanism_sees_committed_cancel(deps):
    """The step loop's per-order db.refresh must read through to a cancel
    committed by another session after the order was loaded — the exact
    mechanism the fill pass relies on to never fill a cancelled order."""
    with deps.session_factory() as db:
        row, acct = build(db, {"SPY": [
            ("2024-06-03", "100", "100", "100", "100"),
            ("2024-06-04", "100", "100", "100", "100")]})
        order = EXEC.place_order(db, account_id=acct.id, symbol="SPY",
                                 side="buy", order_type="market", qty=1)
        db.commit()
        loaded = db.get(Order, order.id)
        assert loaded.status == "pending"
        with deps.session_factory() as other:
            EXEC.cancel_order(other, order.id)
            other.commit()
        assert loaded.status == "pending"   # stale in-memory state: the hazard
        db.refresh(loaded)                  # the guard
        assert loaded.status == "cancelled"
