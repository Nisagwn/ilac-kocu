"""İlaç kataloğu: kürate TR seed, arama, openFDA senkronu (httpx mock), ilaç-katalog bağı, bakım veren ingest kısıtı."""

import httpx
import pytest
from sqlalchemy import select

from app.models import DrugCatalog, EducationContent, Medication, PatientCaregiver
from app.routers import drugs
from app.services import drug_catalog as dc
from app.services import openfda
from tests.conftest import register

LABEL = openfda.parse_label("Glifor", "metformin", {
    "set_id": "abc",
    "openfda": {"generic_name": ["METFORMIN HYDROCHLORIDE"], "substance_name": ["METFORMIN HYDROCHLORIDE"]},
    "indications_and_usage": ["Metformin is indicated as an adjunct to diet and exercise..."],
    "warnings": ["Lactic acidosis..."],
})


@pytest.fixture(autouse=True)
def _clear_label_cache():
    openfda.label_cache.clear()
    yield
    openfda.label_cache.clear()


def _search(client, h, q, **params):
    r = client.get("/drugs/search", params={"q": q, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- normalizasyon / çözümleme
@pytest.mark.parametrize("term,expected", [
    ("SITAGLIPTIN AND METFORMIN HYDROCHLORIDE", "metformin + sitagliptin"),
    ("EMPAGLIFLOZIN, LINAGLIPTIN, METFORMIN HYDROCHLORIDE", "empagliflozin + linagliptin + metformin"),
    ("Metformin hydrochloride ER", "metformin"),
    ("TRANDOLAPRIL TABLETS", "trandolapril"),
    ("amlodipine besylate and valsartan", "amlodipine + valsartan"),
    ("INSULIN GLARGINE", "insulin glargine"),
])
def test_canonical_generic(term, expected):
    assert dc.canonical_generic(term) == expected


@pytest.mark.parametrize("name,expected", [
    ("Glifor", "metformin"),
    ("Glifor 1000 mg", "metformin"),
    ("JANUMET", "metformin + sitagliptin"),
    ("Galvus", "vildagliptin"),
    ("Galvus Met", "metformin + vildagliptin"),
    ("Asetilsalisilik asit", "aspirin"),
    ("bilinmeyenilac", "bilinmeyenilac"),
])
def test_resolve_generic_from_catalog(name, expected):
    assert openfda.resolve_generic(name) == expected


def test_combination_label_query_uses_and():
    seen = {}

    def handler(request: httpx.Request):
        seen["search"] = request.url.params["search"]
        return httpx.Response(200, json={"results": [{"openfda": {"generic_name": ["SITAGLIPTIN AND METFORMIN"]},
                                                      "indications_and_usage": ["x"]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        data = openfda.fetch_label("Janumet", client=c)
    assert seen["search"] == 'openfda.generic_name:"metformin" AND openfda.generic_name:"sitagliptin"'
    assert data["resolved_generic"] == "metformin + sitagliptin"


# ---------------------------------------------------------------- seed
def test_seed_catalog_idempotent(db):
    created, updated = dc.seed_catalog(db)
    assert created >= 60 and updated == 0
    assert dc.seed_catalog(db) == (0, 0)
    met = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "metformin"))
    assert met.source == "tr_curated" and "Glifor" in met.tr_brand_names and met.openfda_available is False
    jan = db.scalar(select(DrugCatalog).where(DrugCatalog.generic_name == "metformin + sitagliptin"))
    assert "Janumet" in jan.brand_names
    brands = sum(len(r.tr_brand_names) for r in db.scalars(select(DrugCatalog)))
    assert brands >= 60


def test_seed_links_existing_unverified_medications(client, patient, db):
    user, h = patient
    # Katalog yokken eklenen ilaç doğrulanamaz
    r = client.post(f"/patients/{user['patient_id']}/medications", json={"name": "Glifor"}, headers=h)
    assert r.json()["verified"] is False
    dc.seed_catalog(db)
    med = db.get(Medication, r.json()["id"])
    db.refresh(med)
    assert med.verified is True and med.active_ingredient == "metformin"


# ---------------------------------------------------------------- arama
def test_search_by_tr_brand(client, patient):
    _, h = patient
    hits = _search(client, h, "glif")
    assert hits[0]["generic_name"] == "metformin"
    assert hits[0]["matched_brand"] == "Glifor" and hits[0]["source"] == "tr_curated"
    assert set(hits[0]) >= {"id", "display_name", "generic_name", "matched_brand", "pharm_class", "source"}


def test_search_by_generic_and_combination(client, patient):
    _, h = patient
    hits = _search(client, h, "metformin")
    assert hits[0]["generic_name"] == "metformin" and hits[0]["matched_brand"] is None  # tam eşleşme önce
    generics = {x["generic_name"] for x in hits}
    assert "metformin + sitagliptin" in generics  # içerir araması kombinasyonları da bulur


def test_search_turkish_chars_and_case(client, patient):
    _, h = patient
    assert _search(client, h, "GLİFOR")[0]["generic_name"] == "metformin"
    assert _search(client, h, "insülin glarjin")[0]["generic_name"] == "insulin glargine"
    assert _search(client, h, "asetilsalisilik")[0]["generic_name"] == "aspirin"
    assert _search(client, h, "galvus met")[0]["generic_name"] == "metformin + vildagliptin"


def test_search_limit_validation_and_auth(client, patient):
    _, h = patient
    assert len(_search(client, h, "an", limit=3)) <= 3
    assert client.get("/drugs/search", params={"q": "g"}, headers=h).status_code == 422
    assert client.get("/drugs/search", params={"q": "glifor"}).status_code == 401
    assert _search(client, h, "zzqqxx") == []


def test_search_available_to_all_roles(client, patient, clinician):
    _, hc = clinician
    cg, hg = register(client, "yakin@example.com", role="caregiver")
    assert _search(client, hc, "concor")[0]["generic_name"] == "bisoprolol"
    assert _search(client, hg, "concor")[0]["generic_name"] == "bisoprolol"


def test_search_rate_limited(client, patient, monkeypatch):
    _, h = patient
    monkeypatch.setattr(drugs, "SEARCH_LIMIT_PER_MINUTE", 2)
    for _ in range(2):
        _search(client, h, "glifor")
    assert client.get("/drugs/search", params={"q": "glifor"}, headers=h).status_code == 429


# ---------------------------------------------------------------- ilaç <-> katalog
def test_create_medication_with_catalog_id(client, patient, db):
    user, h = patient
    entry = _search(client, h, "janumet")[0]
    r = client.post(f"/patients/{user['patient_id']}/medications", headers=h, json={
        "name": "Janumet", "catalog_id": entry["id"], "active_ingredient": "yanlış değer",
        "schedules": [{"time_of_day": "08:00"}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verified"] is True and body["catalog_id"] == entry["id"]
    assert body["active_ingredient"] == "metformin + sitagliptin"  # istemci değeri değil, katalog


def test_create_medication_unknown_catalog_id(client, patient):
    user, h = patient
    r = client.post(f"/patients/{user['patient_id']}/medications", json={"name": "X", "catalog_id": 99999}, headers=h)
    assert r.status_code == 422


def test_create_unverified_medication(client, patient):
    user, h = patient
    _search(client, h, "glifor")  # katalog yüklensin
    r = client.post(f"/patients/{user['patient_id']}/medications", headers=h,
                    json={"name": "Bitkisel karışım", "active_ingredient": "bilinmiyor"})
    assert r.status_code == 201
    body = r.json()
    assert body["verified"] is False and body["catalog_id"] is None and body["active_ingredient"] == "bilinmiyor"


def test_exact_name_auto_links(client, patient):
    user, h = patient
    _search(client, h, "glifor")
    body = client.post(f"/patients/{user['patient_id']}/medications", json={"name": "glifor"}, headers=h).json()
    assert body["verified"] is True and body["active_ingredient"] == "metformin"


def test_update_medication_catalog_link(client, patient):
    user, h = patient
    pid = user["patient_id"]
    concor = _search(client, h, "concor")[0]
    med = client.post(f"/patients/{pid}/medications", json={"name": "Tansiyon ilacı"}, headers=h).json()
    assert med["verified"] is False
    r = client.patch(f"/patients/{pid}/medications/{med['id']}", json={"name": "Concor", "catalog_id": concor["id"]},
                     headers=h)
    assert r.json()["verified"] is True and r.json()["active_ingredient"] == "bisoprolol"
    # Doğrulanmış ilacın etken maddesi elle değiştirilemez
    r = client.patch(f"/patients/{pid}/medications/{med['id']}", json={"active_ingredient": "x"}, headers=h)
    assert r.json()["active_ingredient"] == "bisoprolol"
    # "Listede yok, yine de ekle": bağ kaldırılır
    r = client.patch(f"/patients/{pid}/medications/{med['id']}", json={"name": "Başka", "catalog_id": None},
                     headers=h)
    assert r.json()["verified"] is False and r.json()["catalog_id"] is None
    # Yalnızca aktiflik değişikliği doğrulamayı etkilemez
    client.patch(f"/patients/{pid}/medications/{med['id']}", json={"catalog_id": concor["id"]}, headers=h)
    assert client.patch(f"/patients/{pid}/medications/{med['id']}", json={"active": True},
                        headers=h).json()["verified"] is True


def test_clinician_sees_verified_flag(client, patient, clinician, db):
    from app.models import Clinician, Patient
    user, h = patient
    _, hc = clinician
    p = db.get(Patient, user["patient_id"])
    p.clinician_id = db.scalar(select(Clinician.id))
    db.commit()
    client.post(f"/patients/{p.id}/medications", json={"name": "Bilinmeyen"}, headers=h)
    meds = client.get(f"/patients/{p.id}/medications", headers=hc).json()
    assert meds[0]["verified"] is False and meds[0]["catalog_id"] is None


# ---------------------------------------------------------------- bakım veren ingest kısıtı
def test_caregiver_label_does_not_ingest(client, patient, db, monkeypatch):
    user, _ = patient
    cg, hg = register(client, "yakin@example.com", role="caregiver")
    db.add(PatientCaregiver(patient_id=user["patient_id"], caregiver_user_id=cg["id"]))
    db.commit()
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL)
    r = client.get("/drugs/label?name=Glifor", headers=hg)
    assert r.status_code == 200 and r.json()["resolved_generic"] == "metformin"
    assert db.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin")) is None


def test_patient_label_still_ingests(client, patient, db, monkeypatch):
    _, h = patient
    monkeypatch.setattr(openfda, "fetch_label", lambda name: LABEL)
    assert client.get("/drugs/label?name=Glifor", headers=h).status_code == 200
    assert db.scalar(select(EducationContent).where(EducationContent.drug_key == "metformin")) is not None


# ---------------------------------------------------------------- openFDA senkronu (ağsız)
def _count_response(terms):
    return httpx.Response(200, json={"meta": {}, "results": [{"term": t, "count": 1} for t in terms]})


def _fake_openfda(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    search, count = p["search"], p["count"]
    if count == "openfda.generic_name.exact":
        if "Dipeptidyl Peptidase 4" in search:
            return _count_response(["SITAGLIPTIN AND METFORMIN HYDROCHLORIDE", "SITAGLIPTIN", "ALOGLIPTIN BENZOATE"])
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}})
    if count == "openfda.brand_name.exact":
        if search == 'openfda.generic_name:"metformin"':
            return _count_response(["METFORMIN HYDROCHLORIDE", "GLUCOPHAGE", "FORTAMET", "JANUMET"])
        if search == 'openfda.generic_name:"metformin" AND openfda.generic_name:"sitagliptin"':
            return _count_response(["JANUMET", "Janumet XR"])
        if search == 'openfda.generic_name:"alogliptin"':
            return _count_response(["NESINA"])
        return httpx.Response(404, json={})
    return httpx.Response(400, json={})


def test_sync_from_openfda_with_mock(db):
    sleeps = []
    with httpx.Client(transport=httpx.MockTransport(_fake_openfda)) as c:
        report = dc.sync_from_openfda(db, client=c, sleeper=sleeps.append, sleep=0.01,
                                      classes=[("Dipeptidyl Peptidase 4 Inhibitor [EPC]", "DPP-4 inhibitörü"),
                                               ("Biguanide [EPC]", "Biguanid")])
    assert report.requests > 2 and sleeps  # istekler arası bekleme uygulandı
    rows = {r.generic_name: r for r in db.scalars(select(DrugCatalog))}
    met = rows["metformin"]
    assert met.openfda_available is True
    assert "Fortamet" in met.brand_names and "Glifor" in met.brand_names
    assert "Janumet" not in met.brand_names  # kombinasyona bağlandı
    assert "Metformin Hydrochloride" not in met.brand_names  # marka alanındaki etken madde adı elendi
    assert "Janumet XR" in rows["metformin + sitagliptin"].brand_names
    new = rows["alogliptin"]
    assert new.source == "openfda" and new.pharm_class == "DPP-4 inhibitörü" and "Nesina" in new.brand_names
    assert rows["bisoprolol"].openfda_available is False and "bisoprolol" in report.unavailable
    # ABD markasıyla da aranabilir
    assert dc.search_catalog(db, "fortamet")[0].entry.generic_name == "metformin"
    # TR seed tekrar çalışınca ABD markaları korunur
    dc.seed_catalog(db)
    db.refresh(met)
    assert "Fortamet" in met.brand_names and met.openfda_available is True


def test_sync_network_error_is_meaningful_and_rolls_back(db):
    def down(request):
        raise httpx.ConnectError("ağ yok")

    with httpx.Client(transport=httpx.MockTransport(down)) as c:
        with pytest.raises(dc.CatalogSyncError, match="ulaşılamadı"):
            dc.sync_from_openfda(db, client=c, sleeper=lambda s: None)


def test_sync_rate_limit_retries_then_fails(db):
    calls = []

    def limited(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"}, json={})

    waits = []
    with httpx.Client(transport=httpx.MockTransport(limited)) as c:
        with pytest.raises(dc.CatalogSyncError, match="429"):
            dc.sync_from_openfda(db, client=c, sleeper=waits.append, sleep=0)
    assert len(calls) == 3 and waits == [1.0, 1.0]


def test_sync_cli_reports_error(monkeypatch, db, capsys):
    from app.scripts import sync_drug_catalog as cli

    def boom(*a, **k):
        raise dc.CatalogSyncError("openFDA'ya ulaşılamadı")

    monkeypatch.setattr(cli, "sync_from_openfda", boom)
    assert cli.main(["--no-brands"], session_factory=lambda: db) == 2
    assert "tamamlanamadı" in capsys.readouterr().err
