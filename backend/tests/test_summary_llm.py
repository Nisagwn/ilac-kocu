"""Hekim haftalık özet anlatısı testleri — şablon ve sahte LLM yolu (ağ çağrısı YOK)."""

from datetime import UTC, datetime, time, timedelta

import pytest

from app.models import (
    Alert,
    DoseLog,
    DoseStatus,
    LlmUsage,
    Measurement,
    MeasurementType,
    Medication,
    MedicationSchedule,
    Severity,
)
from app.services.llm import narrative
from app.services.llm.narrative import DISCLAIMER, template_narrative
from app.services.summary import get_narrative_client, weekly_summary
from tests.test_coach_llm import FakeClient, response, text


@pytest.fixture(autouse=True)
def _clear_cache():
    narrative.clear_cache()
    yield
    narrative.clear_cache()


def seed_week(db, patient_id):
    """Sabah dozları hep alınmış, akşam dozları hiç alınmamış; birkaç ölçüm ve bir kritik uyarı."""
    today = datetime.now(UTC).date()
    med = Medication(patient_id=patient_id, name="Metformin", dose_text="1000 mg")
    db.add(med)
    db.flush()
    morning = MedicationSchedule(medication_id=med.id, time_of_day=time(8, 0), start_date=today - timedelta(days=30))
    evening = MedicationSchedule(medication_id=med.id, time_of_day=time(20, 0), start_date=today - timedelta(days=30))
    db.add_all([morning, evening])
    db.flush()
    for i in range(0, 8):
        db.add(DoseLog(patient_id=patient_id, schedule_id=morning.id, scheduled_date=today - timedelta(days=i),
                       status=DoseStatus.taken))
    now = datetime.now(UTC)
    for i, v in enumerate([120, 150, 180]):
        db.add(Measurement(patient_id=patient_id, type=MeasurementType.glucose_fasting, value=v, unit="mg/dL",
                           measured_at=now - timedelta(days=i + 1)))
    db.add(Measurement(patient_id=patient_id, type=MeasurementType.blood_pressure, value=140, value2=90,
                       unit="mmHg", measured_at=now - timedelta(days=1)))
    db.add(Alert(patient_id=patient_id, severity=Severity.critical, kind="emergency_symptom", message="test"))
    db.commit()


def test_template_narrative_when_llm_unavailable(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    s = weekly_summary(db, pid)
    assert s["narrative_source"] == "template"
    n = s["narrative"]
    assert "Açlık kan şekeri: 3 ölçüm, ort. 150 mg/dL (aralık 120–180)" in n
    assert "Tansiyon: 1 ölçüm, ort. 140/90 mmHg" in n
    assert "akşam" in n and "kritik" in n and n.endswith(DISCLAIMER)
    assert s["missed_by_time_of_day"]["akşam"]["missed"] >= 6
    assert s["missed_by_time_of_day"]["sabah"]["missed"] == 0
    assert db.query(LlmUsage).count() == 0


def test_no_data_means_no_narrative(db, patient):
    user, _ = patient
    s = weekly_summary(db, user["patient_id"])
    assert s["narrative"] is None and s["narrative_source"] is None


def test_template_flags_low_adherence():
    facts = {"period_days": 7, "adherence": {"score": 0.4, "planned": 10, "taken": 4, "band": "dusuk"},
             "missed_by_time_of_day": {"akşam": {"planned": 5, "missed": 5}, "sabah": {"planned": 5, "missed": 1}},
             "measurements": [], "alerts": {"total": 0, "critical": 0, "by_kind": {}}}
    n = template_narrative(facts)
    assert "uyum %40, düşük bant" in n and "akşam saatlerinde (5/5)" in n and "Dikkat: düşük ilaç uyumu" in n


def test_llm_narrative_is_generated_cached_and_recorded(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    fake = FakeClient([response([text("Hasta son 7 günde sabah dozlarını düzenli almış; akşam dozları kaçmış. "
                                      "Açlık kan şekeri ortalaması 150 mg/dL. Dikkat: 1 kritik uyarı.")],
                                inp=800, out=120)])
    s = weekly_summary(db, pid, llm_client=fake)
    assert s["narrative_source"] == "llm"
    assert s["narrative"].startswith("Hasta son 7 günde") and s["narrative"].endswith(DISCLAIMER)

    call = fake.calls[0]
    payload = call["messages"][0]["content"]
    assert '"akşam"' in payload and "ayse" not in payload.lower()  # kimlik bilgisi yok
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"} and "tools" not in call

    row = db.query(LlmUsage).one()
    assert row.purpose == "summary" and row.patient_id == pid and row.cost_usd > 0

    # Aynı gün, aynı veri: önbellekten (ikinci model çağrısı yok)
    assert weekly_summary(db, pid, llm_client=fake)["narrative"] == s["narrative"]
    assert len(fake.calls) == 1


def test_llm_narrative_blocked_by_guardrail_uses_template(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    fake = FakeClient([response([text("Uyum düşük, dozunu artır.")])])
    s = weekly_summary(db, pid, llm_client=fake)
    assert s["narrative_source"] == "template" and "artır" not in s["narrative"]


def test_llm_narrative_error_uses_template(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    s = weekly_summary(db, pid, llm_client=FakeClient([RuntimeError("zaman aşımı")]))
    assert s["narrative_source"] == "template" and s["narrative"]


def test_summary_budget_limits_llm_calls(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    for _ in range(3):  # varsayılan llm_daily_summary_requests = 3
        db.add(LlmUsage(patient_id=pid, purpose="summary", model="claude-opus-5-5", input_tokens=1, output_tokens=1,
                        cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0))
    db.commit()
    fake = FakeClient([response([text("kullanılmamalı")])])
    s = weekly_summary(db, pid, llm_client=fake)
    assert s["narrative_source"] == "template" and fake.calls == []


def test_summary_endpoint_uses_narrative_client_dependency(client, db, patient):
    from app.main import app

    user, h = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    fake = FakeClient([response([text("Hasta akşam dozlarını kaçırmış.")])])
    app.dependency_overrides[get_narrative_client] = lambda: fake
    body = client.get(f"/summaries/{pid}", headers=h).json()
    assert body["narrative_source"] == "llm" and body["narrative"].startswith("Hasta akşam")
