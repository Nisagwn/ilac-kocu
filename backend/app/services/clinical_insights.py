"""Hekim için klinik içgörüler: deterministik, LLM'den bağımsız; teşhis ya da tedavi önerisi içermez.

- Uyum eğilimi: son 7 gün ile önceki 7 gün (8-14. gün) uyum skoru farkı.
- Hedef aralıkta kalma (time in range): kan şekeri 70-180 mg/dL, tansiyon <130/80 ve sistolik ≥90 mmHg
  (frontend/src/components/clinician/format.ts ile aynı görselleştirme eşikleri; uyarı eşikleri ayrı:
  services/thresholds.py).
- Doz atlama nedenleri: hastanın serbest metin nedenleri anahtar kelimeyle kategorilere ayrılır.
- Ölçüm sıklığı: tür başına sayı, haftalık ortalama, ölçüm yapılan gün sayısı, son ölçümden beri geçen gün.
- `highlights`: yukarıdakilerden türetilen kısa, olgusal Türkçe cümleler (hekim kartında madde işaretli).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DoseLog, DoseStatus, Measurement, MeasurementType
from app.services.coach import get_adherence
from app.services.guardrails import normalize
from app.services.llm.patient_data import missed_by_time_slot

GLUCOSE_LOW, GLUCOSE_HIGH = 70, 180
SYSTOLIC_TARGET, DIASTOLIC_TARGET, SYSTOLIC_LOW = 130, 80, 90
TREND_DELTA = 0.10  # 10 puanlık değişim "iyileşiyor/kötüleşiyor" sayılır
TIR_GOAL = 0.70  # kan şekerinde hedef aralıkta kalma için yaygın kullanılan hedef (%70)
STALE_MEASUREMENT_DAYS = 3

# Kategori -> anahtar kelime kökleri (normalize edilmiş: Türkçe karakter ve büyük/küçük harf duyarsız)
SKIP_REASON_CATEGORIES: dict[str, tuple[str, ...]] = {
    "unutma": ("unut", "aklimdan", "hatirla"),
    "yan_etki": ("yan etki", "mide", "bulanti", "kusma", "ishal", "bas don", "basim don", "agri", "kasinti",
                 "halsiz", "sersem"),
    "ilac_bitti": ("bitti", "kalmadi", "tukendi", "eczane", "recete", "alamadim ilac"),
    "rutin_disi": ("disarida", "yolculuk", "seyahat", "isteydim", "evde degil", "misafir", "uyuya", "uyudum",
                   "gec kalk"),
    "gerek_duymama": ("iyi hissed", "gerek yok", "gerek duy", "ihtiyac", "istemedim", "istemiyorum"),
    "ogun_oruc": ("ac karn", "yemek yeme", "oruc", "ogun", "kahvalti yapma"),
}
SKIP_REASON_LABELS = {
    "unutma": "Unutma",
    "yan_etki": "Yan etki / rahatsızlık",
    "ilac_bitti": "İlaç bitti / temin edilemedi",
    "rutin_disi": "Rutin dışı gün (yolculuk, dışarıda)",
    "gerek_duymama": "Gerek duymama",
    "ogun_oruc": "Öğün atlama / oruç",
    "diger": "Diğer",
    "belirtilmemis": "Neden belirtilmemiş",
}


def categorize_skip_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        return "belirtilmemis"
    text = normalize(reason)
    for cat, keys in SKIP_REASON_CATEGORIES.items():
        if any(k in text for k in keys):
            return cat
    return "diger"


def _trend(current: float | None, previous: float | None) -> str | None:
    if current is None or previous is None:
        return None
    delta = current - previous
    if delta >= TREND_DELTA:
        return "improving"
    if delta <= -TREND_DELTA:
        return "worsening"
    return "stable"


def adherence_trend(db: Session, patient_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    cur = get_adherence(db, patient_id, 7, now=now).score
    prev = get_adherence(db, patient_id, 7, now=now - timedelta(days=7)).score
    delta = None if cur is None or prev is None else round(cur - prev, 3)
    return {"current_7d": cur, "previous_7d": prev, "delta": delta, "trend": _trend(cur, prev)}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite saat dilimi saklamaz


@dataclass
class RangeStat:
    count: int = 0
    in_range: int = 0
    below: int = 0
    above: int = 0

    @property
    def ratio(self) -> float | None:
        return round(self.in_range / self.count, 3) if self.count else None


@dataclass
class ClinicalInsights:
    period_days: int
    adherence: dict
    glucose: dict
    blood_pressure: dict
    measurement_frequency: dict
    skip_reasons: list[dict]
    missed_by_time_of_day: dict
    highlights: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def compute_insights(db: Session, patient_id: int, days: int = 30, now: datetime | None = None) -> ClinicalInsights:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    tz = ZoneInfo(get_settings().timezone)

    trend = adherence_trend(db, patient_id, now)

    rows = db.scalars(select(Measurement).where(Measurement.patient_id == patient_id,
                                                Measurement.measured_at >= since)).all()
    glucose, bp = RangeStat(), RangeStat()
    per_type: Counter[str] = Counter()
    measured_days: set = set()
    last_at: datetime | None = None
    for m in rows:
        at = _aware(m.measured_at)
        per_type[m.type.value] += 1
        measured_days.add(at.astimezone(tz).date())
        last_at = at if last_at is None or at > last_at else last_at
        if m.type == MeasurementType.blood_pressure:
            bp.count += 1
            dia = m.value2
            if m.value < SYSTOLIC_LOW:
                bp.below += 1
            elif m.value >= SYSTOLIC_TARGET or (dia is not None and dia >= DIASTOLIC_TARGET):
                bp.above += 1
            else:
                bp.in_range += 1
        else:
            glucose.count += 1
            if m.value < GLUCOSE_LOW:
                glucose.below += 1
            elif m.value > GLUCOSE_HIGH:
                glucose.above += 1
            else:
                glucose.in_range += 1
    if last_at is None:  # pencere dışındaki son ölçüm de "son ölçümden beri" için önemli
        last_at = db.scalar(select(Measurement.measured_at).where(Measurement.patient_id == patient_id)
                            .order_by(Measurement.measured_at.desc()).limit(1))
        last_at = _aware(last_at) if last_at else None
    days_since_last = (now - last_at).days if last_at else None

    skipped = db.scalars(select(DoseLog.reason).where(
        DoseLog.patient_id == patient_id, DoseLog.status == DoseStatus.skipped,
        DoseLog.scheduled_date >= (now.astimezone(tz).date() - timedelta(days=days - 1)))).all()
    reason_counts = Counter(categorize_skip_reason(r) for r in skipped)
    skip_reasons = [{"category": c, "label": SKIP_REASON_LABELS[c], "count": n}
                    for c, n in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    weeks = days / 7
    insights = ClinicalInsights(
        period_days=days,
        adherence=trend,
        glucose={"target": f"{GLUCOSE_LOW}-{GLUCOSE_HIGH} mg/dL", "count": glucose.count,
                 "time_in_range": glucose.ratio, "below": glucose.below, "above": glucose.above},
        blood_pressure={"target": f"<{SYSTOLIC_TARGET}/{DIASTOLIC_TARGET} mmHg", "count": bp.count,
                        "at_target": bp.ratio, "below": bp.below, "above": bp.above},
        measurement_frequency={
            "by_type": dict(sorted(per_type.items())),
            "per_week": round(len(rows) / weeks, 1) if weeks else 0.0,
            "days_with_measurement": len(measured_days),
            "last_measured_at": last_at.isoformat() if last_at else None,
            "days_since_last": days_since_last,
        },
        skip_reasons=skip_reasons,
        missed_by_time_of_day=missed_by_time_slot(db, patient_id, 14, now),
    )
    insights.highlights = _highlights(insights)
    return insights


def _pct(x: float) -> str:
    return f"%{round(x * 100)}"


def _highlights(i: ClinicalInsights) -> list[str]:
    out: list[str] = []
    a = i.adherence
    if a["trend"] == "worsening":
        out.append(f"Uyum son 7 günde düştü: {_pct(a['previous_7d'])} → {_pct(a['current_7d'])}.")
    elif a["trend"] == "improving":
        out.append(f"Uyum son 7 günde arttı: {_pct(a['previous_7d'])} → {_pct(a['current_7d'])}.")
    g = i.glucose
    if g["time_in_range"] is not None and g["time_in_range"] < TIR_GOAL:
        out.append(f"Hedef aralıktaki kan şekeri ölçümü: {_pct(g['time_in_range'])} ({g['target']}); "
                   f"{g['below']} düşük, {g['above']} yüksek.")
    if g["below"]:
        out.append(f"Son {i.period_days} günde {g['below']} düşük kan şekeri ölçümü (<{GLUCOSE_LOW} mg/dL).")
    b = i.blood_pressure
    if b["at_target"] is not None and b["at_target"] < 0.5:
        out.append(f"Hedefteki tansiyon ölçümü: {_pct(b['at_target'])} ({b['target']}).")
    f = i.measurement_frequency
    if f["days_since_last"] is None:
        out.append("Hiç ölçüm kaydı yok.")
    elif f["days_since_last"] >= STALE_MEASUREMENT_DAYS:
        out.append(f"Son ölçümden bu yana {f['days_since_last']} gün geçti.")
    top = next((r for r in i.skip_reasons if r["category"] not in ("diger", "belirtilmemis")), None)
    if top:
        out.append(f"En sık belirtilen doz atlama nedeni: {top['label'].lower()} ({top['count']} kez).")
    worst = max(i.missed_by_time_of_day.items(), key=lambda kv: kv[1]["missed"] / kv[1]["planned"], default=None)
    if worst and worst[1]["planned"] >= 4 and worst[1]["missed"] / worst[1]["planned"] >= 0.4:
        out.append(f"Kaçırılan dozlar en çok {worst[0]} saatlerinde ({worst[1]['missed']}/{worst[1]['planned']}).")
    return out
