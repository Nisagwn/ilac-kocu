"""fastembed (anlamsal) embedding sağlayıcısı testleri — gerçek fastembed/model indirme YOK (sahte modül)."""

import builtins
import sys
import types

import pytest

from app.config import get_settings
from app.models import EducationContent
from app.services.rag import embeddings
from app.services.rag.embeddings import LOCAL_MODEL_ID, FastEmbedEmbedding, LocalHashEmbedding, get_provider
from app.services.rag.search import ensure_embeddings
from app.services.rag.vector_type import EMBEDDING_DIM


class FakeTextEmbedding:
    """fastembed.TextEmbedding taklidi: aldığı metinleri kaydeder, metne göre deterministik vektör döndürür."""

    instances: list["FakeTextEmbedding"] = []
    dim = EMBEDDING_DIM

    def __init__(self, model_name, **kwargs):
        self.model_name = model_name
        self.kwargs = kwargs
        self.seen: list[str] = []
        FakeTextEmbedding.instances.append(self)

    def embed(self, documents, batch_size=256):
        for doc in documents:
            self.seen.append(doc)
            vec = [0.0] * self.dim
            vec[len(doc) % self.dim] = 3.0
            vec[0] += 4.0
            yield vec


@pytest.fixture()
def fake_fastembed(monkeypatch):
    FakeTextEmbedding.instances = []
    FakeTextEmbedding.dim = EMBEDDING_DIM
    module = types.ModuleType("fastembed")
    module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    monkeypatch.setattr(get_settings(), "embedding_provider", "fastembed")
    embeddings.reset_provider_cache()
    yield FakeTextEmbedding
    embeddings.reset_provider_cache()


def test_fastembed_provider_prefixes_and_dims(fake_fastembed):
    provider = get_provider()
    assert isinstance(provider, FastEmbedEmbedding)
    assert provider.model_id == f"fastembed:intfloat/multilingual-e5-small:{EMBEDDING_DIM}"
    assert provider.dim == EMBEDDING_DIM
    [model] = fake_fastembed.instances
    assert model.model_name == "intfloat/multilingual-e5-small"

    docs = provider.embed_documents(["Metformin yan etkileri", "Tansiyon ölçümü"])
    q = provider.embed_query("şekerim düştü")
    assert model.seen == ["passage: Metformin yan etkileri", "passage: Tansiyon ölçümü", "query: şekerim düştü"]
    assert len(docs) == 2 and all(len(v) == EMBEDDING_DIM for v in [*docs, q])
    assert abs(sum(x * x for x in q) - 1.0) < 1e-9  # L2-normalize

    # model süreç başına bir kez yüklenir
    assert get_provider() is provider and len(fake_fastembed.instances) == 1


def test_fastembed_wrong_dimension_raises(fake_fastembed):
    fake_fastembed.dim = 768
    provider = get_provider()
    with pytest.raises(ValueError, match="768"):
        provider.embed_query("test")


def test_fastembed_missing_package_falls_back_to_local(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "embedding_provider", "fastembed")
    monkeypatch.delitem(sys.modules, "fastembed", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError("No module named 'fastembed'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    embeddings.reset_provider_cache()
    try:
        provider = get_provider()
    finally:
        embeddings.reset_provider_cache()
    assert isinstance(provider, LocalHashEmbedding) and provider.model_id == LOCAL_MODEL_ID
    assert any("fastembed" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


def test_fastembed_model_load_error_falls_back_to_local(monkeypatch, fake_fastembed):
    class Broken(FakeTextEmbedding):
        def __init__(self, model_name, **kwargs):
            raise RuntimeError("indirme başarısız")

    sys.modules["fastembed"].TextEmbedding = Broken
    assert isinstance(get_provider(), LocalHashEmbedding)


def test_provider_switch_triggers_reembedding(db, fake_fastembed):
    row = EducationContent(title="İlacı rutine bağlamak", body="Kahvaltıya bağlamak hatırlamayı kolaylaştırır.",
                           source_name="Test", source_url="https://example.org", tags="uyum")
    db.add(row)
    db.commit()

    local = LocalHashEmbedding()
    assert ensure_embeddings(db, local) == 1 and row.embedding_model == LOCAL_MODEL_ID
    assert ensure_embeddings(db, local) == 0  # aynı model: yeniden embed yok

    semantic = get_provider()
    assert ensure_embeddings(db, semantic) == 1  # farklı model_id -> yeniden embed (reindex de bunu kullanır)
    assert row.embedding_model == semantic.model_id and len(row.embedding) == EMBEDDING_DIM
    assert fake_fastembed.instances[0].seen[0].startswith("passage: ")
