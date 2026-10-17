import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.observability import RequestContextMiddleware, metrics_router
from app.platform import API_PREFIX, install_platform
from app.routers import (
    alerts,
    auth,
    coach,
    doses,
    drugs,
    education,
    health,
    measurements,
    medications,
    patients,
    push,
    resources,
    summaries,
)
from app.services.reminders import start_scheduler

settings = get_settings()
logger = logging.getLogger(__name__)

# Depo herkese açık: bu değerlerle imzalanan token'lar herkesçe üretilebilir
_PLACEHOLDER_JWT_SECRETS = {
    "dev-secret-change-me",
    "buraya-en-az-32-karakterlik-rastgele-bir-anahtar-yazin",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.jwt_secret in _PLACEHOLDER_JWT_SECRETS or len(settings.jwt_secret) < 32:
        logger.warning("JWT_SECRET varsayılan ya da 32 karakterden kısa; yalnızca yerel demo için kullanın.")
    # Doz hatırlatma zamanlayıcısı kuyruk işçisinde (python -m app.worker) çalışır; API birden fazla
    # worker ile koşabildiği için burada başlatılmaz (çift bildirim olur). Tek süreçli geliştirme için
    # RUN_SCHEDULER_IN_API=true. Her durumda REMINDERS_ENABLED ve VAPID anahtarları gerekir (testlerde asla).
    scheduler = start_scheduler(settings) if settings.run_scheduler_in_api else None
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Tip 2 diyabet ve hipertansiyon hastaları için ilaç uyumu takibi ve güvenli LLM koç API'si.\n\n"
        "**Uyarı:** Koç tıbbi tavsiye vermez; doz önermez, teşhis koymaz. Acil durumlarda 112'yi arayın."
    ),
    lifespan=lifespan,
)

# Platform katmanı (app/platform.py): Deprecation, kullanıcı başına istek sınırı, Idempotency-Key, ETag
install_platform(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Gözlemlenebilirlik: istek kimliği + JSON erişim logu + Prometheus (en dışta; /metrics OpenAPI'de görünmez)
app.add_middleware(RequestContextMiddleware)
app.include_router(metrics_router)

# İş uçları /v1 altında. Sürümsüz eski yollar geriye dönük uyum için bir süre kalır: OpenAPI'de görünmez,
# yanıtlarına "Deprecation: true" eklenir. Üst düzey kaynak yolları (resources) yalnızca /v1'de.
for r in (auth, patients, medications, doses, measurements, alerts, drugs, education, coach, summaries, push):
    app.include_router(r.router, prefix=API_PREFIX)
    app.include_router(r.router, include_in_schema=False)
app.include_router(resources.router, prefix=API_PREFIX)
app.include_router(health.router)  # /health, /health/live, /health/ready (sürümsüz)
