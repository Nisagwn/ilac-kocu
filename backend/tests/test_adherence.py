from datetime import date, datetime, time, timedelta

import pytest

from app.services.adherence import LogSpec, ScheduleSpec, adherence_band, compute_adherence

START = date(2026, 10, 5)  # Pazartesi
END = START + timedelta(days=6)

MORNING = ScheduleSpec(schedule_id=1, medication_id=10, time_of_day=time(8, 0))
EVENING = ScheduleSpec(schedule_id=2, medication_id=10, time_of_day=time(20, 0))
MWF = ScheduleSpec(schedule_id=3, medication_id=20, time_of_day=time(9, 0), days_of_week="135")


def taken(sid, d):
    return LogSpec(sid, d, True)


def test_all_taken():
    logs = [taken(1, START + timedelta(days=i)) for i in range(7)]
    r = compute_adherence([MORNING], logs, START, END)
    assert (r.planned, r.taken, r.score, r.percent) == (7, 7, 1.0, 100)


def test_spec_example_three_missed_in_seven_days():
    """Spec senaryosu: son 7 günde 3 atlanmış doz."""
    logs = [taken(1, START + timedelta(days=i)) for i in range(4)]
    logs.append(LogSpec(1, START + timedelta(days=4), False))  # açıkça 'almadım'
    r = compute_adherence([MORNING], logs, START, END)
    assert r.planned == 7 and r.taken == 4
    assert r.skipped == 1 and r.missing == 2  # 2 gün hiç kayıt yok
    assert r.score == pytest.approx(4 / 7, abs=1e-3)
    assert len(r.missed_dates) == 3


def test_twice_daily_and_weekdays():
    logs = [taken(1, START), taken(2, START), taken(3, START)]
    r = compute_adherence([MORNING, EVENING, MWF], logs, START, END)
    assert r.planned == 7 + 7 + 3
    assert r.by_medication[20]["planned"] == 3
    assert r.by_medication[20]["score"] == pytest.approx(1 / 3, abs=1e-3)


def test_future_doses_today_excluded():
    as_of = datetime.combine(END, time(12, 0))  # son gün öğlen: akşam dozu henüz gelmedi
    r = compute_adherence([MORNING, EVENING], [], START, END, as_of=as_of)
    assert r.planned == 7 + 6


def test_schedule_start_date_respected():
    s = ScheduleSpec(1, 10, time(8, 0), start_date=START + timedelta(days=5))
    r = compute_adherence([s], [], START, END)
    assert r.planned == 2


def test_no_schedule_gives_none():
    r = compute_adherence([], [], START, END)
    assert r.score is None and adherence_band(r.score) == "veri_yok"


def test_invalid_range():
    with pytest.raises(ValueError):
        compute_adherence([MORNING], [], END, START)


@pytest.mark.parametrize("score,band", [(1.0, "iyi"), (0.8, "iyi"), (0.79, "orta"), (0.6, "orta"), (0.3, "dusuk")])
def test_bands(score, band):
    assert adherence_band(score) == band
