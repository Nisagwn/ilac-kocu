"""Klinik içgörüler: uyum eğilimi, hedef aralıkta kalma, doz atlama nedenleri, ölçüm sıklığı, hasta listesi
eğilimi ve erişim kuralları."""

from datetime import UTC, datetime, time, timedelta

import pytest

from app.models import DoseLog, DoseStatus, Measurement, MeasurementType, Medication, MedicationSchedule
from app.routers.doses import local_today
from app.services.clinical_insights import _trend, categorize_skip_reason, compute_insights
from tests.conftest import register

NOW = datetime.now(UTC)


def _med(db, pid, hour=0, days_back=20) -> MedicationSchedule:  # 00:00: bugünün dozu daima vadesi gelmiş
    med = Medication(patient_id=pid, name="Glifor", active_ingredient="metformin")
    sched = MedicationSchedule(time_of_day=time(hour), start_date=local_today() - timedelta(days=days_back))
    med.schedules = [sched]
    db.add(med)
    db.commit()
    return sched


def _dose(db, pid, sched, days_ago, taken, reason=None):
    db.add(DoseLog(patient_id=pid, schedule_id=sched.id, scheduled_date=local_today() - timedelta(days=days_ago),
                   status=DoseStatus.taken if taken else DoseStatus.skipped, reason=reason))


def _glucose(db, pid, value, days_ago=1, mtype=MeasurementType.glucose_fasting):
    db.add(Measurement(patient_id=pid, type=mtype, value=value, unit="mg/dL",
                       measured_at=NOW - timedelta(days=days_ago, hours=1)))


def _bp(db, pid, sys_, dia, days_ago=1):
    db.add(Measurement(patient_id=pid, type=MeasurementType.blood_pressure, value=sys_, value2=dia, unit="mmHg",
                       measured_at=NOW - timedelta(days=days_ago, hours=2)))


# ---------------- birim ----------------
@pytest.mark.parametrize("reason, cat", [
    ("Unuttum", "unutma"),
    ("aklımdan çıktı", "unutma"),
    ("MİDEM BULANDI", "yan_etki"),
    ("başım döndü", "yan_etki"),
    ("ilacım bitti", "ilac_bitti"),
    ("eczaneye gidemedim", "ilac_bitti"),
    ("yolculuktaydım", "rutin_disi"),
    ("iyi hissediyordum gerek görmedim", "gerek_duymama"),
    ("oruçluydum", "ogun_oruc"),
    ("bilmem", "diger"),
    ("", "belirtilmemis"),
    (None, "belirtilmemis"),
])
def test_categorize_skip_reason(reason, cat):
    assert categorize_skip_reason(reason) == cat


def test_trend_thresholds():
    assert _trend(0.9, 0.7) == "improving"
    assert _trend(0.5, 0.7) == "worsening"
    assert _trend(0.75, 0.7) == "stable"
    assert _trend(None, 0.7) is None and _trend(0.7, None) is None


# ---------------- hesaplama ----------------
def test_adherence_trend_worsening(db, patient):
    pid = patient[0]["patient_id"]
    sched = _med(db, pid)
    for d in range(7, 14):  # önceki hafta (7-13 gün önce): hepsi alındı
        _dose(db, pid, sched, d, True)
    for d in range(0, 7):  # bu hafta: 2/7
        _dose(db, pid, sched, d, d <= 1, reason=None if d <= 1 else "unuttum")
    db.commit()
    ins = compute_insights(db, pid, 30)
    a = ins.adherence
    assert a["previous_7d"] == 1.0 and a["current_7d"] < 0.5 and a["trend"] == "worsening" and a["delta"] < 0
    assert a["current_7d"] == 0.286
    assert ins.skip_reasons[0] == {"category": "unutma", "label": "Unutma", "count": 5}
    assert any(h.startswith("Uyum son 7 günde düştü") for h in ins.highlights)
    assert any("unutma" in h for h in ins.highlights)


def test_time_in_range_and_bp_target(db, patient):
    pid = patient[0]["patient_id"]
    for v in (100, 150, 180, 65, 250):  # 3 hedefte, 1 düşük, 1 yüksek
        _glucose(db, pid, v)
    _glucose(db, pid, 160, mtype=MeasurementType.glucose_postprandial)
    for s, d in ((125, 78), (135, 85), (120, 82), (85, 55)):  # 1 hedefte, 2 yüksek, 1 düşük
        _bp(db, pid, s, d)
    db.commit()
    ins = compute_insights(db, pid, 30)
    g, b = ins.glucose, ins.blood_pressure
    assert g == {"target": "70-180 mg/dL", "count": 6, "time_in_range": 0.667, "below": 1, "above": 1}
    assert b == {"target": "<130/80 mmHg", "count": 4, "at_target": 0.25, "below": 1, "above": 2}
    assert "Hedef aralıktaki kan şekeri ölçümü: %67 (70-180 mg/dL); 1 düşük, 1 yüksek." in ins.highlights
    assert any("1 düşük kan şekeri" in h for h in ins.highlights)
    assert "Hedefteki tansiyon ölçümü: %25 (<130/80 mmHg)." in ins.highlights


def test_measurement_frequency_and_staleness(db, patient):
    pid = patient[0]["patient_id"]
    for d in (5, 6, 6, 20, 40):  # 40 gün önceki pencere dışında
        _glucose(db, pid, 110, days_ago=d)
    db.commit()
    f = compute_insights(db, pid, 30).measurement_frequency
    assert f["by_type"] == {"glucose_fasting": 4}
    assert f["per_week"] == round(4 / (30 / 7), 1)
    assert f["days_with_measurement"] == 3
    assert f["days_since_last"] == 5
    assert any("5 gün geçti" in h for h in compute_insights(db, pid, 30).highlights)


def test_no_data(db, patient):
    pid = patient[0]["patient_id"]
    ins = compute_insights(db, pid, 30)
    assert ins.adherence["trend"] is None and ins.glucose["time_in_range"] is None
    assert ins.measurement_frequency["days_since_last"] is None and ins.skip_reasons == []
    assert ins.highlights == ["Hiç ölçüm kaydı yok."]


def test_old_measurement_outside_window_still_counts_for_staleness(db, patient):
    pid = patient[0]["patient_id"]
    _glucose(db, pid, 110, days_ago=45)
    db.commit()
    f = compute_insights(db, pid, 30).measurement_frequency
    assert f["by_type"] == {} and f["days_since_last"] == 45


# ---------------- API ----------------
def test_insights_endpoint_and_access(client, db, patient, clinician):
    pid, ph = patient[0]["patient_id"], patient[1]
    _glucose(db, pid, 120)
    db.commit()
    r = client.get(f"/v1/patients/{pid}/insights", headers=ph)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"period_days", "adherence", "glucose", "blood_pressure", "measurement_frequency",
                         "skip_reasons", "missed_by_time_of_day", "highlights"}
    assert body["glucose"]["time_in_range"] == 1.0

    assert client.get(f"/v1/patients/{pid}/insights", headers=clinician[1]).status_code == 404  # atanmamış
    client.put("/v1/patients/me/clinician", headers=ph, json={"clinician_email": "dr.mehmet@example.com"})
    assert client.get(f"/v1/patients/{pid}/insights?days=14", headers=clinician[1]).json()["period_days"] == 14
    _, other = register(client, "baska@example.com")
    assert client.get(f"/v1/patients/{pid}/insights", headers=other).status_code == 404
    assert client.get(f"/v1/patients/{pid}/insights?days=3", headers=ph).status_code == 422


def test_patient_list_includes_trend(client, db, patient, clinician):
    pid, ph = patient[0]["patient_id"], patient[1]
    client.put("/v1/patients/me/clinician", headers=ph, json={"clinician_email": "dr.mehmet@example.com"})
    sched = _med(db, pid)
    for d in range(0, 14):
        _dose(db, pid, sched, d, d >= 7)  # önceki hafta alındı, bu hafta atlandı
    db.commit()
    row = client.get("/v1/patients", headers=clinician[1]).json()[0]
    assert row["adherence_trend"] == "worsening" and row["adherence_prev_7d"] == 1.0
    assert row["adherence_7d"] == 0.0
