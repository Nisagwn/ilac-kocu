from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import alerts, auth, coach, doses, drugs, measurements, medications, patients

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Tip 2 diyabet ve hipertansiyon hastaları için ilaç uyumu takibi ve güvenli LLM koç API'si.\n\n"
        "**Uyarı:** Koç tıbbi tavsiye vermez; doz önermez, teşhis koymaz. Acil durumlarda 112'yi arayın."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, patients, medications, doses, measurements, alerts, drugs, coach):
    app.include_router(r.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
