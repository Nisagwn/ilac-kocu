from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.deps import DB, CurrentUser, get_patient_for_read, get_patient_for_write
from app.models import Alert, Measurement, MeasurementType
from app.schemas import AlertOut, MeasurementCreated, MeasurementIn, MeasurementOut
from app.services.thresholds import evaluate_measurement

router = APIRouter(prefix="/patients/{patient_id}/measurements", tags=["measurements"])

UNITS = {
    MeasurementType.glucose_fasting: "mg/dL",
    MeasurementType.glucose_postprandial: "mg/dL",
    MeasurementType.blood_pressure: "mmHg",
}


@router.post("", response_model=MeasurementCreated, status_code=status.HTTP_201_CREATED)
def create_measurement(patient_id: int, data: MeasurementIn, db: DB, user: CurrentUser):
    get_patient_for_write(db, user, patient_id)
    m = Measurement(
        patient_id=patient_id, type=data.type, value=data.value, value2=data.value2, unit=UNITS[data.type],
        note=data.note, measured_at=data.measured_at or datetime.now(UTC),
    )
    db.add(m)
    alert = None
    if hit := evaluate_measurement(data.type, data.value, data.value2):
        alert = Alert(patient_id=patient_id, severity=hit.severity, kind="out_of_range", message=hit.message)
        db.add(alert)
    db.commit()
    db.refresh(m)
    out = MeasurementCreated.model_validate(m)
    if alert:
        db.refresh(alert)
        out.alert = AlertOut.model_validate(alert)
    return out


@router.get("", response_model=list[MeasurementOut])
def list_measurements(
    patient_id: int, db: DB, user: CurrentUser,
    type: MeasurementType | None = None,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    get_patient_for_read(db, user, patient_id)
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(Measurement).where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
    if type:
        q = q.where(Measurement.type == type)
    return db.scalars(q.order_by(Measurement.measured_at)).all()


@router.delete("/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_measurement(patient_id: int, measurement_id: int, db: DB, user: CurrentUser):
    get_patient_for_write(db, user, patient_id)
    m = db.get(Measurement, measurement_id)
    if m and m.patient_id == patient_id:
        db.delete(m)
        db.commit()
