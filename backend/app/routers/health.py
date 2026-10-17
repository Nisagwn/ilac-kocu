"""Sağlık uçları (yük dengeleyici / orkestratör için; kimlik doğrulama yok, OpenAPI'de `system`).

- `GET /health/live`: süreç ayakta mı (bağımlılıklara bakmaz; yeniden başlatma kararı için).
- `GET /health/ready`: trafik alabilir mi: veritabanı, (tanımlıysa) Redis ve şema alembic head'de mi.
  Biri bozuksa 503 ve bileşen bazında durum döner. Yanıt sağlık verisi ya da bağlantı dizesi içermez.
- `GET /health`: eski uç (canlılık), geriye dönük.
"""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.deps import DB
from app.redis_client import get_redis

router = APIRouter(tags=["system"])

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@lru_cache
def alembic_heads() -> frozenset[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: DB, response: Response):
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        db.rollback()
        checks["database"] = "error"

    if get_settings().redis_url:
        r = get_redis()
        try:
            checks["redis"] = "ok" if r is not None and r.ping() else "error"
        except Exception:
            checks["redis"] = "error"
    else:
        checks["redis"] = "disabled"

    if get_settings().health_check_migrations and checks["database"] == "ok":
        try:
            current = {row[0] for row in db.execute(text("SELECT version_num FROM alembic_version"))}
            checks["migrations"] = "ok" if current == set(alembic_heads()) else "pending"
        except Exception:
            db.rollback()
            checks["migrations"] = "missing"

    healthy = all(v in ("ok", "disabled") for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "unavailable", "checks": checks}
