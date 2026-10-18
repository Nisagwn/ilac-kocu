"""Zamanlanmış haftalık özetler.

Her pazartesi 07:00'de (settings.timezone, varsayılan Europe/Istanbul; WEEKLY_SUMMARY_* ayarları) yalnızca
lider worker'da çalışır (app/services/leader.py). Her aktif hasta için geçen 7 günün özeti mevcut
`weekly_summary()` ile üretilir ve `weekly_summaries` tablosuna yazılır. Anlatı LLM bütçesine, günlük özet
sınırına ve devre kesiciye tabidir (açıksa şablon). Aynı (hasta, dönem) için ikinci çalıştırma kayıt eklemez.

Aktif hasta: onam vermiş ve aktif ilacı ya da dönem içinde ölçümü olan hasta.

Hekim bildirimi: hastası için özet üretilen her hekime TEK web push ("N hastanızın haftalık özeti hazır");
kilit ekranında hasta adı/sağlık verisi yer almaz. VAPID yapılandırılmamışsa push atlanır; özetler yine
GET /summaries/{patient_id} ile döner.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Clinician, Measurement, Medication, Patient, WeeklySummary
from app.services.push import PushSender, send_to_user, webpush_sender
from app.services.summary import weekly_summary

logger = logging.getLogger(__name__)

PERIOD_DAYS = 7
STORED_MAX_AGE = timedelta(days=7)  # GET /summaries bu süreden eski kaydı değil anlık özeti döndürür


def _period(now: datetime, tz: str) -> tuple:
    end = (now.astimezone(ZoneInfo(tz)) - timedelta(days=1)).date()  # dün dahil (pazartesi sabahı: pazar)
    return end - timedelta(days=PERIOD_DAYS - 1), end


def active_patient_ids(db: Session, since: datetime) -> list[int]:
    has_med = exists().where(Medication.patient_id == Patient.id, Medication.active.is_(True))
    has_meas = exists().where(Measurement.patient_id == Patient.id, Measurement.measured_at >= since)
    return list(db.scalars(
        select(Patient.id).where(Patient.consent_at.is_not(None), has_med | has_meas).order_by(Patient.id)))


def notification_payload(count: int) -> dict:
    return {"title": "Haftalık özetler hazır", "body": f"{count} hastanızın haftalık özeti hazır.", "url": "/hekim"}


def generate_weekly_summaries(db: Session, now: datetime | None = None, llm_client=None,
                              sender: PushSender | None = None, settings: Settings | None = None) -> dict:
    """Tüm aktif hastalar için özeti üretir ve saklar. Dönüş: {"created", "skipped", "failed", "notified"}."""
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    start, end = _period(now, s.timezone)
    created = skipped = failed = 0
    per_clinician: Counter[int] = Counter()

    for pid in active_patient_ids(db, now - timedelta(days=PERIOD_DAYS)):
        if db.scalar(select(WeeklySummary.id).where(WeeklySummary.patient_id == pid, WeeklySummary.period_end == end)):
            skipped += 1
            continue
        try:
            data = weekly_summary(db, pid, days=PERIOD_DAYS, llm_client=llm_client, now=now)
            db.add(WeeklySummary(patient_id=pid, period_start=start, period_end=end, data=data,
                                 narrative_source=data.get("narrative_source"), generated_at=now))
            db.commit()
        except IntegrityError:  # başka bir kopya aynı anda yazdı
            db.rollback()
            skipped += 1
            continue
        except Exception as exc:  # bir hastanın hatası diğerlerini durdurmaz
            db.rollback()
            failed += 1
            logger.warning("Hasta %s için haftalık özet üretilemedi (%s)", pid, type(exc).__name__)
            continue
        created += 1
        clinician_id = db.scalar(select(Patient.clinician_id).where(Patient.id == pid))
        if clinician_id:
            per_clinician[clinician_id] += 1

    notified = 0
    if per_clinician and (sender is not None or s.vapid_configured):
        for clinician_id, count in per_clinician.items():
            user_id = db.scalar(select(Clinician.user_id).where(Clinician.id == clinician_id))
            if user_id and send_to_user(db, user_id, notification_payload(count), sender or webpush_sender) > 0:
                notified += 1
        db.commit()
    result = {"created": created, "skipped": skipped, "failed": failed, "notified": notified}
    logger.info("Haftalık özetler: %s", result)
    return result


def latest_stored_summary(db: Session, patient_id: int, now: datetime | None = None) -> WeeklySummary | None:
    """Son 7 gün içinde üretilmiş en yeni saklı özet (yoksa None)."""
    now = now or datetime.now(UTC)
    row = db.scalars(select(WeeklySummary).where(WeeklySummary.patient_id == patient_id)
                     .order_by(WeeklySummary.period_end.desc(), WeeklySummary.id.desc()).limit(1)).first()
    if row is None:
        return None
    generated = row.generated_at if row.generated_at.tzinfo else row.generated_at.replace(tzinfo=UTC)
    return row if now - generated <= STORED_MAX_AGE else None


def stored_payload(row: WeeklySummary) -> dict:
    return {**row.data, "stored": True, "generated_at": row.generated_at.isoformat(),
            "period_start": row.period_start.isoformat(), "period_end": row.period_end.isoformat()}
