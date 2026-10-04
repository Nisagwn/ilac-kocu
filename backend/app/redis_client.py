"""Ortak Redis istemcisi.

SÖZLEŞME (kuyruk, istek sınırı, önbellek ve token iptali buna bağlıdır):
    get_redis() -> redis.Redis | None

REDIS_URL yoksa ya da Redis'e ulaşılamıyorsa None döner; çağıran taraf süreç içi bir yedeğe düşer
(testler ve Docker'sız geliştirme Redis olmadan çalışır). Bağlantı bir kez denenir ve sonuç önbelleğe alınır;
testlerde `reset_redis()` ile sıfırlanır.
"""

import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Any = None
_checked = False


def get_redis():
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
        client.ping()
        _client = client
    except Exception as exc:  # Redis yoksa uygulama çalışmaya devam eder
        logger.warning("Redis'e bağlanılamadı (%s); süreç içi yedek kullanılacak", type(exc).__name__)
        _client = None
    return _client


def reset_redis(client: Any = None) -> None:
    """Testler için: istemciyi sıfırlar ya da sahte bir istemci enjekte eder."""
    global _client, _checked
    _client = client
    _checked = client is not None
