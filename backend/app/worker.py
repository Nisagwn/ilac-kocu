"""Kuyruk işçisi: `python -m app.worker`

- Redis "coach:jobs" listesinden koç işlerini alır (BRPOP, 5 sn zaman aşımı) ve her iş için kendi DB
  oturumuyla app.services.jobs.process_job'u çalıştırır. Başarısız işler üstel beklemeyle yeniden denenir
  ("coach:jobs:retry"; vadesi gelenler her turda kuyruğa geri alınır), hak bitince DLQ'ya düşer.
- Zamanlayıcı (app/services/scheduler.py: doz hatırlatmaları, pazartesi haftalık özet, bakım işleri) yalnızca
  LİDER kopyada çalışır: PostgreSQL advisory lock (app/services/leader.py). Lider olmayan kopyalar kilidi
  LEADER_RETRY_SECONDS aralıkla dener; lider ölünce biri devralır. SQLite'ta her kopya liderdir (tek süreç).
  API süreci zamanlayıcıyı başlatmaz (RUN_SCHEDULER_IN_API=false).
- SIGTERM/SIGINT ile düzgün kapanır: yeni iş almayı bırakır, elindeki işi bitirir (en geç BRPOP zaman aşımı
  kadar sonra döngüden çıkar), zamanlayıcıyı süren işlerini bekleyerek durdurur ve lider kilidini bırakır.
- Loglarda mesaj içeriği yer almaz (yalnızca iş kimliği, süre, sonuç).
"""

import logging
import signal
import threading
import time

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.observability import setup_tracing
from app.services import jobs
from app.services.leader import LeaderLock
from app.services.scheduler import start_scheduler

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
        jobs.dlq_size(redis)  # DLQ boyutu metriğini tazele
    except Exception:
        logger.exception("Takılı koç işleri kurtarılamadı")
        db.rollback()
    finally:
        db.close()


def _process(job_id: str, session_factory=SessionLocal, redis=None) -> None:
    db = session_factory()
    try:
        jobs.process_job(db, job_id, redis=redis)
    except Exception:
        logger.exception("Koç işi %s işlenirken beklenmeyen hata", job_id)
        db.rollback()
    finally:
        db.close()


def run(stop: threading.Event, redis=None, timeout: int = 5, session_factory=SessionLocal) -> None:
    """İşçi döngüsü (testlerde sahte Redis ve stop olayı ile çağrılabilir).
    stop kurulunca yeni iş alınmaz; elde olan iş bitirilir."""
    redis = redis if redis is not None else _blocking_client(timeout)
    if redis is None:
        logger.warning("REDIS_URL yok veya Redis'e ulaşılamıyor; koç kuyruğu işlenmeyecek (yalnızca zamanlayıcı)")
        stop.wait()
        return
    _recover(redis, session_factory)
    last_recovery = time.monotonic()
    while not stop.is_set():
        try:
            jobs.promote_due_retries(redis)
        except Exception as exc:  # yeniden deneme kümesi okunamasa da ana kuyruk işlenmeye devam eder
            logger.warning("Yeniden deneme kuyruğu okunamadı (%s)", type(exc).__name__)
        try:
            job_id = jobs.dequeue_job(redis, timeout=timeout)
        except Exception as exc:  # Redis geçici olarak yoksa bekle ve yeniden dene
            logger.warning("Kuyruk okunamadı (%s); yeniden denenecek", type(exc).__name__)
            stop.wait(timeout)
            continue
        if job_id:
            _process(job_id, session_factory, redis)
        if time.monotonic() - last_recovery > RECOVERY_INTERVAL_SECONDS:
            _recover(redis, session_factory)
            last_recovery = time.monotonic()


class LeaderScheduler:
    """Lider kilidini periyodik dener; liderken zamanlayıcıyı çalıştırır, liderliği kaybedince durdurur."""

    def __init__(self, lock: LeaderLock, settings: Settings, scheduler_factory=start_scheduler):
        self.lock = lock
        self.settings = settings
        self.scheduler_factory = scheduler_factory
        self.scheduler = None

    def tick(self) -> bool:
        """Bir tur: lider mi? Gerekirse zamanlayıcıyı başlatır/durdurur. Lider ise True."""
        leader = self.lock.try_acquire()
        if leader and self.scheduler is None:
            self.scheduler = self.scheduler_factory(self.settings)
            logger.info("Bu kopya lider (zamanlayıcı: %s)", "açık" if self.scheduler else "iş yok")
        elif not leader and self.scheduler is not None:
            logger.warning("Liderlik kaybedildi; zamanlayıcı durduruluyor")
            self._stop_scheduler(wait=False)
        return leader

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Lider denetimi başarısız")
            stop.wait(max(1, self.settings.leader_retry_seconds))

    def _stop_scheduler(self, wait: bool) -> None:
        scheduler, self.scheduler = self.scheduler, None
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=wait)
            except Exception:
                logger.exception("Zamanlayıcı durdurulamadı")

    def shutdown(self) -> None:
        """Düzgün kapanış: zamanlayıcıyı süren işini bitirerek durdurur, sonra kilidi bırakır."""
        self._stop_scheduler(wait=True)
        self.lock.release()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    setup_tracing(service_name="ilac-kocu-worker")  # yalnızca OTEL_EXPORTER_OTLP_ENDPOINT tanımlıysa
    stop = threading.Event()

    def _handle(signum, _frame):
        logger.info("Sinyal alındı (%s): yeni iş alınmayacak, süren iş bitirilip kapanılacak", signum)
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

    from app.db import engine

    leader = LeaderScheduler(LeaderLock(engine, settings.leader_lock_id), settings)
    leader_thread = threading.Thread(target=leader.run, args=(stop,), name="leader", daemon=True)
    leader_thread.start()
    logger.info("Koç işçisi başladı")
    try:
        run(stop)
    finally:
        stop.set()
        leader_thread.join(timeout=5)
        leader.shutdown()
        logger.info("Koç işçisi durdu")


if __name__ == "__main__":
    main()
