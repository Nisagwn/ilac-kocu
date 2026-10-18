"""Sağlık uçları (sürümsüz; kimlik doğrulama gerektirmez, hasta verisi döndürmez).

- `/health` ve `/health/live`: süreç ayakta mı (bağımlılıklara bakmaz; liveness probe).
- `/health/ready`: trafik almaya hazır mı (readiness probe). Kontroller: veritabanı `SELECT 1`, Redis `PING`
  (yalnızca REDIS_URL tanımlıysa), veritabanındaki `alembic_version` = migration head(ler)i. Biri düşerse 503 ve
  `checks` içinde hangi kontrolün başarısız olduğu döner. Hata ayrıntısı olarak yalnızca istisna türü yazılır.
"""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.deps import DB
from app.redis_client import get_redis

router = APIRouter(tags=["system"])

_BACKEND_DIR = Path(__file__).resolve().parents[2]


@lru_cache
def alembic_heads() -> frozenset[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


@router.get("/health")
@router.get("/health/live")
def live():
    return {"status": "ok"}


def _check_db(db) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": type(exc).__name__}


def _check_redis() -> dict | None:
    if not get_settings().redis_url:
        return None  # yapılandırılmamış: kontrol edilmez
    r = get_redis()
    if r is None:
        return {"ok": False, "error": "unavailable"}
    try:
        r.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _check_migrations(db) -> dict:
    try:
        current = frozenset(db.scalars(text("SELECT version_num FROM alembic_version")).all())
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": type(exc).__name__}
    try:
        heads = alembic_heads()
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
    out = {"ok": current == heads, "current": sorted(current), "head": sorted(heads)}
    if not out["ok"]:
        out["error"] = "not_at_head"
    return out


@router.get("/health/ready", responses={503: {"description": "En az bir kontrol başarısız"}})
def ready(db: DB):
    checks = {"database": _check_db(db)}
    if (redis_check := _check_redis()) is not None:
        checks["redis"] = redis_check
    checks["migrations"] = _check_migrations(db) if checks["database"]["ok"] else {"ok": False, "error": "skipped"}
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status_code=200 if ok else 503,
        headers={"Cache-Control": "no-store"},
    )
