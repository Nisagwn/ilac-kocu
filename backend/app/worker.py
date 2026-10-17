"""Kuyruk işçisi: `python -m app.worker`

- Redis "coach:jobs" listesinden koç işlerini alır (BRPOP, 5 sn zaman aşımı) ve her iş için kendi DB
  oturumuyla app.services.jobs.process_job'u çalıştırır. Başarısız deneme üstel beklemeyle yeniden
  zamanlanır ("coach:jobs:delayed"); vadesi gelenler her döngüde kuyruğa taşınır. Denemeler tükenince DLQ.
- Zamanlanmış işler (app/scheduler.py: doz hatırlatma, haftalık özet, temizlik) yalnızca LİDER işçide
  çalışır (PostgreSQL advisory lock, services/leader.py). Birden çok işçi açılabilir; lider düşerse
  bekleyenlerden biri LEADER_RETRY_SECONDS içinde devralır. API zamanlayıcıyı başlatmaz.
- Düzgün kapanma (SIGTERM/SIGINT): yeni iş alınmaz, elindeki iş bitirilir, çalışan zamanlanmış iş
  beklenir, liderlik kilidi bırakılır. En geç BRPOP zaman aşımı + iş süresi kadar sürer (compose
  stop_grace_period: 30s). İkinci sinyal beklemeden çıkar.
- Loglarda mesaj içeriği yer almaz (yalnızca iş kimliği, süre, sonuç).
"""

import logging
import signal
import threading
import time

from app.config import get_settings
from app.db import SessionLocal, engine
from app.scheduler import start_scheduler
from app.services import dlq, jobs
from app.services.leader import LeaderLock

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
        dlq.dlq_size(db)  # coach_dlq_size metriği
    except Exception:
        logger.exception("Takılı koç işleri kurtarılamadı")
        db.rollback()
    finally:
        db.close()


def _process(job_id: str, redis=None, session_factory=SessionLocal) -> None:
    db = session_factory()
    try:
        job = jobs.process_job(db, job_id)
    except Exception as exc:
        logger.exception("Koç işi %s işlenirken beklenmeyen hata", job_id)
        db.rollback()
        try:
            job = jobs.record_attempt_failure(db, job_id, type(exc).__name__)
        except Exception:
            logger.exception("Koç işi %s için deneme kaydedilemedi (takılı iş kurtarmaya kalır)", job_id)
            job = None
    try:
        if job is not None and job.status == jobs.QUEUED and redis is not None:
            jobs.schedule_retry(redis, job_id, jobs.retry_delay(job.attempts))
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
            jobs.promote_due(redis)  # vadesi gelen yeniden denemeler kuyruğa
        except Exception as exc:
            logger.debug("Gecikmeli işler taşınamadı (%s)", type(exc).__name__)
        try:
            job_id = jobs.dequeue_job(redis, timeout=timeout)
        except Exception as exc:  # Redis geçici olarak yoksa bekle ve yeniden dene
            logger.warning("Kuyruk okunamadı (%s); yeniden denenecek", type(exc).__name__)
            stop.wait(timeout)
            continue
        if job_id:
            _process(job_id, redis, session_factory)
        if time.monotonic() - last_recovery > RECOVERY_INTERVAL_SECONDS:
            _recover(redis, session_factory)
            last_recovery = time.monotonic()


class Leadership(threading.Thread):
    """Liderliği dener; lider olunca zamanlayıcıyı başlatır, liderlik düşerse durdurur."""

    def __init__(self, stop: threading.Event, lock: LeaderLock | None = None, starter=start_scheduler,
                 interval: float | None = None):
        super().__init__(name="leadership", daemon=True)
        self.stop = stop
        self.lock = lock or LeaderLock(engine)
        self.starter = starter
        self.interval = interval if interval is not None else get_settings().leader_retry_seconds
        self.scheduler = None

    def tick(self) -> None:
        if self.lock.is_leader and not self.lock.check():
            self._stop_scheduler(wait=False)
        if not self.lock.is_leader and self.lock.try_acquire():
            self.scheduler = self.starter(get_settings())

    def run(self) -> None:
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(self.interval)

    def _stop_scheduler(self, wait: bool) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=wait)
            self.scheduler = None

    def shutdown(self) -> None:
        self._stop_scheduler(wait=True)  # çalışan zamanlanmış işin bitmesini bekle
        self.lock.release()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    stop = threading.Event()

    def _handle(signum, _frame):
        if stop.is_set():  # ikinci sinyal: beklemeden çık
            logger.warning("İkinci sinyal (%s): işçi hemen kapanıyor", signum)
            raise SystemExit(1)
        logger.info("Sinyal alındı (%s), işçi düzgün kapanıyor (elindeki iş bitirilecek)", signum)
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

    from app.tracing import setup_tracing

    setup_tracing(service_name="ilac-kocu-worker")  # OTEL_EXPORTER_OTLP_ENDPOINT yoksa kapalı
    leadership = Leadership(stop)
    leadership.tick()  # ilk deneme senkron: lider hemen zamanlayıcıyı başlatsın
    leadership.start()
    logger.info("Koç işçisi başladı (lider: %s)", "evet" if leadership.lock.is_leader else "hayır")
    try:
        run(stop)
    finally:
        stop.set()
        leadership.shutdown()
        logger.info("Koç işçisi durdu")


if __name__ == "__main__":
    main()
