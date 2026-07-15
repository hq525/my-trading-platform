from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Order
from app.replay.stepper import step_session
from app.strategy.base import Strategy
from tests.factories import make_replay_account, make_replay_bar, make_replay_session
from tests.test_jobs import deps  # noqa: F401  (fixture reuse)


class BuyOneSpy(Strategy):
    def run(self, ctx):
        if not ctx.positions() and not ctx.orders(status="pending"):
            ctx.buy("SPY", qty=1)


class Exploder(Strategy):
    def run(self, ctx):
        raise RuntimeError("boom")


class TradesOutsideUniverse(Strategy):
    def run(self, ctx):
        ctx.get_quote("TSLA")


def build(db, strategies):
    row = make_replay_session(db, symbols=("SPY",), strategies=strategies,
                              start="2024-06-03", end="2024-06-06")
    for day, open_, close in (("2024-06-03", "99", "100"),
                              ("2024-06-04", "100", "101"),
                              ("2024-06-05", "102", "103"),
                              ("2024-06-06", "104", "105")):
        make_replay_bar(db, row.id, "SPY", day, open_=open_, close=close)
    make_replay_account(db, row.id)
    for name in strategies:
        make_replay_account(db, row.id, role=name)
    return row


def test_strategy_orders_fill_on_the_following_bar(deps):
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy}
    with deps.session_factory() as db:
        row = build(db, ("BuyOneSpy",))
        r1 = step_session(db, deps, row.id)   # -> 06-04; strategy places order
        assert r1.fills == []                 # nothing fills the step it's placed
        acct = db.scalar(select(Account).where(
            Account.name == f"replay:{row.id}:strategy:BuyOneSpy"))
        order = db.scalar(select(Order).where(Order.account_id == acct.id))
        assert order.status == "pending"
        assert order.placed_at == datetime(2024, 6, 4, 21, 0)
        r2 = step_session(db, deps, row.id)   # -> 06-05; fills at open 102
        assert r2.fills[0]["order_id"] == order.id
        assert r2.fills[0]["price"] == Decimal("102")


def test_strategy_errors_are_contained_per_strategy(deps):
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy, "Exploder": Exploder}
    with deps.session_factory() as db:
        row = build(db, ("BuyOneSpy", "Exploder"))
        r = step_session(db, deps, row.id)
        assert "boom" in r.strategy_errors["Exploder"]
        assert "BuyOneSpy" not in r.strategy_errors
        assert r.cursor_date == date(2024, 6, 4)  # step itself succeeded
        acct = db.scalar(select(Account).where(
            Account.name == f"replay:{row.id}:strategy:BuyOneSpy"))
        assert db.scalar(select(Order).where(Order.account_id == acct.id)) is not None


def test_missing_strategy_class_is_an_error_entry_not_a_500(deps):
    deps.runner.strategies = {}
    with deps.session_factory() as db:
        row = build(db, ("Ghost",))
        r = step_session(db, deps, row.id)
        assert "not found" in r.strategy_errors["Ghost"]


def test_out_of_universe_strategy_symbol_surfaces_as_error(deps):
    deps.runner.strategies = {"TradesOutsideUniverse": TradesOutsideUniverse}
    with deps.session_factory() as db:
        row = build(db, ("TradesOutsideUniverse",))
        r = step_session(db, deps, row.id)
        assert "TSLA" in r.strategy_errors["TradesOutsideUniverse"]


def test_global_enabled_toggle_is_ignored(deps):
    from app.models import StrategyState
    deps.runner.strategies = {"BuyOneSpy": BuyOneSpy}
    with deps.session_factory() as db:
        db.add(StrategyState(name="BuyOneSpy", enabled=False))
        row = build(db, ("BuyOneSpy",))
        step_session(db, deps, row.id)
        acct = db.scalar(select(Account).where(
            Account.name == f"replay:{row.id}:strategy:BuyOneSpy"))
        assert db.scalar(select(Order).where(
            Order.account_id == acct.id)) is not None  # ran despite disabled
