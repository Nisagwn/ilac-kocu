"""Eğitim içeriği arama ve embedding bakımı."""

from __future__ import annotations

import logging
from functools import lru_cache

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, bindparam, cast, or_, select
from sqlalchemy.orm import Session, undefer

from app.config import get_settings
from app.models import EducationContent
from app.services.guardrails import normalize
from app.services.rag import EducationHit
from app.services.rag.embeddings import (
    EmbeddingProvider,
    LocalHashEmbedding,
    cosine,
    document_text,
    get_provider,
    keyword_keys,
)
from app.services.rag.vector_type import EMBEDDING_DIM

log = logging.getLogger(__name__)

VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3
PG_CANDIDATES = 40


def _doc_text(row: EducationContent) -> str:
    return document_text(row.title, row.body, row.tags)


def ensure_embeddings(db: Session, provider: EmbeddingProvider | None = None, force: bool = False) -> int:
    """Embedding'i olmayan ya da başka bir modelle üretilmiş satırları embed eder (flush, commit etmez).

    Döndürür: güncellenen satır sayısı."""
    provider = provider or get_provider()
    stmt = select(EducationContent).options(undefer(EducationContent.embedding))
    if not force:
        stmt = stmt.where(or_(
            EducationContent.embedding.is_(None),
            EducationContent.embedding_model.is_(None),
            EducationContent.embedding_model != provider.model_id,
        ))
    rows = list(db.scalars(stmt))
    if not rows:
        return 0
    vectors = provider.embed_documents([_doc_text(r) for r in rows])
    for row, vec in zip(rows, vectors, strict=True):
        row.embedding = vec
        row.embedding_model = provider.model_id
    db.flush()
    return len(rows)


def _tag_set(tags: str | None) -> set[str]:
    return {normalize(t) for t in (tags or "").split(",") if t.strip()}


@lru_cache(maxsize=4096)
def _doc_keys(text: str) -> frozenset[str]:
    return frozenset(keyword_keys(text))


def _pg_candidates(db: Session, qvec: list[float], model_id: str, tags: set[str]) -> list[tuple[EducationContent, float]]:
    qparam = cast(bindparam("qv", qvec, type_=Vector(EMBEDDING_DIM)), Vector(EMBEDDING_DIM))
    dist = EducationContent.embedding.op("<=>", return_type=Float)(qparam)
    stmt = select(EducationContent, dist.label("dist")).where(EducationContent.embedding_model == model_id)
    if tags:
        stmt = stmt.where(or_(*[EducationContent.tags.ilike(f"%{t}%") for t in tags]))
    stmt = stmt.order_by(dist).limit(PG_CANDIDATES)
    return [(row, 1.0 - float(d)) for row, d in db.execute(stmt).all()]


def _py_candidates(db: Session, qvec: list[float], provider: EmbeddingProvider,
                   on_the_fly: bool) -> list[tuple[EducationContent, float]]:
    stmt = select(EducationContent)
    if not on_the_fly:
        stmt = stmt.options(undefer(EducationContent.embedding))
    rows = list(db.scalars(stmt))
    if on_the_fly:
        vecs = provider.embed_documents([_doc_text(r) for r in rows])
        return [(r, cosine(qvec, v)) for r, v in zip(rows, vecs, strict=True)]
    return [(r, cosine(qvec, r.embedding)) for r in rows
            if r.embedding is not None and r.embedding_model == provider.model_id]


def search(db: Session, query: str, k: int = 3, tags: list[str] | None = None) -> list[EducationHit]:
    query = (query or "").strip()
    if not query or k <= 0:
        return []
    tagset = {normalize(t) for t in (tags or []) if t and t.strip()}

    provider: EmbeddingProvider = get_provider()
    on_the_fly = False
    try:
        ensure_embeddings(db, provider)
        qvec = provider.embed_query(query)
    except Exception:  # harici sağlayıcı (Voyage) hatası: yerel embedding ile anlık hesapla
        log.warning("Embedding sağlayıcısı (%s) başarısız; yerel embedding'e düşülüyor", provider.model_id,
                    exc_info=True)
        provider, on_the_fly = LocalHashEmbedding(), True
        qvec = provider.embed_query(query)

    if db.get_bind().dialect.name == "postgresql" and not on_the_fly:
        candidates = _pg_candidates(db, qvec, provider.model_id, tagset)
    else:
        candidates = _py_candidates(db, qvec, provider, on_the_fly)

    qkeys = keyword_keys(query)
    min_score = get_settings().rag_min_score
    hits: list[EducationHit] = []
    for row, cos in candidates:
        if tagset and not tagset & _tag_set(row.tags):
            continue
        overlap = len(qkeys & _doc_keys(_doc_text(row))) / len(qkeys) if qkeys else 0.0
        score = VECTOR_WEIGHT * max(cos, 0.0) + KEYWORD_WEIGHT * overlap
        if score >= min_score:
            hits.append(EducationHit(row.id, row.title, row.body, row.source_name, row.source_url, round(score, 4)))
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits[:k]
