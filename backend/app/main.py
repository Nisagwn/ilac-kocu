from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    alerts,
    auth,
    coach,
    doses,
    drugs,
    education,
    measurements,
    medications,
    patients,
    push,
    summaries,
)
from app.services.reminders import start_scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Doz hatırlatma zamanlayıcısı: yalnızca REMINDERS_ENABLED ve VAPID anahtarları varsa (testlerde asla)
    scheduler = start_scheduler(settings)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, patients, medications, doses, measurements, alerts, drugs, education, coach, summaries, push):
    app.include_router(r.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
