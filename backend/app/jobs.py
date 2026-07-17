from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.engine.options_expiry import settle_expired_options
from app.engine.valuation import take_snapshots

log = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")


def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        deps.crypto_execution.process_pending(session)
        if deps.options_execution is not None:
            deps.options_execution.process_pending(session)
        if deps.live_execution is not None:
            deps.live_execution.process_pending(session)
        session.commit()


def run_live_sync(deps) -> None:
    with deps.session_factory() as session:
        deps.live_execution.sync_account(session)
        session.commit()


def run_snapshots(deps) -> None:
    with deps.session_factory() as session:
        take_snapshots(session, deps.market_data_for_symbol)
        session.commit()


def run_option_expiry(deps) -> None:
    with deps.session_factory() as session:
        settle_expired_options(session, engine=deps.engine,
                               stock_market_data=deps.market_data)
        session.commit()


def build_scheduler(deps) -> BackgroundScheduler:
    # APScheduler logs and swallows job exceptions, so one bad run
    # never kills the scheduler (spec: error containment).
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_process_pending, "interval", minutes=2, args=[deps],
                      id="process_pending")
    scheduler.add_job(run_option_expiry,
                      CronTrigger(hour=16, minute=5, day_of_week="mon-fri",
                                  timezone=NY_TZ),
                      args=[deps], id="option_expiry")
    scheduler.add_job(run_snapshots,
                      CronTrigger(hour=16, minute=10, timezone=NY_TZ),
                      args=[deps], id="snapshots")
    if deps.live_execution is not None:
        scheduler.add_job(run_live_sync, "interval", minutes=10, args=[deps],
                          id="live_sync")
    deps.runner.register_jobs(scheduler)
    return scheduler
