"""Uyum skoru — deterministik, LLM'den bağımsız.

Tanım (dose-based adherence):
    skor = alınan doz / planlanan doz   (pencere: [start, end], uçlar dahil)

- Planlanan dozlar, aktif ilaçların zamanlamalarından (gün + hafta günü) türetilir.
- Hiç kaydedilmemiş planlı doz "kaçırıldı" sayılır.
- Bugünün henüz saati gelmemiş dozları paydaya eklenmez (as_of ile kontrol edilir).
  İstisna: saati gelmemiş ama hasta tarafından zaten kaydedilmiş doz (erken "aldım" ya da
  "almayacağım") sayılır; aksi halde erken alınan doz gün sonuna kadar görünmez olurdu.
- as_of, ilaç saatlerinin yorumlandığı yerel saattir. Saat dilimi bilgisi taşıyan (aware) bir as_of
  verilirse ve tz parametresi belirtilmişse önce o dilime çevrilir; tz yoksa duvar saati kullanılır.
- score (3 basamak) ve percent kesin sayılardan yarım-yukarı yuvarlanır (1/16 -> 0.063, 5/8 -> %63).

Ayrıntılı doğrulama: docs/M4/uyum-skoru-dogrulama.md
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo


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
        # round() "banker's rounding" yapar (round(62.5) == 62); kesin sayılardan yarım-yukarı yuvarla
        if self.score is None or not self.planned:
            return None
        return (self.taken * 200 + self.planned) // (self.planned * 2)


def _ratio(n_taken: int, n_planned: int) -> float | None:
    """taken/planned oranını 3 basamağa YARIM-YUKARI yuvarlar (round(0.0625, 3) == 0.062 hatasından kaçınır)."""
    if not n_planned:
        return None
    return ((n_taken * 2000 + n_planned) // (n_planned * 2)) / 1000


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
    tz: tzinfo | None = None,
) -> AdherenceResult:
    if end < start:
        raise ValueError("end, start'tan önce olamaz")
    if as_of is not None:
        if as_of.tzinfo is not None and tz is not None:
            as_of = as_of.astimezone(tz)
        as_of = as_of.replace(tzinfo=None)  # ilaç saatleriyle aynı (yerel duvar saati) düzlemde karşılaştır

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
            status = log_map.get((s.schedule_id, d))
            if as_of and datetime.combine(d, s.time_of_day) > as_of:
                # Henüz zamanı gelmedi: yalnızca as_of gününe ait ve önceden kaydedilmişse say
                if status is None or d > as_of.date():
                    continue

            planned += 1
            med = per_med.setdefault(s.medication_id, {"planned": 0, "taken": 0})
            med["planned"] += 1

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
        med["score"] = _ratio(med["taken"], med["planned"])

    return AdherenceResult(
        start=start,
        end=end,
        planned=planned,
        taken=taken,
        skipped=skipped,
        missing=missing,
        score=_ratio(taken, planned),
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
