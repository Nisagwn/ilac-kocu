from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.deps import DB, CurrentUser, get_own_patient, get_patient_for_read, get_patient_for_write, require_roles
from app.models import AccessLog, Alert, Measurement, Patient, PatientCaregiver, Role, Severity, User
from app.schemas import (
    AddCaregiverIn,
    AdherenceOut,
    AssignClinicianIn,
    ClinicalInsightsOut,
    PatientOut,
    PatientSummaryOut,
    PatientUpdate,
)
from app.services import clinical_insights
from app.services import summary as summary_service
from app.services.adherence import adherence_band
from app.services.clinical_insights import adherence_overview
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

    # Hasta sayısından bağımsız sabit sayıda sorgu (N+1 yok): uyum girdileri, uyarı ve son ölçüm toplu çekilir
    ids = [p.id for p in patients]
    overview = adherence_overview(db, ids)
    alert_counts: dict[int, tuple[int, int]] = {}
    last_measured: dict[int, datetime] = {}
    if ids:
        for pid, n_open, n_critical in db.execute(
            select(Alert.patient_id, func.count(), func.sum(case((Alert.severity == Severity.critical, 1), else_=0)))
            .where(Alert.patient_id.in_(ids), Alert.acknowledged_at.is_(None))
            .group_by(Alert.patient_id)
        ).all():
            alert_counts[pid] = (n_open or 0, int(n_critical or 0))
        last_measured = dict(db.execute(
            select(Measurement.patient_id, func.max(Measurement.measured_at))
            .where(Measurement.patient_id.in_(ids))
            .group_by(Measurement.patient_id)
        ).all())

    rows = []
    for p in patients:
        r7, r30, trend = overview[p.id]
        open_alerts, critical = alert_counts.get(p.id, (0, 0))
        rows.append(PatientSummaryOut(
            id=p.id, full_name=p.user.full_name, adherence_7d=r7.score, adherence_30d=r30.score,
            band=adherence_band(r7.score), open_alerts=open_alerts, critical_alerts=critical,
            last_measurement_at=last_measured.get(p.id),
            adherence_trend_delta=trend.delta_points, adherence_trend_direction=trend.direction,
        ))
    # Önce kritik uyarısı olanlar, sonra uyumu düşük olanlar
    rows.sort(key=lambda r: (-r.critical_alerts, r.adherence_7d if r.adherence_7d is not None else 2))
    return rows


@router.get("/me", response_model=PatientOut)
def my_profile(db: DB, user: CurrentUser):
    return to_out(get_own_patient(db, user))


class AccessLogOut(BaseModel):
    id: int
    actor_name: str | None  # eriştiren hesap silinmişse None
    actor_role: str
    action: str
    path: str
    created_at: datetime


@router.get("/me/access-log", response_model=list[AccessLogOut])
def my_access_log(
    db: DB, user: Annotated[User, Depends(require_roles(Role.patient))],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    """KVKK: hastanın verisine son `days` günde hangi hekim/bakım verenin eriştiği (en yeni önce, en çok 200)."""
    p = get_own_patient(db, user)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(AccessLog, User.full_name)
        .outerjoin(User, User.id == AccessLog.actor_user_id)
        .where(AccessLog.patient_id == p.id, AccessLog.created_at >= since)
        .order_by(AccessLog.created_at.desc(), AccessLog.id.desc())
        .limit(200)
    ).all()
    return [
        AccessLogOut(
            id=log.id, actor_name=name, actor_role=log.actor_role, action=log.action, path=log.path,
            created_at=log.created_at,
        )
        for log, name in rows
    ]


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


@router.get("/{patient_id}/insights", response_model=ClinicalInsightsOut)
def patient_insights(patient_id: int, db: DB, user: CurrentUser):
    """Hekim değerlendirmesi için klinik veri özeti (teşhis değildir): haftalık uyum eğilimi, hedefte kalma
    oranları (30/7 gün), doz atlama nedenleri (30 gün) ve son 7 günde ölçüm sıklığı.
    Erişim: hasta kendisi, atanmış hekim, bağlı bakım veren; aksi halde 404."""
    get_patient_for_read(db, user, patient_id)
    return clinical_insights.patient_insights(db, patient_id)


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
    """Haftalık özet. Geriye dönük uyumluluk için korunur; yeni istemciler `GET /summaries/{patient_id}` kullanır."""
    get_patient_for_read(db, user, patient_id)
    return summary_service.weekly_summary(db, patient_id)
