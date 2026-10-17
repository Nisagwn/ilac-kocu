"""İstek sınırı: token bucket (jeton kovası).

SÖZLEŞME:
    hit(key, limit, window_seconds, cost=1) -> RateLimitResult
        Kovadan `cost` jeton harcamayı dener; sonucu (izin, kalan, Retry-After) döndürür, istisna fırlatmaz.
    check_rate_limit(key: str, limit: int, window_seconds: int) -> None
        Uçlara özgü sabit pencere sınırını uygular (giriş/kayıt/koç/ilaç arama).

`hit` token bucket algoritması kullanır: kova `window_seconds` içinde tamamen dolar. Bu, API platformundaki
kullanıcı başına genel sınırlar içindir. Kimlik doğrulama gibi uçların kendi sınırları ise sabit penceredir;
pencere dolmadan token yenilenmesine izin verilmez.

Redis varsa kova Redis'te bir hash'tir ("rl:<key>" → tokens, ts) ve Lua betiğiyle atomik güncellenir
(saat olarak Redis TIME kullanılır: birden çok API sürecinde saat kayması sorun olmaz). Redis yoksa ya da
geçici olarak ulaşılamıyorsa süreç içi, kilitle korunan (thread-safe) eşdeğerine düşülür.
Anahtar örnekleri: "login:ip:<ip>", "coach:patient:<id>", "user:<id>:write".
"""

import math
import time
from dataclasses import dataclass
from threading import Lock

from fastapi import HTTPException, status

from app.redis_client import get_redis


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int  # isteğin ardından kovada kalan tam jeton
    retry_after: int  # izin verilmediyse bir sonraki jetona kadar saniye (>= 1); izinliyse 0

    def headers(self) -> dict[str, str]:
        h = {"X-RateLimit-Limit": str(self.limit), "X-RateLimit-Remaining": str(self.remaining)}
        if not self.allowed:
            h["Retry-After"] = str(self.retry_after)
        return h


# KEYS[1] = kova anahtarı; ARGV = kapasite, saniyedeki dolum, maliyet, TTL (ms)
# Dönüş: {izin (0/1), kalan jeton (metin), bekleme saniyesi (metin)} — Lua sayıları tamsayıya kırpılır,
# bu yüzden ondalıklar metin olarak döner.
_LUA_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  wait = (cost - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], ttl)
return {allowed, tostring(tokens), tostring(wait)}
"""

_local: dict[str, tuple[float, float, int]] = {}  # key -> (jeton, son güncelleme zamanı, pencere)
_fixed_windows: dict[str, tuple[int, float]] = {}  # key -> (sayaç, pencere bitişi)
_lock = Lock()
_LOCAL_MAX_KEYS = 50_000  # bu sayıyı aşınca dolmuş kovalar atılır (bellek sınırı)
_now = time.monotonic  # testlerde değiştirilebilir saat
_scripts: dict[int, object] = {}  # id(redis istemcisi) -> kayıtlı Lua betiği


def _result(allowed: bool, limit: int, tokens: float, wait: float) -> RateLimitResult:
    return RateLimitResult(
        allowed=allowed, limit=limit, remaining=max(0, int(math.floor(tokens))),
        retry_after=max(1, math.ceil(wait)) if not allowed else 0,
    )


def _prune_locked(now: float) -> None:
    # Dolu kova "hiç görülmemiş" anahtarla aynı davranır; bir pencereden uzun süredir dokunulmamış kova
    # kesinlikle dolmuştur, silmek davranışı değiştirmez.
    stale = [k for k, (_, ts, window) in _local.items() if now - ts > window]
    for k in stale:
        del _local[k]


def _hit_local(key: str, limit: int, window: int, cost: int) -> RateLimitResult:
    rate = limit / window
    with _lock:
        now = _now()
        tokens, ts, _ = _local.get(key, (float(limit), now, window))
        tokens = min(float(limit), tokens + max(0.0, now - ts) * rate)
        if tokens >= cost:
            tokens -= cost
            allowed, wait = True, 0.0
        else:
            allowed, wait = False, (cost - tokens) / rate
        _local[key] = (tokens, now, window)
        if len(_local) > _LOCAL_MAX_KEYS:
            _prune_locked(now)
    return _result(allowed, limit, tokens, wait)


def _hit_redis(r, key: str, limit: int, window: int, cost: int) -> RateLimitResult:
    script = _scripts.get(id(r))
    if script is None:
        script = r.register_script(_LUA_TOKEN_BUCKET)
        _scripts.clear()  # tek istemci beklenir; eski istemcinin betiği tutulmaz
        _scripts[id(r)] = script
    ttl_ms = int(window * 1000) + 1000  # kova bu sürede kendiliğinden dolar; sonra anahtar gereksizdir
    allowed, tokens, wait = script(keys=[f"rl:{key}"], args=[limit, limit / window, cost, ttl_ms])
    return _result(int(allowed) == 1, limit, float(tokens), float(wait))


def _hit_fixed_local(key: str, window: int) -> tuple[int, int]:
    now = _now()
    with _lock:
        count, reset_at = _fixed_windows.get(key, (0, now + window))
        if now >= reset_at:
            count, reset_at = 0, now + window
        count += 1
        _fixed_windows[key] = (count, reset_at)
        if len(_fixed_windows) > _LOCAL_MAX_KEYS:
            for stale_key, (_, expiry) in list(_fixed_windows.items()):
                if expiry <= now:
                    del _fixed_windows[stale_key]
    return count, max(1, math.ceil(reset_at - now))


def _hit_fixed_redis(r, key: str, window: int) -> tuple[int, int]:
    rkey = f"rl:fixed:{key}"
    pipe = r.pipeline()
    pipe.incr(rkey)
    pipe.ttl(rkey)
    count, ttl = pipe.execute()
    if ttl is None or ttl < 0:
        r.expire(rkey, window)
        ttl = window
    return int(count), max(1, int(ttl))


def hit(key: str, limit: int, window_seconds: int, cost: int = 1) -> RateLimitResult:
    if limit <= 0 or window_seconds <= 0:
        raise ValueError("limit ve window_seconds pozitif olmalı")
    r = get_redis()
    if r is not None:
        try:
            return _hit_redis(r, key, limit, window_seconds, cost)
        except Exception:  # Redis geçici olarak yoksa süreç içi kovaya düş
            pass
    return _hit_local(key, limit, window_seconds, cost)


TOO_MANY_REQUESTS_DETAIL = "Çok fazla istek gönderildi. Lütfen biraz sonra tekrar deneyin."


def check_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    if limit <= 0 or window_seconds <= 0:
        raise ValueError("limit ve window_seconds pozitif olmalı")
    r = get_redis()
    try:
        count, retry_after = _hit_fixed_redis(r, key, window_seconds) if r else _hit_fixed_local(key, window_seconds)
    except Exception:  # Redis geçici olarak yoksa süreç içi sayaca düş
        count, retry_after = _hit_fixed_local(key, window_seconds)
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_REQUESTS_DETAIL,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )


def reset_local_limits() -> None:
    """Testler için süreç içi kovaları temizler."""
    with _lock:
        _local.clear()
        _fixed_windows.clear()
    _scripts.clear()
