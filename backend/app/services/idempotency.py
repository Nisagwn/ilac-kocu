"""Idempotency-Key deposu (yazma isteklerinin tekrarında aynı yanıt).

Akış (app/middleware.py IdempotencyMiddleware):
    begin()   -> yeni kayıt (işleniyor) | saklanan yanıt | anahtar başka istekle kullanılmış | hâlâ işleniyor
    complete()-> yanıt saklanır (5xx yanıtlar saklanmaz: kayıt silinir, istemci aynı anahtarla yeniden deneyebilir)
    purge_expired() -> TTL'i dolan kayıtlar silinir (kuyruk işçisinin zamanlayıcısı çağırır)

Anahtar kullanıcı başına tekildir (user_id, key); farklı kullanıcılar aynı anahtarı kullanabilir.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyKey

# Bu süreden uzun "işleniyor" kalan kayıt (süreç çöktü) terk edilmiş sayılır ve yeniden işlenir
STALE_IN_PROGRESS = timedelta(minutes=5)


def request_fingerprint(method: str, path: str, query: str, body: bytes) -> str:
    h = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), query.encode(), body):
        h.update(len(part).to_bytes(8, "big"))
        h.update(part)
    return h.hexdigest()


@dataclass
class BeginResult:
    outcome: Literal["new", "replay", "mismatch", "in_progress"]
    record_id: int | None = None
    status_code: int | None = None
    body: str | None = None
    headers: dict | None = None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite saat dilimi saklamaz


def begin(db: Session, user_id: int, key: str, method: str, path: str, fingerprint: str,
          ttl: timedelta) -> BeginResult:
    now = datetime.now(UTC)
    row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    if row is not None:
        age = now - _aware(row.created_at)
        expired = age > ttl or (row.status_code is None and age > STALE_IN_PROGRESS)
        if expired:
            db.delete(row)
            db.commit()
        elif row.request_hash != fingerprint:
            return BeginResult("mismatch")
        elif row.status_code is None:
            return BeginResult("in_progress")
        else:
            return BeginResult("replay", row.id, row.status_code, row.response_body, row.response_headers or {})

    row = IdempotencyKey(user_id=user_id, key=key, method=method.upper(), path=path[:255],
                         request_hash=fingerprint, created_at=now)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # aynı anahtarla eşzamanlı ikinci istek
        db.rollback()
        return BeginResult("in_progress")
    return BeginResult("new", row.id)


def complete(db: Session, record_id: int, status_code: int, body: str | None, headers: dict) -> None:
    row = db.get(IdempotencyKey, record_id)
    if row is None:
        return
    if status_code >= 500 or body is None:
        db.delete(row)
    else:
        row.status_code, row.response_body, row.response_headers = status_code, body, headers
    db.commit()


def abandon(db: Session, record_id: int) -> None:
    db.execute(delete(IdempotencyKey).where(IdempotencyKey.id == record_id))
    db.commit()


def purge_expired(db: Session, ttl: timedelta) -> int:
    cutoff = datetime.now(UTC) - ttl
    n = db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)).rowcount or 0
    db.commit()
    return n
