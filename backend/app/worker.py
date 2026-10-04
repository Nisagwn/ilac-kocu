"""Kuyruk işçisi: `python -m app.worker`

- Redis "coach:jobs" listesinden koç işlerini alır (BRPOP, 5 sn zaman aşımı) ve her iş için kendi DB
  oturumuyla app.services.jobs.process_job'u çalıştırır.
- Doz hatırlatma zamanlayıcısını (APScheduler) başlatır; API süreci zamanlayıcıyı başlatmaz
  (RUN_SCHEDULER_IN_API=false), böylece API birden fazla worker ile koşsa da bildirimler tekrarlanmaz.
  Zamanlayıcı tek işçi sürecinde çalışmalıdır (birden fazla işçi süreci açılacaksa ikincisinde
  REMINDERS_ENABLED=false).
- SIGTERM/SIGINT ile düzgün kapanır: elindeki işi bitirir, en geç BRPOP zaman aşımı kadar sonra çıkar.
- Loglarda mesaj içeriği yer almaz (yalnızca iş kimliği, süre, sonuç).
"""

import logging
import signal
import threading
import time

from app.config import get_settings
from app.db import SessionLocal
from app.services import jobs
from app.services.reminders import start_scheduler

logger = logging.getLogger("app.worker")

RECOVERY_INTERVAL_SECONDS = 300


def _blocking_client(timeout: int):
    """BRPOP için ayrı istemci: ortak istemcinin 2 sn socket_timeout'u bloklayan komutu keserdi."""
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            url, socket_timeout=timeout + 5, socket_connect_timeout=2, decode_responses=True,
            health_check_interval=30,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis'e bağlanılamadı (%s)", type(exc).__name__)
        return None


def _recover(redis, session_factory=SessionLocal) -> None:
    db = session_factory()
    try:
        jobs.recover_stale_jobs(db, redis)
    except Exception:
        logger.exception("Takılı koç işleri kurtarılamadı")
        db.rollback()
    finally:
        db.close()


def _process(job_id: str, session_factory=SessionLocal) -> None:
    db = session_factory()
    try:
        jobs.process_job(db, job_id)
    except Exception:
        logger.exception("Koç işi %s işlenirken beklenmeyen hata", job_id)
        db.rollback()
    finally:
        db.close()


def run(stop: threading.Event, redis=None, timeout: int = 5, session_factory=SessionLocal) -> None:
    """İşçi döngüsü (testlerde sahte Redis ve stop olayı ile çağrılabilir)."""
    redis = redis if redis is not None else _blocking_client(timeout)
    if redis is None:
        logger.warning("REDIS_URL yok veya Redis'e ulaşılamıyor; koç kuyruğu işlenmeyecek (yalnızca zamanlayıcı)")
        stop.wait()
        return
    _recover(redis, session_factory)
    last_recovery = time.monotonic()
    while not stop.is_set():
        try:
            job_id = jobs.dequeue_job(redis, timeout=timeout)
        except Exception as exc:  # Redis geçici olarak yoksa bekle ve yeniden dene
            logger.warning("Kuyruk okunamadı (%s); yeniden denenecek", type(exc).__name__)
            stop.wait(timeout)
            continue
        if job_id:
            _process(job_id, session_factory)
        if time.monotonic() - last_recovery > RECOVERY_INTERVAL_SECONDS:
            _recover(redis, session_factory)
            last_recovery = time.monotonic()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    stop = threading.Event()

    def _handle(signum, _frame):
        logger.info("Sinyal alındı (%s), işçi kapanıyor", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    if settings.metrics_enabled and settings.worker_metrics_port:
        # LLM metrikleri bu süreçte oluşur; API'nin /metrics'i yalnız API sürecini gösterir.
        # observability içe aktarımı CoachMessage sayacını (after_insert) bu süreçte de kaydeder.
        from prometheus_client import start_http_server

        import app.observability  # noqa: F401

        start_http_server(settings.worker_metrics_port)
        logger.info("Worker metrikleri :%s/metrics adresinde", settings.worker_metrics_port)

    scheduler = start_scheduler(settings)
    logger.info("Koç işçisi başladı (zamanlayıcı: %s)", "açık" if scheduler else "kapalı")
    try:
        run(stop)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        logger.info("Koç işçisi durdu")


if __name__ == "__main__":
    main()
