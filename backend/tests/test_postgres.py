"""Gerçek PostgreSQL + pgvector testleri (RAG araması, lider kilidi).

Yalnızca DATABASE_URL bir PostgreSQL adresiyse koşar (CI backend job'ı: pgvector/pgvector:pg16); aksi halde atlanır.
    DATABASE_URL=postgresql+psycopg://ilac:ilac@localhost:5432/ilac_kocu pytest -m postgres

Mevcut verilere dokunulmaz: testler geçici bir şemada (pgtest_<rastgele>) tablo kurar ve sonunda şemayı siler.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL.startswith("postgresql"), reason="DATABASE_URL PostgreSQL değil (pgvector testi)"),
]


@pytest.fixture(scope="module")
def pg_engine():
    from app.db import Base

    schema = f"pgtest_{uuid.uuid4().hex[:10]}"
    admin = create_engine(URL)
    with admin.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(URL, connect_args={"options": f"-csearch_path={schema},public"})
    try:
        Base.metadata.create_all(eng)
        with eng.begin() as c:  # migration 0003 ile aynı HNSW indeksi
            c.execute(text("CREATE INDEX ix_test_edu_hnsw ON education_contents "
                           "USING hnsw (embedding vector_cosine_ops)"))
        yield eng
    finally:
        eng.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(scope="module")
def pg_seeded(pg_engine):
    from app.seed import seed_education

    db = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)()
    seed_education(db)
    yield db
    db.close()


def test_embedding_column_is_pgvector(pg_engine):
    with pg_engine.connect() as c:
        col_type = c.execute(text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = 'education_contents'::regclass AND a.attname = 'embedding'")).scalar()
    assert col_type == "vector(384)"


def test_search_uses_pgvector_path(pg_seeded, monkeypatch):
    from app.services.rag import search as rag_search
    from app.services.rag import search_education

    def no_python_path(*a, **kw):
        raise AssertionError("PostgreSQL'de Python cosine yoluna düşülmemeli")

    monkeypatch.setattr(rag_search, "_py_candidates", no_python_path)
    hits = search_education(pg_seeded, "akşam ilacımı unutuyorum", k=3)
    assert hits and hits[0].title == "Akşam dozlarını unutmamak için ipuçları"
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)


@pytest.mark.parametrize("query, expected_title", [
    ("dozu kaçırdım ne yapayım", "Bir dozu kaçırdığınızda ne yapmalı"),
    ("metformin ishal yapıyor", "Metformin ve mide-bağırsak yakınmaları"),
    ("tuzu nasıl azaltırım", "Tuz tüketimini azaltmak"),
    ("ayağımda yara var", "Diyabette ayak bakımı"),
])
def test_pgvector_results_match_expected(pg_seeded, query, expected_title):
    from app.services.rag import search_education

    assert expected_title in [h.title for h in search_education(pg_seeded, query, k=3)[:2]]


def test_pgvector_cosine_matches_python_cosine(pg_seeded):
    from app.services.rag.embeddings import cosine, get_provider
    from app.services.rag.search import _pg_candidates

    provider = get_provider()
    qvec = provider.embed_query("tansiyonu nasıl ölçerim")
    for row, score in _pg_candidates(pg_seeded, qvec, provider.model_id, set())[:5]:
        assert score == pytest.approx(cosine(qvec, row.embedding), abs=1e-5)


def test_irrelevant_query_and_tag_filter(pg_seeded):
    from app.models import EducationContent
    from app.services.rag import search_education

    assert search_education(pg_seeded, "futbol maçı kaç kaç bitti") == []
    hits = search_education(pg_seeded, "ölçüm nasıl yapılır", k=5, tags=["diyabet"])
    assert hits
    for h in hits:
        assert "diyabet" in pg_seeded.get(EducationContent, h.id).tags.split(",")


def test_leader_lock_is_exclusive_and_handed_over(pg_engine):
    from app.services.leader import LeaderLock

    lock_id = 900_000_000 + uuid.uuid4().int % 1_000_000
    first, second = LeaderLock(pg_engine, lock_id), LeaderLock(pg_engine, lock_id)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False  # ikinci kopya lider olamaz
        assert first.check() is True
        first.release()
        assert second.try_acquire() is True  # lider bırakınca diğeri devralır
    finally:
        first.release()
        second.release()


def test_leader_lock_released_when_connection_dies(pg_engine):
    from app.services.leader import LeaderLock

    lock_id = 900_000_000 + uuid.uuid4().int % 1_000_000
    first, second = LeaderLock(pg_engine, lock_id), LeaderLock(pg_engine, lock_id)
    try:
        assert first.try_acquire()
        first._conn.invalidate()  # süreç ölmüş gibi: bağlantı kapanır, kilit sunucuda düşer
        assert first.check() is False
        assert second.try_acquire() is True
    finally:
        second.release()
