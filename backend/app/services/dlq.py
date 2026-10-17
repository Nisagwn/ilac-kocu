"""Koç işleri için ölü mektup kuyruğu (DLQ): tüm denemeleri tükenen işler.

DLQ veritabanındadır (coach_jobs.dead_lettered_at dolu): kalıcıdır, Redis yeniden başlasa da kaybolmaz.
Gizlilik: listede mesaj içeriği gösterilmez; mesaj yalnızca yeniden deneme için DLQ_RETENTION_DAYS
boyunca tutulur, `purge` sonrası silinir (iş kaydı "failed" olarak kalır).

CLI (backend/ dizininde):
    python -m app.services.dlq list               # DLQ'daki işler (kimlik, hasta, deneme, hata türü, zaman)
    python -m app.services.dlq retry <iş-kimliği> # tek işi yeniden kuyruğa al
    python -m app.services.dlq retry-all
    python -m app.services.dlq purge              # saklama süresi dolanların mesajını sil, DLQ'dan çıkar
"""

from __future__ import annotations

import sys
from datetime import timedelta

from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CoachJob, utcnow

DEAD_LETTERED = Counter("coach_jobs_dead_lettered", "DLQ'ya düşen koç işleri")
DLQ_SIZE = Gauge("coach_dlq_size", "DLQ'daki koç işi sayısı")


def dlq_size(db: Session) -> int:
    n = db.scalar(select(func.count(CoachJob.id)).where(CoachJob.dead_lettered_at.is_not(None))) or 0
    DLQ_SIZE.set(n)
    return n


def list_dead(db: Session, limit: int = 100) -> list[CoachJob]:
    return list(db.scalars(select(CoachJob).where(CoachJob.dead_lettered_at.is_not(None))
                           .order_by(CoachJob.dead_lettered_at.desc()).limit(limit)))


def retry(db: Session, job_id: str, redis=None) -> bool:
    """İşi deneme sayacı sıfırlanmış olarak yeniden kuyruğa alır. Redis yoksa aynı süreçte işler.
    Mesajı silinmiş (purge edilmiş) ya da DLQ'da olmayan iş için False."""
    from app.services import jobs

    job = db.get(CoachJob, job_id)
    if job is None or job.dead_lettered_at is None or not job.message:
        return False
    job.status, job.attempts, job.error, job.finished_at = jobs.QUEUED, 0, None, None
    job.dead_lettered_at = None
    db.commit()
    if redis is None or not jobs.enqueue_job(redis, job_id):
        jobs.process_job(db, job_id)
    dlq_size(db)
    return True


def retry_all(db: Session, redis=None) -> int:
    return sum(retry(db, j.id, redis) for j in list_dead(db, limit=10_000))


def purge(db: Session, retention_days: int | None = None) -> int:
    """Saklama süresi dolan DLQ işlerinin mesajını siler ve DLQ'dan çıkarır (iş "failed" kalır)."""
    days = retention_days if retention_days is not None else get_settings().dlq_retention_days
    cutoff = utcnow() - timedelta(days=days)
    rows = db.scalars(select(CoachJob).where(CoachJob.dead_lettered_at.is_not(None),
                                             CoachJob.dead_lettered_at < cutoff)).all()
    for job in rows:
        job.message, job.dead_lettered_at = None, None
    db.commit()
    dlq_size(db)
    return len(rows)


def main(argv: list[str]) -> int:
    from app.db import SessionLocal
    from app.redis_client import get_redis

    cmd = argv[0] if argv else ""
    db = SessionLocal()
    try:
        if cmd == "list":
            rows = list_dead(db)
            for j in rows:
                print(f"{j.id}  hasta={j.patient_id}  deneme={j.attempts}  hata={j.last_error or '-'}  "
                      f"zaman={j.dead_lettered_at:%Y-%m-%d %H:%M}  mesaj={'var' if j.message else 'silinmiş'}")
            print(f"DLQ: {len(rows)} iş")
        elif cmd == "retry" and len(argv) == 2:
            ok = retry(db, argv[1], get_redis())
            print("Yeniden kuyruğa alındı." if ok else "İş DLQ'da yok ya da mesajı silinmiş.")
            return 0 if ok else 1
        elif cmd == "retry-all":
            print(f"{retry_all(db, get_redis())} iş yeniden kuyruğa alındı.")
        elif cmd == "purge":
            print(f"{purge(db)} işin mesajı silindi.")
        else:
            print(__doc__)
            return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
