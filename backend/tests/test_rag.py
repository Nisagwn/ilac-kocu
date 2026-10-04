"""RAG (eğitim içeriği) testleri — ağ erişimi yok; openFDA monkeypatch / MockTransport ile taklit edilir."""

import math

import httpx
import pytest
from sqlalchemy import func, select

from app.models import EducationContent
from app.routers import drugs
from app.seed import load_education_yaml, seed_education
from app.services import openfda
from app.services.rag import EducationHit, search_education
from app.services.rag.embeddings import LOCAL_MODEL_ID, LocalHashEmbedding, VoyageEmbedding, cosine
from app.services.rag.ingest import DISCLAIMER, ingest_drug_label, ingest_label_data
from app.services.rag.search import ensure_embeddings
from app.services.rag.vector_type import EMBEDDING_DIM
from tests.test_api import FAKE_LABEL

LABEL_DATA = openfda.parse_label("Glifor", "metformin", {
    **FAKE_LABEL["results"][0],
    "indications_and_usage": ["1 INDICATIONS AND USAGE Metformin is indicated as an adjunct to diet and exercise "
                              "to improve glycemic control in adults with type 2 diabetes mellitus."],
    "warnings_and_cautions": ["Lactic acidosis has been reported. The usual starting dose is 500 mg twice daily. "
                              "Assess renal function before initiation."],
})


@pytest.fixture()
def seeded(db):
    seed_education(db)
    return db


@pytest.fixture(autouse=True)
def _clear_label_cache():
    openfda.label_cache.clear()
    yield
    openfda.label_cache.clear()


# ---------------- embedding ----------------
def test_local_embedding_deterministic_and_normalized():
    emb = LocalHashEmbedding()
    a = emb.embed_query("Akşam ilacımı unutuyorum")
    assert a == LocalHashEmbedding().embed_query("Akşam ilacımı unutuyorum")
    assert len(a) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)
    assert emb.embed_documents(["x y", "x y"])[0] == emb.embed_documents(["x y"])[0]


def test_local_embedding_turkish_normalization():
    emb = LocalHashEmbedding()
    assert cosine(emb.embed_query("göğüs ağrısı"), emb.embed_query("gogus agrisi")) > 0.99
    assert cosine(emb.embed_query("GÖĞÜS"), emb.embed_query("göğüs")) > 0.99
    # ek almış biçimler yakın, ilgisiz kelime uzak
    near = cosine(emb.embed_query("tansiyon"), emb.embed_query("tansiyonumu"))
    far = cosine(emb.embed_query("tansiyon"), emb.embed_query("futbol"))
    assert near > 0.5 > far


def test_voyage_provider_uses_http_and_truncates():
    def handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer k"
        body = request.read().decode()
        assert '"input_type":"query"' in body.replace(" ", "")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 512}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        vec = VoyageEmbedding("k", client=c).embed_query("merhaba")
    assert len(vec) == EMBEDDING_DIM
    assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-9)


# ---------------- seed ----------------
def test_seed_loads_yaml_idempotently(db):
    items = load_education_yaml()
    assert 25 <= len(items) <= 35
    for it in items:
        assert it["source_url"].startswith("https://")
        assert it["tags"] and it["reviewed"] is False
    assert seed_education(db) == (len(items), 0)
    assert seed_education(db) == (0, 0)
    assert db.scalar(select(func.count()).select_from(EducationContent)) == len(items)
    rows = db.scalars(select(EducationContent)).all()
    assert all(r.embedding_model == LOCAL_MODEL_ID and r.kind == "curated" for r in rows)
    assert len(rows[0].embedding) == EMBEDDING_DIM


def test_seed_updates_changed_and_prunes_removed(db, tmp_path):
    seed_education(db)
    row = db.scalar(select(EducationContent).where(EducationContent.title == "Diyabette ayak bakımı"))
    row.body = "eski metin"
    db.add(EducationContent(title="Artık YAML'da yok", body="x", source_name="y", kind="curated"))
    db.commit()
    created, updated = seed_education(db)
    assert (created, updated) == (0, 1)
    assert "ayna" in row.body
    assert db.scalar(select(EducationContent).where(EducationContent.title == "Artık YAML'da yok")) is None


# ---------------- arama ----------------
def test_search_contract(seeded):
    hits = search_education(seeded, "akşam ilacımı unutuyorum")
    assert hits and all(isinstance(h, EducationHit) for h in hits)
    assert len(hits) <= 3
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)


@pytest.mark.parametrize("query, expected_title", [
    ("akşam ilacımı unutuyorum", "Akşam dozlarını unutmamak için ipuçları"),
    ("dozu kaçırdım ne yapayım", "Bir dozu kaçırdığınızda ne yapmalı"),
    ("şekerim düştü titriyorum", "Düşük kan şekeri (hipoglisemi) belirtileri"),
    ("metformin ishal yapıyor", "Metformin ve mide-bağırsak yakınmaları"),
    ("ayağımda yara var", "Diyabette ayak bakımı"),
    ("tuzu nasıl azaltırım", "Tuz tüketimini azaltmak"),
    ("spor yapmalı mıyım", "Fiziksel aktivite ve hareket"),
    ("ateşim var kusuyorum ilaçlarımı ne yapayım", "Hasta olduğunuz günler (ateş, kusma, ishal)"),
    ("sigarayı bırakmak istiyorum", "Sigarayı bırakmak"),
])
def test_relevant_query_finds_entry(seeded, query, expected_title):
    hits = search_education(seeded, query, k=3)
    assert expected_title in [h.title for h in hits[:2]], [h.title for h in hits]


def test_bp_measurement_query(seeded):
    hits = search_education(seeded, "tansiyonu nasıl ölçerim", k=3)
    titles = [h.title for h in hits]
    assert "Evde tansiyon ölçümü nasıl yapılır" in titles
    top = seeded.get(EducationContent, hits[0].id)
    assert {"hipertansiyon", "olcum"} <= set(top.tags.split(","))


@pytest.mark.parametrize("query", ["bugün hava nasıl", "futbol maçı kaç kaç bitti", "en sevdiğin film hangisi",
                                   "bilgisayarım bozuldu", "merhaba", "nasılsın", ""])
def test_irrelevant_query_returns_empty(seeded, query):
    assert search_education(seeded, query) == []


def test_tags_filter(seeded):
    hits = search_education(seeded, "ölçüm nasıl yapılır", k=5, tags=["diyabet"])
    assert hits
    for h in hits:
        assert "diyabet" in seeded.get(EducationContent, h.id).tags.split(",")
    assert search_education(seeded, "ölçüm nasıl yapılır", tags=["olmayan-etiket"]) == []


def test_lazy_embedding_of_new_rows(seeded):
    seeded.add(EducationContent(title="Göz muayenesi", body="Diyabette yılda bir göz dibi muayenesi önerilir.",
                                source_name="Test", tags="diyabet,goz"))
    seeded.commit()
    hits = search_education(seeded, "göz muayenesi ne zaman", k=1)
    assert hits and hits[0].title == "Göz muayenesi"
    assert ensure_embeddings(seeded) == 0  # artık eksik embedding yok


def test_rows_from_other_model_are_reembedded(seeded):
    row = seeded.scalars(select(EducationContent)).first()
    row.embedding_model = "baska-model"
    seeded.commit()
    assert ensure_embeddings(seeded) == 1
    assert row.embedding_model == LOCAL_MODEL_ID


# ---------------- openFDA ingestion ----------------
def test_ingest_label_summary_and_idempotent(db):
    row = ingest_label_data(db, LABEL_DATA)
    assert row.kind == "openfda" and row.drug_key == "metformin"
    assert row.title == "Metformin — ilaç etiketi bilgisi (openFDA)"
    assert row.body.endswith(DISCLAIMER)
    assert "METFORMIN HYDROCHLORIDE" in row.body and "Lactic acidosis" in row.body
    assert "500 mg" not in row.body  # doz içeren cümleler atılır
    assert "INDICATIONS AND USAGE" not in row.body
    assert row.source_url.startswith("https://api.fda.gov/drug/label.json?search=set_id")
    assert row.embedding_model == LOCAL_MODEL_ID

    again = ingest_label_data(db, LABEL_DATA)
    assert again.id == row.id
    assert db.scalar(select(func.count()).select_from(EducationContent)) == 1
    hits = search_education(db, "metformin uyarıları", k=1)
    assert hits and hits[0].id == row.id


def test_ingest_without_content_returns_none(db):
    assert ingest_label_data(db, {"resolved_generic": "metformin", "query": "x"}) is None


def test_ingest_drug_label_uses_fetch_label_with_mock_transport(db, monkeypatch):
    calls = []
    real_fetch = openfda.fetch_label

    def fake_fetch(name):
        calls.append(name)
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=FAKE_LABEL))) as c:
            return real_fetch(name, client=c)

    monkeypatch.setattr(openfda, "fetch_label", fake_fetch)
    first = ingest_drug_label(db, "Glifor")
    second = ingest_drug_label(db, "metformin")  # aynı etken madde: önbellekten, aynı satır
    assert first.id == second.id and first.drug_key == "metformin"
    assert calls == ["Glifor"]


def test_ingest_endpoint(client, patient, db, monkeypatch):
    _, h = patient
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL_DATA)
    assert client.post("/education/ingest-drug?name=Glifor").status_code == 401
    r = client.post("/education/ingest-drug?name=Glifor", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "openfda" and body["drug_key"] == "metformin" and body["reviewed"] is False
    assert client.post("/education/ingest-drug?name=Glifor", headers=h).json()["id"] == body["id"]


def test_ingest_endpoint_errors(client, patient, monkeypatch):
    _, h = patient

    def not_found(name):
        raise openfda.DrugLabelNotFound(name)

    def down(name):
        raise openfda.OpenFDAUnavailable("x")

    monkeypatch.setattr(openfda, "fetch_label", not_found)
    assert client.post("/education/ingest-drug?name=yokilac", headers=h).status_code == 404
    monkeypatch.setattr(openfda, "fetch_label", down)
    assert client.post("/education/ingest-drug?name=baskailac", headers=h).status_code == 503


def test_drug_label_endpoint_auto_ingests(client, patient, db, monkeypatch):
    _, h = patient
    drugs._cache.clear()
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL_DATA)
    r = client.get("/drugs/label?name=Glifor", headers=h)
    assert r.status_code == 200
    row = db.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin"))
    assert row is not None and row.kind == "openfda"


def test_drug_label_endpoint_survives_ingest_failure(client, patient, monkeypatch):
    _, h = patient
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL_DATA)

    def boom(db, data):
        raise RuntimeError("db hatası")

    monkeypatch.setattr(drugs, "ingest_label_data", boom)
    r = client.get("/drugs/label?name=Glifor", headers=h)
    assert r.status_code == 200 and r.json()["resolved_generic"] == "metformin"


def test_label_cache_in_process(monkeypatch):
    calls = []

    def fake(name):
        calls.append(name)
        return LABEL_DATA

    monkeypatch.setattr(openfda, "fetch_label", fake)
    openfda.get_label_cached("Glifor")
    openfda.get_label_cached("glucophage")  # aynı etken madde
    assert calls == ["Glifor"] and "metformin" in openfda.label_cache


# ---------------- uç noktalar ----------------
def test_education_list_and_search_endpoints(client, patient, seeded):
    _, h = patient
    assert client.get("/education").status_code == 401
    assert client.get("/education/search?q=tansiyon").status_code == 401

    items = client.get("/education", headers=h).json()
    assert len(items) >= 25 and {"reviewed", "kind", "source_url"} <= set(items[0])
    only_foot = client.get("/education?tag=ayak", headers=h).json()
    assert [i["title"] for i in only_foot] == ["Diyabette ayak bakımı"]

    hits = client.get("/education/search", params={"q": "akşam ilacımı unutuyorum", "k": 2}, headers=h).json()
    assert len(hits) <= 2 and hits[0]["title"] == "Akşam dozlarını unutmamak için ipuçları"
    assert {"id", "title", "body", "source_name", "source_url", "score"} == set(hits[0])
    assert client.get("/education/search", params={"q": "futbol maçı skoru"}, headers=h).json() == []
    filtered = client.get("/education/search", params={"q": "ölçüm", "tags": "hipertansiyon"}, headers=h).json()
    assert filtered and all("tansiyon" in f["title"].lower() for f in filtered)
