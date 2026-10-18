"""Arka plan zamanlayıcısı (APScheduler): yalnızca lider worker'da çalışır (app/services/leader.py).

İşler (saat dilimi settings.timezone, varsayılan Europe/Istanbul):
- dose_reminders      : her dakika doz hatırlatması (REMINDERS_ENABLED + VAPID anahtarları gerekir)
- weekly_summaries    : her pazartesi 07:00 haftalık özet üretimi + hekim bildirimi (WEEKLY_SUMMARY_*)
- idempotency_purge   : her gün 03:30 süresi dolan Idempotency-Key kayıtlarının temizliği

Her iş kendi veritabanı oturumunu açar; hata loglanır, zamanlayıcıyı durdurmaz.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobSpec:
    id: str
    func: Any
    trigger: str
    kwargs: dict = field(default_factory=dict)


def _with_session(name: str, fn) -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        fn(db)
    except Exception:
        log.exception("Zamanlanmış iş başarısız: %s", name)
        db.rollback()
    finally:
        db.close()


def run_dose_reminders() -> None:
    from app.services.reminders import send_due_reminders

    def job(db):
        n = send_due_reminders(db)
        if n:
            log.info("%s doz hatırlatması gönderildi", n)

    _with_session("dose_reminders", job)


def run_weekly_summaries() -> None:
    from app.services.weekly_summaries import generate_weekly_summaries

    _with_session("weekly_summaries", generate_weekly_summaries)


def run_idempotency_purge() -> None:
    from app.platform import purge_expired_idempotency_keys

    def job(db):
        n = purge_expired_idempotency_keys(db)
        if n:
            log.info("%s süresi dolmuş Idempotency-Key kaydı silindi", n)

    _with_session("idempotency_purge", job)


def scheduled_jobs(settings: Settings) -> list[JobSpec]:
    jobs: list[JobSpec] = []
    if settings.reminders_enabled and settings.vapid_configured:
        jobs.append(JobSpec("dose_reminders", run_dose_reminders, "interval", {"minutes": 1}))
    if settings.weekly_summary_enabled:
        jobs.append(JobSpec("weekly_summaries", run_weekly_summaries, "cron", {
            "day_of_week": settings.weekly_summary_day_of_week, "hour": settings.weekly_summary_hour,
            "minute": settings.weekly_summary_minute}))
    jobs.append(JobSpec("idempotency_purge", run_idempotency_purge, "cron", {"hour": 3, "minute": 30}))
    return jobs


def build_scheduler(settings: Settings, jobs: list[JobSpec] | None = None):
    """Başlatılmamış BackgroundScheduler (testler işleri buradan inceler)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    for spec in jobs if jobs is not None else scheduled_jobs(settings):
        # misfire_grace_time: lider değişiminde birkaç dakika gecikmiş haftalık iş yine de çalışsın
        scheduler.add_job(spec.func, spec.trigger, id=spec.id, max_instances=1, coalesce=True,
                          misfire_grace_time=3600, **spec.kwargs)
    return scheduler


def scheduler_should_start(settings: Settings) -> bool:
    if "pytest" in sys.modules:  # testlerde asla arka plan işi başlatma
        return False
    return bool(scheduled_jobs(settings))


def start_scheduler(settings: Settings):
    """Koşullar uygunsa zamanlayıcıyı başlatır; aksi halde None."""
    if not scheduler_should_start(settings):
        return None
    scheduler = build_scheduler(settings)
    scheduler.start()
    log.info("Zamanlayıcı başlatıldı: %s", ", ".join(j.id for j in scheduler.get_jobs()))
    return scheduler
