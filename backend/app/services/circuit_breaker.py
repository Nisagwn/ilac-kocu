"""Devre kesici (circuit breaker): kapalı -> açık -> yarı açık.

Harici bir bağımlılık (LLM sağlayıcısı, openFDA...) art arda hata verdiğinde her isteğin zaman aşımını
beklemek yerine çağrıyı hemen kesip yedek yola (kural tabanlı koç, şablon özet, önbellek) düşmeyi sağlar.

Durumlar:
- closed    : çağrılar geçer; `window_seconds` içinde `failure_threshold` hata olursa -> open
- open      : çağrılar anında reddedilir (CircuitOpenError); `recovery_seconds` sonra -> half_open
- half_open : tek bir deneme çağrısına izin verilir (tüm kopyalar arasında tek); başarılıysa -> closed,
              hatalıysa -> open (süre yeniden başlar)

Durum Redis'te tutulur (API ve worker kopyaları paylaşır); Redis yoksa / ulaşılamıyorsa süreç içi yedek.
Ayarlar config'ten: CIRCUIT_BREAKER_FAILURE_THRESHOLD / _WINDOW_SECONDS / _RECOVERY_SECONDS / _ENABLED.

Kullanım:
    from app.services.circuit_breaker import CircuitOpenError, get_breaker

    breaker = get_breaker("openfda")
    try:
        data = breaker.call(fetch_label, name)          # ya da: with breaker.guard(): ...
    except CircuitOpenError:
        data = None                                      # yedek yol (önbellek / "şu an ulaşılamıyor")

`ignore` ile verilen istisnalar (ör. 404 "bulunamadı") hata sayılmaz.
Metrikler (observability): circuit_breaker_state{name} (0 kapalı, 1 yarı açık, 2 açık) ve
circuit_breaker_opened_total{name}.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
STATE_VALUES = {CLOSED: 0, HALF_OPEN: 1, OPEN: 2}

KEY_PREFIX = "cb:"


class CircuitOpenError(RuntimeError):
    """Devre açık: çağrı yapılmadı, yedek yol kullanılmalı."""

    def __init__(self, name: str):
        super().__init__(f"devre açık: {name}")
        self.name = name


def _metrics(name: str, state: str, opened: bool = False) -> None:
    try:
        from app.observability import record_breaker_state

        record_breaker_state(name, STATE_VALUES[state], opened)
    except Exception:  # metrik hatası çağrıyı asla bozmaz
        logger.debug("devre kesici metriği yazılamadı", exc_info=True)


class _LocalStore:
    """Süreç içi durum (Redis yokken)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = CLOSED
        self.opened_at = 0.0
        self.failures: deque[float] = deque()
        self.probe_until = 0.0


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int | None = None, window_seconds: float | None = None,
                 recovery_seconds: float | None = None, redis: Any = None, use_redis: bool = True,
                 clock: Callable[[], float] = time.time, ignore: tuple[type[BaseException], ...] = ()):
        s = get_settings()
        self.name = name
        self.failure_threshold = max(1, failure_threshold or s.circuit_breaker_failure_threshold)
        self.window_seconds = float(window_seconds or s.circuit_breaker_window_seconds)
        self.recovery_seconds = float(recovery_seconds or s.circuit_breaker_recovery_seconds)
        self._redis = redis
        self._use_redis = use_redis
        self.clock = clock
        self.ignore = ignore
        self._local = _LocalStore()

    # ------------------------------------------------------------ depolama
    def _r(self):
        if not self._use_redis:
            return None
        if self._redis is not None:
            return self._redis
        from app.redis_client import get_redis

        return get_redis()

    def _k(self, suffix: str) -> str:
        return f"{KEY_PREFIX}{self.name}:{suffix}"

    def _redis_op(self, fn: Callable[[Any], Any]) -> tuple[bool, Any]:
        r = self._r()
        if r is None:
            return False, None
        try:
            return True, fn(r)
        except Exception as exc:  # Redis geçici olarak yoksa süreç içi duruma düş
            logger.warning("Devre kesici durumu Redis'te okunamadı/yazılamadı (%s); süreç içi", type(exc).__name__)
            return False, None

    # ------------------------------------------------------------ durum
    @property
    def state(self) -> str:
        """Gözlemlenen durum (yan etkisiz): open süresi dolduysa half_open görünür."""
        ok, val = self._redis_op(lambda r: r.hmget(self._k("state"), "state", "opened_at"))
        if ok:
            state, opened_at = (val or [None, None])
            state, opened_at = state or CLOSED, float(opened_at or 0)
        else:
            state, opened_at = self._local.state, self._local.opened_at
        if state == OPEN and self.clock() - opened_at >= self.recovery_seconds:
            return HALF_OPEN
        return state

    def allow(self) -> bool:
        """Çağrı yapılabilir mi? Yarı açık durumda yalnızca bir deneme çağrısına True döner."""
        now = self.clock()
        ok, result = self._redis_op(lambda r: self._allow_redis(r, now))
        if ok:
            return result
        return self._allow_local(now)

    def _try_probe_redis(self, r) -> bool:
        # Deneme hakkı: SET NX; sonuç gelmezse (süreç öldü) recovery süresi sonunda kendiliğinden düşer
        return bool(r.set(self._k("probe"), uuid.uuid4().hex, nx=True, ex=max(1, int(self.recovery_seconds))))

    def _allow_redis(self, r, now: float) -> bool:
        state, opened_at = r.hmget(self._k("state"), "state", "opened_at")
        state = state or CLOSED
        if state == CLOSED:
            return True
        if state == OPEN:
            if now - float(opened_at or 0) < self.recovery_seconds:
                return False
            r.hset(self._k("state"), mapping={"state": HALF_OPEN})
            _metrics(self.name, HALF_OPEN)
        return self._try_probe_redis(r)

    def _allow_local(self, now: float) -> bool:
        st = self._local
        with st.lock:
            if st.state == CLOSED:
                return True
            if st.state == OPEN:
                if now - st.opened_at < self.recovery_seconds:
                    return False
                st.state = HALF_OPEN
                _metrics(self.name, HALF_OPEN)
            if st.probe_until > now:
                return False
            st.probe_until = now + self.recovery_seconds
            return True

    def record_success(self) -> None:
        ok, prev = self._redis_op(self._success_redis)
        if not ok:
            st = self._local
            with st.lock:
                prev = st.state
                if prev != CLOSED:  # kapalıyken başarı pencereyi sıfırlamaz ("60 sn'de 5 hata")
                    st.state, st.opened_at, st.probe_until = CLOSED, 0.0, 0.0
                    st.failures.clear()
        if prev and prev != CLOSED:
            logger.info("Devre kapandı: %s", self.name)
            _metrics(self.name, CLOSED)

    def _success_redis(self, r) -> str | None:
        prev = r.hget(self._k("state"), "state")
        if prev and prev != CLOSED:
            r.delete(self._k("state"), self._k("failures"), self._k("probe"))
        return prev

    def record_failure(self) -> None:
        now = self.clock()
        ok, opened = self._redis_op(lambda r: self._failure_redis(r, now))
        if not ok:
            opened = self._failure_local(now)
        if opened:
            logger.warning("Devre açıldı: %s (%s sn boyunca çağrı yapılmayacak)", self.name, int(self.recovery_seconds))
            _metrics(self.name, OPEN, opened=True)

    def _open_redis(self, r, now: float) -> None:
        r.hset(self._k("state"), mapping={"state": OPEN, "opened_at": repr(now)})
        r.delete(self._k("failures"), self._k("probe"))

    def _failure_redis(self, r, now: float) -> bool:
        state = r.hget(self._k("state"), "state") or CLOSED
        if state in (HALF_OPEN, OPEN):
            if state == HALF_OPEN:  # deneme çağrısı başarısız: yeniden aç
                self._open_redis(r, now)
                return True
            return False
        key = self._k("failures")
        pipe = r.pipeline()
        pipe.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zremrangebyscore(key, "-inf", now - self.window_seconds)
        pipe.zcard(key)
        pipe.expire(key, max(1, int(self.window_seconds) + 1))
        count = pipe.execute()[2]
        if count >= self.failure_threshold:
            self._open_redis(r, now)
            return True
        return False

    def _failure_local(self, now: float) -> bool:
        st = self._local
        with st.lock:
            if st.state == HALF_OPEN:
                st.state, st.opened_at, st.probe_until = OPEN, now, 0.0
                return True
            if st.state == OPEN:
                return False
            st.failures.append(now)
            while st.failures and st.failures[0] < now - self.window_seconds:
                st.failures.popleft()
            if len(st.failures) >= self.failure_threshold:
                st.state, st.opened_at = OPEN, now
                st.failures.clear()
                return True
            return False

    def reset(self) -> None:
        """Durumu sıfırlar (testler ve yönetim için)."""
        self._redis_op(lambda r: r.delete(self._k("state"), self._k("failures"), self._k("probe")))
        self._local = _LocalStore()

    # ------------------------------------------------------------ çağrı sarmalayıcıları
    @contextmanager
    def guard(self, ignore: tuple[type[BaseException], ...] | None = None) -> Iterator[None]:
        """Blok çalışmadan önce izin ister (açıksa CircuitOpenError); sonucu kaydeder.
        `ignore` (verilmezse kurucudaki) istisnaları hata sayılmaz: bağımlılık yanıt vermiştir."""
        if not self.allow():
            raise CircuitOpenError(self.name)
        try:
            yield
        except ignore if ignore is not None else self.ignore:
            self.record_success()
            raise
        except BaseException as exc:
            if isinstance(exc, CircuitOpenError | GeneratorExit | KeyboardInterrupt):
                raise
            self.record_failure()
            raise
        else:
            self.record_success()

    def call(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        with self.guard():
            return fn(*args, **kwargs)


class _DisabledBreaker(CircuitBreaker):
    """CIRCUIT_BREAKER_ENABLED=false: her çağrıya izin verir, durum tutmaz."""

    def allow(self) -> bool:
        return True

    def record_success(self) -> None:
        pass

    def record_failure(self) -> None:
        pass

    @property
    def state(self) -> str:
        return CLOSED


_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Ada göre paylaşılan devre kesici (aynı ad = aynı Redis durumu)."""
    with _registry_lock:
        b = _registry.get(name)
        if b is None:
            cls = CircuitBreaker if get_settings().circuit_breaker_enabled else _DisabledBreaker
            b = _registry[name] = cls(name, **kwargs)
        return b


def reset_breakers() -> None:
    """Testler için: tüm kayıtlı devre kesicileri unutur (süreç içi durum dahil)."""
    with _registry_lock:
        for b in _registry.values():
            b._local = _LocalStore()
        _registry.clear()


LLM_BREAKER = "llm"


def llm_breaker() -> CircuitBreaker:
    """Koç ajanı ve özet anlatısı aynı LLM sağlayıcısını paylaşır: tek devre."""
    return get_breaker(LLM_BREAKER)
