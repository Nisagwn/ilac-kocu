"""RAG — kürate hasta eğitim içeriğinde anlamsal arama.

SÖZLEŞME (koç ve özet servisleri buna bağlıdır, imza değişmez):
    search_education(db, query, k=3, tags=None) -> list[EducationHit]

Uygulama `app.services.rag.search` içindedir: sorgu embed edilir; PostgreSQL'de pgvector
`<=>` (cosine mesafesi) ile, SQLite'ta Python'da cosine benzerliği hesaplanır; anahtar kelime
örtüşmesiyle hibrit skor üretilir ve `RAG_MIN_SCORE` altındaki sonuçlar atılır.

Not: `app.models` bu paketin `vector_type` modülünü içe aktarır; döngüsel içe aktarmayı önlemek
için bu dosya modelleri modül düzeyinde içe aktarmaz.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class EducationHit:
    id: int
    title: str
    body: str
    source_name: str
    source_url: str | None
    score: float


def search_education(db: Session, query: str, k: int = 3, tags: list[str] | None = None) -> list[EducationHit]:
    from app.services.rag.search import search

    return search(db, query, k=k, tags=tags)


__all__ = ["EducationHit", "search_education"]
