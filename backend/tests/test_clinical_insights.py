"""Klinik içgörü servisi: elle hesaplanmış örneklerle eğilim, hedefte kalma, atlama nedenleri, ölçüm sıklığı."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.models import DoseLog, DoseStatus, Measurement, MeasurementType, Medication, MedicationSchedule
from app.routers.doses import local_today
from app.services.adherence import LogSpec, ScheduleSpec
from app.services.clinical_insights import (
    MeasurementPoint,
    categorize_skip_reason,
    measurement_frequency,
    patient_insights,
    skip_reason_breakdown,
    target_stats,
    trend_direction,
    weekly_adherence_trend,
)
from app.services.thresholds import is_in_target
from tests.conftest import register

F, P, BP = MeasurementType.glucose_fasting, MeasurementType.glucose_postprandial, MeasurementType.blood_pressure
AS_OF = datetime(2026, 10, 14, 23, 0)  # yerel saat; günün tüm dozları zamanı gelmiş
TODAY = AS_OF.date()
DAILY = [ScheduleSpec(1, 1, time(8, 0), "1234567", date(2026, 9, 1))]


def _logs(days_ago: range, taken: bool) -> list[LogSpec]:
    return [LogSpec(1, TODAY - timedelta(days=i), taken) for i in days_ago]


# ---------------- 1. Uyum eğilimi ----------------
def test_trend_up_hand_computed():
    # Bu hafta (8–14 Ekim): 7 planlı, 6 alındı -> 6/7 = %86. Geçen hafta (1–7 Ekim): 4 alındı -> 4/7 = %57.
    logs = _logs(range(0, 6), True) + _logs(range(7, 11), True)
    t = weekly_adherence_trend(DAILY, logs, AS_OF)
    assert (t.current_start, t.current_end) == (date(2026, 10, 8), TODAY)
    assert (t.previous_start, t.previous_end) == (date(2026, 10, 1), date(2026, 10, 7))
    assert (t.current_percent, t.previous_percent) == (86, 57)
    assert t.current_score == 0.857 and t.previous_score == 0.571
    assert t.delta_points == 29 and t.direction == "up"


def test_trend_down_and_flat():
    # Bu hafta 3/7 = %43, geçen hafta 7/7 = %100 -> -57
    down = weekly_adherence_trend(DAILY, _logs(range(0, 3), True) + _logs(range(7, 14), True), AS_OF)
    assert (down.delta_points, down.direction) == (-57, "down")
    # Her iki hafta 5/7 = %71 -> fark 0, yön "flat"
    flat = weekly_adherence_trend(DAILY, _logs(range(0, 5), True) + _logs(range(7, 12), True), AS_OF)
    assert (flat.delta_points, flat.direction) == (0, "flat")


def test_trend_direction_deadband():
    assert trend_direction(None) is None
    assert [trend_direction(d) for d in (4, -4, 5, -5, 0)] == ["flat", "flat", "up", "down", "flat"]


def test_trend_none_without_previous_week():
    # İlaç 10 Ekim'de başladı: geçen hafta planlı doz yok -> fark/yön hesaplanmaz
    new = [ScheduleSpec(1, 1, time(8, 0), "1234567", date(2026, 10, 10))]
    t = weekly_adherence_trend(new, _logs(range(0, 5), True), AS_OF)
    assert t.current_percent == 100 and t.previous_percent is None
    assert t.delta_points is None and t.direction is None


def test_trend_excludes_not_yet_due_today():
    # Sabah 07:00: bugünün 08:00 dozu henüz paydada değil -> bu hafta 6 planlı
    t = weekly_adherence_trend(DAILY, _logs(range(1, 7), True), datetime(2026, 10, 14, 7, 0))
    assert t.current_percent == 100


# ---------------- 2. Hedefte kalma ----------------
@pytest.mark.parametrize(("mtype", "value", "value2", "expected"), [
    (F, 79, None, False), (F, 80, None, True), (F, 130, None, True), (F, 131, None, False),
    (P, 179.9, None, True), (P, 180, None, False),
    (BP, 129, 79, True), (BP, 130, 79, False), (BP, 129, 80, False), (BP, 120, None, False),
])
def test_is_in_target_boundaries(mtype, value, value2, expected):
    assert is_in_target(mtype, value, value2) is expected


def test_target_stats_hand_computed():
    pts = [MeasurementPoint(F, TODAY, v) for v in (79, 80, 130, 131, 100)]
    s = target_stats(pts, F, TODAY - timedelta(days=6), TODAY)
    # Hedefte: 80, 130, 100 -> 3/5 = 0.6; ortalama 520/5 = 104
    assert (s.count, s.in_target, s.in_target_ratio, s.avg, s.avg_diastolic) == (5, 3, 0.6, 104.0, None)

    pp = [MeasurementPoint(P, TODAY, v) for v in (170, 180, 200, 150)]
    s = target_stats(pp, P, TODAY, TODAY)
    assert (s.count, s.in_target, s.in_target_ratio, s.avg) == (4, 2, 0.5, 175.0)

    bp = [MeasurementPoint(BP, TODAY, a, b) for a, b in ((128, 79), (130, 79), (120, 80), (110, 70))]
    s = target_stats(bp, BP, TODAY, TODAY)
    # Hedefte (<130/80): 128/79 ve 110/70 -> 2/4; ort. sistolik 488/4 = 122, diyastolik 308/4 = 77
    assert (s.count, s.in_target, s.in_target_ratio, s.avg, s.avg_diastolic) == (4, 2, 0.5, 122.0, 77.0)


def test_target_stats_windows_and_empty():
    pts = [
        MeasurementPoint(F, TODAY, 100),  # hedefte
        MeasurementPoint(F, TODAY - timedelta(days=6), 200),  # 7 günlük pencerenin ilk günü
        MeasurementPoint(F, TODAY - timedelta(days=7), 110),  # yalnızca 30 gün
        MeasurementPoint(F, TODAY - timedelta(days=30), 90),  # 30 günlük pencere dışında
    ]
    w7 = target_stats(pts, F, TODAY - timedelta(days=6), TODAY)
    w30 = target_stats(pts, F, TODAY - timedelta(days=29), TODAY)
    assert (w7.count, w7.in_target, w7.in_target_ratio) == (2, 1, 0.5)
    assert (w30.count, w30.in_target, w30.in_target_ratio) == (3, 2, 0.667)  # 2/3 yarım-yukarı
    empty = target_stats(pts, BP, TODAY - timedelta(days=29), TODAY)
    assert (empty.count, empty.in_target_ratio, empty.avg) == (0, None, None)


# ---------------- 3. Doz atlama nedenleri ----------------
@pytest.mark.parametrize(("reason", "category"), [
    ("Unuttum", "unuttum"),
    ("AKLIMDAN ÇIKTI", "unuttum"),
    ("Midem bulandı", "yan_etki"),
    ("baş dönmesi yaptı", "yan_etki"),
    ("Kendimi iyi hissetmedim", "yan_etki"),
    ("İlacım bitti", "ilac_bitti"),
    ("Eczaneye gidemedim", "ilac_bitti"),
    ("Kendimi iyi hissettim", "iyi_hissetti"),
    ("şekerim normaldi gerek yok", "iyi_hissetti"),
    ("misafir vardı", "diger"),
    ("", "diger"),
    (None, "diger"),
])
def test_categorize_skip_reason(reason, category):
    assert categorize_skip_reason(reason) == category


def test_skip_reason_breakdown_counts_and_percent():
    b = skip_reason_breakdown(["unuttum", "Unuttum!", None])
    assert b["total"] == 3 and b["window_days"] == 30
    by_key = {c["key"]: c for c in b["categories"]}
    assert list(by_key) == ["yan_etki", "ilac_bitti", "unuttum", "iyi_hissetti", "diger"]
    assert (by_key["unuttum"]["count"], by_key["unuttum"]["percent"]) == (2, 67)
    assert (by_key["diger"]["count"], by_key["diger"]["percent"]) == (1, 33)
    assert by_key["yan_etki"]["count"] == 0
    assert skip_reason_breakdown([])["total"] == 0


# ---------------- 4. Ölçüm sıklığı ----------------
def test_measurement_frequency():
    f = measurement_frequency([TODAY, TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=7)], TODAY)
    assert (f["days_with_measurement"], f["low_data"]) == (2, True)  # 7 gün önce pencere dışı
    f = measurement_frequency([TODAY - timedelta(days=i) for i in (0, 3, 6)], TODAY)
    assert (f["days_with_measurement"], f["low_data"]) == (3, False)


# ---------------- DB + API ----------------
def _setup_patient_data(db, pid, today: date):
    med = Medication(patient_id=pid, name="Glifor", active_ingredient="metformin")
    db.add(med)
    db.flush()
    sched = MedicationSchedule(medication_id=med.id, time_of_day=time(8, 0), start_date=today - timedelta(days=20))
    db.add(sched)
    db.flush()
    # Geçen hafta (7–13 gün önce) hepsi alındı; bu hafta (1–6 gün önce) hepsi atlandı
    for i in range(7, 14):
        db.add(DoseLog(patient_id=pid, schedule_id=sched.id, scheduled_date=today - timedelta(days=i),
                       status=DoseStatus.taken))
    for i, reason in zip(range(1, 7), ["unuttum", "unuttum", "midem bulandı", "ilacım bitti", None, "tatildeydim"],
                         strict=True):
        db.add(DoseLog(patient_id=pid, schedule_id=sched.id, scheduled_date=today - timedelta(days=i),
                       status=DoseStatus.skipped, reason=reason))
    db.commit()


def test_patient_insights_service_with_fixed_now(db, client, patient):
    user, _ = patient
    pid = user["patient_id"]
    now = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # İstanbul 23:00
    today = date(2026, 10, 14)
    _setup_patient_data(db, pid, today)
    for days_ago, mtype, v, v2 in [
        (0, F, 110, None), (0, F, 150, None), (2, F, 95, None), (10, P, 200, None),
        (1, BP, 125, 78), (1, BP, 140, 90),
    ]:
        db.add(Measurement(patient_id=pid, type=mtype, value=v, value2=v2, unit="x",
                           measured_at=now - timedelta(days=days_ago)))
    db.commit()

    r = patient_insights(db, pid, now=now)
    t = r["adherence_trend"]
    # Bu hafta: 7 planlı (bugün kayıtsız dahil), 0 alındı -> %0; geçen hafta 7/7 -> %100
    assert (t["current_percent"], t["previous_percent"], t["delta_points"], t["direction"]) == (0, 100, -100, "down")
    fasting = r["targets"]["glucose_fasting"]
    assert fasting["target"] == "80–130 mg/dL"
    assert fasting["window_7d"]["count"] == 3 and fasting["window_7d"]["in_target"] == 2
    assert fasting["window_7d"]["in_target_ratio"] == 0.667 and fasting["window_7d"]["avg"] == 118.3
    assert r["targets"]["glucose_postprandial"]["window_7d"]["count"] == 0
    assert r["targets"]["glucose_postprandial"]["window_30d"]["count"] == 1
    bp = r["targets"]["blood_pressure"]["window_30d"]
    assert (bp["count"], bp["in_target"], bp["avg"], bp["avg_diastolic"]) == (2, 1, 132.5, 84.0)
    cats = {c["key"]: c["count"] for c in r["skip_reasons"]["categories"]}
    assert r["skip_reasons"]["total"] == 6
    assert cats == {"yan_etki": 1, "ilac_bitti": 1, "unuttum": 2, "iyi_hissetti": 0, "diger": 2}
    freq = r["measurement_frequency"]
    assert freq["days_with_measurement"] == 3 and freq["low_data"] is False
    assert "teşhis" in r["disclaimer"]


def test_insights_endpoint_authorization(client, patient, clinician):
    user, h = patient
    pid = user["patient_id"]
    _, ch = clinician
    assert client.get(f"/patients/{pid}/insights").status_code == 401
    assert client.get(f"/patients/{pid}/insights", headers=h).status_code == 200  # hasta kendisi
    assert client.get(f"/patients/{pid}/insights", headers=ch).status_code == 404  # atanmamış hekim

    client.put("/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    r = client.get(f"/patients/{pid}/insights", headers=ch)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"patient_id", "as_of", "adherence_trend", "targets", "skip_reasons",
                         "measurement_frequency", "disclaimer"}
    assert set(body["targets"]) == {"glucose_fasting", "glucose_postprandial", "blood_pressure"}
    assert body["measurement_frequency"]["low_data"] is True  # ölçüm yok

    _, cg = register(client, "yakin@example.com", role="caregiver")
    assert client.get(f"/patients/{pid}/insights", headers=cg).status_code == 404
    client.post("/patients/me/caregivers", headers=h, json={"caregiver_email": "yakin@example.com"})
    assert client.get(f"/patients/{pid}/insights", headers=cg).status_code == 200

    other, oh = register(client, "baska@example.com")
    assert client.get(f"/patients/{pid}/insights", headers=oh).status_code == 404
    assert client.get("/patients/99999/insights", headers=ch).status_code == 404


def test_patient_list_includes_trend(client, db, patient, clinician):
    user, h = patient
    pid = user["patient_id"]
    _, ch = clinician
    client.put("/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    _setup_patient_data(db, pid, local_today())
    # İkinci hasta: ilaç yok -> eğilim hesaplanmaz
    _, h2 = register(client, "ikinci@example.com")
    client.put("/patients/me/clinician", headers=h2, json={"clinician_email": "dr.mehmet@example.com"})

    rows = {r["id"]: r for r in client.get("/patients", headers=ch).json()}
    assert len(rows) == 2
    me = rows[pid]
    # Bu hafta: tüm geçmiş günler atlandı (bugünkü doz saatine göre %0) -> geçen hafta %100'den düşüş
    assert me["adherence_trend_delta"] == -100 and me["adherence_trend_direction"] == "down"
    assert me["adherence_7d"] == 0.0 and me["adherence_30d"] is not None
    other = next(r for k, r in rows.items() if k != pid)
    assert other["adherence_trend_delta"] is None and other["adherence_trend_direction"] is None
    assert other["band"] == "veri_yok"
