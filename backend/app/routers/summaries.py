from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import DB, CurrentUser, get_patient_for_read
from app.services.summary import get_narrative_client, weekly_summary

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("/{patient_id}")
def get_summary(patient_id: int, db: DB, user: CurrentUser, llm_client: Annotated[object, Depends(get_narrative_client)]):
    """Hastanın haftalık özeti: uyum, gün dilimine göre kaçırılan dozlar, ölçüm istatistikleri, uyarı sayısı
    ve hekim için kısa anlatı (`narrative`; LLM varsa Claude ile, yoksa şablonla — `narrative_source`).
    Okuma yetkisi gerekir."""
    get_patient_for_read(db, user, patient_id)
    return weekly_summary(db, patient_id, llm_client=llm_client)
