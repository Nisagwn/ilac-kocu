"""RAG'in PostgreSQL + pgvector yolu (`_pg_candidates`, `<=>` kosinüs uzaklığı). Diğer RAG testleri
SQLite'ta Python yolunu sınar; bu dosya gerçek veritabanı ister: TEST_DATABASE_URL yoksa atlanır.

Her test oturumu geçici bir şemada çalışır (search_path: <şema>, public): geliştirme veritabanına dokunmaz.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import EducationContent, set_review
from app.seed import seed_education
from app.services.rag import search as rag_search
from app.services.rag import search_education

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL yok (PostgreSQL)"),
]


@pytest.fixture(scope="module")
def pg():
    url = os.environ["TEST_DATABASE_URL"]
    schema = f"test_rag_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db = Session()
    seed_education(db)
    yield db
    db.close()
    eng.dispose()
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture()
def spy_pg(monkeypatch):
    calls = []
    real = rag_search._pg_candidates

    def wrapped(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(rag_search, "_pg_candidates", wrapped)
    return calls


def test_embeddings_stored_as_vector(pg):
    typ = pg.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                          "WHERE attrelid = 'education_contents'::regclass AND attname = 'embedding'")).scalar()
    assert typ == "vector(384)"
    assert pg.scalar(text("SELECT count(*) FROM education_contents WHERE embedding IS NULL")) == 0


@pytest.mark.parametrize("query, expected", [
    ("akşam ilacımı unutuyorum", "Akşam dozlarını unutmamak için ipuçları"),
    ("şekerim düştü titriyorum", "Düşük kan şekeri (hipoglisemi) belirtileri"),
    ("tuzu nasıl azaltırım", "Tuz tüketimini azaltmak"),
])
def test_pgvector_search_finds_entry(pg, spy_pg, query, expected):
    hits = search_education(pg, query, k=3)
    assert spy_pg, "PostgreSQL yolu kullanılmadı"
    assert expected in [h.title for h in hits[:2]], [h.title for h in hits]
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)


def test_pgvector_irrelevant_query_empty(pg, spy_pg):
    assert search_education(pg, "futbol maçı skoru kaç", k=3) == []


def test_pgvector_tag_filter(pg):
    hits = search_education(pg, "ölçüm nasıl yapılır", k=5, tags=["diyabet"])
    assert hits and all("diyabet" in pg.get(EducationContent, h.id).tags.split(",") for h in hits)
    assert search_education(pg, "ölçüm nasıl yapılır", tags=["olmayan-etiket"]) == []


def test_pgvector_excludes_rejected(pg, spy_pg):
    title = "Akşam dozlarını unutmamak için ipuçları"
    row = pg.scalar(select(EducationContent).where(EducationContent.title == title))
    set_review(row, "rejected", None, "test")
    pg.commit()
    try:
        assert title not in [h.title for h in search_education(pg, "akşam ilacımı unutuyorum", k=5)]
    finally:
        set_review(row, "pending")
        pg.commit()
    assert title in [h.title for h in search_education(pg, "akşam ilacımı unutuyorum", k=3)]
