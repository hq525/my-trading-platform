from decimal import Decimal
from pathlib import Path

import pytest

from app.assets import is_crypto_symbol, is_option_symbol
from app.config import Settings
from app.engine.engine import TradingEngine
from app.engine.options_sim_adapter import OptionsSimAdapter
from app.engine.sim_adapter import SimAdapter
from app.jobs import build_scheduler, run_process_pending, run_snapshots
from app.main import AppDeps
from app.models import EquitySnapshot
from app.strategy.runner import StrategyRunner
from tests.factories import make_account
from tests.fakes import Clock, FakeCalendar, FakeMarketData, FakeOptionsData


@pytest.fixture
def deps(session_factory, tmp_path):
    md = FakeMarketData()
    md.set_quote("SPY", "100")
    cal = FakeCalendar(open_=True)
    engine = TradingEngine(md)
    execution = SimAdapter(engine, md, cal,
                           owns_order=lambda o: o.account.mode == "paper"
                           and not is_crypto_symbol(o.symbol)
                           and not is_option_symbol(o.symbol))

    crypto_md = FakeMarketData()
    crypto_md.set_quote("BTC-USD", "65000")
    crypto_cal = FakeCalendar(open_=True)
    crypto_engine = TradingEngine(crypto_md)
    crypto_execution = SimAdapter(crypto_engine, crypto_md, crypto_cal,
                                  owns_order=lambda o: o.account.mode == "paper"
                                  and is_crypto_symbol(o.symbol))

    options_md = FakeOptionsData()
    options_md.set_option_quote("SPY260821C00625000", bid="4.90", ask="5.10")
    options_cal = FakeCalendar(open_=False)
    options_clock = Clock()  # pinned 2026-07-01: contract never expires under test
    options_engine = TradingEngine(options_md, now_fn=options_clock)
    options_execution = OptionsSimAdapter(options_engine, options_md, options_cal,
                                          now_fn=options_clock,
                                          owns_order=lambda o: o.account.mode == "paper"
                                          and is_option_symbol(o.symbol))

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()

    def execution_for_symbol(symbol: str):
        if is_option_symbol(symbol):
            return options_execution
        return crypto_execution if is_crypto_symbol(symbol) else execution

    def market_data_for_symbol(symbol: str):
        if is_option_symbol(symbol):
            return options_md
        return crypto_md if is_crypto_symbol(symbol) else md

    runner = StrategyRunner(Path(strategies_dir), session_factory, execution_for_symbol,
                            market_data_for_symbol, Decimal("100000"))
    with session_factory() as s:
        make_account(s)
        s.commit()
    deps_obj = AppDeps(settings=Settings(), session_factory=session_factory,
                       market_data=md, calendar=cal, engine=engine,
                       execution=execution, runner=runner,
                       crypto_market_data=crypto_md, crypto_calendar=crypto_cal,
                       crypto_engine=crypto_engine, crypto_execution=crypto_execution,
                       options_market_data=options_md,
                       options_engine=options_engine,
                       options_execution=options_execution)
    deps_obj.options_calendar_for_test = options_cal
    return deps_obj


def test_run_process_pending_fills_queued_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.execution.place_order(
            s, account_id=acct.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    deps.calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_run_snapshots_writes_rows(deps):
    run_snapshots(deps)
    with deps.session_factory() as s:
        assert s.query(EquitySnapshot).count() == 1


def test_build_scheduler_registers_jobs(deps):
    sched = build_scheduler(deps)
    ids = {job.id for job in sched.get_jobs()}
    assert {"process_pending", "snapshots"} <= ids


def test_run_process_pending_fills_queued_crypto_order(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    deps.crypto_calendar.open = False
    with deps.session_factory() as s:
        acct = s.scalar(select(Account))
        order = deps.crypto_execution.place_order(
            s, account_id=acct.id, symbol="BTC-USD", side="buy",
            order_type="market", qty=Decimal("0.01"))
        s.commit()
        order_id = order.id
    deps.crypto_calendar.open = True
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_sim_adapters_never_touch_live_orders(deps):
    from sqlalchemy import select

    from app.models import Account, Order

    with deps.session_factory() as s:
        live = make_account(s, name="live", mode="live")
        # Pending live order created via the engine (as if submitted to the
        # broker); the sim adapters must not fill or expire it.
        order = deps.engine.place_order(
            s, account_id=live.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "pending"


def test_run_process_pending_mirrors_live_fill(deps):
    import httpx

    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.models import Order

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "b-7", "status": "accepted"})
        return httpx.Response(200, json={"status": "filled",
                                         "filled_avg_price": "101"})

    deps.live_execution = AlpacaLiveAdapter(
        deps.engine, "https://paper-api.test", "k", "s",
        transport=httpx.MockTransport(handler))
    with deps.session_factory() as s:
        live = make_account(s, name="live", mode="live")
        order = deps.live_execution.place_order(
            s, account_id=live.id, symbol="SPY", side="buy",
            order_type="market", qty=10)
        s.commit()
        order_id = order.id
    run_process_pending(deps)
    with deps.session_factory() as s:
        assert s.get(Order, order_id).status == "filled"


def test_run_live_sync_updates_cash(deps):
    import httpx
    from decimal import Decimal
    from sqlalchemy import select

    from app.engine.alpaca_live_adapter import AlpacaLiveAdapter
    from app.jobs import run_live_sync
    from app.models import Account

    def handler(request):
        if request.url.path == "/v2/account":
            return httpx.Response(200, json={"cash": "42000"})
        return httpx.Response(200, json=[])

    deps.live_execution = AlpacaLiveAdapter(
        deps.engine, "https://paper-api.test", "k", "s",
        transport=httpx.MockTransport(handler))
    with deps.session_factory() as s:
        make_account(s, name="live", mode="live")
        s.commit()
    run_live_sync(deps)
    with deps.session_factory() as s:
        live = s.scalar(select(Account).where(Account.mode == "live"))
        assert live.cash == Decimal("42000")


def test_build_scheduler_registers_live_sync_only_when_enabled(deps):
    ids = {j.id for j in build_scheduler(deps).get_jobs()}
    assert "live_sync" not in ids

    deps.live_execution = object()
    ids = {j.id for j in build_scheduler(deps).get_jobs()}
    assert "live_sync" in ids


def test_run_process_pending_fills_queued_option_order(deps, session_factory):
    from sqlalchemy import select

    from app.models import Account, Fill, Order

    with session_factory() as s:
        account = s.scalar(select(Account))
        order = deps.options_execution.place_order(
            s, account_id=account.id, symbol="SPY260821C00625000",
            side="buy", order_type="market", qty=Decimal("1"), tif="gtc")
        s.commit()
        assert order.status == "pending"  # options calendar starts closed
        order_id = order.id

    deps.options_calendar_for_test.open = True
    run_process_pending(deps)

    with session_factory() as s:
        order = s.get(Order, order_id)
        assert order.status == "filled"
        fill = s.scalar(select(Fill).where(Fill.order_id == order_id))
        assert fill.price == Decimal("5.10")  # fills at the ask


def test_scheduler_registers_option_expiry_before_snapshots(deps):
    scheduler = build_scheduler(deps)
    job = scheduler.get_job("option_expiry")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "16" and fields["minute"] == "5"

    snapshots_job = scheduler.get_job("snapshots")
    assert snapshots_job is not None
    snapshots_fields = {f.name: str(f) for f in snapshots_job.trigger.fields}
    assert snapshots_fields["hour"] == "16" and snapshots_fields["minute"] == "10"


def test_run_option_expiry_settles_expired_position(deps, session_factory):
    from sqlalchemy import select

    from app.jobs import run_option_expiry
    from app.models import Account, Position

    with session_factory() as s:
        account = s.scalar(select(Account))
        s.add(Position(account_id=account.id, symbol="SPY250620C00090000",
                       qty=Decimal("1"), avg_cost=Decimal("2"),
                       realized_pnl=Decimal("0")))
        cash_before = account.cash
        s.commit()

    run_option_expiry(deps)

    with session_factory() as s:
        account = s.scalar(select(Account))
        # SPY fake quote is 100, strike 90 -> intrinsic 10 * 1 * 100
        assert account.cash == cash_before + Decimal("1000")
