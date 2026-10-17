"""Devre kesici (circuit breaker): art arda hata veren dış servise bir süre hiç istek atılmaz.

Durumlar:
    closed     normal; `window_seconds` içinde `failure_threshold` hata olursa -> open
    open       `open_seconds` boyunca istek atılmadan CircuitOpenError (çağıran hemen yedeğe düşer)
    half_open  süre dolunca TEK deneme isteğine izin verilir: başarılı -> closed, hatalı -> yeniden open

Durum Redis'te tutulur (`cb:<ad>:failures|open_until|probe`): API kopyaları ve işçi aynı devreyi görür,
biri devreyi açınca diğerleri de beklemez. Redis yoksa süreç içi durum. Saat olarak duvar saati
(time.time) kullanılır; kopyalar arası küçük kaymalar yalnızca açık kalma süresini birkaç ms oynatır.

Kullanım:
    breaker = get_breaker("llm")
    result = breaker.call(lambda: client.messages.create(...))   # açıkken CircuitOpenError
Yalnızca ağ/sunucu hataları sayılır: çağrı başarılı dönüp içeriği kullanılamazsa (ret, şema dışı) devre
etkilenmez, çünkü bu hatalar `call` dışında oluşur.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import TypeVar

from prometheus_client import Counter, Gauge

from app.config import get_settings
from app.redis_client import get_redis

log = logging.getLogger(__name__)
T = TypeVar("T")

STATE_VALUE = {"closed": 0, "open": 1, "half_open": 2}
BREAKER_STATE = Gauge("circuit_breaker_state", "Devre durumu (0 kapalı, 1 açık, 2 yarı açık)", ["name"])
BREAKER_REJECTED = Counter("circuit_breaker_rejected", "Devre açıkken reddedilen çağrılar", ["name"])
BREAKER_OPENED = Counter("circuit_breaker_opened", "Devrenin açılma sayısı", ["name"])


class CircuitOpenError(RuntimeError):
    """Devre açık: dış servise istek atılmadı."""

    def __init__(self, name: str, retry_after: float):
        super().__init__(f"{name} devresi açık ({retry_after:.0f} sn)")
        self.name = name
        self.retry_after = retry_after


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int = 5
    window_seconds: int = 60
    open_seconds: int = 30


class _LocalState:
    def __init__(self) -> None:
        self.failures: list[float] = []
        self.open_until: float | None = None
        self.probe_until: float | None = None


_local: dict[str, _LocalState] = {}
_local_lock = Lock()


class CircuitBreaker:
    def __init__(self, name: str, config: BreakerConfig, clock: Callable[[], float] = time.time):
        self.name = name
        self.config = config
        self.clock = clock

    # ------------------------------------------------------------ anahtarlar
    def _k(self, part: str) -> str:
        return f"cb:{self.name}:{part}"

    def _local_state(self) -> _LocalState:
        with _local_lock:
            return _local.setdefault(self.name, _LocalState())

    # ------------------------------------------------------------ durum
    def state(self) -> str:
        now = self.clock()
        r = get_redis()
        if r is not None:
            try:
                raw = r.get(self._k("open_until"))
                open_until = float(raw) if raw else None
                return self._label(open_until, now)
            except Exception:
                log.warning("Devre durumu Redis'ten okunamadı; süreç içi durum kullanılıyor", exc_info=True)
        return self._label(self._local_state().open_until, now)

    @staticmethod
    def _label(open_until: float | None, now: float) -> str:
        if open_until is None:
            return "closed"
        return "open" if now < open_until else "half_open"

    def _publish(self, state: str) -> None:
        BREAKER_STATE.labels(self.name).set(STATE_VALUE[state])

    def allow(self) -> None:
        """Çağrıya izin verilmiyorsa CircuitOpenError fırlatır. Yarı açık durumda tek deneme hakkı verir."""
        now = self.clock()
        r = get_redis()
        if r is not None:
            try:
                return self._allow_redis(r, now)
            except CircuitOpenError:
                raise
            except Exception:
                log.warning("Devre kesici Redis'e ulaşamadı; süreç içi durum kullanılıyor", exc_info=True)
        return self._allow_local(now)

    def _reject(self, retry_after: float) -> None:
        BREAKER_REJECTED.labels(self.name).inc()
        raise CircuitOpenError(self.name, max(retry_after, 0.0))

    def _allow_redis(self, r, now: float) -> None:
        raw = r.get(self._k("open_until"))
        if not raw:
            self._publish("closed")
            return
        open_until = float(raw)
        if now < open_until:
            self._publish("open")
            self._reject(open_until - now)
        # yarı açık: deneme hakkını yalnızca bir çağıran alır (SET NX); deneme takılırsa süre sonunda düşer
        if not r.set(self._k("probe"), "1", nx=True, ex=max(1, self.config.open_seconds)):
            self._publish("half_open")
            self._reject(1.0)
        self._publish("half_open")

    def _allow_local(self, now: float) -> None:
        st = self._local_state()
        with _local_lock:
            if st.open_until is None:
                self._publish("closed")
                return
            if now < st.open_until:
                self._publish("open")
                self._reject(st.open_until - now)
            if st.probe_until is not None and now < st.probe_until:
                self._publish("half_open")
                self._reject(1.0)
            st.probe_until = now + self.config.open_seconds
            self._publish("half_open")

    # ------------------------------------------------------------ sonuç kaydı
    def record_success(self) -> None:
        r = get_redis()
        if r is not None:
            try:
                r.delete(self._k("failures"), self._k("open_until"), self._k("probe"))
                self._publish("closed")
                return
            except Exception:
                log.warning("Devre kesici Redis'e yazamadı", exc_info=True)
        st = self._local_state()
        with _local_lock:
            st.failures.clear()
            st.open_until = st.probe_until = None
        self._publish("closed")

    def record_failure(self) -> None:
        now = self.clock()
        r = get_redis()
        if r is not None:
            try:
                return self._fail_redis(r, now)
            except Exception:
                log.warning("Devre kesici Redis'e yazamadı", exc_info=True)
        self._fail_local(now)

    def _open(self, now: float) -> float:
        BREAKER_OPENED.labels(self.name).inc()
        self._publish("open")
        log.warning("%s devresi açıldı (%s sn)", self.name, self.config.open_seconds)
        return now + self.config.open_seconds

    def _fail_redis(self, r, now: float) -> None:
        c = self.config
        if r.get(self._k("open_until")):  # yarı açık deneme başarısız: yeniden aç
            r.set(self._k("open_until"), str(self._open(now)), ex=c.open_seconds * 10)
            r.delete(self._k("probe"))
            return
        count = int(r.incr(self._k("failures")))
        if count == 1:  # pencere ilk hatayla başlar
            r.expire(self._k("failures"), c.window_seconds)
        if count >= c.failure_threshold:
            r.set(self._k("open_until"), str(self._open(now)), ex=c.open_seconds * 10)
            r.delete(self._k("failures"), self._k("probe"))

    def _fail_local(self, now: float) -> None:
        c = self.config
        st = self._local_state()
        with _local_lock:
            if st.open_until is not None:
                st.open_until, st.probe_until = self._open(now), None
                return
            st.failures = [t for t in st.failures if now - t < c.window_seconds] + [now]
            if len(st.failures) >= c.failure_threshold:
                st.open_until, st.failures = self._open(now), []

    # ------------------------------------------------------------ sarmalayıcı
    def call(self, fn: Callable[[], T]) -> T:
        self.allow()
        try:
            result = fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


def get_breaker(name: str) -> CircuitBreaker:
    """Ayarlardan yapılandırılmış devre: "llm" (LLM_BREAKER_*) ya da "openfda" (OPENFDA_BREAKER_*)."""
    s = get_settings()
    prefix = {"llm": "llm_breaker", "openfda": "openfda_breaker"}[name]
    return CircuitBreaker(name, BreakerConfig(
        failure_threshold=getattr(s, f"{prefix}_failure_threshold"),
        window_seconds=getattr(s, f"{prefix}_window_seconds"),
        open_seconds=getattr(s, f"{prefix}_open_seconds"),
    ))


def reset_local_breakers() -> None:
    """Testler için süreç içi devre durumlarını temizler."""
    with _local_lock:
        _local.clear()
