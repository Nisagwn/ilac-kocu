"""Lider işçi seçimi: PostgreSQL oturum düzeyi advisory lock.

Birden çok işçi süreci çalışabilir (koç kuyruğunu hepsi işler) ama zamanlanmış işler (doz hatırlatma,
haftalık özet, temizlik) yalnızca kilidi tutan TEK işçide çalışır; çift bildirim olmaz.
Kilit ayrı bir AUTOCOMMIT bağlantıda tutulur; süreç ölür ya da bağlantı koparsa PostgreSQL kilidi
kendiliğinden bırakır ve bekleyen işçilerden biri `leader_retry_seconds` içinde lider olur.
PostgreSQL dışındaki veritabanlarında (SQLite: testler, tek süreçli geliştirme) süreç her zaman liderdir.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

LOCK_KEY = 0x11AC_0C01  # uygulamaya özgü sabit 64-bit anahtar ("ilaç koçu")


class LeaderLock:
    def __init__(self, engine: Engine, key: int = LOCK_KEY):
        self.engine = engine
        self.key = key
        self._conn = None
        self.is_leader = False

    @property
    def _pg(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    def try_acquire(self) -> bool:
        if self.is_leader:
            return True
        if not self._pg:
            self.is_leader = True
            return True
        conn = None
        try:
            conn = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar())
        except Exception:
            log.warning("Liderlik kilidi denenemedi", exc_info=True)
            got = False
        if got:
            self._conn, self.is_leader = conn, True
            log.info("Bu işçi lider (zamanlanmış işler burada çalışır)")
        elif conn is not None:
            conn.close()
        return got

    def check(self) -> bool:
        """Liderliğin sürdüğünü doğrular; kilit bağlantısı koptuysa liderlik bırakılır."""
        if not self.is_leader or not self._pg:
            return self.is_leader
        try:
            self._conn.execute(text("SELECT 1"))
            return True
        except Exception:
            log.warning("Liderlik bağlantısı koptu; liderlik bırakıldı")
            self._drop()
            return False

    def release(self) -> None:
        if self._pg and self._conn is not None:
            try:
                self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
            except Exception:
                log.warning("Liderlik kilidi bırakılamadı (bağlantı kapanınca düşer)", exc_info=True)
        self._drop()

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn, self.is_leader = None, False
