from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.deps import DB, CurrentUser, get_patient_for_read, require_roles
from app.models import Alert, Patient, Role, User
from app.pagination import MAX_LIMIT, paginate, set_next_cursor_headers
from app.schemas import AlertOut

router = APIRouter(tags=["alerts"])


@router.get("/patients/{patient_id}/alerts", response_model=list[AlertOut])
def patient_alerts(
    patient_id: int, db: DB, user: CurrentUser, open_only: bool = False,
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT)] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    *, request: Request, response: Response,
):
    """Hastanın uyarıları, yeniden eskiye. `limit`/`cursor` verilirse sayfalı; sonraki sayfa `X-Next-Cursor`."""
    get_patient_for_read(db, user, patient_id)
    q = select(Alert).where(Alert.patient_id == patient_id)
    if open_only:
        q = q.where(Alert.acknowledged_at.is_(None))
    if limit is None and cursor is None:
        return db.scalars(q.order_by(Alert.created_at.desc())).all()
    items, next_cursor = paginate(db, q, Alert.created_at, Alert.id, limit=limit or 50, cursor=cursor)
    set_next_cursor_headers(request, response, next_cursor)
    return items


@router.get("/alerts", response_model=list[AlertOut])
def my_open_alerts(db: DB, user: Annotated[User, Depends(require_roles(Role.clinician))]):
    """Hekim: tüm hastalarındaki açık uyarılar, kritik olanlar önce."""
    alerts = db.scalars(
        select(Alert)
        .join(Patient)
        .where(Patient.clinician_id == user.clinician.id, Alert.acknowledged_at.is_(None))
        .order_by(Alert.created_at.desc())
    ).all()
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a.severity.value])


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def acknowledge(alert_id: int, db: DB, user: Annotated[User, Depends(require_roles(Role.clinician))]):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Uyarı bulunamadı")
    get_patient_for_read(db, user, alert.patient_id)
    alert.acknowledged_at = datetime.now(UTC)
    db.commit()
    db.refresh(alert)
    return alert
