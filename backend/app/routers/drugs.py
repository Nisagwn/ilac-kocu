import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.models import EducationContent
from app.services import openfda
from app.services.rag.ingest import ingest_label_data

log = logging.getLogger(__name__)

router = APIRouter(prefix="/drugs", tags=["drugs (openFDA)"])

# REDIS_URL tanımlıysa Redis (24 saat TTL), değilse süreç içi önbellek. Testler `_cache.clear()` kullanır.
_cache = openfda.label_cache


def _auto_ingest(db, data: dict) -> None:
    """Etiketi eğitim içeriğine (RAG) ekler — en iyi çaba: hata isteği bozmaz."""
    key = (data.get("resolved_generic") or "").strip().lower()
    if not key:
        return
    try:
        if db.scalar(select(EducationContent.id).where(EducationContent.drug_key == key)) is None:
            ingest_label_data(db, data)
    except Exception:
        db.rollback()
        log.warning("openFDA etiketi eğitim içeriğine eklenemedi (%s)", key, exc_info=True)


@router.get("/label")
def drug_label(name: Annotated[str, Query(min_length=2, max_length=80)], user: CurrentUser, db: DB):
    """İlaç adından (TR ticari isim veya etken madde) openFDA etiket bilgisi: etken madde, endikasyon,
    uyarılar, etkileşimler. Yalnızca eğitim amaçlıdır. Etiket özeti ayrıca eğitim içeriğine (RAG) eklenir."""
    try:
        data = openfda.get_label_cached(name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    _auto_ingest(db, data)
    return data
