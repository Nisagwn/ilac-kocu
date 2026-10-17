import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.middleware import (
    API_VERSION_PREFIX,
    DeprecationMiddleware,
    ETagMiddleware,
    IdempotencyMiddleware,
    RateLimitMiddleware,
)
from app.observability import RequestContextMiddleware, metrics_router
from app.routers import (
    alerts,
    auth,
    coach,
    collections,
    doses,
    drugs,
    education,
    health,
    measurements,
    medications,
    patients,
    push,
    summaries,
)
from app.scheduler import start_scheduler
from app.tracing import setup_tracing

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
    # Zamanlanmış işler (doz hatırlatma, haftalık özet, temizlik) lider kuyruk işçisinde (python -m app.worker)
    # çalışır; API birden fazla kopya ile koşabildiği için burada başlatılmaz (çift bildirim olur). Tek süreçli
    # geliştirme için RUN_SCHEDULER_IN_API=true (testlerde asla).
    scheduler = start_scheduler(settings) if settings.run_scheduler_in_api else None
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Tip 2 diyabet ve hipertansiyon hastaları için ilaç uyumu takibi ve güvenli LLM koç API'si.\n\n"
        "**Uyarı:** Koç tıbbi tavsiye vermez; doz önermez, teşhis koymaz. Acil durumlarda 112'yi arayın.\n\n"
        "**Sürüm:** tüm uçlar `/v1` altındadır. Sürümsüz eski yollar geriye dönük olarak çalışır ama şemada "
        "görünmez ve `Deprecation` başlığı taşır.\n\n"
        "**Platform:** istek sınırı (token bucket, `X-RateLimit-*`, aşımda 429 + `Retry-After`), yazma "
        "isteklerinde `Idempotency-Key`, GET yanıtlarında `ETag` / `If-None-Match` → 304, liste uçlarında "
        "`limit` + `cursor` ile sayfalama (`X-Next-Cursor`, `Link: rel=next`)."
    ),
    lifespan=lifespan,
)

# Middleware sırası (dıştan içe): istek kimliği/log → CORS → istek sınırı → sürüm başlığı → ETag → idempotency
app.add_middleware(IdempotencyMiddleware)
app.add_middleware(ETagMiddleware)
app.add_middleware(DeprecationMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
                    "X-Next-Cursor", "Link", "Idempotent-Replayed", "Deprecation", "X-Request-ID"],
)
# Gözlemlenebilirlik: istek kimliği + JSON erişim logu + Prometheus (en dışta; /metrics OpenAPI'de görünmez)
app.add_middleware(RequestContextMiddleware)
app.include_router(metrics_router)
app.include_router(health.router)

_ROUTERS = (auth, patients, medications, doses, measurements, alerts, drugs, education, coach, summaries, push)
for r in _ROUTERS:
    app.include_router(r.router, prefix=API_VERSION_PREFIX)
app.include_router(collections.router, prefix=API_VERSION_PREFIX)
# Geriye dönük: sürümsüz eski yollar (şemada görünmez, DeprecationMiddleware başlık ekler)
for r in _ROUTERS:
    app.include_router(r.router, include_in_schema=False, deprecated=True)

# Dağıtık izleme: yalnızca OTEL_EXPORTER_OTLP_ENDPOINT tanımlıysa (compose "observability" profili → Jaeger)
setup_tracing(app)
