from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.engine.engine import TradingEngine
from app.engine.options_expiry import settle_expired_options
from app.models import Fill, Order, Position
from tests.factories import make_account
from tests.fakes import Clock, FakeMarketData

# Settlement runs after the close on 2026-07-01 (a Wednesday).
NOW = datetime(2026, 7, 1, 20, 5)  # 16:05 NY
ITM_CALL = "SPY260701C00600000"    # strike 600
ITM_PUT = "SPY260701P00650000"     # strike 650
OTM_CALL = "SPY260701C00650000"    # strike 650
LIVE_CONTRACT = "SPY260821C00600000"  # expires later


def setup(session, cash="10000", commission="0", spy_price="625"):
    md = FakeMarketData()
    if spy_price is not None:
        md.set_quote("SPY", spy_price)
    engine = TradingEngine(md, now_fn=Clock(NOW))
    account = make_account(session, cash=cash, commission=commission)
    return md, engine, account


def add_position(session, account, symbol, qty="2", avg_cost="5"):
    pos = Position(account_id=account.id, symbol=symbol, qty=Decimal(qty),
                   avg_cost=Decimal(avg_cost), realized_pnl=Decimal("0"))
    session.add(pos)
    session.flush()
    return pos


def settle(session, engine, md):
    settle_expired_options(session, engine=engine, stock_market_data=md, now=NOW)


def test_itm_call_settles_at_intrinsic(session):
    md, engine, account = setup(session)
    pos = add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000") + 25 * 2 * 100  # intrinsic 625-600
    order = session.scalar(select(Order).where(
        Order.idempotency_key == f"settle:{account.id}:{ITM_CALL}"))
    assert order.status == "filled" and order.side == "sell"
    fill = session.scalar(select(Fill).where(Fill.order_id == order.id))
    assert fill.price == Decimal("25")
    assert fill.commission == Decimal("0")
    assert fill.realized_pnl == Decimal("4000.0000")  # (25-5)*2*100


def test_itm_put_settles_at_intrinsic(session):
    md, engine, account = setup(session)
    add_position(session, account, ITM_PUT)
    settle(session, engine, md)
    assert account.cash == Decimal("10000") + 25 * 2 * 100  # 650-625


def test_otm_settles_at_zero_and_moves_no_cash(session):
    md, engine, account = setup(session, commission="1")
    pos = add_position(session, account, OTM_CALL)
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000")  # exactly zero cash movement
    fill = session.scalar(select(Fill).join(Order, Fill.order_id == Order.id)
                          .where(Order.symbol == OTM_CALL))
    assert fill.price == Decimal("0")
    assert fill.realized_pnl == Decimal("-1000.0000")  # (0-5)*2*100, no commission


def test_pending_sell_released_before_settlement(session):
    md, engine, account = setup(session)
    pos = add_position(session, account, ITM_CALL)
    gtc = Order(account_id=account.id, symbol=ITM_CALL, side="sell",
                order_type="limit", tif="gtc", qty=Decimal("2"),
                limit_price=Decimal("30"), placed_at=NOW)
    session.add(gtc)
    session.flush()
    settle(session, engine, md)
    session.refresh(gtc)
    assert gtc.status == "expired"
    assert gtc.reject_reason == "contract expired"
    assert pos.qty == 0  # settled in the same run despite the open sell


def test_pending_buy_releases_reserved_cash(session):
    md, engine, account = setup(session)
    dead_buy = Order(account_id=account.id, symbol=OTM_CALL, side="buy",
                     order_type="limit", tif="gtc", qty=Decimal("1"),
                     limit_price=Decimal("5"), reserved_cash=Decimal("500"),
                     placed_at=NOW)
    session.add(dead_buy)
    session.flush()
    settle(session, engine, md)
    session.refresh(dead_buy)
    assert dead_buy.status == "expired"
    assert engine.available_cash(session, account) == Decimal("10000")


def test_rerun_is_noop(session):
    md, engine, account = setup(session)
    add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    cash_after = account.cash
    settle(session, engine, md)
    assert account.cash == cash_after
    orders = session.scalars(select(Order).where(
        Order.symbol == ITM_CALL)).all()
    assert len(orders) == 1


def test_quote_failure_skips_then_later_run_settles(session):
    md, engine, account = setup(session, spy_price=None)  # no SPY quote yet
    pos = add_position(session, account, ITM_CALL)
    settle(session, engine, md)
    assert pos.qty == 2  # skipped, will retry
    md.set_quote("SPY", "625")
    settle(session, engine, md)
    assert pos.qty == 0
    assert account.cash == Decimal("10000") + Decimal("5000")


def test_unexpired_and_nonpaper_positions_untouched(session):
    md, engine, account = setup(session)
    live = make_account(session, name="live", mode="live")
    replay = make_account(session, name="replay:1:manual", mode="replay")
    add_position(session, account, LIVE_CONTRACT)      # not yet expired
    add_position(session, live, ITM_CALL)              # expired but live mode
    add_position(session, replay, ITM_CALL)            # expired but replay mode
    settle(session, engine, md)
    positions = session.scalars(select(Position)).all()
    assert all(p.qty == 2 for p in positions)
