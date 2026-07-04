from __future__ import annotations

import importlib.util
import logging
import traceback
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.models import Account, StrategyRun, StrategyState
from app.strategy.base import Context, Strategy
from app.timeutil import utcnow

NY_TZ = ZoneInfo("America/New_York")

log = logging.getLogger(__name__)


class StrategyRunner:
    def __init__(self, strategies_dir: Path, session_factory, execution_for_symbol,
                 market_data_for_symbol, starting_cash: Decimal):
        self.strategies_dir = strategies_dir
        self.session_factory = session_factory
        self.execution_for_symbol = execution_for_symbol
        self.market_data_for_symbol = market_data_for_symbol
        self.starting_cash = starting_cash
        self.strategies: dict[str, type[Strategy]] = {}

    def discover(self) -> None:
        for path in sorted(self.strategies_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(
                f"user_strategies_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                log.exception("skipping strategy file %s: import failed", path.name)
                continue
            for obj in vars(module).values():
                if (isinstance(obj, type) and issubclass(obj, Strategy)
                        and obj is not Strategy):
                    self.strategies[obj.strategy_name()] = obj

    def sync_accounts(self) -> None:
        with self.session_factory() as session:
            for name in self.strategies:
                acct_name = f"strategy:{name}"
                if session.scalar(select(Account).where(
                        Account.name == acct_name)) is None:
                    session.add(Account(name=acct_name, kind="strategy",
                                        cash=self.starting_cash,
                                        starting_cash=self.starting_cash))
                if session.scalar(select(StrategyState).where(
                        StrategyState.name == name)) is None:
                    session.add(StrategyState(name=name, enabled=False))
            session.commit()

    def run_strategy(self, name: str) -> StrategyRun | None:
        cls = self.strategies[name]
        with self.session_factory() as session:
            state = session.scalar(select(StrategyState).where(
                StrategyState.name == name))
            if state is None or not state.enabled:
                return None
            account = session.scalar(select(Account).where(
                Account.name == f"strategy:{name}"))
            run = StrategyRun(strategy_name=name, started_at=utcnow())
            ctx = Context(session, account, self.execution_for_symbol,
                         self.market_data_for_symbol)
            try:
                cls().run(ctx)
                run.detail = f"orders placed: {len(ctx.placed)}"
            except Exception:
                session.rollback()  # discards uncommitted partial state only;
                # orders already placed were committed by Context and survive
                run.status = "error"
                run.detail = traceback.format_exc()[-2000:]
            run.finished_at = utcnow()
            session.add(run)
            session.commit()
            return run

    def register_jobs(self, scheduler) -> None:
        for name, cls in self.strategies.items():
            try:
                trigger = (CronTrigger(day_of_week="mon-fri", hour=16, minute=5,
                                       timezone=NY_TZ)
                           if cls.schedule == "daily_after_close"
                           else CronTrigger.from_crontab(cls.schedule, timezone=NY_TZ))
            except ValueError:
                log.exception("skipping strategy %s: invalid schedule %r", name, cls.schedule)
                continue
            scheduler.add_job(self.run_strategy, trigger, args=[name],
                              id=f"strategy:{name}", replace_existing=True)
