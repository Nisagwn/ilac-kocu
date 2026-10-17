"""İlaç kataloğu: kürate liste, openFDA senkronu, arama, ilaç doğrulama ve bakım veren kısıtı."""

import httpx
import pytest
from sqlalchemy import func, select

from app.models import DrugCatalog, EducationContent
from app.routers import drugs
from app.services import drug_catalog, openfda
from tests.conftest import register

SCHED = [{"time_of_day": "08:00"}]


def _hdr(patient):
    user, h = patient
    return user["patient_id"], h


# ---------------- kürate liste ----------------
def test_curated_yaml_is_valid_and_feeds_resolver():
    items = drug_catalog.load_curated_yaml()
    assert len(items) >= 40
    assert {it["group"] for it in items} <= set(drug_catalog.GROUPS)
    assert openfda.resolve_generic("Glifor") == "metformin"
    assert openfda.resolve_generic("GLİFOR 1000 mg") == "metformin"
    assert openfda.resolve_generic("Metformin HCl") == "metformin"
    assert openfda.resolve_generic("Lantus SoloStar") == "insulin glargine"
    assert openfda.resolve_generic("Forziga") == "dapagliflozin"
    assert openfda.resolve_generic("bilinmeyenilac") == "bilinmeyenilac"
    assert openfda.resolve_generic("   ") == ""


@pytest.mark.parametrize("raw, expected", [
    ("METFORMIN HYDROCHLORIDE", "metformin"),
    ("Amlodipine Besylate", "amlodipine"),
    ("olmesartan medoxomil", "olmesartan"),
    ("Metoprolol Succinate", "metoprolol"),
    ("insulin glargine", "insulin glargine"),
    ("Glifor 1000 mg tablet", "glifor"),
])
def test_canonical_generic(raw, expected):
    assert drug_catalog.canonical_generic(raw) == expected


def test_upsert_curated_is_idempotent(db):
    created, updated = drug_catalog.upsert_curated(db)
    assert created == len(drug_catalog.load_curated_yaml()) and updated == 0
    assert drug_catalog.upsert_curated(db) == (0, 0)
    met = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "metformin"))
    assert "Glifor" in met.brand_names and met.indication_group == "diabetes" and met.source == "curated"


def test_duplicate_generic_in_yaml_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("- {generic: metformin, brands: [A]}\n- {generic: Metformin HCl, brands: [B]}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="iki kez"):
        drug_catalog.load_curated_yaml(p)


# ---------------- openFDA senkronu ----------------
def test_sync_openfda_with_mock_transport(db):
    drug_catalog.upsert_curated(db)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["search"])
        assert request.url.params["count"] == "openfda.generic_name.exact"
        if "Biguanide" in request.url.params["search"]:
            return httpx.Response(200, json={"results": [
                {"term": "METFORMIN HYDROCHLORIDE", "count": 120},
                {"term": "METFORMIN HYDROCHLORIDE AND SITAGLIPTIN", "count": 9},  # kombinasyon: atlanır
            ]})
        if "Receptor Blocker" in request.url.params["search"]:
            return httpx.Response(200, json={"results": [{"term": "AZILSARTAN KAMEDOXOMIL", "count": 3}]})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        created, updated = drug_catalog.sync_openfda(db, client=c, classes={
            "Biguanide [EPC]": "diabetes", "Angiotensin 2 Receptor Blocker [EPC]": "hypertension",
            "Unknown [EPC]": "other"})
    assert len(calls) == 3
    assert (created, updated) == (1, 1)
    met = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "metformin"))
    assert met.label_count == 120 and met.source == "curated" and "Glifor" in met.brand_names
    new = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "azilsartan kamedoxomil"))
    assert new.source == "openfda" and new.indication_group == "hypertension" and new.brand_names == []
    assert db.scalar(select(func.count()).select_from(DrugCatalog).where(
        DrugCatalog.generic_name.like("%sitagliptin%and%"))) == 0


def test_sync_openfda_raises_on_server_error(db):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
        with pytest.raises(httpx.HTTPStatusError):
            drug_catalog.sync_openfda(db, client=c, classes={"Biguanide [EPC]": "diabetes"})


# ---------------- arama ----------------
def test_search_by_brand_turkish_insensitive(client, patient):
    _, h = _hdr(patient)
    r = client.get("/drugs/search?q=glİ", headers=h)
    assert r.status_code == 200
    hits = r.json()
    assert hits[0]["brand"] == "Glifor" and hits[0]["generic_name"] == "metformin"
    assert hits[0]["display_name"] == "Glifor (metformin)"


def test_search_by_generic_and_ranking(client, patient):
    _, h = _hdr(patient)
    hits = client.get("/drugs/search?q=amlo", headers=h).json()
    assert hits[0]["generic_name"] == "amlodipine" and hits[0]["brand"] is None
    hits = client.get("/drugs/search?q=glargine", headers=h).json()
    assert hits[0]["generic_name"] == "insulin glargine"


def test_search_validation_and_auth(client, patient):
    _, h = _hdr(patient)
    assert client.get("/drugs/search?q=g", headers=h).status_code == 422
    assert client.get("/drugs/search?q=glifor").status_code == 401
    assert client.get("/drugs/search?q=zzzz", headers=h).json() == []
    # LIKE jokerleri metin olarak aranır
    assert client.get("/drugs/search?q=%25%25", headers=h).json() == []
    assert client.get("/drugs/search?q=__", headers=h).json() == []


def test_search_limit(client, patient):
    _, h = _hdr(patient)
    assert len(client.get("/drugs/search?q=in&limit=3", headers=h).json()) == 3


# ---------------- ilaç ekleme / doğrulama ----------------
def test_medication_from_catalog_is_verified(client, patient, db):
    pid, h = _hdr(patient)
    hit = client.get("/drugs/search?q=jardi", headers=h).json()[0]
    r = client.post(f"/patients/{pid}/medications", headers=h,
                    json={"name": "Jardiance", "catalog_id": hit["id"], "schedules": SCHED})
    assert r.status_code == 201, r.text
    med = r.json()
    assert med["verified"] is True and med["catalog_id"] == hit["id"]
    assert med["active_ingredient"] == "empagliflozin"  # boş bırakılınca katalogdan doldurulur


def test_medication_exact_name_match_is_verified(client, patient):
    pid, h = _hdr(patient)
    med = client.post(f"/patients/{pid}/medications", headers=h,
                      json={"name": "Glifor 1000", "active_ingredient": "metformin", "schedules": SCHED}).json()
    assert med["verified"] is True and med["catalog_id"] is not None


def test_unknown_medication_not_verified(client, patient):
    pid, h = _hdr(patient)
    med = client.post(f"/patients/{pid}/medications", headers=h,
                      json={"name": "Bitkisel karışım", "schedules": SCHED}).json()
    assert med["verified"] is False and med["catalog_id"] is None and med["active_ingredient"] is None


def test_invalid_catalog_id_rejected(client, patient):
    pid, h = _hdr(patient)
    r = client.post(f"/patients/{pid}/medications", headers=h,
                    json={"name": "Glifor", "catalog_id": 99999, "schedules": SCHED})
    assert r.status_code == 422


def test_rename_recomputes_verification(client, patient):
    pid, h = _hdr(patient)
    med = client.post(f"/patients/{pid}/medications", headers=h, json={"name": "Glifor", "schedules": SCHED}).json()
    assert med["verified"] is True
    r = client.patch(f"/patients/{pid}/medications/{med['id']}", headers=h, json={"name": "Benim ilacım"})
    assert r.json()["verified"] is False and r.json()["catalog_id"] is None
    r = client.patch(f"/patients/{pid}/medications/{med['id']}", headers=h, json={"notes": "akşam"})
    assert r.json()["verified"] is False  # ad değişmedikçe yeniden hesaplanmaz


def test_clinician_sees_verified_flag(client, patient, clinician):
    pid, h = _hdr(patient)
    client.post(f"/patients/{pid}/medications", headers=h, json={"name": "Bitkisel karışım", "schedules": SCHED})
    client.put("/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    meds = client.get(f"/patients/{pid}/medications", headers=clinician[1]).json()
    assert [m["verified"] for m in meds] == [False]


# ---------------- bakım veren ----------------
LABEL = {"resolved_generic": "metformin", "query": "Glifor", "active_ingredients": ["METFORMIN"],
         "indications": "Metformin is indicated to improve glycemic control.", "warnings": "Lactic acidosis.",
         "drug_interactions": None}


def test_caregiver_label_lookup_does_not_write_education(client, patient, db, monkeypatch):
    _, h = _hdr(patient)
    drugs._cache.clear()
    monkeypatch.setattr(openfda, "fetch_label", lambda name: dict(LABEL))
    _, ch = register(client, "kizi@example.com", role="caregiver")

    assert client.get("/drugs/label?name=Glifor", headers=ch).status_code == 200
    assert db.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin")) is None

    assert client.get("/drugs/label?name=Glifor", headers=h).status_code == 200
    assert db.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin")) is not None
