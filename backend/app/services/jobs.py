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

Yeniden deneme ve dead-letter queue (DLQ):
    Provider aşaması istisna fırlatırsa (ör. veritabanı hıçkırığı) iş en fazla `coach_job_max_attempts` (3)
    kez denenir; denemeler arası üstel bekleme (base, 2*base, ...). Redis varsa bekleme "coach:jobs:retry"
    sıralı kümesinde tutulur (işçi bloklanmaz, vadesi gelen iş kuyruğa geri alınır); yoksa süreç içinde beklenir.
    Hak biterse iş "coach:jobs:dlq" listesine eklenir, durumu `failed` olur ve hastaya yine de güvenli bir
    yedek yanıt yazılır (iş sessizce kaybolmaz). İnceleme / yeniden kuyruğa alma: `python -m app.scripts.dlq`.
    LLM sağlayıcı hataları zaten provider içinde kural tabanlı koça düşer (devre kesici ile); yeniden deneme
    bu nedenle LLM'e ikinci kez ücret ödetmez.

Gizlilik: loglara mesaj içeriği yazılmaz; iş bitince `CoachJob.message` temizlenir.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import CoachJob, Patient, RiskLevel, utcnow
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
RETRY_KEY = "coach:jobs:retry"  # sıralı küme: iş kimliği -> yeniden deneme zamanı (unix sn)
DLQ_KEY = "coach:jobs:dlq"  # liste: JSON {job_id, patient_id, attempts, error, failed_at}; mesaj içeriği YOK

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

FAILED_MESSAGE = "Koç yanıtı üretilemedi. Lütfen mesajınızı tekrar gönderin."
DLQ_ERROR = "Akıllı koç şu an yanıt üretemedi; güvenli bir yedek yanıt sohbet geçmişine eklendi."
SAFE_FALLBACK_MESSAGE = (
    "Şu an sana ayrıntılı yanıt veremiyorum, kısa süre sonra tekrar yazabilirsin. Bu arada ilaçlarını "
    "hekiminin önerdiği şekilde kullanmaya devam et. Kendini kötü hissedersen hekimine danış; göğüs ağrısı, "
    "nefes darlığı ya da bayılma gibi acil bir belirti olursa hemen 112'yi ara."
)


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


class _StaticSafeProvider:
    """Kural tabanlı koç da çalışamıyorsa (ör. veritabanı okunamıyor): veri okumayan sabit güvenli yanıt."""

    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply:
        return CoachReply(message=SAFE_FALLBACK_MESSAGE, risk_level=RiskLevel.low, guardrail_action="job_failed")


def backoff_seconds(attempt: int, settings: Settings | None = None) -> float:
    """attempt. denemeden sonraki bekleme: base * 2^(attempt-1)."""
    s = settings or get_settings()
    return max(0.0, s.coach_job_backoff_seconds) * (2 ** max(0, attempt - 1))


def _claim(db: Session, job_id: str) -> bool:
    """queued -> running geçişini atomik yapar; birden fazla işçi aynı işi işleyemez."""
    res = db.execute(
        update(CoachJob)
        .where(CoachJob.id == job_id, CoachJob.status == QUEUED)
        .values(status=RUNNING, started_at=utcnow(), attempts=CoachJob.attempts + 1)
    )
    db.commit()
    return res.rowcount == 1


def _finish(db: Session, job_id: str, status: str, reply: CoachReply | None = None, error: str | None = None,
            keep_message: bool = False):
    job = db.get(CoachJob, job_id, populate_existing=True)
    if job is None:
        return None
    job.status = status
    job.reply = reply.model_dump(mode="json") if reply else None
    job.error = error
    job.finished_at = utcnow()
    if not keep_message:
        job.message = None  # hasta mesajı coach_messages'ta; iş kaydında tutulmaz
    db.commit()
    return job


def _release(db: Session, job_id: str) -> None:
    """running -> queued (yeniden deneme için)."""
    db.execute(update(CoachJob).where(CoachJob.id == job_id).values(status=QUEUED, started_at=None))
    db.commit()


def _schedule_retry(redis, job_id: str, delay: float) -> bool:
    try:
        redis.zadd(RETRY_KEY, {job_id: time.time() + delay})
        return True
    except Exception as exc:
        logger.warning("Koç işi %s yeniden denemeye alınamadı (%s)", job_id, type(exc).__name__)
        return False


def promote_due_retries(redis, now: float | None = None, limit: int = 100) -> int:
    """Vadesi gelen yeniden denemeleri ana kuyruğa taşır. ZREM tek kopyada başarılı olur: iş bir kez eklenir."""
    now = time.time() if now is None else now
    moved = 0
    for job_id in redis.zrangebyscore(RETRY_KEY, "-inf", now, start=0, num=limit):
        if redis.zrem(RETRY_KEY, job_id) and enqueue_job(redis, job_id):
            moved += 1
    return moved


def _dead_letter(db: Session, job_id: str, redis=None, error: str = "unknown") -> CoachJob | None:
    """Deneme hakkı biten iş: hastaya güvenli yedek yanıt + failed + DLQ kaydı."""
    job = db.get(CoachJob, job_id, populate_existing=True)
    if job is None:
        return None
    message, attempts, patient_id = job.message or "", job.attempts, job.patient_id
    patient = db.get(Patient, patient_id)
    reply = None
    if patient is not None:
        for fallback in (_FallbackProvider(), _StaticSafeProvider()):
            try:
                reply = run_provider_stage(db, patient, message, fallback, commit=False)
                break
            except Exception as exc:
                db.rollback()
                patient = db.get(Patient, patient_id)
                logger.warning("Koç işi %s yedek yanıtı üretilemedi (%s)", job_id, type(exc).__name__)
    # Mesaj, DLQ'dan yeniden kuyruğa alınabilsin diye failed işte tutulur (bkz. app/scripts/dlq.py purge)
    job = _finish(db, job_id, FAILED, reply=reply, error=DLQ_ERROR if reply else FAILED_MESSAGE, keep_message=True)
    if redis is not None:
        entry = {"job_id": job_id, "patient_id": patient_id, "attempts": attempts, "error": error[:120],
                 "failed_at": utcnow().isoformat()}
        try:
            redis.lpush(DLQ_KEY, json.dumps(entry))
            _update_dlq_gauge(redis)
        except Exception as exc:
            logger.error("Koç işi %s DLQ'ya yazılamadı (%s); durum veritabanında failed", job_id, type(exc).__name__)
    logger.error("Koç işi %s %s denemeden sonra başarısız (%s); DLQ'ya alındı", job_id, attempts, error)
    return job


def process_job(db: Session, job_id: str, provider: CoachProvider | None = None, redis=None,
                settings: Settings | None = None, sleep=time.sleep) -> CoachJob | None:
    """Bir koç işini işler (işçi ve süreç içi yedek aynı fonksiyonu kullanır).

    Yalnızca queued durumdaki iş sahiplenilir; done/failed/running işler olduğu gibi döner (tekrar
    işlenmez). Provider hatası provider içinde/aşamada kural tabanlı yanıta düşer (done + llm_fallback).
    Aşamanın kendisi istisna fırlatırsa iş üstel beklemeyle yeniden denenir (redis verilmişse
    "coach:jobs:retry" üzerinden, yoksa süreç içinde); hak biterse DLQ + failed + güvenli yedek yanıt."""
    s = settings or get_settings()
    if not _claim(db, job_id):
        return db.get(CoachJob, job_id)
    job = db.get(CoachJob, job_id, populate_existing=True)
    message, patient_id, attempt = job.message, job.patient_id, job.attempts
    patient = db.get(Patient, patient_id)
    if patient is None or not message:
        logger.warning("Koç işi %s işlenemedi: hasta veya mesaj yok", job_id)
        return _finish(db, job_id, FAILED, error=FAILED_MESSAGE)

    started = time.monotonic()
    try:
        reply = run_provider_stage(db, patient, message, provider or get_coach_provider(), commit=False)
    except Exception as exc:
        db.rollback()
        err = type(exc).__name__
        if attempt >= s.coach_job_max_attempts:
            return _dead_letter(db, job_id, redis, err)
        delay = backoff_seconds(attempt, s)
        logger.warning("Koç işi %s deneme %s/%s başarısız (%s); %.1f sn sonra yeniden denenecek",
                       job_id, attempt, s.coach_job_max_attempts, err, delay)
        _record_retry()
        _release(db, job_id)
        if redis is not None and _schedule_retry(redis, job_id, delay):
            return db.get(CoachJob, job_id, populate_existing=True)
        sleep(delay)
        return process_job(db, job_id, provider, redis=None, settings=s, sleep=sleep)
    # Koç yanıtı kaydı ile iş durumu aynı işlemde commit edilir (yarım kalmış iş olmaz)
    job = _finish(db, job_id, DONE, reply=reply)
    logger.info("Koç işi %s tamamlandı (%.1f sn, guardrail=%s)", job_id, time.monotonic() - started,
                reply.guardrail_action or "-")
    return job


def _record_retry() -> None:
    try:
        from app.observability import record_job_retry

        record_job_retry()
    except Exception:
        logger.debug("yeniden deneme metriği yazılamadı", exc_info=True)


def _update_dlq_gauge(redis) -> int:
    n = int(redis.llen(DLQ_KEY) or 0)
    try:
        from app.observability import set_dlq_size

        set_dlq_size(n)
    except Exception:
        logger.debug("DLQ metriği yazılamadı", exc_info=True)
    return n


# --------- DLQ yönetimi (app/scripts/dlq.py) ---------


def dlq_size(redis) -> int:
    return _update_dlq_gauge(redis)


def dlq_entries(redis, limit: int = 50) -> list[dict]:
    """DLQ kayıtları (en yeni önce). Bozuk satırlar {"raw": ...} olarak döner."""
    out = []
    for raw in redis.lrange(DLQ_KEY, 0, max(0, limit - 1)):
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            out.append({"raw": raw})
    return out


def dlq_requeue(db: Session, redis, job_id: str | None = None) -> list[str]:
    """DLQ'daki işi (job_id None ise tümünü) yeniden kuyruğa alır; deneme sayacı sıfırlanır.
    Mesajı temizlenmiş işler atlanır. Yeniden kuyruğa alınan iş kimliklerini döner.
    Not: hasta zaten yedek yanıt aldı; iş başarılı olursa sohbete asıl koç yanıtı da eklenir."""
    requeued = []
    for raw in list(redis.lrange(DLQ_KEY, 0, -1)):
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        jid = entry.get("job_id")
        if job_id is not None and jid != job_id:
            continue
        job = db.get(CoachJob, jid, populate_existing=True) if jid else None
        if job is None or job.status != FAILED or not job.message:
            logger.info("DLQ kaydı %s yeniden kuyruğa alınamadı (iş yok / mesaj yok)", jid)
            continue
        job.status, job.attempts, job.error, job.started_at, job.finished_at = QUEUED, 0, None, None, None
        db.commit()
        redis.lrem(DLQ_KEY, 1, raw)
        if not enqueue_job(redis, jid):
            logger.warning("İş %s kuyruğa eklenemedi; işçi açılışında kurtarılacak", jid)
        requeued.append(jid)
    _update_dlq_gauge(redis)
    return requeued


def dlq_purge(db: Session, redis) -> int:
    """DLQ'yu boşaltır ve failed işlerde tutulan hasta mesajlarını temizler (gizlilik). Silinen kayıt sayısı."""
    n = int(redis.llen(DLQ_KEY) or 0)
    redis.delete(DLQ_KEY)
    db.execute(update(CoachJob).where(CoachJob.status == FAILED).values(message=None))
    db.commit()
    _update_dlq_gauge(redis)
    return n


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

    Deneme hakkı kalan iş yeniden kuyruğa alınır; hakkı biten iş DLQ'ya alınır (failed + güvenli yedek yanıt).
    Aynı işin kuyrukta iki kez bulunması zararsızdır (_claim yalnızca bir kez sahiplenir)."""
    s = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(minutes=s.coach_job_stale_minutes)
    stale = db.scalars(select(CoachJob).where(CoachJob.status == RUNNING, CoachJob.started_at < cutoff)).all()
    lost = db.scalars(select(CoachJob).where(CoachJob.status == QUEUED, CoachJob.created_at < cutoff)).all()
    recovered = 0
    for job in stale:
        job_id, exhausted = job.id, job.attempts >= s.coach_job_max_attempts
        if exhausted:  # işçi her denemede öldü: DLQ + güvenli yedek yanıt (kullanıcı askıda kalmaz)
            _dead_letter(db, job_id, redis, "stale")
            recovered += 1
            continue
        job.status, job.started_at = QUEUED, None
        db.commit()
        if redis is None or not enqueue_job(redis, job_id):
            process_job(db, job_id, settings=s)
        recovered += 1
    for job in lost:
        if redis is not None:
            enqueue_job(redis, job.id)
            recovered += 1
    if recovered:
        logger.info("%s koç işi kurtarıldı", recovered)
    return recovered
