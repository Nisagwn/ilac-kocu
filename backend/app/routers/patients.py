from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import DB, CurrentUser, get_own_patient, get_patient_for_read, get_patient_for_write, require_roles
from app.models import Alert, Measurement, Patient, PatientCaregiver, Role, Severity, User
from app.schemas import (
    AddCaregiverIn,
    AdherenceOut,
    AssignClinicianIn,
    PatientOut,
    PatientSummaryOut,
    PatientUpdate,
)
from app.services.adherence import adherence_band
from app.services.coach import get_adherence

router = APIRouter(prefix="/patients", tags=["patients"])


def to_out(p: Patient) -> PatientOut:
    return PatientOut(
        id=p.id, full_name=p.user.full_name, birth_year=p.birth_year, has_diabetes=p.has_diabetes,
        has_hypertension=p.has_hypertension, clinician_id=p.clinician_id,
    )


@router.get("", response_model=list[PatientSummaryOut])
def list_patients(db: DB, user: CurrentUser):
    """Hekim: atanmış hastalar (uyum skoruna göre artan). Bakım veren: bağlı hastalar. Hasta: kendisi."""
    if user.role == Role.clinician:
        patients = db.scalars(select(Patient).where(Patient.clinician_id == user.clinician.id)).all()
    elif user.role == Role.caregiver:
        patients = db.scalars(
            select(Patient).join(PatientCaregiver).where(PatientCaregiver.caregiver_user_id == user.id)
        ).all()
    else:
        patients = [get_own_patient(db, user)]

    rows = []
    for p in patients:
        a7 = get_adherence(db, p.id, 7).score
        a30 = get_adherence(db, p.id, 30).score
        open_alerts = db.scalar(
            select(func.count()).select_from(Alert).where(Alert.patient_id == p.id, Alert.acknowledged_at.is_(None))
        )
        critical = db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.patient_id == p.id, Alert.acknowledged_at.is_(None), Alert.severity == Severity.critical
            )
        )
        last_m = db.scalar(select(func.max(Measurement.measured_at)).where(Measurement.patient_id == p.id))
        rows.append(PatientSummaryOut(
            id=p.id, full_name=p.user.full_name, adherence_7d=a7, adherence_30d=a30, band=adherence_band(a7),
            open_alerts=open_alerts or 0, critical_alerts=critical or 0, last_measurement_at=last_m,
        ))
    # Önce kritik uyarısı olanlar, sonra uyumu düşük olanlar
    rows.sort(key=lambda r: (-r.critical_alerts, r.adherence_7d if r.adherence_7d is not None else 2))
    return rows


@router.get("/me", response_model=PatientOut)
def my_profile(db: DB, user: CurrentUser):
    return to_out(get_own_patient(db, user))


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: int, db: DB, user: CurrentUser):
    return to_out(get_patient_for_read(db, user, patient_id))


@router.patch("/{patient_id}", response_model=PatientOut)
def update_patient(patient_id: int, data: PatientUpdate, db: DB, user: CurrentUser):
    p = get_patient_for_write(db, user, patient_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    return to_out(p)


@router.get("/{patient_id}/adherence", response_model=AdherenceOut)
def patient_adherence(patient_id: int, db: DB, user: CurrentUser, days: Annotated[int, Query(ge=1, le=90)] = 7):
    get_patient_for_read(db, user, patient_id)
    r = get_adherence(db, patient_id, days)
    return AdherenceOut(
        start=r.start, end=r.end, planned=r.planned, taken=r.taken, skipped=r.skipped, missing=r.missing,
        score=r.score, percent=r.percent, band=adherence_band(r.score), by_medication=r.by_medication,
        missed_dates=r.missed_dates,
    )


@router.put("/me/clinician", response_model=PatientOut)
def assign_clinician(data: AssignClinicianIn, db: DB, user: Annotated[User, Depends(require_roles(Role.patient))]):
    """Hasta, takip edecek hekimi e-posta ile seçer (veri paylaşımı hastanın onayıyla olur)."""
    p = get_own_patient(db, user)
    doc = db.scalar(select(User).where(User.email == data.clinician_email.lower(), User.role == Role.clinician))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hekim bulunamadı")
    p.clinician_id = doc.clinician.id
    db.commit()
    return to_out(p)


@router.post("/me/caregivers", status_code=status.HTTP_204_NO_CONTENT)
def add_caregiver(data: AddCaregiverIn, db: DB, user: Annotated[User, Depends(require_roles(Role.patient))]):
    p = get_own_patient(db, user)
    cg = db.scalar(select(User).where(User.email == data.caregiver_email.lower(), User.role == Role.caregiver))
    if cg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bakım veren hesabı bulunamadı")
    if db.get(PatientCaregiver, (p.id, cg.id)) is None:
        db.add(PatientCaregiver(patient_id=p.id, caregiver_user_id=cg.id))
        db.commit()


@router.delete("/me/caregivers/{caregiver_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_caregiver(caregiver_user_id: int, db: DB, user: Annotated[User, Depends(require_roles(Role.patient))]):
    p = get_own_patient(db, user)
    link = db.get(PatientCaregiver, (p.id, caregiver_user_id))
    if link:
        db.delete(link)
        db.commit()


@router.get("/{patient_id}/summary")
def weekly_summary(patient_id: int, db: DB, user: CurrentUser):
    """Haftalık özet (M2: deterministik istatistik; M4'te LLM ile anlatı özeti eklenecek)."""
    get_patient_for_read(db, user, patient_id)
    since = datetime.now(UTC) - timedelta(days=7)
    adh = get_adherence(db, patient_id, 7)
    stats = db.execute(
        select(Measurement.type, func.count(), func.avg(Measurement.value), func.min(Measurement.value),
               func.max(Measurement.value), func.avg(Measurement.value2))
        .where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
        .group_by(Measurement.type)
    ).all()
    alerts = db.scalar(
        select(func.count()).select_from(Alert).where(Alert.patient_id == patient_id, Alert.created_at >= since)
    )
    return {
        "period_days": 7,
        "adherence": {"score": adh.score, "planned": adh.planned, "taken": adh.taken, "band": adherence_band(adh.score)},
        "measurements": [
            {"type": t.value, "count": c, "avg": round(a, 1), "min": mn, "max": mx,
             "avg_diastolic": round(a2, 1) if a2 else None}
            for t, c, a, mn, mx, a2 in stats
        ],
        "alerts_last_7d": alerts or 0,
        "narrative": None,
    }
