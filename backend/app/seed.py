"""Demo için sentetik veri. Kullanım: python -m app.seed

Oluşturulan hesaplar (şifre: demo12345):
  hasta@demo.local     — Tip 2 diyabet + hipertansiyon, 30 günlük doz ve ölçüm geçmişi
  hekim@demo.local     — hastanın hekimi
  yakin@demo.local     — bakım veren (salt okunur)
"""

import random
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Clinician,
    DoseLog,
    DoseStatus,
    EducationContent,
    Measurement,
    MeasurementType,
    Medication,
    MedicationSchedule,
    Patient,
    PatientCaregiver,
    Role,
    User,
)
from app.security import hash_password

PW = "demo12345"

EDUCATION = [
    ("İlacı günlük rutine bağlamak", "İlacı kahvaltı veya diş fırçalama gibi her gün yapılan bir işle eşleştirmek unutmayı azaltır.",
     "WHO — Adherence to long-term therapies (2003)", "https://iris.who.int/handle/10665/42682", "uyum,ipucu"),
    ("Açlık kan şekeri nasıl ölçülür", "Açlık ölçümü en az 8 saatlik açlıktan sonra, sabah yapılır. Ölçümü aynı saatte yapmak takibi kolaylaştırır.",
     "Kürate hasta eğitim içeriği", None, "diyabet,olcum"),
    ("Evde tansiyon ölçümü", "Ölçümden önce 5 dakika oturarak dinlenin, kolunuzu kalp hizasında tutun, kafein ve sigaradan 30 dk önce kaçının.",
     "Kürate hasta eğitim içeriği", None, "hipertansiyon,olcum"),
]


def run() -> None:
    db = SessionLocal()
    if db.scalar(select(User).where(User.email == "hasta@demo.local")):
        print("Demo verisi zaten var.")
        return

    rnd = random.Random(42)
    doc_u = User(email="hekim@demo.local", password_hash=hash_password(PW), full_name="Dr. Elif Aydın", role=Role.clinician)
    pat_u = User(email="hasta@demo.local", password_hash=hash_password(PW), full_name="Ahmet Yılmaz", role=Role.patient)
    cg_u = User(email="yakin@demo.local", password_hash=hash_password(PW), full_name="Zeynep Yılmaz", role=Role.caregiver)
    db.add_all([doc_u, pat_u, cg_u])
    db.flush()

    doc = Clinician(user_id=doc_u.id, title="Uzm. Dr.", institution="Demo Aile Sağlığı Merkezi")
    db.add(doc)
    db.flush()
    pat = Patient(user_id=pat_u.id, clinician_id=doc.id, birth_year=1961, has_diabetes=True,
                  has_hypertension=True, consent_at=datetime.now(UTC))
    db.add(pat)
    db.flush()
    db.add(PatientCaregiver(patient_id=pat.id, caregiver_user_id=cg_u.id))

    today = datetime.now(UTC).date()
    start = today - timedelta(days=30)
    met = Medication(patient_id=pat.id, name="Glifor", active_ingredient="metformin", dose_text="1000 mg")
    met.schedules = [MedicationSchedule(time_of_day=time(8), start_date=start),
                     MedicationSchedule(time_of_day=time(20), start_date=start)]
    aml = Medication(patient_id=pat.id, name="Norvasc", active_ingredient="amlodipine", dose_text="5 mg")
    aml.schedules = [MedicationSchedule(time_of_day=time(9), start_date=start)]
    db.add_all([met, aml])
    db.flush()

    for i in range(30, 0, -1):
        d = today - timedelta(days=i)
        for s in met.schedules + aml.schedules:
            # akşam dozları daha sık kaçıyor: gerçekçi bir örüntü
            p_taken = 0.7 if s.time_of_day.hour == 20 else 0.92
            if rnd.random() < 0.95:  # bazı günler hiç kayıt yok
                db.add(DoseLog(patient_id=pat.id, schedule_id=s.id, scheduled_date=d,
                               status=DoseStatus.taken if rnd.random() < p_taken else DoseStatus.skipped))
        at = datetime.combine(d, time(7, 30), tzinfo=UTC)
        db.add(Measurement(patient_id=pat.id, type=MeasurementType.glucose_fasting,
                           value=round(rnd.gauss(135, 18)), unit="mg/dL", measured_at=at))
        if i % 2 == 0:
            db.add(Measurement(patient_id=pat.id, type=MeasurementType.blood_pressure, value=round(rnd.gauss(138, 9)),
                               value2=round(rnd.gauss(86, 6)), unit="mmHg", measured_at=at + timedelta(hours=12)))

    for title, body, src, url, tags in EDUCATION:
        db.add(EducationContent(title=title, body=body, source_name=src, source_url=url, tags=tags))

    db.commit()
    print("Demo verisi oluşturuldu. Şifre:", PW)


if __name__ == "__main__":
    run()
