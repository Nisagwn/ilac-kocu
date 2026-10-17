from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from app.deps import DB, CurrentUser, get_patient_for_read, get_patient_for_write
from app.models import Alert, Measurement, MeasurementType
from app.pagination import MAX_PAGE, paginate, set_next_page
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
    patient_id: int, db: DB, user: CurrentUser, request: Request, response: Response,
    type: MeasurementType | None = None,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int | None, Query(ge=1, le=MAX_PAGE, description="Sayfa boyutu (cursor sayfalama)")] = None,
    cursor: Annotated[str | None, Query(description="Önceki yanıtın X-Next-Cursor değeri")] = None,
):
    """Zamana göre artan sırada ölçümler. `limit` verilirse sayfalanır; sonraki sayfa `X-Next-Cursor` / `Link`."""
    get_patient_for_read(db, user, patient_id)
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(Measurement).where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
    if type:
        q = q.where(Measurement.type == type)
    if limit is None and cursor is None:
        return db.scalars(q.order_by(Measurement.measured_at, Measurement.id)).all()
    limit = limit or 50
    rows, nxt = paginate(db, q, [Measurement.measured_at, Measurement.id], descending=False, cursor=cursor,
                         limit=limit)
    set_next_page(response, request, nxt, limit)
    return rows


@router.delete("/{measurement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_measurement(patient_id: int, measurement_id: int, db: DB, user: CurrentUser):
    get_patient_for_write(db, user, patient_id)
    m = db.get(Measurement, measurement_id)
    if m and m.patient_id == patient_id:
        db.delete(m)
        db.commit()
