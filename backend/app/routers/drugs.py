import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.models import EducationContent, Role
from app.schemas import DrugSearchOut
from app.services import drug_catalog, openfda
from app.services.rag.ingest import ingest_label_data
from app.services.ratelimit import check_rate_limit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/drugs", tags=["drugs (openFDA)"])

# Otomatik tamamlama (≈250 ms debounce) için yeterli, kötüye kullanımı sınırlayan kullanıcı başı sınır
SEARCH_LIMIT_PER_MINUTE = 90

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
    uyarılar, etkileşimler. Yalnızca eğitim amaçlıdır. Etiket özeti ayrıca eğitim içeriğine (RAG) eklenir;
    bakım veren (salt okunur rol) için bilgi döner ama ortak eğitim tablosuna yazılmaz."""
    try:
        data = openfda.get_label_cached(name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    if user.role != Role.caregiver:
        _auto_ingest(db, data)
    return data


@router.get("/search", response_model=list[DrugSearchOut])
def drug_search(
    q: Annotated[str, Query(min_length=2, max_length=80, description="İlaç adı: TR/ABD ticari ad veya etken madde")],
    user: CurrentUser,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
):
    """Yerel ilaç kataloğunda arama (ön ek + içerir; Türkçe karakter ve büyük/küçük harf duyarsız).
    TR ticari adla ("Glifor"), ABD markasıyla veya etken maddeyle ("metformin") bulur. Giriş yapmış her
    rol kullanabilir. Harici ağa gitmez; katalog boşsa kürate Türkçe liste yüklenir."""
    check_rate_limit(f"drugsearch:user:{user.id}", SEARCH_LIMIT_PER_MINUTE, 60)
    drug_catalog.ensure_catalog(db)
    return [
        DrugSearchOut(
            id=h.entry.id, display_name=h.entry.display_name, generic_name=h.entry.generic_name,
            matched_brand=h.matched_brand, tr_brand_names=list(h.entry.tr_brand_names or []),
            pharm_class=h.entry.pharm_class, source=h.entry.source, openfda_available=h.entry.openfda_available,
        )
        for h in drug_catalog.search_catalog(db, q, limit)
    ]
