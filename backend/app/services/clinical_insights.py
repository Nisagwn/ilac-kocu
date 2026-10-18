"""Hekim dashboard'u için klinik içgörüler — deterministik, LLM'den bağımsız, test edilebilir.

Bu modül TEŞHİS KOYMAZ ve tedavi önermez; hekimin değerlendirmesi için hastanın kendi girdiği verinin
sayısal özetini üretir:

1. Uyum eğilimi: bu hafta (son 7 gün, bugün dahil) ile geçen hafta (ondan önceki 7 gün) uyum skoru,
   yüzde puan farkı ve yön. Skor `services/adherence.compute_adherence` ile hesaplanır (aynı tanım).
   |fark| < TREND_FLAT_POINTS puan ise yön "flat" (=) kabul edilir: 7 günlük pencerede tek doz bile
   birkaç puan oynattığı için küçük dalgalanmalar ok ile abartılmaz.
2. Hedefte kalma oranı (TIR benzeri): kan şekeri (açlık 80–130, tokluk <180 mg/dL) ve tansiyon
   (<130/80 mmHg) ölçümlerinin hedef içinde kalan oranı, ortalama ve adet; 30 gün + son 7 gün.
   Eşiklerin tek kaynağı ve literatür dayanağı: `services/thresholds.py`.
   Not: gerçek TIR sürekli glukoz izlemi (CGM) ister; burada parmak ucu ölçümlerinin oranıdır.
3. Doz atlama nedenleri: son 30 günde "almadım" (skipped) işaretlenen dozların serbest metin
   nedenleri basit anahtar kelime kurallarıyla kategorilere ayrılır.
4. Ölçüm sıklığı: son 7 günde ölçüm girilen gün sayısı (veri eksikliği sinyali).

Pencereler hastanın yerel saat dilimindeki (ayarlar: `timezone`) takvim günleridir.
Saf fonksiyonlar (ön ekli `_` olmayanlar, DB almayanlar) doğrudan birim testlerle doğrulanır;
DB'den veri yükleyen yardımcılar dosyanın sonundadır.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DoseLog, DoseStatus, Measurement, MeasurementType, Medication, MedicationSchedule
from app.services.adherence import AdherenceResult, LogSpec, ScheduleSpec, compute_adherence
from app.services.guardrails import normalize
from app.services.thresholds import TARGET_LABELS, is_in_target

TREND_FLAT_POINTS = 5  # yüzde puan; bunun altındaki değişim "değişim yok" sayılır
MEASUREMENT_WINDOW_DAYS = 30
RECENT_WINDOW_DAYS = 7
SKIP_REASON_WINDOW_DAYS = 30
MIN_MEASUREMENT_DAYS_PER_WEEK = 3  # son 7 günde bundan az ölçüm günü: "veri eksik" sinyali

DISCLAIMER = "Hekim değerlendirmesi için veri özetidir; teşhis veya tedavi önerisi değildir."


def _half_up_ratio(n: int, total: int) -> float | None:
    """n/total oranını 3 basamağa yarım-yukarı yuvarlar (adherence._ratio ile aynı yaklaşım)."""
    if not total:
        return None
    return ((n * 2000 + total) // (total * 2)) / 1000


def _half_up_percent(n: int, total: int) -> int:
    return (n * 200 + total) // (total * 2) if total else 0


def _local_tz():
    return ZoneInfo(get_settings().timezone)


# --- 1. Uyum eğilimi --------------------------------------------------------------------------


@dataclass(frozen=True)
class AdherenceTrend:
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date
    current_score: float | None
    previous_score: float | None
    current_percent: int | None
    previous_percent: int | None
    delta_points: int | None  # current_percent - previous_percent
    direction: str | None  # up | down | flat; iki haftadan biri veri içermiyorsa None


def trend_direction(delta_points: int | None) -> str | None:
    if delta_points is None:
        return None
    if delta_points >= TREND_FLAT_POINTS:
        return "up"
    if delta_points <= -TREND_FLAT_POINTS:
        return "down"
    return "flat"


def adherence_trend(current: AdherenceResult, previous: AdherenceResult) -> AdherenceTrend:
    cur, prev = current.percent, previous.percent
    delta = cur - prev if cur is not None and prev is not None else None
    return AdherenceTrend(
        current_start=current.start, current_end=current.end,
        previous_start=previous.start, previous_end=previous.end,
        current_score=current.score, previous_score=previous.score,
        current_percent=cur, previous_percent=prev,
        delta_points=delta, direction=trend_direction(delta),
    )


def weekly_adherence_trend(
    schedules: list[ScheduleSpec], logs: list[LogSpec], as_of: datetime
) -> AdherenceTrend:
    """as_of (yerel saat) gününü içeren son 7 gün ile ondan önceki 7 günü karşılaştırır."""
    today = as_of.date()
    current = compute_adherence(schedules, logs, today - timedelta(days=6), today, as_of=as_of)
    previous = compute_adherence(
        schedules, logs, today - timedelta(days=13), today - timedelta(days=7), as_of=as_of
    )
    return adherence_trend(current, previous)


# --- 2. Hedefte kalma oranı ---------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementPoint:
    type: MeasurementType
    day: date  # yerel takvim günü
    value: float
    value2: float | None = None


@dataclass(frozen=True)
class TargetStats:
    count: int
    in_target: int
    in_target_ratio: float | None  # 0..1, ölçüm yoksa None
    avg: float | None  # kan şekeri mg/dL ya da sistolik mmHg (1 basamak)
    avg_diastolic: float | None = None  # yalnızca tansiyon


def target_stats(points: list[MeasurementPoint], mtype: MeasurementType, start: date, end: date) -> TargetStats:
    sel = [p for p in points if p.type == mtype and start <= p.day <= end]
    if not sel:
        return TargetStats(0, 0, None, None, None)
    hits = sum(1 for p in sel if is_in_target(p.type, p.value, p.value2))
    avg = round(sum(p.value for p in sel) / len(sel), 1)
    avg2 = None
    if mtype == MeasurementType.blood_pressure:
        dia = [p.value2 for p in sel if p.value2 is not None]
        avg2 = round(sum(dia) / len(dia), 1) if dia else None
    return TargetStats(len(sel), hits, _half_up_ratio(hits, len(sel)), avg, avg2)


def target_summary(points: list[MeasurementPoint], today: date) -> dict[str, dict]:
    """Her ölçüm türü için 30 günlük ve 7 günlük hedefte kalma istatistiği."""
    s30 = today - timedelta(days=MEASUREMENT_WINDOW_DAYS - 1)
    s7 = today - timedelta(days=RECENT_WINDOW_DAYS - 1)
    out: dict[str, dict] = {}
    for mtype in MeasurementType:
        out[mtype.value] = {
            "target": TARGET_LABELS[mtype],
            "window_30d": target_stats(points, mtype, s30, today).__dict__,
            "window_7d": target_stats(points, mtype, s7, today).__dict__,
        }
    return out


# --- 3. Doz atlama nedenleri --------------------------------------------------------------------

SKIP_CATEGORY_LABELS: dict[str, str] = {
    "yan_etki": "Yan etki / rahatsızlık",
    "ilac_bitti": "İlaç bitti / temin edilemedi",
    "unuttum": "Unuttum",
    "iyi_hissetti": "Kendimi iyi hissettim",
    "diger": "Diğer / belirtilmedi",
}

# Sıra önceliktir: bir neden birden çok kurala uyuyorsa ilk eşleşen kategori seçilir (klinik olarak
# en önemli olan yan etki en başta). Anahtar kelimeler `guardrails.normalize` sonrası (küçük harf,
# Türkçe karakterler ASCII'ye çevrilmiş) metinde alt dize olarak aranır.
SKIP_REASON_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("yan_etki", (
        "yan etki", "yanetki", "mide", "bulanti", "kusma", "ishal", "bas don", "basim don", "bas agri",
        "basim agri", "kasinti", "dokuntu", "halsiz", "kotu hisset", "rahatsiz", "kramp", "agri yap",
        "iyi hissetmed", "iyi degil",
    )),
    ("ilac_bitti", (
        "bitti", "bitmis", "kalmadi", "tukendi", "eczane", "recete", "temin", "bulamadim",
    )),
    ("unuttum", (
        "unut", "aklimdan cik", "hatirlamadim", "gozden kac", "uyuyakal", "uyuya kal",
    )),
    ("iyi_hissetti", (
        "iyi hisset", "iyiyim", "iyi gibi", "gerek yok", "gerek duymad", "ihtiyac duymad", "ihtiyacim yok",
        "sekerim normal", "tansiyonum normal", "degerlerim normal", "normaldi",
    )),
)


def categorize_skip_reason(reason: str | None) -> str:
    text = normalize(reason or "")
    if not text:
        return "diger"
    for key, words in SKIP_REASON_RULES:
        if any(w in text for w in words):
            return key
    return "diger"


def skip_reason_breakdown(reasons: list[str | None]) -> dict:
    counts = Counter(categorize_skip_reason(r) for r in reasons)
    total = len(reasons)
    categories = [
        {"key": key, "label": label, "count": counts.get(key, 0), "percent": _half_up_percent(counts.get(key, 0), total)}
        for key, label in SKIP_CATEGORY_LABELS.items()
    ]
    return {"window_days": SKIP_REASON_WINDOW_DAYS, "total": total, "categories": categories}


# --- 4. Ölçüm sıklığı -------------------------------------------------------------------------


def measurement_frequency(days: list[date], today: date) -> dict:
    start = today - timedelta(days=RECENT_WINDOW_DAYS - 1)
    distinct = {d for d in days if start <= d <= today}
    n = len(distinct)
    return {
        "window_days": RECENT_WINDOW_DAYS,
        "days_with_measurement": n,
        "min_expected_days": MIN_MEASUREMENT_DAYS_PER_WEEK,
        "low_data": n < MIN_MEASUREMENT_DAYS_PER_WEEK,
    }


# --- Birleştirme --------------------------------------------------------------------------------


def build_insights(
    *,
    patient_id: int,
    as_of: datetime,
    schedules: list[ScheduleSpec],
    logs: list[LogSpec],
    points: list[MeasurementPoint],
    skip_reasons: list[str | None],
) -> dict:
    """Tüm içgörüleri tek sözlükte toplar (saf; as_of yerel saattir)."""
    today = as_of.date()
    return {
        "patient_id": patient_id,
        "as_of": as_of.date(),
        "adherence_trend": weekly_adherence_trend(schedules, logs, as_of).__dict__,
        "targets": target_summary(points, today),
        "skip_reasons": skip_reason_breakdown(skip_reasons),
        "measurement_frequency": measurement_frequency([p.day for p in points], today),
        "disclaimer": DISCLAIMER,
    }


# --- DB yükleyiciler ----------------------------------------------------------------------------


def local_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(_local_tz())


def _local_day(dt: datetime, tz) -> date:
    if dt.tzinfo is None:  # SQLite saat dilimi bilgisini düşürür; değerler UTC yazılır
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz).date()


def load_adherence_inputs(
    db: Session, patient_ids: list[int], start: date, end: date
) -> dict[int, tuple[list[ScheduleSpec], list[LogSpec]]]:
    """Birden çok hastanın aktif zamanlamaları ve [start, end] doz kayıtları — 2 sorguda (N+1 yok)."""
    out: dict[int, tuple[list[ScheduleSpec], list[LogSpec]]] = {pid: ([], []) for pid in patient_ids}
    if not patient_ids:
        return out
    rows = db.execute(
        select(MedicationSchedule, Medication.id, Medication.patient_id)
        .join(Medication)
        .where(Medication.patient_id.in_(patient_ids), Medication.active.is_(True))
    ).all()
    for s, med_id, pid in rows:
        out[pid][0].append(ScheduleSpec(s.id, med_id, s.time_of_day, s.days_of_week, s.start_date))
    for lg in db.scalars(
        select(DoseLog).where(
            DoseLog.patient_id.in_(patient_ids), DoseLog.scheduled_date >= start, DoseLog.scheduled_date <= end
        )
    ):
        out[lg.patient_id][1].append(LogSpec(lg.schedule_id, lg.scheduled_date, lg.status == DoseStatus.taken))
    return out


def adherence_overview(
    db: Session, patient_ids: list[int], now: datetime | None = None
) -> dict[int, tuple[AdherenceResult, AdherenceResult, AdherenceTrend]]:
    """Hasta listesi için: her hastanın 7 gün, 30 gün uyumu ve haftalık eğilimi (toplam 2 sorgu)."""
    as_of = local_now(now)
    today = as_of.date()
    inputs = load_adherence_inputs(db, patient_ids, today - timedelta(days=29), today)
    out = {}
    for pid, (schedules, logs) in inputs.items():
        a7 = compute_adherence(schedules, logs, today - timedelta(days=6), today, as_of=as_of)
        a30 = compute_adherence(schedules, logs, today - timedelta(days=29), today, as_of=as_of)
        out[pid] = (a7, a30, weekly_adherence_trend(schedules, logs, as_of))
    return out


def patient_insights(db: Session, patient_id: int, now: datetime | None = None) -> dict:
    as_of = local_now(now)
    tz = as_of.tzinfo
    today = as_of.date()
    schedules, logs = load_adherence_inputs(db, [patient_id], today - timedelta(days=13), today)[patient_id]

    # Pencere başı yerel gün başlangıcıdır; bir gün pay bırakıp yerel güne göre süzülür
    since = datetime.combine(today - timedelta(days=MEASUREMENT_WINDOW_DAYS), datetime.min.time(), tzinfo=tz)
    points = [
        MeasurementPoint(m.type, _local_day(m.measured_at, tz), m.value, m.value2)
        for m in db.scalars(
            select(Measurement).where(Measurement.patient_id == patient_id, Measurement.measured_at >= since)
        )
    ]
    points = [p for p in points if p.day <= today]

    skip_start = today - timedelta(days=SKIP_REASON_WINDOW_DAYS - 1)
    reasons = list(db.scalars(
        select(DoseLog.reason).where(
            DoseLog.patient_id == patient_id, DoseLog.status == DoseStatus.skipped,
            DoseLog.scheduled_date >= skip_start, DoseLog.scheduled_date <= today,
        )
    ))
    return build_insights(
        patient_id=patient_id, as_of=as_of, schedules=schedules, logs=logs, points=points, skip_reasons=reasons
    )
