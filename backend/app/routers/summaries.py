from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.deps import DB, CurrentUser, get_patient_for_read
from app.models import WeeklySummary
from app.services.summary import get_narrative_client, weekly_summary

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("/{patient_id}")
def get_summary(patient_id: int, db: DB, user: CurrentUser, llm_client: Annotated[object, Depends(get_narrative_client)]):
    """Hastanın haftalık özeti: uyum, gün dilimine göre kaçırılan dozlar, ölçüm istatistikleri, uyarı sayısı
    ve hekim için kısa anlatı (`narrative`; LLM varsa Claude ile, yoksa şablonla — `narrative_source`).
    Okuma yetkisi gerekir."""
    get_patient_for_read(db, user, patient_id)
    return weekly_summary(db, patient_id, llm_client=llm_client)


class StoredSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_start: date
    period_end: date
    payload: dict
    narrative_source: str | None
    created_at: datetime


@router.get("/{patient_id}/history", response_model=list[StoredSummaryOut])
def summary_history(patient_id: int, db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=52)] = 12):
    """Zamanlanmış (Pazartesi 07:00) üretilip saklanan haftalık özetler, yeniden eskiye. Okuma yetkisi gerekir."""
    get_patient_for_read(db, user, patient_id)
    return db.scalars(select(WeeklySummary).where(WeeklySummary.patient_id == patient_id)
                      .order_by(WeeklySummary.period_start.desc()).limit(limit)).all()
