"""Parola hash'leme, erişim JWT'si, yenileme (refresh) token'ı ve JWT iptal listesi.

Oturum modeli (docs/M1/03-veri-gizliligi.md "Uygulanan iyileştirmeler"):
- Erişim token'ı kısa ömürlü JWT'dir (varsayılan 15 dk) ve benzersiz bir `jti` taşır.
- Yenileme token'ı opak rastgele bir dizedir; veritabanında yalnızca SHA-256 özeti tutulur.
- Çıkışta erişim token'ının `jti`'si kalan ömrü kadar iptal listesine eklenir
  (Redis "jwt:deny:<jti>"; Redis yoksa süreç içi sözlük).
"""

import hashlib
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from threading import Lock

import bcrypt
import jwt

from app.config import get_settings
from app.redis_client import get_redis

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# --- Erişim token'ı (JWT) ---


def create_access_token(user_id: int, role: str, expires_delta: timedelta | None = None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + (expires_delta if expires_delta is not None else timedelta(minutes=s.jwt_expire_minutes))
    payload = {
        "sub": str(user_id), "role": role, "typ": "access", "jti": uuid.uuid4().hex, "iat": now, "exp": exp,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]})


# --- Yenileme token'ı (opak) ---


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- JWT iptal listesi (çıkış yapılmış erişim token'ları) ---

_DENY_PREFIX = "jwt:deny:"
_local_deny: dict[str, float] = {}  # jti -> son geçerlilik (time.time())
_deny_lock = Lock()


def deny_jti(jti: str, exp_ts: float) -> None:
    """`jti`'yi token'ın kalan ömrü kadar iptal listesine ekler (süre dolunca kendiliğinden düşer)."""
    ttl = int(exp_ts - time.time()) + 1
    if ttl <= 0:
        return  # süresi zaten dolmuş; iptal gerekmez
    now = time.time()
    with _deny_lock:
        # Süreç içi kopya her zaman tutulur (Redis geçici olarak kesilirse de iptal geçerli kalsın)
        for k in [k for k, v in _local_deny.items() if v <= now]:
            del _local_deny[k]
        _local_deny[jti] = exp_ts
    r = get_redis()
    if r is not None:
        try:
            r.set(f"{_DENY_PREFIX}{jti}", "1", ex=ttl)
        except Exception as exc:
            logger.warning("JWT iptali Redis'e yazılamadı (%s); süreç içi liste kullanılıyor", type(exc).__name__)


def is_jti_denied(jti: str) -> bool:
    with _deny_lock:
        exp_ts = _local_deny.get(jti)
    if exp_ts is not None and exp_ts > time.time():
        return True
    r = get_redis()
    if r is not None:
        try:
            return bool(r.exists(f"{_DENY_PREFIX}{jti}"))
        except Exception as exc:
            logger.warning("JWT iptal listesi Redis'ten okunamadı (%s)", type(exc).__name__)
    return False


def reset_local_denylist() -> None:
    """Testler için süreç içi iptal listesini temizler."""
    with _deny_lock:
        _local_deny.clear()
