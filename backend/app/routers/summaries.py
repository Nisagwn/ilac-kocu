from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.deps import DB, CurrentUser, get_patient_for_read
from app.services.summary import get_narrative_client, weekly_summary
from app.services.weekly_summaries import latest_stored_summary, stored_payload

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("/{patient_id}")
def get_summary(
    patient_id: int, db: DB, user: CurrentUser, llm_client: Annotated[object, Depends(get_narrative_client)],
    source: Annotated[Literal["auto", "live"], Query(
        description="auto: son 7 günde saklanan haftalık özet varsa o, yoksa anlık üretim; live: her zaman anlık")
    ] = "auto",
):
    """Hastanın haftalık özeti: uyum, gün dilimine göre kaçırılan dozlar, ölçüm istatistikleri, uyarı sayısı
    ve hekim için kısa anlatı (`narrative`; LLM varsa Claude ile, yoksa şablonla — `narrative_source`).

    Her pazartesi 07:00'de üretilip saklanan özet varsa (`stored: true`, `generated_at`, `period_start`,
    `period_end`) o döner; yoksa özet anlık üretilir (bu alanlar olmadan). Okuma yetkisi gerekir."""
    get_patient_for_read(db, user, patient_id)
    if source != "live":
        row = latest_stored_summary(db, patient_id)
        if row is not None:
            return stored_payload(row)
    return weekly_summary(db, patient_id, llm_client=llm_client)
