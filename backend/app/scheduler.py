"""Zamanlanmış işler (APScheduler). Yalnızca lider işçide başlatılır (app/worker.py, services/leader.py);
tek süreçli geliştirmede API'de de açılabilir (RUN_SCHEDULER_IN_API=true).

| İş | Zaman | Koşul |
|---|---|---|
| dose_reminders | her dakika | REMINDERS_ENABLED + VAPID anahtarları |
| weekly_summaries | WEEKLY_SUMMARY_CRON (Pazartesi 07:00) | WEEKLY_SUMMARY_ENABLED |
| idempotency_purge | saatte bir | her zaman (TTL'i dolan Idempotency-Key kayıtları) |
| dlq_purge | günde bir (03:30) | her zaman (DLQ'da saklama süresi dolan mesajlar) |

Testlerde (pytest) asla başlatılmaz.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import timedelta

from app.config import Settings

log = logging.getLogger(__name__)


def _with_db(name: str, fn: Callable) -> Callable[[], None]:
    def run() -> None:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            result = fn(db)
            if result:
                log.info("Zamanlanmış iş %s: %s", name, result)
        except Exception:
            log.exception("Zamanlanmış iş %s başarısız", name)
            db.rollback()
        finally:
            db.close()

    return run


def scheduler_jobs(settings: Settings) -> list[tuple[str, Callable[[], None], dict]]:
    """(iş kimliği, çağrılabilir, APScheduler tetik argümanları) listesi."""
    from apscheduler.triggers.cron import CronTrigger

    from app.services import dlq, idempotency, reminders, weekly_reports

    jobs: list[tuple[str, Callable[[], None], dict]] = []
    if settings.reminders_enabled and settings.vapid_configured:
        jobs.append(("dose_reminders", _with_db("dose_reminders", reminders.send_due_reminders),
                     {"trigger": "interval", "minutes": 1}))
    if settings.weekly_summary_enabled:
        jobs.append(("weekly_summaries", _with_db("weekly_summaries", weekly_reports.generate_weekly_summaries),
                     {"trigger": CronTrigger.from_crontab(settings.weekly_summary_cron, timezone=settings.timezone)}))
    ttl = timedelta(hours=settings.idempotency_ttl_hours)
    jobs.append(("idempotency_purge", _with_db("idempotency_purge", lambda db: idempotency.purge_expired(db, ttl)),
                 {"trigger": "interval", "hours": 1}))
    jobs.append(("dlq_purge", _with_db("dlq_purge", dlq.purge),
                 {"trigger": CronTrigger(hour=3, minute=30, timezone=settings.timezone)}))
    return jobs


def start_scheduler(settings: Settings):
    """Zamanlanmış işleri başlatır; testlerde ya da iş yoksa None döner."""
    if "pytest" in sys.modules:
        return None
    from apscheduler.schedulers.background import BackgroundScheduler

    jobs = scheduler_jobs(settings)
    if not jobs:
        return None
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    for job_id, fn, trigger in jobs:
        scheduler.add_job(fn, id=job_id, name=job_id, max_instances=1, coalesce=True, misfire_grace_time=3600,
                          **trigger)
    scheduler.start()
    log.info("Zamanlayıcı başladı: %s", ", ".join(j[0] for j in jobs))
    return scheduler
