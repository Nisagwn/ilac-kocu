"""Hekim hasta listesi (GET /patients, /v1/patients) sabit sayıda SQL sorgusuyla çalışır (N+1 yok).

Sorgular SQLAlchemy `before_cursor_execute` olayıyla sayılır. Kimlik haritası (identity map) önbelleği
sayımı gizlemesin diye istekten önce oturum boşaltılır: her istek gerçek sunucudaki gibi soğuk başlar.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import event

from app.models import (
    Alert,
    DoseLog,
    DoseStatus,
    Measurement,
    MeasurementType,
    Medication,
    MedicationSchedule,
    Patient,
    Role,
    Severity,
    User,
)
from tests.conftest import engine, register

# Kimlik doğrulama + erişim kaydı + liste sorguları için cömert ama hasta sayısından bağımsız üst sınır
MAX_QUERIES = 12


def _add_patients(db, clinician_id: int, n: int, start: int = 0) -> None:
    today = date.today()
    for i in range(start, start + n):
        u = User(email=f"sayac-hasta-{i}@demo.local", password_hash="x", full_name=f"Sayaç Hasta {i}",
                 role=Role.patient)
        db.add(u)
        db.flush()
        p = Patient(user_id=u.id, clinician_id=clinician_id, has_diabetes=True)
        db.add(p)
        db.flush()
        med = Medication(patient_id=p.id, name="Metformin", dose_text="1000 mg")
        db.add(med)
        db.flush()
        s = MedicationSchedule(medication_id=med.id, time_of_day=time(8, 0), start_date=today - timedelta(days=40))
        db.add(s)
        db.flush()
        for d in range(1, 10):
            db.add(DoseLog(patient_id=p.id, schedule_id=s.id, scheduled_date=today - timedelta(days=d),
                           status=DoseStatus.taken if (d + i) % 3 else DoseStatus.skipped))
        db.add(Measurement(patient_id=p.id, type=MeasurementType.glucose_fasting, value=110 + i, unit="mg/dL",
                           measured_at=datetime.now(UTC) - timedelta(hours=i + 1)))
        if i % 2 == 0:
            db.add(Alert(patient_id=p.id, severity=Severity.critical if i % 4 == 0 else Severity.warning,
                         kind="out_of_range", message="Sentetik uyarı"))
    db.commit()


class _QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self)


def _count_list_queries(client, db, headers, path: str) -> tuple[int, list]:
    db.expunge_all()  # soğuk oturum: tembel yüklemeler önbellekten değil veritabanından gelsin
    with _QueryCounter() as qc:
        r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    return qc.count, r.json()


@pytest.mark.parametrize("path", ["/v1/patients", "/patients"])
def test_clinician_patient_list_constant_query_count(client, db, path):
    doc, headers = register(client, "dr.sayac@example.com", role="clinician")
    clinician_id = db.get(User, doc["id"]).clinician.id

    _add_patients(db, clinician_id, 2)
    small, rows_small = _count_list_queries(client, db, headers, path)
    assert len(rows_small) == 2

    _add_patients(db, clinician_id, 18, start=2)
    large, rows_large = _count_list_queries(client, db, headers, path)
    assert len(rows_large) == 20

    assert small == large, f"2 hastada {small}, 20 hastada {large} sorgu (N+1)"
    assert large <= MAX_QUERIES

    # Yanıt şekli ve sıralama korunur: önce kritik uyarılılar, isimler dolu
    names = {r["full_name"] for r in rows_large}
    assert names == {f"Sayaç Hasta {i}" for i in range(20)}
    crit = [r["critical_alerts"] for r in rows_large]
    assert crit == sorted(crit, reverse=True)
    assert all(r["last_measurement_at"] is not None and r["adherence_7d"] is not None for r in rows_large)
