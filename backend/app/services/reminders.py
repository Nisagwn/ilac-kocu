"""Doz hatırlatma zamanlayıcısı.

Her dakika çalışır: saati (settings.timezone) son 15 dakika içinde gelmiş, henüz işaretlenmemiş ve
henüz hatırlatılmamış planlı dozlar için hastanın tüm tarayıcı aboneliklerine web push gönderir.
Bir planlı doz için gün başına en fazla bir hatırlatma yapılır (reminder_logs).
"""

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import DoseLog, Medication, MedicationSchedule, Patient, ReminderLog
from app.services.push import PushSender, send_to_user, webpush_sender

log = logging.getLogger(__name__)

REMINDER_WINDOW = timedelta(minutes=15)


def build_payload(med: Medication, sched: MedicationSchedule) -> dict:
    # Gizlilik: bildirim kilit ekranında görünür; ilaç adı/dozu (sağlık verisi) yazılmaz.
    # Ayrıntı uygulama açılınca /bugun ekranında gösterilir.
    return {
        "title": "İlaç zamanı",
        "body": f"Saat {sched.time_of_day.strftime('%H:%M')} dozunu işaretlemeyi unutma.",
        "url": "/bugun",
    }


def due_schedules(db: Session, now: datetime) -> list[tuple[MedicationSchedule, Medication, Patient, date]]:
    """Hatırlatma zamanı gelmiş (planlı saat ∈ [now-15dk, now]) ve henüz işaretlenmemiş/hatırlatılmamış dozlar."""
    tz = ZoneInfo(get_settings().timezone)
    local_now = now.astimezone(tz)
    window_start = local_now - REMINDER_WINDOW
    days = sorted({window_start.date(), local_now.date()})  # gece yarısını kesen pencere için iki gün

    rows = db.execute(
        select(MedicationSchedule, Medication, Patient)
        .join(Medication, MedicationSchedule.medication_id == Medication.id)
        .join(Patient, Medication.patient_id == Patient.id)
        .where(Medication.active.is_(True))
    ).all()

    due = []
    for sched, med, patient in rows:
        for day in days:
            planned = datetime.combine(day, sched.time_of_day, tzinfo=tz)
            if not (window_start <= planned <= local_now):
                continue
            if str(day.isoweekday()) not in sched.days_of_week or sched.start_date > day:
                continue
            already_logged = db.scalar(
                select(DoseLog.id).where(DoseLog.schedule_id == sched.id, DoseLog.scheduled_date == day)
            )
            already_reminded = db.scalar(
                select(ReminderLog.id).where(ReminderLog.schedule_id == sched.id, ReminderLog.scheduled_date == day)
            )
            if already_logged is None and already_reminded is None:
                due.append((sched, med, patient, day))
    return due


def send_due_reminders(db: Session, now: datetime | None = None, sender: PushSender = webpush_sender) -> int:
    """Zamanı gelen dozlar için push gönderir; gönderilen hatırlatma (doz) sayısını döner.

    En az bir aboneliğe ulaşılırsa hatırlatma kaydedilir; hiç ulaşılamazsa (abonelik yok / geçici hata)
    pencere süresince sonraki dakikalarda yeniden denenir.
    """
    now = now or datetime.now(UTC)
    reminded = 0
    for sched, med, patient, day in due_schedules(db, now):
        if send_to_user(db, patient.user_id, build_payload(med, sched), sender) > 0:
            db.add(ReminderLog(schedule_id=sched.id, scheduled_date=day))
            reminded += 1
    db.commit()
    return reminded


# ---------------- Zamanlayıcı ----------------
# İş tanımı ve başlatma app/scheduler.py'dedir (lider işçide). Bu yardımcı geriye dönük uyumluluk içindir.
def scheduler_should_start(settings: Settings) -> bool:
    import sys

    if "pytest" in sys.modules:  # testlerde asla arka plan işi başlatma
        return False
    return settings.reminders_enabled and settings.vapid_configured
