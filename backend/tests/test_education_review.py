"""Eğitim içeriği hekim onayı: onay/ret uçları, yetki, RAG politikası, seed ve openFDA güncellemesi."""

from sqlalchemy import select

from app.models import EducationContent
from app.seed import seed_education
from app.services import openfda
from app.services.rag import search_education
from app.services.rag.ingest import ingest_label_data
from app.services.rag.search import PENDING_REVIEW_NOTE
from tests.conftest import register
from tests.test_rag import LABEL_DATA


def _openfda_row(db) -> EducationContent:
    openfda.label_cache.clear()
    return ingest_label_data(db, LABEL_DATA)


def test_pending_list_and_approve(client, db, clinician):
    user, ch = clinician
    row = _openfda_row(db)
    assert row.review_status == "pending" and row.reviewed is False

    pending = client.get("/education?reviewed=false", headers=ch).json()
    assert [r["id"] for r in pending] == [row.id]
    assert pending[0]["review_status"] == "pending" and pending[0]["reviewed_by"] is None

    r = client.post(f"/education/{row.id}/review", headers=ch)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "approved" and body["reviewed"] is True
    assert body["reviewed_by"] == user["id"] and body["reviewed_at"] is not None
    assert client.get("/education?reviewed=false", headers=ch).json() == []
    assert [r["id"] for r in client.get("/education?reviewed=true", headers=ch).json()] == [row.id]


def test_reject_hides_from_rag_and_non_clinicians(client, db, clinician, patient):
    _, ch = clinician
    _, ph = patient
    row = _openfda_row(db)
    assert search_education(db, "metformin uyarıları", k=1)[0].id == row.id

    r = client.post(f"/education/{row.id}/reject", headers=ch)
    assert r.status_code == 200 and r.json()["review_status"] == "rejected" and r.json()["reviewed"] is False
    db.expire_all()
    assert all(h.id != row.id for h in search_education(db, "metformin uyarıları", k=5))
    assert client.get("/education/search", params={"q": "metformin uyarıları"}, headers=ph).json() == []

    # Reddedilen içerik hastaya listelenmez, hekim review_status ile görebilir
    assert all(i["id"] != row.id for i in client.get("/education", headers=ph).json())
    rejected = client.get("/education?review_status=rejected", headers=ch).json()
    assert [i["id"] for i in rejected] == [row.id]

    # Karar geri alınabilir: onaylanınca yeniden aranabilir
    assert client.post(f"/education/{row.id}/review", headers=ch).status_code == 200
    db.expire_all()
    assert search_education(db, "metformin uyarıları", k=1)[0].id == row.id


def test_pending_openfda_is_marked_in_sources(client, db, clinician):
    _, ch = clinician
    row = _openfda_row(db)
    hit = search_education(db, "metformin uyarıları", k=1)[0]
    assert hit.id == row.id and PENDING_REVIEW_NOTE in hit.title
    client.post(f"/education/{row.id}/review", headers=ch)
    db.expire_all()
    hit = search_education(db, "metformin uyarıları", k=1)[0]
    assert hit.title == row.title and PENDING_REVIEW_NOTE not in hit.title


def test_curated_pending_not_marked(db):
    seed_education(db)
    hits = search_education(db, "akşam ilacımı unutuyorum", k=1)
    assert hits and PENDING_REVIEW_NOTE not in hits[0].title


def test_review_requires_clinician(client, db, patient):
    user, ph = patient
    row = _openfda_row(db)
    _, cg = register(client, "yakin@example.com", role="caregiver")
    for h in (ph, cg):
        assert client.post(f"/education/{row.id}/review", headers=h).status_code == 403
        assert client.post(f"/education/{row.id}/reject", headers=h).status_code == 403
    assert client.post(f"/education/{row.id}/review").status_code == 401
    db.expire_all()
    assert db.get(EducationContent, row.id).review_status == "pending"


def test_review_unknown_content_404(client, clinician):
    _, ch = clinician
    assert client.post("/education/99999/review", headers=ch).status_code == 404
    assert client.post("/education/99999/reject", headers=ch).status_code == 404


def test_ingest_drug_roles(client, clinician, monkeypatch):
    openfda.label_cache.clear()
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL_DATA)
    _, ch = clinician
    _, cg = register(client, "yakin@example.com", role="caregiver")
    assert client.post("/education/ingest-drug?name=Glifor", headers=cg).status_code == 403
    r = client.post("/education/ingest-drug?name=Glifor", headers=ch)
    assert r.status_code == 201 and r.json()["review_status"] == "pending"


def test_label_change_resets_approval(client, db, clinician):
    _, ch = clinician
    row = _openfda_row(db)
    client.post(f"/education/{row.id}/review", headers=ch)
    db.expire_all()
    # Aynı metin tekrar alınırsa onay korunur
    same = ingest_label_data(db, LABEL_DATA)
    assert same.review_status == "approved"
    changed = ingest_label_data(db, {**LABEL_DATA, "warnings": "Yeni bir uyarı metni eklendi."})
    assert changed.id == row.id and changed.review_status == "pending" and changed.reviewed_by is None


def test_seed_preserves_clinician_decision(client, db, clinician):
    _, ch = clinician
    seed_education(db)
    row = db.scalars(select(EducationContent).where(EducationContent.kind == "curated")).first()
    client.post(f"/education/{row.id}/reject", headers=ch)
    seed_education(db)  # YAML'da reviewed: false olsa da hekim kararı ezilmez
    db.expire_all()
    assert db.get(EducationContent, row.id).review_status == "rejected"
