"""Eğitim içeriği (RAG korpusu): listeleme, arama ve openFDA'dan ilaç etiketi ekleme.

Şeffaflık/demo amaçlıdır: koçun gösterdiği kaynakların hangi içerikten geldiği buradan incelenebilir."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.models import EducationContent
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
def list_education(user: CurrentUser, db: DB, kind: Annotated[str | None, Query(pattern="^(curated|openfda)$")] = None,
                   tag: str | None = None):
    """Tüm eğitim içerikleri (kürate + openFDA). `reviewed=false`: klinisyen gözden geçirmesi bekleniyor."""
    stmt = select(EducationContent).order_by(EducationContent.kind, EducationContent.id)
    if kind:
        stmt = stmt.where(EducationContent.kind == kind)
    rows = list(db.scalars(stmt))
    if tag:
        rows = [r for r in rows if tag.strip().lower() in (r.tags or "").split(",")]
    return rows


@router.get("/search", response_model=list[EducationHitOut])
def search(user: CurrentUser, db: DB, q: Annotated[str, Query(min_length=2, max_length=300)],
           k: Annotated[int, Query(ge=1, le=10)] = 3, tags: str | None = None):
    """Anlamsal + anahtar kelime hibrit arama. Eşik altı (ilgisiz) sorgularda boş liste döner.
    `tags`: virgülle ayrılmış etiket filtresi (ör. `diyabet,olcum`)."""
    tag_list = [t for t in (tags or "").split(",") if t.strip()] or None
    return [h.__dict__ for h in search_education(db, q, k=k, tags=tag_list)]


@router.post("/ingest-drug", response_model=EducationOut, status_code=status.HTTP_201_CREATED)
def ingest_drug(user: CurrentUser, db: DB, name: Annotated[str, Query(min_length=2, max_length=80)]):
    """openFDA etiketinden ilaç bilgisini çekip eğitim içeriğine ekler (aynı etken madde için idempotent).
    Ana kullanım: hasta ilacını eklerken ilgili etiket özetinin koçun kaynaklarına girmesi."""
    try:
        row = ingest_drug_label(db, name)
    except openfda.DrugLabelNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{name}' için openFDA'da etiket bulunamadı") from e
    except openfda.OpenFDAUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "openFDA şu an yanıt vermiyor") from e
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Etiketten eğitim içeriği çıkarılamadı")
    return row
