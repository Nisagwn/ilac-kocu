"""Proje tanımındaki üst düzey kaynak yolları (yalnızca `/v1` altında): /medications, /doses, /measurements.

`patient_id` hasta için isteğe bağlıdır (verilmezse kendisi); hekim ve bakım veren için zorunludur. Yetki kuralı
iç içe yollarla aynıdır (`get_patient_for_read`): erişemediği hastada 404. Doz ve ölçüm listeleri cursor ile
sayfalıdır ve `{items, next_cursor}` döner (app/pagination.py). İç içe `/patients/{id}/...` yolları da çalışır.
"""

from datetime import UTC, date, datetime, time
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.config import get_settings
from app.deps import DB, CurrentUser, get_patient_for_read
from app.models import DoseLog, Measurement, MeasurementType, Medication, Patient, Role, User
from app.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.schemas import DoseLogOut, MeasurementOut, MedicationOut

router = APIRouter(tags=["resources"])

PatientIdQ = Annotated[int | None, Query(description="Hasta için isteğe bağlı (kendisi); hekim/bakım veren için zorunlu")]
LimitQ = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
CursorQ = Annotated[str | None, Query(max_length=200, description="Önceki yanıttaki next_cursor (opak)")]


def _resolve_patient(db, user: User, patient_id: int | None) -> Patient:
    if patient_id is None:
        if user.role != Role.patient or user.patient is None:
            raise HTTPException(422, "patient_id zorunludur")
        patient_id = user.patient.id
    return get_patient_for_read(db, user, patient_id)


def _as_datetime(value: date | datetime, *, end: bool) -> datetime:
    """Sorgu sınırını UTC'ye çevirir. Yalnız tarih verilirse yerel günün başı / sonu; saat dilimsiz zaman UTC."""
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    local = datetime.combine(value, time.max if end else time.min, tzinfo=ZoneInfo(get_settings().timezone))
    return local.astimezone(UTC)


@router.get("/medications", response_model=list[MedicationOut])
def list_medications(db: DB, user: CurrentUser, patient_id: PatientIdQ = None, include_inactive: bool = False):
    """Hastanın ilaçları (sayfalı değil; ilaç listesi küçüktür)."""
    patient = _resolve_patient(db, user, patient_id)
    q = select(Medication).where(Medication.patient_id == patient.id)
    if not include_inactive:
        q = q.where(Medication.active.is_(True))
    return db.scalars(q.order_by(Medication.name)).all()


@router.get("/doses", response_model=Page[DoseLogOut])
def list_doses(
    db: DB, user: CurrentUser, patient_id: PatientIdQ = None,
    from_: Annotated[date | None, Query(alias="from", description="Planlı tarih alt sınırı (dahil)")] = None,
    to: Annotated[date | None, Query(description="Planlı tarih üst sınırı (dahil)")] = None,
    limit: LimitQ = DEFAULT_LIMIT, cursor: CursorQ = None,
):
    """Doz kayıtları, planlı tarihe göre yeniden eskiye."""
    patient = _resolve_patient(db, user, patient_id)
    q = select(DoseLog).where(DoseLog.patient_id == patient.id)
    if from_:
        q = q.where(DoseLog.scheduled_date >= from_)
    if to:
        q = q.where(DoseLog.scheduled_date <= to)
    items, next_cursor = paginate(db, q, DoseLog.scheduled_date, DoseLog.id, limit=limit, cursor=cursor)
    return Page[DoseLogOut](items=items, next_cursor=next_cursor)


@router.get("/measurements", response_model=Page[MeasurementOut])
def list_measurements(
    db: DB, user: CurrentUser, patient_id: PatientIdQ = None, type: MeasurementType | None = None,
    from_: Annotated[datetime | date | None, Query(alias="from", description="ISO tarih/zaman (dahil)")] = None,
    to: Annotated[datetime | date | None, Query(description="ISO tarih/zaman (dahil)")] = None,
    limit: LimitQ = DEFAULT_LIMIT, cursor: CursorQ = None,
):
    """Ölçümler, ölçüm zamanına göre yeniden eskiye."""
    patient = _resolve_patient(db, user, patient_id)
    q = select(Measurement).where(Measurement.patient_id == patient.id)
    if type:
        q = q.where(Measurement.type == type)
    if from_:
        q = q.where(Measurement.measured_at >= _as_datetime(from_, end=False))
    if to:
        q = q.where(Measurement.measured_at <= _as_datetime(to, end=True))
    items, next_cursor = paginate(db, q, Measurement.measured_at, Measurement.id, limit=limit, cursor=cursor)
    return Page[MeasurementOut](items=items, next_cursor=next_cursor)
