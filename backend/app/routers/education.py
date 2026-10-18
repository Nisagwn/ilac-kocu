"""Eğitim içeriği (RAG korpusu): listeleme, arama, openFDA'dan ilaç etiketi ekleme ve hekim onayı.

Şeffaflık/demo amaçlıdır: koçun gösterdiği kaynakların hangi içerikten geldiği buradan incelenebilir.
Onay politikası (reddedilen içerik RAG'de kullanılmaz vb.): docs/egitim-icerigi-onayi.md"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.deps import DB, CurrentUser, require_roles
from app.models import EducationContent, Role, User
from app.services import openfda
from app.services.rag import search_education
from app.services.rag.ingest import ingest_drug_label

router = APIRouter(prefix="/education", tags=["education (RAG)"])


class EducationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    source_name: str
    source_url: str | None
    tags: str | None
    kind: str
    drug_key: str | None
    reviewed: bool
    review_status: str  # pending | approved | rejected
    reviewed_by: int | None
    reviewed_at: datetime | None
    embedding_model: str | None
    created_at: datetime


class EducationHitOut(BaseModel):
    id: int
    title: str
    body: str
    source_name: str
    source_url: str | None
    score: float


@router.get("", response_model=list[EducationOut])
def list_education(
    user: CurrentUser, db: DB, kind: Annotated[str | None, Query(pattern="^(curated|openfda)$")] = None,
    tag: str | None = None, reviewed: bool | None = None,
    review_status: Annotated[str | None, Query(pattern="^(pending|approved|rejected)$")] = None,
):
    """Eğitim içerikleri (kürate + openFDA).

    - `reviewed=false`: hekim onayı bekleyenler (pending); `reviewed=true`: onaylananlar.
    - `review_status`: pending | approved | rejected (tam eşleşme).
    - Reddedilen içerik yalnızca hekime listelenir; diğer roller için hiç görünmez."""
    stmt = select(EducationContent).order_by(EducationContent.kind, EducationContent.id)
    if kind:
        stmt = stmt.where(EducationContent.kind == kind)
    if reviewed is not None:
        stmt = stmt.where(EducationContent.review_status == ("approved" if reviewed else "pending"))
    if review_status:
        stmt = stmt.where(EducationContent.review_status == review_status)
    if user.role != Role.clinician:
        stmt = stmt.where(EducationContent.review_status != "rejected")
    rows = list(db.scalars(stmt))
    if tag:
        rows = [r for r in rows if tag.strip().lower() in (r.tags or "").split(",")]
    return rows


ClinicianUser = Annotated[User, Depends(require_roles(Role.clinician))]
PatientOrClinician = Annotated[User, Depends(require_roles(Role.clinician, Role.patient))]


def _set_review(db: DB, content_id: int, user: User, decision: str) -> EducationContent:
    row = db.get(EducationContent, content_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eğitim içeriği bulunamadı")
    row.review_status = decision
    row.reviewed = decision == "approved"
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    db.commit()
    return row


@router.post("/{content_id}/review", response_model=EducationOut)
def approve_education(content_id: int, user: ClinicianUser, db: DB):
    """Hekim onayı: içerik RAG'de kaynak olarak "onaylı" kullanılır (yalnızca hekim)."""
    return _set_review(db, content_id, user, "approved")


@router.post("/{content_id}/reject", response_model=EducationOut)
def reject_education(content_id: int, user: ClinicianUser, db: DB):
    """Hekim reddi: içerik RAG aramasından çıkarılır ve koç tarafından kaynak gösterilmez (yalnızca hekim).
    Karar geri alınabilir: aynı içerik sonradan `/review` ile onaylanabilir."""
    return _set_review(db, content_id, user, "rejected")


@router.get("/search", response_model=list[EducationHitOut])
def search(user: CurrentUser, db: DB, q: Annotated[str, Query(min_length=2, max_length=300)],
           k: Annotated[int, Query(ge=1, le=10)] = 3, tags: str | None = None):
    """Anlamsal + anahtar kelime hibrit arama. Eşik altı (ilgisiz) sorgularda boş liste döner.
    `tags`: virgülle ayrılmış etiket filtresi (ör. `diyabet,olcum`)."""
    tag_list = [t for t in (tags or "").split(",") if t.strip()] or None
    return [h.__dict__ for h in search_education(db, q, k=k, tags=tag_list)]


@router.post("/ingest-drug", response_model=EducationOut, status_code=status.HTTP_201_CREATED)
def ingest_drug(user: PatientOrClinician, db: DB, name: Annotated[str, Query(min_length=2, max_length=80)]):
    """openFDA etiketinden ilaç bilgisini çekip eğitim içeriğine ekler (aynı etken madde için idempotent).
    Ana kullanım: hasta ilacını eklerken ilgili etiket özetinin koçun kaynaklarına girmesi.
    Yalnızca hasta ve hekim; bakım veren salt okunur olduğu için 403. Eklenen içerik hekim onayı bekler."""
    try:
        row = ingest_drug_label(db, name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Etiketten eğitim içeriği çıkarılamadı")
    return row
