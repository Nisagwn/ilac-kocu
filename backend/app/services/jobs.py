"""Asenkron koç işleri (Redis kuyruğu + işçi).

Akış:
    POST /coach/jobs -> submit_coach_message():
        hasta mesajı kaydedilir -> Katman 1 (girdi guardrail) SENKRON çalışır.
        Tetiklenirse (acil / kendine zarar / doz) yanıt hemen döner, iş oluşturulmaz.
        Tetiklenmezse CoachJob (queued) oluşturulur ve kimliği Redis "coach:jobs" listesine LPUSH edilir.
    app/worker.py -> dequeue_job() (BRPOP) -> process_job():
        iş atomik olarak sahiplenilir (queued -> running), provider aşaması (LLM + Katman 3 + kayıt + alert)
        çalışır ve iş aynı işlemde done olarak kapatılır.

Redis yoksa (testler, Docker'sız geliştirme) iş aynı süreçte FastAPI BackgroundTask ile işlenir;
istemci açısından davranış aynıdır (202 + yoklama).

Gizlilik: loglara mesaj içeriği yazılmaz; iş bitince `CoachJob.message` temizlenir.
"""

import logging
import time
import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import CoachJob, Patient, utcnow
from app.services.coach import (
    CoachProvider,
    CoachReply,
    RuleBasedCoach,
    get_coach_provider,
    run_input_stage,
    run_provider_stage,
    save_patient_message,
)

logger = logging.getLogger(__name__)

QUEUE_KEY = "coach:jobs"

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

FAILED_MESSAGE = "Koç yanıtı üretilemedi. Lütfen mesajınızı tekrar gönderin."


class CoachJobOut(BaseModel):
    """POST /coach/jobs ve GET /coach/jobs/{id} yanıtı. Girdi guardrail'i tetiklendiğinde job_id boştur."""

    job_id: str | None = None
    status: str
    reply: CoachReply | None = None
    error: str | None = None


def job_out(job: CoachJob) -> CoachJobOut:
    return CoachJobOut(
        job_id=job.id, status=job.status,
        reply=CoachReply.model_validate(job.reply) if job.reply else None, error=job.error,
    )


# --------- İş oluşturma ---------


def submit_coach_message(db: Session, patient: Patient, message: str) -> tuple[CoachReply | None, CoachJob | None]:
    """Hasta mesajını kaydeder ve girdi guardrail'ini çalıştırır.

    Dönüş: (reply, None) guardrail tetiklendiyse (yanıt kaydedildi), (None, job) iş kuyruğa alınacaksa."""
    save_patient_message(db, patient, message)
    reply = run_input_stage(db, patient, message)
    if reply is not None:
        db.commit()
        return reply, None
    job = CoachJob(
        id=str(uuid.uuid4()), patient_id=patient.id, message=message, status=QUEUED, attempts=0,
        created_at=utcnow(),
    )
    db.add(job)
    db.commit()
    return None, job


def get_patient_job(db: Session, patient: Patient, job_id: str) -> CoachJob | None:
    job = db.get(CoachJob, job_id)
    if job is None or job.patient_id != patient.id:
        return None
    return job


# --------- Kuyruk ---------


def enqueue_job(redis, job_id: str) -> bool:
    """İş kimliğini kuyruğa ekler. Redis hatasında False döner (çağıran süreç içi işlemeye düşer)."""
    try:
        redis.lpush(QUEUE_KEY, job_id)
        return True
    except Exception as exc:
        logger.warning("Koç işi kuyruğa eklenemedi (%s)", type(exc).__name__)
        return False


def dequeue_job(redis, timeout: int = 5) -> str | None:
    """Kuyruktan bir iş kimliği alır (BRPOP; LPUSH ile birlikte FIFO). Zaman aşımında None."""
    item = redis.brpop([QUEUE_KEY], timeout=timeout)
    if not item:
        return None
    _, job_id = item
    return job_id.decode() if isinstance(job_id, bytes) else str(job_id)


# --------- İşleme ---------


class _FallbackProvider:
    """LLM hattı tamamen çöktüğünde kullanıcıyı bekletmemek için kural tabanlı yanıt."""

    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply:
        return RuleBasedCoach().reply(db, patient, message).model_copy(update={"guardrail_action": "llm_fallback"})


def _claim(db: Session, job_id: str) -> bool:
    """queued -> running geçişini atomik yapar; birden fazla işçi aynı işi işleyemez."""
    res = db.execute(
        update(CoachJob)
        .where(CoachJob.id == job_id, CoachJob.status == QUEUED)
        .values(status=RUNNING, started_at=utcnow(), attempts=CoachJob.attempts + 1)
    )
    db.commit()
    return res.rowcount == 1


def _finish(db: Session, job_id: str, status: str, reply: CoachReply | None = None, error: str | None = None):
    job = db.get(CoachJob, job_id, populate_existing=True)
    if job is None:
        return None
    job.status = status
    job.reply = reply.model_dump(mode="json") if reply else None
    job.error = error
    job.finished_at = utcnow()
    job.message = None  # hasta mesajı coach_messages'ta; iş kaydında tutulmaz
    db.commit()
    return job


def process_job(db: Session, job_id: str, provider: CoachProvider | None = None) -> CoachJob | None:
    """Bir koç işini işler (işçi ve süreç içi yedek aynı fonksiyonu kullanır).

    Yalnızca queued durumdaki iş sahiplenilir; done/failed/running işler olduğu gibi döner (tekrar
    işlenmez). Provider hatası kural tabanlı yanıta düşer (done + guardrail_action llm_fallback);
    yalnızca yedek yanıt da üretilemezse iş failed olur."""
    if not _claim(db, job_id):
        return db.get(CoachJob, job_id)
    job = db.get(CoachJob, job_id, populate_existing=True)
    message, patient_id = job.message, job.patient_id
    patient = db.get(Patient, patient_id)
    if patient is None or not message:
        logger.warning("Koç işi %s işlenemedi: hasta veya mesaj yok", job_id)
        return _finish(db, job_id, FAILED, error=FAILED_MESSAGE)

    started = time.monotonic()
    try:
        reply = run_provider_stage(db, patient, message, provider or get_coach_provider(), commit=False)
    except Exception as exc:
        db.rollback()
        logger.warning("Koç işi %s provider aşamasında hata (%s); kural tabanlı yanıta düşülüyor",
                       job_id, type(exc).__name__)
        try:
            patient = db.get(Patient, patient_id)
            reply = run_provider_stage(db, patient, message, _FallbackProvider(), commit=False)
        except Exception as exc2:
            db.rollback()
            logger.error("Koç işi %s başarısız (%s)", job_id, type(exc2).__name__)
            return _finish(db, job_id, FAILED, error=FAILED_MESSAGE)
    # Koç yanıtı kaydı ile iş durumu aynı işlemde commit edilir (yarım kalmış iş olmaz)
    job = _finish(db, job_id, DONE, reply=reply)
    logger.info("Koç işi %s tamamlandı (%.1f sn, guardrail=%s)", job_id, time.monotonic() - started,
                reply.guardrail_action or "-")
    return job


def run_job_inline(bind, job_id: str, provider: CoachProvider | None = None) -> None:
    """Redis yokken BackgroundTask olarak çalışır; istekten bağımsız kendi oturumunu açar."""
    db = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)()
    try:
        process_job(db, job_id, provider)
    except Exception:
        logger.exception("Süreç içi koç işi %s başarısız", job_id)
        db.rollback()
    finally:
        db.close()


def recover_stale_jobs(db: Session, redis=None, now: datetime | None = None, settings: Settings | None = None) -> int:
    """İşçi açılışında: süresi aşan "running" işleri kurtarır, kuyruktan düşmüş "queued" işleri yeniden ekler.

    Deneme hakkı kalan iş yeniden kuyruğa alınır; hakkı biten iş kural tabanlı yanıtla kapatılır.
    Aynı işin kuyrukta iki kez bulunması zararsızdır (_claim yalnızca bir kez sahiplenir)."""
    s = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(minutes=s.coach_job_stale_minutes)
    stale = db.scalars(select(CoachJob).where(CoachJob.status == RUNNING, CoachJob.started_at < cutoff)).all()
    lost = db.scalars(select(CoachJob).where(CoachJob.status == QUEUED, CoachJob.created_at < cutoff)).all()
    recovered = 0
    for job in stale:
        job_id, exhausted = job.id, job.attempts >= s.coach_job_max_attempts
        job.status, job.started_at = QUEUED, None
        db.commit()
        if exhausted or redis is None or not enqueue_job(redis, job_id):
            process_job(db, job_id, _FallbackProvider() if exhausted else None)
        recovered += 1
    for job in lost:
        if redis is not None:
            enqueue_job(redis, job.id)
            recovered += 1
    if recovered:
        logger.info("%s koç işi kurtarıldı", recovered)
    return recovered
