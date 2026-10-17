"""Eğitim içeriği hekim onayı: onay/ret/geri alma uçları, RAG'in reddedileni kullanmaması, seed ve openFDA
güncellemesinde onay durumunun korunması/sıfırlanması, bakım veren kısıtı."""

import pytest
from sqlalchemy import select

from app.models import EducationContent
from app.seed import seed_education
from app.services import openfda
from app.services.rag import search_education
from app.services.rag.ingest import ingest_label_data
from tests.conftest import register
from tests.test_rag import LABEL_DATA

TITLE = "Akşam dozlarını unutmamak için ipuçları"
QUERY = "akşam ilacımı unutuyorum"


@pytest.fixture()
def seeded(db):
    seed_education(db)
    return db


@pytest.fixture()
def linked(client, patient, clinician):
    """Hastaya atanmış hekim: (hasta başlığı, hekim başlığı)."""
    client.put("/v1/patients/me/clinician", headers=patient[1], json={"clinician_email": "dr.mehmet@example.com"})
    return patient[1], clinician[1]


def _row(db, title=TITLE) -> EducationContent:
    return db.scalar(select(EducationContent).where(EducationContent.title == title))


def test_new_content_is_pending(seeded):
    rows = seeded.scalars(select(EducationContent)).all()
    assert rows and {r.review_status for r in rows} == {"pending"} and not any(r.reviewed for r in rows)


def test_approve_and_reject_flow(client, seeded, linked):
    _, ch = linked
    row = _row(seeded)
    r = client.post(f"/v1/education/{row.id}/approve", headers=ch, json={"note": "uygun"})
    assert r.status_code == 200
    body = r.json()
    assert body["review_status"] == "approved" and body["reviewed"] is True
    assert body["reviewer_name"] == "Dr.Mehmet" and body["review_note"] == "uygun" and body["reviewed_at"]

    r = client.post(f"/v1/education/{row.id}/reject", headers=ch, json={"note": "Kaynak güncel değil"})
    body = r.json()
    assert body["review_status"] == "rejected" and body["reviewed"] is False
    assert body["review_note"] == "Kaynak güncel değil"

    r = client.post(f"/v1/education/{row.id}/reset", headers=ch)
    assert r.json()["review_status"] == "pending" and r.json()["reviewed_at"] is None


def test_approve_without_body(client, seeded, linked):
    _, ch = linked
    r = client.post(f"/v1/education/{_row(seeded).id}/approve", headers=ch)
    assert r.status_code == 200 and r.json()["review_note"] is None


def test_reject_requires_note(client, seeded, linked):
    _, ch = linked
    row = _row(seeded)
    assert client.post(f"/v1/education/{row.id}/reject", headers=ch, json={}).status_code == 422
    assert client.post(f"/v1/education/{row.id}/reject", headers=ch, json={"note": " a "}).status_code == 422
    assert _row(seeded).review_status == "pending"


def test_only_clinician_reviews(client, seeded, patient):
    row = _row(seeded)
    _, cg = register(client, "yakin@example.com", role="caregiver")
    for h in (patient[1], cg):
        assert client.post(f"/v1/education/{row.id}/approve", headers=h).status_code == 403
        assert client.post(f"/v1/education/{row.id}/reject", headers=h, json={"note": "hayır"}).status_code == 403
    assert client.post(f"/v1/education/{row.id}/approve").status_code == 401


def test_unknown_content_404(client, seeded, linked):
    _, ch = linked
    assert client.post("/v1/education/99999/approve", headers=ch).status_code == 404


def test_list_filter_by_status(client, seeded, linked):
    ph, ch = linked
    row = _row(seeded)
    client.post(f"/v1/education/{row.id}/reject", headers=ch, json={"note": "eski bilgi"})
    rejected = client.get("/v1/education?review_status=rejected", headers=ph).json()
    assert [r["id"] for r in rejected] == [row.id]
    pending = client.get("/v1/education?review_status=pending", headers=ph).json()
    assert row.id not in [r["id"] for r in pending] and pending
    assert client.get("/v1/education?review_status=bilinmez", headers=ph).status_code == 422


def test_rejected_content_not_used_by_rag(client, seeded, linked):
    ph, ch = linked
    assert TITLE in [h.title for h in search_education(seeded, QUERY, k=3)]
    row = _row(seeded)
    client.post(f"/v1/education/{row.id}/reject", headers=ch, json={"note": "yanlış yönlendiriyor"})
    assert TITLE not in [h.title for h in search_education(seeded, QUERY, k=5)]
    # koç da kaynak gösteremez
    reply = client.post("/v1/coach/chat", headers=ph, json={"message": QUERY}).json()
    assert not any(s["title"].startswith(TITLE) for s in reply["sources"])
    # karar geri alınınca yeniden kullanılabilir
    client.post(f"/v1/education/{row.id}/reset", headers=ch)
    assert TITLE in [h.title for h in search_education(seeded, QUERY, k=3)]


def test_reseed_keeps_clinician_decision(client, seeded, linked):
    _, ch = linked
    row = _row(seeded)
    client.post(f"/v1/education/{row.id}/approve", headers=ch)
    seed_education(seeded)
    assert _row(seeded).review_status == "approved"


def test_reseed_with_changed_text_requires_new_review(seeded, tmp_path, client, linked):
    _, ch = linked
    row = _row(seeded)
    client.post(f"/v1/education/{row.id}/approve", headers=ch)
    yaml_path = tmp_path / "edu.yaml"
    yaml_path.write_text(
        f'- title: "{TITLE}"\n  body: "Yeni metin: alarm kurun."\n  source_name: "Test"\n'
        '  source_url: "https://example.org"\n  tags: "uyum"\n', encoding="utf-8")
    seed_education(seeded, yaml_path)
    assert _row(seeded).review_status == "pending" and _row(seeded).reviewed_by_user_id is None


def test_openfda_update_resets_review(seeded, client, linked):
    _, ch = linked
    row = ingest_label_data(seeded, LABEL_DATA)
    assert row.review_status == "pending"
    client.post(f"/v1/education/{row.id}/reject", headers=ch, json={"note": "ABD etiketi yetersiz"})
    ingest_label_data(seeded, LABEL_DATA)  # aynı metin: ret korunur
    assert seeded.get(EducationContent, row.id).review_status == "rejected"
    changed = {**LABEL_DATA, "drug_interactions": "Alcohol increases the risk of lactic acidosis."}
    ingest_label_data(seeded, changed)  # metin değişti: yeniden onay
    assert seeded.get(EducationContent, row.id).review_status == "pending"


def test_ingest_drug_closed_to_caregiver(client, patient, seeded, monkeypatch):
    monkeypatch.setattr(openfda, "fetch_label", lambda name: dict(LABEL_DATA))
    openfda.label_cache.clear()
    _, cg = register(client, "yakin@example.com", role="caregiver")
    assert client.post("/v1/education/ingest-drug?name=Glifor", headers=cg).status_code == 403
    assert seeded.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin")) is None
    r = client.post("/v1/education/ingest-drug?name=Glifor", headers=patient[1])
    assert r.status_code == 201 and r.json()["review_status"] == "pending"
