"""Hasta verisinin LLM'e (tool sonucu) ve hekim özetine giden özetleri.

Gizlilik: buradaki çıktılarda ad, e-posta, kimlik bilgisi YOKTUR; yalnızca sayısal özetler,
ilaç adları ve saatler bulunur (docs/M1/03-veri-gizliligi.md §3). Tüm fonksiyonlar patient_id'yi
sunucu tarafından alır.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DoseLog, DoseStatus, Measurement, MeasurementType, Medication, MedicationSchedule
from app.services.adherence import LogSpec, ScheduleSpec, compute_adherence
from app.services.coach import get_adherence
from app.services.thresholds import evaluate_measurement

ALLOWED_DAYS = (7, 30)

MEASUREMENT_LABELS = {
    MeasurementType.glucose_fasting.value: ("Açlık kan şekeri", "mg/dL"),
    MeasurementType.glucose_postprandial.value: ("Tokluk kan şekeri", "mg/dL"),
    MeasurementType.blood_pressure.value: ("Tansiyon", "mmHg"),
}

# Doz saatini gün dilimine eşler (kaçırılan doz örüntüsü için)
TIME_SLOTS = (("sabah", 5, 11), ("öğle", 11, 15), ("akşam", 15, 21))


def clamp_days(days) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        return 7
    return d if d in ALLOWED_DAYS else (30 if d > 7 else 7)


def _now_local(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(get_settings().timezone))


def _slot(hour: int) -> str:
    for name, start, end in TIME_SLOTS:
        if start <= hour < end:
            return name
    return "gece"


def medication_names(db: Session, patient_id: int) -> dict[int, str]:
    return dict(db.execute(select(Medication.id, Medication.name).where(Medication.patient_id == patient_id)).all())


def missed_by_time_slot(db: Session, patient_id: int, days: int = 7, now: datetime | None = None) -> dict:
    """Gün dilimine göre planlanan/kaçırılan doz sayısı: {"akşam": {"planned": 7, "missed": 4}, ...}."""
    local_now = _now_local(now)
    end = local_now.date()
    start = end - timedelta(days=days - 1)
    rows = db.execute(
        select(MedicationSchedule, Medication.id).join(Medication)
        .where(Medication.patient_id == patient_id, Medication.active.is_(True))
    ).all()
    logs = db.scalars(
        select(DoseLog).where(DoseLog.patient_id == patient_id, DoseLog.scheduled_date >= start,
                              DoseLog.scheduled_date <= end)
    ).all()
    out: dict[str, dict] = {}
    for sched, med_id in rows:
        spec = ScheduleSpec(sched.id, med_id, sched.time_of_day, sched.days_of_week, sched.start_date)
        own_logs = [LogSpec(lg.schedule_id, lg.scheduled_date, lg.status == DoseStatus.taken)
                    for lg in logs if lg.schedule_id == sched.id]
        res = compute_adherence([spec], own_logs, start, end, as_of=local_now)
        if res.planned == 0:
            continue
        bucket = out.setdefault(_slot(sched.time_of_day.hour), {"planned": 0, "missed": 0})
        bucket["planned"] += res.planned
        bucket["missed"] += res.planned - res.taken
    return out


def adherence_payload(db: Session, patient_id: int, days: int = 7, now: datetime | None = None) -> dict:
    days = clamp_days(days)
    adh = get_adherence(db, patient_id, days=days, now=now)
    names = medication_names(db, patient_id)
    return {
        "period_days": days,
        "planned": adh.planned,
        "taken": adh.taken,
        "missed": adh.planned - adh.taken,
        "skipped_marked": adh.skipped,
        "not_logged": adh.missing,
        "score": adh.score,
        "percent": adh.percent,
        "by_medication": [
            {"medication": names.get(mid, f"ilaç-{mid}"), "planned": m["planned"], "taken": m["taken"],
             "score": m["score"]}
            for mid, m in sorted(adh.by_medication.items())
        ],
        "missed_by_time_of_day": missed_by_time_slot(db, patient_id, days, now),
        "days_with_missed_dose": len(adh.missed_dates),
    }


def _as_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:  # SQLite saat dilimini saklamaz; değerler UTC'dir
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZoneInfo(get_settings().timezone))


def measurements_payload(db: Session, patient_id: int, days: int = 7, mtype: str | None = None,
                         now: datetime | None = None, recent: int = 5) -> dict:
    days = clamp_days(days)
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    q = select(Measurement).where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
    if mtype and mtype != "all":
        try:
            q = q.where(Measurement.type == MeasurementType(mtype))
        except ValueError:
            return {"period_days": days, "error": f"bilinmeyen ölçüm türü: {mtype}", "types": []}
    rows = db.scalars(q.order_by(Measurement.measured_at.desc())).all()

    groups: dict[str, list[Measurement]] = {}
    for m in rows:
        groups.setdefault(m.type.value, []).append(m)

    types = []
    for t, items in sorted(groups.items()):
        vals = [m.value for m in items]
        label, unit = MEASUREMENT_LABELS.get(t, (t, items[0].unit))
        entry = {
            "type": t, "label": label, "unit": unit, "count": len(items),
            "avg": round(sum(vals) / len(vals), 1), "min": min(vals), "max": max(vals),
            "out_of_range_count": sum(1 for m in items if evaluate_measurement(m.type, m.value, m.value2)),
            "recent": [
                {"date": _as_local(m.measured_at).strftime("%Y-%m-%d %H:%M"), "value": m.value,
                 **({"diastolic": m.value2} if m.value2 is not None else {})}
                for m in items[:recent]
            ],
        }
        dia = [m.value2 for m in items if m.value2 is not None]
        if dia:
            entry.update(avg_diastolic=round(sum(dia) / len(dia), 1), min_diastolic=min(dia), max_diastolic=max(dia))
        types.append(entry)
    return {"period_days": days, "types": types}


def medications_payload(db: Session, patient_id: int) -> dict:
    meds = db.scalars(
        select(Medication).where(Medication.patient_id == patient_id, Medication.active.is_(True))
        .order_by(Medication.id)
    ).all()
    return {
        "note": "Doz bilgisi yalnızca bağlamdır; doz değişikliği önerilemez.",
        "medications": [
            {
                "name": m.name,
                "active_ingredient": m.active_ingredient,
                "dose_text": m.dose_text,
                "schedule": [
                    {"time": s.time_of_day.strftime("%H:%M"), "days_of_week": s.days_of_week}
                    for s in sorted(m.schedules, key=lambda s: s.time_of_day)
                ],
            }
            for m in meds
        ],
    }
