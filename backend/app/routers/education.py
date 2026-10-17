"""Eğitim içeriği (RAG korpusu): listeleme, arama, openFDA'dan ilaç etiketi ekleme ve hekim onayı.

Şeffaflık/demo amaçlıdır: koçun gösterdiği kaynakların hangi içerikten geldiği buradan incelenebilir.
Hekim içeriği onaylar ya da reddeder (`/hekim/egitim`); reddedilen içerik RAG aramasına girmez."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.deps import DB, CurrentUser, require_roles
from app.models import EducationContent, Role, User, set_review
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
    review_status: str
    reviewed_at: datetime | None
    review_note: str | None
    reviewer_name: str | None = None
    embedding_model: str | None
    created_at: datetime


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000, description="Hekim notu (reddederken zorunlu)")


Clinician = Annotated[User, Depends(require_roles(Role.clinician))]
# Bakım veren rolü salt okunurdur: eğitim içeriğine yazamaz
Writer = Annotated[User, Depends(require_roles(Role.patient, Role.clinician))]


def _out(db, row: EducationContent) -> EducationOut:
    out = EducationOut.model_validate(row)
    if row.reviewed_by_user_id:
        reviewer = db.get(User, row.reviewed_by_user_id)
        out.reviewer_name = reviewer.full_name if reviewer else None
    return out


class EducationHitOut(BaseModel):
    id: int
    title: str
    body: str
    source_name: str
    source_url: str | None
    score: float


@router.get("", response_model=list[EducationOut])
def list_education(user: CurrentUser, db: DB, kind: Annotated[str | None, Query(pattern="^(curated|openfda)$")] = None,
                   tag: str | None = None,
                   review_status: Annotated[str | None, Query(pattern="^(pending|approved|rejected)$")] = None):
    """Tüm eğitim içerikleri (kürate + openFDA). `review_status`: pending (hekim onayı bekliyor) | approved |
    rejected (RAG'de kullanılmaz)."""
    stmt = select(EducationContent).order_by(EducationContent.kind, EducationContent.id)
    if kind:
        stmt = stmt.where(EducationContent.kind == kind)
    if review_status:
        stmt = stmt.where(EducationContent.review_status == review_status)
    rows = list(db.scalars(stmt))
    if tag:
        rows = [r for r in rows if tag.strip().lower() in (r.tags or "").split(",")]
    return [_out(db, r) for r in rows]


def _get(db, content_id: int) -> EducationContent:
    row = db.get(EducationContent, content_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "İçerik bulunamadı")
    return row


@router.post("/{content_id}/approve", response_model=EducationOut)
def approve(content_id: int, user: Clinician, db: DB, data: ReviewIn | None = None):
    """Hekim içeriği onaylar: koç kaynak olarak gösterebilir."""
    row = _get(db, content_id)
    set_review(row, "approved", user.id, (data.note or "").strip() or None if data else None)
    db.commit()
    return _out(db, row)


@router.post("/{content_id}/reject", response_model=EducationOut)
def reject(content_id: int, data: ReviewIn, user: Clinician, db: DB):
    """Hekim içeriği reddeder (not zorunlu): içerik RAG aramasından çıkar, koç kaynak gösteremez."""
    note = (data.note or "").strip()
    if len(note) < 3:
        raise HTTPException(422, "Reddetme nedeni yazın (en az 3 karakter)")
    row = _get(db, content_id)
    set_review(row, "rejected", user.id, note)
    db.commit()
    return _out(db, row)


@router.post("/{content_id}/reset", response_model=EducationOut)
def reset_review(content_id: int, user: Clinician, db: DB):
    """Kararı geri alır: içerik yeniden onay bekler (RAG'de kullanılabilir)."""
    row = _get(db, content_id)
    set_review(row, "pending")
    db.commit()
    return _out(db, row)


@router.get("/search", response_model=list[EducationHitOut])
def search(user: CurrentUser, db: DB, q: Annotated[str, Query(min_length=2, max_length=300)],
           k: Annotated[int, Query(ge=1, le=10)] = 3, tags: str | None = None):
    """Anlamsal + anahtar kelime hibrit arama. Eşik altı (ilgisiz) sorgularda boş liste döner.
    `tags`: virgülle ayrılmış etiket filtresi (ör. `diyabet,olcum`)."""
    tag_list = [t for t in (tags or "").split(",") if t.strip()] or None
    return [h.__dict__ for h in search_education(db, q, k=k, tags=tag_list)]


@router.post("/ingest-drug", response_model=EducationOut, status_code=status.HTTP_201_CREATED)
def ingest_drug(user: Writer, db: DB, name: Annotated[str, Query(min_length=2, max_length=80)]):
    """openFDA etiketinden ilaç bilgisini çekip eğitim içeriğine ekler (aynı etken madde için idempotent).
    Ana kullanım: hasta ilacını eklerken ilgili etiket özetinin koçun kaynaklarına girmesi.
    Bakım veren rolü kullanamaz (salt okunur); eklenen içerik hekim onayı bekler."""
    try:
        row = ingest_drug_label(db, name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    if row is None:
        raise HTTPException(422, "Etiketten eğitim içeriği çıkarılamadı")
    return _out(db, row)
