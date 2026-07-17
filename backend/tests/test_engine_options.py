from datetime import datetime
from decimal import Decimal

from app.engine.engine import TradingEngine
from tests.factories import make_account
from tests.fakes import Clock, FakeMarketData

OCC = "SPY260821C00625000"       # expires 2026-08-21
EXPIRED = "SPY260630C00625000"   # expired 2026-06-30
ZERO_DTE = "SPY260701C00625000"  # expires 2026-07-01 (Clock's default day)

# Clock default = 2026-07-01 12:00 UTC = 08:00 NY (before the close).
AFTER_CLOSE = datetime(2026, 7, 1, 20, 30)  # 16:30 NY


def setup(session, cash="100000", commission="0", now=None):
    md = FakeMarketData()
    md.set_option_quote(OCC, bid="4.90", ask="5.10")
    engine = TradingEngine(md, now_fn=Clock(now))
    account = make_account(session, cash=cash, commission=commission)
    return md, engine, account


def place_buy(engine, session, account, symbol=OCC, qty="2", order_type="market",
              limit_price=None):
    return engine.place_order(session, account_id=account.id, symbol=symbol,
                              side="buy", order_type=order_type,
                              qty=Decimal(qty), limit_price=limit_price)


def test_market_buy_reserves_at_ask_times_100(session):
    md, engine, account = setup(session, commission="1")
    order = place_buy(engine, session, account)
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("5.10") * 2 * 100 + 1  # 1021


def test_limit_buy_reserves_at_limit_times_100(session):
    md, engine, account = setup(session)
    order = place_buy(engine, session, account, qty="1", order_type="limit",
                      limit_price=Decimal("5"))
    assert order.reserved_cash == Decimal("500")


def test_insufficient_cash_check_uses_multiplier(session):
    md, engine, account = setup(session, cash="500")
    order = place_buy(engine, session, account, qty="1")  # ask 5.10 -> 510
    assert order.status == "rejected"
    assert "insufficient cash" in order.reject_reason


def test_fill_debits_cash_times_100_and_keeps_per_share_avg_cost(session):
    md, engine, account = setup(session, commission="1")
    order = place_buy(engine, session, account)
    engine.apply_fill(session, order, Decimal("5.10"))
    assert account.cash == Decimal("100000") - Decimal("1021")
    from sqlalchemy import select
    from app.models import Position
    pos = session.scalar(select(Position).where(Position.symbol == OCC))
    assert pos.qty == 2 and pos.avg_cost == Decimal("5.1000")


def test_sell_realized_pnl_times_100(session):
    md, engine, account = setup(session, commission="1")
    buy = place_buy(engine, session, account)
    engine.apply_fill(session, buy, Decimal("5.10"))
    sell = engine.place_order(session, account_id=account.id, symbol=OCC,
                              side="sell", order_type="market", qty=Decimal("2"))
    fill = engine.apply_fill(session, sell, Decimal("6"))
    # (6 - 5.10) * 2 * 100 - 1 commission
    assert fill.realized_pnl == Decimal("179.0000")
    assert account.cash == Decimal("100000") - Decimal("1021") + Decimal("1199")


def test_apply_fill_commission_override(session):
    md, engine, account = setup(session, commission="1")
    buy = place_buy(engine, session, account)
    engine.apply_fill(session, buy, Decimal("5.10"))
    sell = engine.place_order(session, account_id=account.id, symbol=OCC,
                              side="sell", order_type="market", qty=Decimal("2"))
    before = account.cash
    fill = engine.apply_fill(session, sell, Decimal("0"), commission=Decimal("0"))
    assert fill.commission == Decimal("0")
    assert account.cash == before  # $0 settlement moves cash by exactly $0


def test_fractional_contracts_rejected(session):
    md, engine, account = setup(session)
    order = place_buy(engine, session, account, qty="1.5")
    assert order.status == "rejected"
    assert order.reject_reason == "quantity must be a whole share count"


def test_expired_contract_rejected_before_quote_lookup(session):
    md, engine, account = setup(session)  # no quote set for EXPIRED
    order = place_buy(engine, session, account, symbol=EXPIRED, qty="1")
    assert order.status == "rejected"
    assert order.reject_reason == "contract expired"


def test_zero_dte_allowed_before_close(session):
    md, engine, account = setup(session)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = place_buy(engine, session, account, symbol=ZERO_DTE, qty="1")
    assert order.status == "pending"


def test_zero_dte_rejected_after_close(session):
    md, engine, account = setup(session, now=AFTER_CLOSE)
    md.set_option_quote(ZERO_DTE, bid="1.00", ask="1.10")
    order = place_buy(engine, session, account, symbol=ZERO_DTE, qty="1")
    assert order.status == "rejected"
    assert order.reject_reason == "contract expired"


def test_stock_orders_unchanged(session):
    md, engine, account = setup(session, commission="1")
    md.set_quote("SPY", "100")
    order = place_buy(engine, session, account, symbol="SPY", qty="5")
    assert order.status == "pending"
    assert order.reserved_cash == Decimal("501")  # multiplier 1
    engine.apply_fill(session, order, Decimal("100"))
    assert account.cash == Decimal("100000") - Decimal("501")
