"""Zamanlanmış haftalık özet (varsayılan Pazartesi 07:00, TIMEZONE): yalnızca lider işçide çalışır.

Hekime atanmış her hasta için son 7 günün özeti (services/summary.weekly_summary: sayısal özet + anlatı;
LLM varsa maliyet limitleri içinde Claude, yoksa şablon) `weekly_summaries` tablosuna yazılır. Aynı dönem
için ikinci kez üretilmez (idempotent: iş tekrar tetiklense de çift kayıt/bildirim olmaz).
Ardından her hekime TEK web push bildirimi gider; bildirimde hasta adı ya da sağlık verisi yer almaz.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Clinician, Patient, WeeklySummary
from app.services.push import PushSender, send_to_user, webpush_sender
from app.services.summary import weekly_summary

log = logging.getLogger(__name__)


def generate_weekly_summaries(db: Session, now: datetime | None = None, sender: PushSender | None = webpush_sender,
                              llm_client=None) -> dict:
    """Döndürür: {"created": n, "skipped": n, "notified": n}. Hasta başına hata diğerlerini durdurmaz."""
    now = now or datetime.now(UTC)
    local_today = now.astimezone(ZoneInfo(get_settings().timezone)).date()
    period_start, period_end = local_today - timedelta(days=6), local_today
    per_clinician: Counter[int] = Counter()
    created = skipped = 0
    for patient in db.scalars(select(Patient).where(Patient.clinician_id.is_not(None))).all():
        exists = db.scalar(select(WeeklySummary.id).where(WeeklySummary.patient_id == patient.id,
                                                          WeeklySummary.period_start == period_start))
        if exists:
            skipped += 1
            continue
        try:
            payload = weekly_summary(db, patient.id, days=7, llm_client=llm_client)
            db.add(WeeklySummary(patient_id=patient.id, period_start=period_start, period_end=period_end,
                                 payload=payload, narrative_source=payload.get("narrative_source")))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Hasta %s için haftalık özet üretilemedi", patient.id)
            continue
        created += 1
        per_clinician[patient.clinician_id] += 1

    notified = 0
    if sender is not None and get_settings().vapid_configured:
        for clinician_id, n in per_clinician.items():
            clinician = db.get(Clinician, clinician_id)
            if clinician is None:
                continue
            payload = {"title": "Haftalık özetler hazır", "body": f"{n} hastanızın haftalık özeti hazır.",
                       "url": "/hekim"}
            notified += 1 if send_to_user(db, clinician.user_id, payload, sender) > 0 else 0
        db.commit()
    log.info("Haftalık özet: %s üretildi, %s zaten vardı, %s hekime bildirildi", created, skipped, notified)
    return {"created": created, "skipped": skipped, "notified": notified}
