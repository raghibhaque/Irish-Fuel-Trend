"""Background scheduled jobs (APScheduler).

Refresh cadence:
    news        every 10 min
    fx          daily at 16:30 UTC (after ECB publishes)
    brent       daily at 22:00 UTC (after markets close)
    bulletin    weekly, Monday 10:00 UTC (EU updates roughly weekly)

Started from the FastAPI lifespan on app boot.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.data_sources import brent_crude, eu_oil_bulletin, fx_rates, news_monitor

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _safe(fn, label: str):
    def wrapper():
        try:
            summary = fn()
            logger.info("[scheduled %s] ok: %s", label, summary)
        except Exception as exc:
            logger.exception("[scheduled %s] FAILED: %s", label, exc)
    return wrapper


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = BackgroundScheduler(timezone="UTC")

    sched.add_job(_safe(news_monitor.ingest, "news"),
                  IntervalTrigger(minutes=10),
                  id="news", replace_existing=True, max_instances=1)

    sched.add_job(_safe(fx_rates.ingest, "fx"),
                  CronTrigger(hour=16, minute=30),
                  id="fx", replace_existing=True, max_instances=1)

    sched.add_job(_safe(brent_crude.ingest, "brent"),
                  CronTrigger(hour=22, minute=0),
                  id="brent", replace_existing=True, max_instances=1)

    sched.add_job(_safe(eu_oil_bulletin.ingest, "bulletin"),
                  CronTrigger(day_of_week="mon", hour=10, minute=0),
                  id="bulletin", replace_existing=True, max_instances=1)

    sched.start()
    _scheduler = sched
    logger.info("Scheduler started with jobs: %s", [j.id for j in sched.get_jobs()])
    return sched


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
