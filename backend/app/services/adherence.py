"""Uyum skoru — deterministik, LLM'den bağımsız.

Tanım (dose-based adherence):
    skor = alınan doz / planlanan doz   (pencere: [start, end], uçlar dahil)

- Planlanan dozlar, aktif ilaçların zamanlamalarından (gün + hafta günü) türetilir.
- Hiç kaydedilmemiş planlı doz "kaçırıldı" sayılır.
- Bugünün henüz saati gelmemiş dozları paydaya eklenmez (as_of ile kontrol edilir).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class ScheduleSpec:
    schedule_id: int
    medication_id: int
    time_of_day: time
    days_of_week: str = "1234567"
    start_date: date | None = None


@dataclass(frozen=True)
class LogSpec:
    schedule_id: int
    scheduled_date: date
    taken: bool


@dataclass
class AdherenceResult:
    start: date
    end: date
    planned: int
    taken: int
    skipped: int
    missing: int
    score: float | None  # 0..1, planlı doz yoksa None
    by_medication: dict[int, dict] = field(default_factory=dict)
    missed_dates: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int | None:
        return None if self.score is None else round(self.score * 100)


def _daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def compute_adherence(
    schedules: list[ScheduleSpec],
    logs: list[LogSpec],
    start: date,
    end: date,
    as_of: datetime | None = None,
) -> AdherenceResult:
    if end < start:
        raise ValueError("end, start'tan önce olamaz")

    log_map = {(lg.schedule_id, lg.scheduled_date): lg.taken for lg in logs}
    planned = taken = skipped = missing = 0
    per_med: dict[int, dict] = {}
    missed: set[str] = set()

    for s in schedules:
        for d in _daterange(start, end):
            if s.start_date and d < s.start_date:
                continue
            if str(d.isoweekday()) not in s.days_of_week:
                continue
            if as_of and datetime.combine(d, s.time_of_day) > as_of.replace(tzinfo=None):
                continue  # henüz zamanı gelmedi

            planned += 1
            med = per_med.setdefault(s.medication_id, {"planned": 0, "taken": 0})
            med["planned"] += 1

            status = log_map.get((s.schedule_id, d))
            if status is True:
                taken += 1
                med["taken"] += 1
            else:
                missed.add(d.isoformat())
                if status is False:
                    skipped += 1
                else:
                    missing += 1

    for med in per_med.values():
        med["score"] = round(med["taken"] / med["planned"], 3) if med["planned"] else None

    return AdherenceResult(
        start=start,
        end=end,
        planned=planned,
        taken=taken,
        skipped=skipped,
        missing=missing,
        score=round(taken / planned, 3) if planned else None,
        by_medication=per_med,
        missed_dates=sorted(missed),
    )


def adherence_band(score: float | None) -> str:
    """Hekim dashboard'u için renk bandı. ≥%80 literatürde yaygın 'uyumlu' eşiğidir."""
    if score is None:
        return "veri_yok"
    if score >= 0.8:
        return "iyi"
    if score >= 0.6:
        return "orta"
    return "dusuk"
