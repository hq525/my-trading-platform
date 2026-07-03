from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.engine.valuation import take_snapshots

log = logging.getLogger(__name__)
NY_TZ = ZoneInfo("America/New_York")


def run_process_pending(deps) -> None:
    with deps.session_factory() as session:
        deps.execution.process_pending(session)
        session.commit()


def run_snapshots(deps) -> None:
    with deps.session_factory() as session:
        take_snapshots(session, deps.market_data, deps.calendar)
        session.commit()


def build_scheduler(deps) -> BackgroundScheduler:
    # APScheduler logs and swallows job exceptions, so one bad run
    # never kills the scheduler (spec: error containment).
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_process_pending, "interval", minutes=2, args=[deps],
                      id="process_pending")
    scheduler.add_job(run_snapshots,
                      CronTrigger(day_of_week="mon-fri", hour=16, minute=10,
                                  timezone=NY_TZ),
                      args=[deps], id="snapshots")
    deps.runner.register_jobs(scheduler)
    return scheduler
