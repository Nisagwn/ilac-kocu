"""Yük testi için sentetik hasta/hekim hesapları (app/seed.py'ye dokunmaz, demo verisini değiştirmez).

Kullanım (docker compose):
    docker compose exec api python -m scripts.seed_load_test --patients 50 --clinicians 5
    docker compose exec api python -m scripts.seed_load_test --purge     # yük testi hesaplarını siler

Oluşturulan hesaplar (şifre: demo12345, tümü SENTETİK):
    yuk-hasta-001@demo.local ... yuk-hasta-NNN@demo.local
    yuk-hekim-01@demo.local  ... yuk-hekim-MM@demo.local   (hastalar hekimlere sırayla atanır)
Her hastaya 2 ilaç / 3 doz planı ve son 14 günlük doz + ölçüm geçmişi eklenir; böylece hekim listesindeki
uyum skoru hesapları gerçekçi miktarda veri üzerinde çalışır. Betik idempotenttir: var olan hesaplar atlanır.
"""

import argparse
import random
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    Clinician,
    DoseLog,
    DoseStatus,
    Measurement,
    MeasurementType,
    Medication,
    MedicationSchedule,
    Patient,
    Role,
    User,
)
from app.security import hash_password

PW = "demo12345"
EMAIL_DOMAIN = "demo.local"
PATIENT_EMAIL = "yuk-hasta-{:03d}@" + EMAIL_DOMAIN
CLINICIAN_EMAIL = "yuk-hekim-{:02d}@" + EMAIL_DOMAIN
HISTORY_DAYS = 14


def _get_or_create_clinicians(db, count: int, pw_hash: str) -> list[Clinician]:
    out = []
    for j in range(1, count + 1):
        email = CLINICIAN_EMAIL.format(j)
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=pw_hash, full_name=f"Yük Testi Hekim {j:02d}", role=Role.clinician)
            db.add(user)
            db.flush()
            db.add(Clinician(user_id=user.id, title="Dr.", institution="Yük Testi ASM (sentetik)"))
            db.flush()
        out.append(db.scalar(select(Clinician).where(Clinician.user_id == user.id)))
    return out


def _add_patient(db, rnd: random.Random, i: int, clinician: Clinician, pw_hash: str) -> None:
    user = User(email=PATIENT_EMAIL.format(i), password_hash=pw_hash, full_name=f"Yük Testi Hasta {i:03d}",
                role=Role.patient)
    db.add(user)
    db.flush()
    pat = Patient(user_id=user.id, clinician_id=clinician.id, birth_year=rnd.randint(1945, 1980), has_diabetes=True,
                  has_hypertension=True, consent_at=datetime.now(UTC))
    db.add(pat)
    db.flush()
    today = datetime.now(UTC).date()
    start = today - timedelta(days=30)
    met = Medication(patient_id=pat.id, name="Glifor", active_ingredient="metformin", dose_text="1000 mg")
    met.schedules = [MedicationSchedule(time_of_day=time(8), start_date=start),
                     MedicationSchedule(time_of_day=time(20), start_date=start)]
    ram = Medication(patient_id=pat.id, name="Delix", active_ingredient="ramipril", dose_text="5 mg")
    ram.schedules = [MedicationSchedule(time_of_day=time(9), start_date=start)]
    db.add_all([met, ram])
    db.flush()

    p_taken = rnd.uniform(0.5, 0.98)  # hastalar arasında farklı uyum profilleri
    for d in range(HISTORY_DAYS, 0, -1):
        day = today - timedelta(days=d)
        for s in met.schedules + ram.schedules:
            db.add(DoseLog(patient_id=pat.id, schedule_id=s.id, scheduled_date=day,
                           status=DoseStatus.taken if rnd.random() < p_taken else DoseStatus.skipped))
        at = datetime.combine(day, time(7, 30), tzinfo=UTC)
        db.add(Measurement(patient_id=pat.id, type=MeasurementType.glucose_fasting, value=round(rnd.gauss(130, 15)),
                           unit="mg/dL", measured_at=at))
        if d % 2 == 0:
            db.add(Measurement(patient_id=pat.id, type=MeasurementType.blood_pressure, value=round(rnd.gauss(132, 8)),
                               value2=round(rnd.gauss(84, 5)), unit="mmHg", measured_at=at + timedelta(hours=12)))


def seed(patients: int, clinicians: int) -> tuple[int, int]:
    """Döndürür: (yeni oluşturulan hasta sayısı, var olduğu için atlanan)."""
    db = SessionLocal()
    try:
        pw_hash = hash_password(PW)  # tek bcrypt özeti tüm sentetik hesaplarda (hızlı kurulum)
        docs = _get_or_create_clinicians(db, max(1, clinicians), pw_hash)
        existing = set(db.scalars(select(User.email).where(User.email.like("yuk-hasta-%@" + EMAIL_DOMAIN))))
        rnd = random.Random(2026)
        created = skipped = 0
        for i in range(1, patients + 1):
            if PATIENT_EMAIL.format(i) in existing:
                skipped += 1
                continue
            _add_patient(db, rnd, i, docs[(i - 1) % len(docs)], pw_hash)
            created += 1
            if created % 25 == 0:
                db.commit()
        db.commit()
        return created, skipped
    finally:
        db.close()


def purge() -> int:
    """Yalnızca yuk-hasta-* / yuk-hekim-* hesaplarını siler (hasta verisi FK CASCADE ile gider)."""
    db = SessionLocal()
    try:
        cond = User.email.like("yuk-hasta-%@" + EMAIL_DOMAIN) | User.email.like("yuk-hekim-%@" + EMAIL_DOMAIN)
        ids = list(db.scalars(select(User.id).where(cond)))
        if ids:
            db.execute(delete(User).where(User.id.in_(ids)))
        db.commit()
        return len(ids)
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Yük testi için sentetik hesaplar")
    ap.add_argument("--patients", type=int, default=50)
    ap.add_argument("--clinicians", type=int, default=5)
    ap.add_argument("--purge", action="store_true", help="yük testi hesaplarını sil")
    args = ap.parse_args()
    if args.purge:
        print(f"Silinen yük testi hesabı: {purge()}")
        return
    created, skipped = seed(args.patients, args.clinicians)
    print(f"Yük testi hastaları: {created} oluşturuldu, {skipped} zaten vardı; hekim: {args.clinicians}. Şifre: {PW}")


if __name__ == "__main__":
    main()
