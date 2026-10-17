import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.models import EducationContent, Role
from app.schemas import DrugSearchHit
from app.services import openfda
from app.services.drug_catalog import search_catalog
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


@router.get("/search", response_model=list[DrugSearchHit])
def drug_search(q: Annotated[str, Query(min_length=2, max_length=80)], user: CurrentUser, db: DB,
                limit: Annotated[int, Query(ge=1, le=20)] = 10):
    """İlaç kataloğunda arama (TR ticari ad veya etken madde; Türkçe karakter ve büyük/küçük harf duyarsız).
    İlaç formundaki otomatik tamamlama bunu kullanır; seçilen kaydın `id`'si ilaç eklerken `catalog_id` olur."""
    return [{**h.__dict__, "display_name": h.display_name} for h in search_catalog(db, q, limit=limit)]


@router.get("/label")
def drug_label(name: Annotated[str, Query(min_length=2, max_length=80)], user: CurrentUser, db: DB):
    """İlaç adından (TR ticari isim veya etken madde) openFDA etiket bilgisi: etken madde, endikasyon,
    uyarılar, etkileşimler. Yalnızca eğitim amaçlıdır. Etiket özeti ayrıca eğitim içeriğine (RAG) eklenir;
    bakım veren rolü salt okunur olduğundan onun istekleri eğitim içeriğine yazmaz."""
    try:
        data = openfda.get_label_cached(name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    if user.role != Role.caregiver:
        _auto_ingest(db, data)
    return data
