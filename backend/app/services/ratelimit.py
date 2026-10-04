"""İstek sınırı (sabit pencere sayacı).

SÖZLEŞME:
    check_rate_limit(key: str, limit: int, window_seconds: int) -> None
        Sınır aşıldıysa HTTP 429 (Retry-After başlığıyla) fırlatır.

Redis varsa sayaç Redis'tedir (tüm süreçler ortak); yoksa süreç içi sözlük kullanılır.
Anahtar örnekleri: "login:ip:<ip>", "coach:patient:<id>".
"""

import time
from threading import Lock

from fastapi import HTTPException, status

from app.redis_client import get_redis

_local: dict[str, tuple[int, float]] = {}  # key -> (sayaç, pencere bitişi)
_lock = Lock()


def _hit_local(key: str, window: int) -> tuple[int, int]:
    now = time.monotonic()
    with _lock:
        count, reset_at = _local.get(key, (0, now + window))
        if now >= reset_at:
            count, reset_at = 0, now + window
        count += 1
        _local[key] = (count, reset_at)
        return count, max(1, int(reset_at - now))


def _hit_redis(r, key: str, window: int) -> tuple[int, int]:
    rkey = f"rl:{key}"
    pipe = r.pipeline()
    pipe.incr(rkey)
    pipe.ttl(rkey)
    count, ttl = pipe.execute()
    if ttl is None or ttl < 0:
        r.expire(rkey, window)
        ttl = window
    return int(count), max(1, int(ttl))


def check_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    r = get_redis()
    try:
        count, retry_after = _hit_redis(r, key, window_seconds) if r else _hit_local(key, window_seconds)
    except Exception:  # Redis geçici olarak yoksa süreç içi sayaca düş
        count, retry_after = _hit_local(key, window_seconds)
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Çok fazla istek gönderildi. Lütfen biraz sonra tekrar deneyin.",
            headers={"Retry-After": str(retry_after)},
        )


def reset_local_limits() -> None:
    """Testler için süreç içi sayaçları temizler."""
    with _lock:
        _local.clear()
