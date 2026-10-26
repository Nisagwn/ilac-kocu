"""Demo için sentetik veri. Kullanım: python -m app.seed

Oluşturulan hesaplar (şifre: demo12345):
  hasta@demo.local     — Tip 2 diyabet + hipertansiyon, 30 günlük doz ve ölçüm geçmişi
  hekim@demo.local     — hastanın hekimi
  yakin@demo.local     — bakım veren (salt okunur)
  + hekime atanmış 4 sentetik hasta (farklı uyum/risk profilleri; hekim panelini anlamlı kılmak için)

Kürate eğitim içeriği (app/data/education.yaml) her çalıştırmada başlığa göre upsert edilir ve embed edilir.
TİTCK tabanlı Türkçe ilaç kataloğu (app/data/drug_catalog_tr.yaml) da her çalıştırmada upsert edilir (çevrimdışı);
openFDA senkronu ayrı ve isteğe bağlıdır: python -m app.scripts.sync_drug_catalog
"""

import random
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    Alert,
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
    Severity,
    User,
)
from app.security import hash_password
from app.services.drug_catalog import link_unverified_medications, seed_catalog
from app.services.rag.search import ensure_embeddings

PW = "demo12345"

EDUCATION_YAML = Path(__file__).parent / "data" / "education.yaml"


def load_education_yaml(path: Path = EDUCATION_YAML) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    for it in items:
        missing = {"title", "body", "source_name"} - set(it)
        if missing:
            raise ValueError(f"education.yaml: '{it.get('title')}' eksik alan: {missing}")
    return items


def seed_education(db: Session, path: Path = EDUCATION_YAML) -> tuple[int, int]:
    """Kürate eğitim içeriğini başlığa göre upsert eder (idempotent) ve embed eder. Commit eder.
    YAML kürate içeriğin tek doğruluk kaynağıdır: YAML'da olmayan 'curated' satırlar silinir
    (openFDA satırlarına dokunulmaz).

    Döndürür: (eklenen, güncellenen)."""
    existing = {c.title: c for c in db.scalars(select(EducationContent).where(EducationContent.kind == "curated"))}
    created = updated = 0
    items = load_education_yaml(path)
    titles = {it["title"] for it in items}
    for title, row in existing.items():
        if title not in titles:
            db.delete(row)
    for it in items:
        yaml_reviewed = bool(it.get("reviewed", False))
        values = {
            "body": it["body"].strip(),
            "source_name": it["source_name"],
            "source_url": it.get("source_url"),
            "tags": it.get("tags"),
        }
        review = {"reviewed": yaml_reviewed, "review_status": "approved" if yaml_reviewed else "pending"}
        row = existing.get(it["title"])
        if row is None:
            db.add(EducationContent(title=it["title"], kind="curated", **values, **review))
            created += 1
            continue
        text_changed = (row.body, row.tags) != (values["body"], values["tags"])
        if row.reviewed_by is not None and not text_changed:
            review = {}  # hekimin onay/ret kararı YAML'daki varsayılandan önceliklidir
        elif row.reviewed_by is not None:
            review |= {"reviewed_by": None, "reviewed_at": None}  # karar eski metne aitti: yeniden onay
        values |= review
        if any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            if text_changed:
                row.embedding_model = None  # yeniden embed edilsin
            updated += 1
    db.flush()
    ensure_embeddings(db)
    db.commit()
    return created, updated


def run() -> None:
    db = SessionLocal()
    created, updated = seed_education(db)
    print(f"Eğitim içeriği: {created} eklendi, {updated} güncellendi.")
    created, updated = seed_catalog(db)
    print(f"İlaç kataloğu (kürate TR): {created} eklendi, {updated} güncellendi. "
          "openFDA senkronu isteğe bağlı: python -m app.scripts.sync_drug_catalog")
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

    for profile in EXTRA_PATIENTS:
        _add_extra_patient(db, rnd, doc.id, today, **profile)

    db.flush()
    link_unverified_medications(db)  # demo ilaçları (Glifor, Norvasc, Coversyl) katalogla doğrulanır
    db.commit()
    print("Demo verisi oluşturuldu. Şifre:", PW)


# Hekim paneli için ek sentetik hastalar: (uyum olasılığı, ölçüm ortalamaları, uyarılar)
EXTRA_PATIENTS = [
    {"email": "hasta2@demo.local", "name": "Fatma Demir", "birth_year": 1955, "diabetes": True, "hypertension": False,
     "p_taken": 0.97, "glucose": 118, "bp": None, "alerts": []},
    {"email": "hasta3@demo.local", "name": "Mehmet Kaya", "birth_year": 1968, "diabetes": True, "hypertension": True,
     "p_taken": 0.45, "glucose": 182, "bp": (152, 94),
     "alerts": [(Severity.critical, "out_of_range", "Çok yüksek kan şekeri: 312 mg/dL"),
                (Severity.warning, "coach_escalation", "Koç hekime yönlendirme önerdi (düşük uyum).")]},
    {"email": "hasta4@demo.local", "name": "Ayşe Çelik", "birth_year": 1972, "diabetes": False, "hypertension": True,
     "p_taken": 0.74, "glucose": None, "bp": (141, 88),
     "alerts": [(Severity.warning, "out_of_range", "Yüksek tansiyon: 162/98 mmHg")]},
    {"email": "hasta5@demo.local", "name": "Hüseyin Arslan", "birth_year": 1959, "diabetes": True, "hypertension": True,
     "p_taken": None, "glucose": None, "bp": None, "alerts": []},  # plan var, hiç kayıt yok → veri yok/düşük
]


def _add_extra_patient(db: Session, rnd: random.Random, clinician_id: int, today, *, email, name, birth_year,
                       diabetes, hypertension, p_taken, glucose, bp, alerts) -> None:
    u = User(email=email, password_hash=hash_password(PW), full_name=name, role=Role.patient)
    db.add(u)
    db.flush()
    pat = Patient(user_id=u.id, clinician_id=clinician_id, birth_year=birth_year, has_diabetes=diabetes,
                  has_hypertension=hypertension, consent_at=datetime.now(UTC))
    db.add(pat)
    db.flush()
    start = today - timedelta(days=30)
    meds = []
    if diabetes:
        m = Medication(patient_id=pat.id, name="Glifor", active_ingredient="metformin", dose_text="1000 mg")
        m.schedules = [MedicationSchedule(time_of_day=time(8), start_date=start),
                       MedicationSchedule(time_of_day=time(20), start_date=start)]
        meds.append(m)
    if hypertension:
        m = Medication(patient_id=pat.id, name="Coversyl", active_ingredient="perindopril", dose_text="5 mg")
        m.schedules = [MedicationSchedule(time_of_day=time(9), start_date=start)]
        meds.append(m)
    db.add_all(meds)
    db.flush()
    for i in range(30, 0, -1):
        d = today - timedelta(days=i)
        if p_taken is not None:
            for m in meds:
                for s in m.schedules:
                    db.add(DoseLog(patient_id=pat.id, schedule_id=s.id, scheduled_date=d,
                                   status=DoseStatus.taken if rnd.random() < p_taken else DoseStatus.skipped))
        at = datetime.combine(d, time(7, 45), tzinfo=UTC)
        if glucose:
            db.add(Measurement(patient_id=pat.id, type=MeasurementType.glucose_fasting,
                               value=round(rnd.gauss(glucose, 15)), unit="mg/dL", measured_at=at))
        if bp and i % 2 == 1:
            db.add(Measurement(patient_id=pat.id, type=MeasurementType.blood_pressure, value=round(rnd.gauss(bp[0], 8)),
                               value2=round(rnd.gauss(bp[1], 5)), unit="mmHg", measured_at=at + timedelta(hours=11)))
    for severity, kind, message in alerts:
        db.add(Alert(patient_id=pat.id, severity=severity, kind=kind, message=message))


if __name__ == "__main__":
    run()
