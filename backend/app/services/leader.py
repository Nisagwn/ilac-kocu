"""Worker lider seçimi: PostgreSQL oturum düzeyi advisory lock (`pg_try_advisory_lock`).

Birden fazla worker kopyası açıldığında zamanlayıcıyı (doz hatırlatmaları, haftalık özet, bakım işleri)
yalnızca kilidi tutan kopya çalıştırır. Kilit, bu amaçla havuzdan ayrılmış tek bir veritabanı bağlantısına
bağlıdır: süreç ölür ya da bağlantı koparsa PostgreSQL kilidi kendiliğinden bırakır ve diğer kopyalardan
biri periyodik denemede (LEADER_RETRY_SECONDS) devralır. Lider de her turda bağlantısının canlı olduğunu
doğrular; koptuysa liderliği bırakır (zamanlayıcıyı durdurur).

SQLite ve diğer veritabanlarında (testler, tek süreçli geliştirme) kilit no-op'tur: her zaman lider.
İkinci güvence: reminder_logs tekil kısıtı ve weekly_summaries (hasta, dönem) tekil kısıtı aynı işin iki kez
etkisini önler.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)


class LeaderLock:
    def __init__(self, engine: Engine, lock_id: int):
        self.engine = engine
        self.lock_id = int(lock_id)
        self._conn: Connection | None = None
        self._noop = engine.dialect.name != "postgresql"
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def try_acquire(self) -> bool:
        """Kilidi almayı dener (bloklamaz). Zaten tutuluyorsa True."""
        if self._noop:
            self._held = True
            return True
        if self._held:
            return self.check()
        try:
            conn = self.engine.connect()
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.lock_id}).scalar())
            conn.commit()  # oturum düzeyi kilit işlemden bağımsızdır; boşta işlem bırakma
        except Exception as exc:
            logger.warning("Lider kilidi denenemedi (%s)", type(exc).__name__)
            return False
        if got:
            self._conn, self._held = conn, True
            logger.info("Lider kilidi alındı (anahtar %s)", self.lock_id)
        else:
            conn.close()
        return got

    def check(self) -> bool:
        """Lider hâlâ kilidi tutuyor mu? Bağlantı koptuysa liderlik bırakılır."""
        if self._noop or not self._held:
            return self._held
        if self._conn is None or self._conn.invalidated or self._conn.closed:
            # Bağlantı yeniden kurulsa bile kilit yeni oturumda yoktur: liderlik bitti
            logger.warning("Lider bağlantısı geçersiz; liderlik bırakıldı")
            self._drop(invalidate=True)
            return False
        try:
            self._conn.execute(text("SELECT 1")).scalar()
            self._conn.commit()
            return True
        except Exception as exc:
            logger.warning("Lider bağlantısı koptu (%s); liderlik bırakıldı", type(exc).__name__)
            self._drop(invalidate=True)
            return False

    def release(self) -> None:
        if self._noop:
            self._held = False
            return
        failed = False
        if self._conn is not None and self._held:
            try:
                self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.lock_id})
                self._conn.commit()
                logger.info("Lider kilidi bırakıldı")
            except Exception as exc:  # bağlantı atılınca kilit sunucuda düşer
                logger.warning("Lider kilidi bırakılamadı (%s)", type(exc).__name__)
                failed = True
        self._drop(invalidate=failed)

    def _drop(self, invalidate: bool = False) -> None:
        """Bağlantıyı bırakır. Hatalı bağlantı havuza dönmez (invalidate): üzerindeki kilit havuzda kalmasın."""
        conn, self._conn, self._held = self._conn, None, False
        if conn is None:
            return
        try:
            if invalidate:
                conn.invalidate()
            conn.close()
        except Exception:
            logger.debug("lider bağlantısı kapatılamadı", exc_info=True)
