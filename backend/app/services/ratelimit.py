"""İstek sınırları.

1) Sabit pencere sayacı (giriş/kayıt gibi hassas uçlar):
    check_rate_limit(key: str, limit: int, window_seconds: int) -> None
        Sınır aşıldıysa HTTP 429 (Retry-After başlığıyla) fırlatır.
   Anahtar örnekleri: "login:ip:<ip>", "coach:patient:<id>".

2) Token bucket (tüm API, app/middleware.py RateLimitMiddleware):
    take_token(key: str, capacity: int, refill_per_second: float) -> BucketResult
   Kova `capacity` jetonla dolu başlar, saniyede `refill_per_second` jeton dolar; her istek bir jeton harcar.
   Redis'te tek bir Lua betiğiyle atomik çalışır (saat Redis'in TIME'ından alınır: API kopyaları arasında
   saat kayması sonucu bozmaz). Redis yoksa süreç içi kova.

Redis varsa durum Redis'tedir (tüm süreçler ortak); yoksa süreç içi sözlük kullanılır.
"""

import math
import time
from dataclasses import dataclass
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


# ---------------------------------------------------------------- token bucket
@dataclass(frozen=True)
class BucketResult:
    allowed: bool
    limit: int
    remaining: int  # istekten sonra kalan tam jeton
    retry_after: int  # reddedildiyse bir jeton için beklenecek saniye (izin verildiyse 0)
    reset_after: int  # kovanın tamamen dolmasına kalan saniye


# KEYS[1]=kova; ARGV: kapasite, saniyede dolum. Dönüş: {izin(0/1), kalan jeton (metin; Lua sayıyı tamsayıya keser)}
TOKEN_BUCKET_LUA = """
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil or ts == nil then
  tokens = cap
  ts = now
end
tokens = math.min(cap, tokens + math.max(0, now - ts) * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 1000) + 1000)
return {allowed, tostring(tokens)}
"""

_buckets: dict[str, tuple[float, float]] = {}  # key -> (jeton, son güncelleme)
_script_cache: dict[int, object] = {}  # id(redis istemcisi) -> kayıtlı Lua betiği


def _take_local(key: str, capacity: int, rate: float) -> tuple[bool, float]:
    now = time.monotonic()
    with _lock:
        tokens, ts = _buckets.get(key, (float(capacity), now))
        tokens = min(float(capacity), tokens + max(0.0, now - ts) * rate)
        allowed = tokens >= 1
        if allowed:
            tokens -= 1
        _buckets[key] = (tokens, now)
        return allowed, tokens


def _take_redis(r, key: str, capacity: int, rate: float) -> tuple[bool, float]:
    script = _script_cache.get(id(r))
    if script is None:
        script = _script_cache[id(r)] = r.register_script(TOKEN_BUCKET_LUA)
    allowed, tokens = script(keys=[f"tb:{key}"], args=[capacity, rate])
    return bool(int(allowed)), float(tokens)


def take_token(key: str, capacity: int, refill_per_second: float) -> BucketResult:
    r = get_redis()
    try:
        allowed, tokens = (_take_redis(r, key, capacity, refill_per_second) if r
                           else _take_local(key, capacity, refill_per_second))
    except Exception:  # Redis geçici olarak yoksa süreç içi kovaya düş
        allowed, tokens = _take_local(key, capacity, refill_per_second)
    return BucketResult(
        allowed=allowed,
        limit=capacity,
        remaining=max(0, math.floor(tokens)),
        retry_after=0 if allowed else max(1, math.ceil((1 - tokens) / refill_per_second)),
        reset_after=max(0, math.ceil((capacity - tokens) / refill_per_second)),
    )


def reset_local_limits() -> None:
    """Testler için süreç içi sayaçları ve kovaları temizler."""
    with _lock:
        _local.clear()
        _buckets.clear()
