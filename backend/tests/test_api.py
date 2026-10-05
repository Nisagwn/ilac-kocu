from datetime import date, timedelta

import httpx
import pytest

from app.models import MedicationSchedule
from app.routers import drugs
from app.routers.doses import local_today
from app.services import openfda
from tests.conftest import register

METFORMIN = {"name": "Glifor", "active_ingredient": "metformin", "dose_text": "1000 mg",
             "schedules": [{"time_of_day": "08:00"}, {"time_of_day": "20:00"}]}


def add_med(client, pid, h, body=METFORMIN):
    r = client.post(f"/patients/{pid}/medications", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- Auth ----------------
def test_register_login_me(client, patient):
    user, h = patient
    me = client.get("/auth/me", headers=h).json()
    assert me["role"] == "patient" and me["patient_id"] == user["patient_id"]


def test_patient_requires_consent(client):
    r = client.post("/auth/register", json={"email": "x@example.com", "password": "sifre1234",
                                            "full_name": "Xx", "role": "patient", "consent": False})
    assert r.status_code == 422


def test_duplicate_email(client, patient):
    r = client.post("/auth/register", json={"email": "AYSE@example.com", "password": "sifre1234",
                                            "full_name": "Ayşe", "consent": True})
    assert r.status_code == 409


def test_wrong_password_and_no_token(client, patient):
    assert client.post("/auth/login", data={"username": "ayse@example.com", "password": "yanlis123"}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer bozuk"}).status_code == 401


# ---------------- Medications & doses ----------------
def test_medication_crud(client, patient):
    user, h = patient
    pid = user["patient_id"]
    med = add_med(client, pid, h)
    assert len(med["schedules"]) == 2

    r = client.patch(f"/patients/{pid}/medications/{med['id']}", headers=h,
                     json={"schedules": [{"time_of_day": "08:00"}]})
    assert len(r.json()["schedules"]) == 1

    assert client.delete(f"/patients/{pid}/medications/{med['id']}", headers=h).status_code == 204
    assert client.get(f"/patients/{pid}/medications", headers=h).json() == []
    assert len(client.get(f"/patients/{pid}/medications?include_inactive=true", headers=h).json()) == 1


def test_dose_logging_and_adherence(client, patient, db):
    user, h = patient
    pid = user["patient_id"]
    med = add_med(client, pid, h)
    today = local_today()
    # zamanlamayı 7 gün öncesinden başlat ki geçmiş günler planlı sayılsın
    for s in db.query(MedicationSchedule).all():
        s.start_date = today - timedelta(days=10)
    db.commit()

    sid = med["schedules"][0]["id"]
    for i in range(1, 7):
        body = {"schedule_id": sid, "scheduled_date": str(today - timedelta(days=i)), "status": "taken"}
        assert client.put(f"/patients/{pid}/doses", json=body, headers=h).status_code == 200

    # idempotent güncelleme
    body = {"schedule_id": sid, "scheduled_date": str(today - timedelta(days=1)), "status": "skipped", "reason": "unuttum"}
    assert client.put(f"/patients/{pid}/doses", json=body, headers=h).json()["status"] == "skipped"
    assert len(client.get(f"/patients/{pid}/doses", headers=h).json()) == 6

    today_list = client.get(f"/patients/{pid}/doses/today", headers=h).json()
    assert len(today_list) == 2 and today_list[0]["medication_name"] == "Glifor"

    adh = client.get(f"/patients/{pid}/adherence?days=7", headers=h).json()
    assert adh["taken"] == 5 and adh["skipped"] == 1
    assert adh["planned"] >= 12  # sabah+akşam × 6 gün + bugünün zamanı gelmişleri


def test_dose_validation(client, patient):
    user, h = patient
    pid = user["patient_id"]
    sid = add_med(client, pid, h)["schedules"][0]["id"]
    future = {"schedule_id": sid, "scheduled_date": str(date.today() + timedelta(days=3)), "status": "taken"}
    assert client.put(f"/patients/{pid}/doses", json=future, headers=h).status_code == 422
    other = {"schedule_id": 9999, "scheduled_date": str(local_today()), "status": "taken"}
    assert client.put(f"/patients/{pid}/doses", json=other, headers=h).status_code == 404


# ---------------- Measurements & alerts ----------------
def test_measurements_and_threshold_alert(client, patient):
    user, h = patient
    pid = user["patient_id"]
    r = client.post(f"/patients/{pid}/measurements", headers=h, json={"type": "glucose_fasting", "value": 112})
    assert r.status_code == 201 and r.json()["alert"] is None and r.json()["unit"] == "mg/dL"

    r = client.post(f"/patients/{pid}/measurements", headers=h,
                    json={"type": "blood_pressure", "value": 190, "value2": 115})
    assert r.json()["alert"]["severity"] == "critical"

    assert client.post(f"/patients/{pid}/measurements", headers=h,
                       json={"type": "blood_pressure", "value": 130}).status_code == 422

    assert len(client.get(f"/patients/{pid}/measurements", headers=h).json()) == 2
    assert len(client.get(f"/patients/{pid}/alerts", headers=h).json()) == 1


# ---------------- Role-based access ----------------
def test_patient_cannot_see_other_patient(client, patient):
    _, h = patient
    other, _ = register(client, "ali@example.com")
    assert client.get(f"/patients/{other['patient_id']}", headers=h).status_code == 404
    assert client.get(f"/patients/{other['patient_id']}/measurements", headers=h).status_code == 404


def test_clinician_access_flow(client, patient, clinician):
    user, h = patient
    pid = user["patient_id"]
    _, dh = clinician

    # atanmadan önce erişemez
    assert client.get(f"/patients/{pid}", headers=dh).status_code == 404

    assert client.put("/patients/me/clinician", headers=h,
                      json={"clinician_email": "dr.mehmet@example.com"}).status_code == 200
    add_med(client, pid, h)
    client.post(f"/patients/{pid}/measurements", headers=h, json={"type": "glucose_fasting", "value": 40})

    rows = client.get("/patients", headers=dh).json()
    assert rows[0]["id"] == pid and rows[0]["critical_alerts"] == 1

    # hekim okur ama yazamaz
    assert client.get(f"/patients/{pid}/medications", headers=dh).status_code == 200
    assert client.post(f"/patients/{pid}/medications", headers=dh, json=METFORMIN).status_code == 403

    alerts = client.get("/alerts", headers=dh).json()
    ack = client.post(f"/alerts/{alerts[0]['id']}/ack", headers=dh)
    assert ack.status_code == 200 and ack.json()["acknowledged_at"]

    # hasta uyarıyı kapatamaz
    assert client.post(f"/alerts/{alerts[0]['id']}/ack", headers=h).status_code == 403

    summary = client.get(f"/patients/{pid}/summary", headers=dh).json()
    assert summary["measurements"][0]["count"] == 1


def test_other_clinician_cannot_access(client, patient, clinician):
    user, h = patient
    client.put("/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    _, h2 = register(client, "dr.baska@example.com", role="clinician")
    assert client.get(f"/patients/{user['patient_id']}", headers=h2).status_code == 404
    assert client.get("/patients", headers=h2).json() == []


def test_caregiver_read_only(client, patient):
    user, h = patient
    pid = user["patient_id"]
    _, ch = register(client, "kizi@example.com", role="caregiver")
    assert client.get(f"/patients/{pid}", headers=ch).status_code == 404

    assert client.post("/patients/me/caregivers", headers=h, json={"caregiver_email": "kizi@example.com"}).status_code == 204
    assert client.get(f"/patients/{pid}/doses/today", headers=ch).status_code == 200
    assert client.post(f"/patients/{pid}/measurements", headers=ch,
                       json={"type": "glucose_fasting", "value": 100}).status_code == 403
    assert client.post("/coach/chat", headers=ch, json={"message": "merhaba"}).status_code == 403


# ---------------- Coach ----------------
def test_coach_emergency_creates_critical_alert(client, patient):
    user, h = patient
    r = client.post("/coach/chat", headers=h, json={"message": "Göğsümde baskı var"}).json()
    assert r["risk_level"] == "emergency" and r["escalate_to_clinician"] and "112" in r["message"]
    alerts = client.get(f"/patients/{user['patient_id']}/alerts", headers=h).json()
    assert alerts[0]["severity"] == "critical" and alerts[0]["kind"] == "emergency_symptom"


def test_coach_refuses_dose_change(client, patient):
    _, h = patient
    r = client.post("/coach/chat", headers=h, json={"message": "Metformin dozumu artırabilir miyim?"}).json()
    assert r["guardrail_action"] == "dose_request" and "hekim" in r["message"]


def test_coach_normal_reply_has_sources_and_history(client, patient):
    user, h = patient
    add_med(client, user["patient_id"], h)
    r = client.post("/coach/chat", headers=h, json={"message": "Bu hafta nasıl gidiyorum?"}).json()
    assert r["guardrail_action"] is None and r["sources"]
    hist = client.get("/coach/history", headers=h).json()
    assert [m["sender"] for m in hist] == ["patient", "coach"]


def test_coach_output_guardrail_blocks_bad_provider(db, client, patient):
    from app.models import Patient
    from app.services.coach import CoachReply, handle_message

    class BadProvider:
        def reply(self, db, patient, message):
            return CoachReply(message="Şekerin iyi, dozunu azalt.")

    user, _ = patient
    p = db.get(Patient, user["patient_id"])
    reply = handle_message(db, p, "nasılım", provider=BadProvider())
    assert reply.guardrail_action.startswith("blocked_output") and "azalt" not in reply.message


# ---------------- openFDA ----------------
FAKE_LABEL = {"results": [{
    "set_id": "abc",
    "openfda": {"brand_name": ["GLUCOPHAGE"], "generic_name": ["METFORMIN HYDROCHLORIDE"],
                "substance_name": ["METFORMIN HYDROCHLORIDE"]},
    "indications_and_usage": ["Metformin is indicated as an adjunct to diet and exercise..."],
    "warnings_and_cautions": ["Lactic acidosis..."],
    "drug_interactions": ["Carbonic anhydrase inhibitors..."],
}]}


def test_openfda_parse_with_mock_transport():
    def handler(request: httpx.Request):
        assert 'openfda.generic_name:"metformin"' in request.url.params["search"]
        return httpx.Response(200, json=FAKE_LABEL)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        data = openfda.fetch_label("Glifor", client=c)
    assert data["resolved_generic"] == "metformin"
    assert data["active_ingredients"] == ["METFORMIN HYDROCHLORIDE"]
    assert data["drug_interactions"].startswith("Carbonic")


def test_openfda_not_found():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))) as c:
        with pytest.raises(openfda.DrugLabelNotFound):
            openfda.fetch_label("bilinmeyenilac", client=c)


def test_drug_label_endpoint(client, patient, monkeypatch):
    _, h = patient
    drugs._cache.clear()
    monkeypatch.setattr(openfda, "fetch_label", lambda name: {"resolved_generic": "metformin", "query": name})
    r = client.get("/drugs/label?name=Glifor", headers=h)
    assert r.status_code == 200 and r.json()["resolved_generic"] == "metformin"
    assert client.get("/drugs/label?name=Glifor").status_code == 401


def test_me_with_seed_style_local_email(client, db):
    """Seed hesapları (@demo.local) EmailStr'ın reddettiği bir alan adı kullanır; /auth/me 500 vermemeli."""
    from app.models import Role, User
    from app.security import create_access_token, hash_password

    u = User(email="hasta@demo.local", password_hash=hash_password("demo12345"), full_name="Demo", role=Role.caregiver)
    db.add(u)
    db.commit()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {create_access_token(u.id, u.role.value)}"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "hasta@demo.local"


def test_rule_based_coach_cites_relevant_education(client, patient, db):
    """LLM anahtarı olmadan da koç, mesajla ilgili kürate içeriği kaynak olarak gösterir (RAG)."""
    from app.models import EducationContent

    db.add(EducationContent(
        title="Akşam dozlarını unutmamak için ipuçları",
        body="Akşam ilacını akşam yemeği gibi sabit bir rutine bağlamak unutmayı azaltır. Telefon alarmı da yardımcı olur.",
        source_name="WHO", source_url="https://www.who.int", tags="uyum",
    ))
    db.commit()
    pid, h = patient[0]["patient_id"], patient[1]
    add_med(client, pid, h)
    r = client.post("/coach/chat", json={"message": "Akşam ilacımı sürekli unutuyorum"}, headers=h).json()
    assert r["sources"][0]["title"].startswith("Akşam dozlarını unutmamak")
    assert "İlgili bilgi" in r["message"]



def test_rule_based_coach_greets_small_talk(client, patient):
    pid, h = patient[0]["patient_id"], patient[1]
    add_med(client, pid, h)
    r = client.post("/coach/chat", json={"message": "Selam!"}, headers=h).json()
    assert r["message"].startswith("Merhaba") and r["escalate_to_clinician"] is False


def test_coach_escalation_alert_not_repeated_within_24h(patient, db):
    """Her mesajda hekime yeni uyarı açılmamalı (uyarı yorgunluğu); onaylanınca yenisi açılabilir."""
    from datetime import UTC, datetime

    from app.models import Alert, Patient
    from app.services.coach import CoachReply, run_provider_stage

    class AlwaysEscalate:
        def reply(self, db, patient, message):
            return CoachReply(message="Hekiminle konuşmanı öneririm.", escalate_to_clinician=True)

    pat = db.get(Patient, patient[0]["patient_id"])
    r1 = run_provider_stage(db, pat, "a", AlwaysEscalate())
    r2 = run_provider_stage(db, pat, "b", AlwaysEscalate())
    alerts = db.query(Alert).filter(Alert.patient_id == pat.id, Alert.kind == "coach_escalation").all()
    assert r1.escalate_to_clinician is True and r2.escalate_to_clinician is False and len(alerts) == 1

    alerts[0].acknowledged_at = datetime.now(UTC)  # hekim okudu -> yeni yönlendirme yeniden uyarı açar
    db.commit()
    assert run_provider_stage(db, pat, "c", AlwaysEscalate()).escalate_to_clinician is True
