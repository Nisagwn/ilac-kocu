from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import DB, CurrentUser, get_patient_for_read, get_patient_for_write
from app.models import DoseLog, Medication, MedicationSchedule
from app.pagination import MAX_PAGE, paginate, set_next_page
from app.schemas import DoseLogIn, DoseLogOut, TodayDoseOut

router = APIRouter(prefix="/patients/{patient_id}/doses", tags=["doses"])


def local_today() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(get_settings().timezone)).date()


@router.get("/today", response_model=list[TodayDoseOut])
def today(patient_id: int, db: DB, user: CurrentUser, day: date | None = None):
    """Hastanın 'Bugün' ekranı: o günün planlı dozları ve durumları."""
    get_patient_for_read(db, user, patient_id)
    day = day or local_today()
    rows = db.execute(
        select(MedicationSchedule, Medication)
        .join(Medication)
        .where(Medication.patient_id == patient_id, Medication.active.is_(True))
        .order_by(MedicationSchedule.time_of_day)
    ).all()
    logs = {
        lg.schedule_id: lg.status
        for lg in db.scalars(select(DoseLog).where(DoseLog.patient_id == patient_id, DoseLog.scheduled_date == day))
    }
    return [
        TodayDoseOut(
            schedule_id=s.id, medication_id=m.id, medication_name=m.name, dose_text=m.dose_text,
            time_of_day=s.time_of_day, status=logs.get(s.id),
        )
        for s, m in rows
        if str(day.isoweekday()) in s.days_of_week and s.start_date <= day
    ]


@router.put("", response_model=DoseLogOut)
def log_dose(patient_id: int, data: DoseLogIn, db: DB, user: CurrentUser):
    """'Aldım / almadım' işaretleme. Aynı gün aynı doz için tekrar çağrılırsa kaydı günceller (idempotent)."""
    get_patient_for_write(db, user, patient_id)
    sched = db.get(MedicationSchedule, data.schedule_id)
    if sched is None or sched.medication.patient_id != patient_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doz planı bulunamadı")
    today_ = local_today()
    if data.scheduled_date > today_:
        raise HTTPException(422, "Gelecek tarihli doz işaretlenemez")
    if data.scheduled_date < today_ - timedelta(days=7):
        raise HTTPException(422, "7 günden eski dozlar değiştirilemez")
    if str(data.scheduled_date.isoweekday()) not in sched.days_of_week:
        raise HTTPException(422, "Bu gün için planlı doz yok")

    log = db.scalar(
        select(DoseLog).where(DoseLog.schedule_id == sched.id, DoseLog.scheduled_date == data.scheduled_date)
    )
    if log is None:
        log = DoseLog(patient_id=patient_id, schedule_id=sched.id, scheduled_date=data.scheduled_date)
        db.add(log)
    log.status = data.status
    log.reason = data.reason
    log.logged_at = datetime.now(UTC)
    db.commit()
    db.refresh(log)
    return log


@router.get("", response_model=list[DoseLogOut])
def list_doses(
    patient_id: int, db: DB, user: CurrentUser, request: Request, response: Response,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    limit: Annotated[int | None, Query(ge=1, le=MAX_PAGE, description="Sayfa boyutu (cursor sayfalama)")] = None,
    cursor: Annotated[str | None, Query(description="Önceki yanıtın X-Next-Cursor değeri")] = None,
):
    """Yeniden eskiye doz kayıtları. `limit` verilirse sayfalanır; sonraki sayfa `X-Next-Cursor` / `Link`."""
    get_patient_for_read(db, user, patient_id)
    since = local_today() - timedelta(days=days - 1)
    q = select(DoseLog).where(DoseLog.patient_id == patient_id, DoseLog.scheduled_date >= since)
    if limit is None and cursor is None:
        return db.scalars(q.order_by(DoseLog.scheduled_date.desc(), DoseLog.id.desc())).all()
    limit = limit or 50
    rows, nxt = paginate(db, q, [DoseLog.scheduled_date, DoseLog.id], descending=True, cursor=cursor, limit=limit)
    set_next_page(response, request, nxt, limit)
    return rows
