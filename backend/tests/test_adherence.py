"""Uyum skoru doğrulaması (docs/M4/uyum-skoru-dogrulama.md).

1. Elle hesaplanmış örnekler (her testte beklenen değerin hesabı yorum olarak yazılıdır)
2. Özellik tabanlı test: sabit tohumlu rastgele planlar/kayıtlar, testte bağımsız yazılmış
   kaba kuvvet (brute-force) referans uygulamayla karşılaştırılır.
"""

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.adherence import LogSpec, ScheduleSpec, adherence_band, compute_adherence

START = date(2026, 10, 5)  # Pazartesi
END = START + timedelta(days=6)  # Pazar
IST = ZoneInfo("Europe/Istanbul")  # UTC+3, yaz saati uygulaması yok

MORNING = ScheduleSpec(schedule_id=1, medication_id=10, time_of_day=time(8, 0))
EVENING = ScheduleSpec(schedule_id=2, medication_id=10, time_of_day=time(20, 0))
MWF = ScheduleSpec(schedule_id=3, medication_id=20, time_of_day=time(9, 0), days_of_week="135")


def day(i: int) -> date:
    return START + timedelta(days=i)


def taken(sid, d):
    return LogSpec(sid, d, True)


def skipped(sid, d):
    return LogSpec(sid, d, False)


# =============================== temel örnekler ===============================


def test_all_taken():
    logs = [taken(1, day(i)) for i in range(7)]
    r = compute_adherence([MORNING], logs, START, END)
    assert (r.planned, r.taken, r.score, r.percent) == (7, 7, 1.0, 100)
    assert r.missed_dates == []


def test_spec_example_three_missed_in_seven_days():
    """Spec senaryosu: son 7 günde 3 atlanmış doz."""
    logs = [taken(1, day(i)) for i in range(4)]
    logs.append(skipped(1, day(4)))  # açıkça 'almadım'
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


# =============================== elle hesaplanmış ===============================


def test_multiple_medications_hand_computed():
    """İlaç 10: sabah + akşam (her gün), ilaç 20: Pzt/Çrş/Cum.

    İlaç 10 sabah : 7/7 alındı
    İlaç 10 akşam : Pzt-Prş alındı (4), Cuma 'almadım' (1 skipped), Cmt-Paz kayıt yok (2 missing)
    İlaç 20       : Pzt alındı, Çrş 'almadım', Cuma kayıt yok
    planned = 7 + 7 + 3 = 17 ; taken = 7 + 4 + 1 = 12 ; skipped = 2 ; missing = 3
    score = 12/17 = 0.70588 -> 0.706 ; percent = 70.588 -> %71 ; bant: orta
    ilaç 10 = 11/14 = 0.786 ; ilaç 20 = 1/3 = 0.333
    kaçırılan günler: Çrş (ilaç 20), Cuma (ikisi de), Cmt, Paz -> 4 farklı gün
    """
    logs = [taken(1, day(i)) for i in range(7)]
    logs += [taken(2, day(i)) for i in range(4)] + [skipped(2, day(4))]
    logs += [taken(3, day(0)), skipped(3, day(2))]
    r = compute_adherence([MORNING, EVENING, MWF], logs, START, END)
    assert (r.planned, r.taken, r.skipped, r.missing) == (17, 12, 2, 3)
    assert r.score == 0.706 and r.percent == 71 and adherence_band(r.score) == "orta"
    assert r.by_medication == {
        10: {"planned": 14, "taken": 11, "score": 0.786},
        20: {"planned": 3, "taken": 1, "score": 0.333},
    }
    assert r.missed_dates == [day(2).isoformat(), day(4).isoformat(), day(5).isoformat(), day(6).isoformat()]


def test_partial_weekdays_weekend_only():
    """days_of_week='67' -> Cumartesi + Pazar: Pzt-Paz penceresinde 2 planlı doz."""
    weekend = ScheduleSpec(4, 30, time(10, 0), days_of_week="67")
    r = compute_adherence([weekend], [taken(4, day(5))], START, END)
    assert (r.planned, r.taken, r.missing) == (2, 1, 1)
    assert r.missed_dates == [day(6).isoformat()]


def test_log_on_unscheduled_day_is_ignored():
    """Planda olmayan güne (Salı) girilmiş kayıt pay veya paydayı değiştirmez."""
    r = compute_adherence([MWF], [taken(3, day(1)), taken(3, day(0))], START, END)
    assert (r.planned, r.taken) == (3, 1)


def test_schedule_start_date_respected():
    s = ScheduleSpec(1, 10, time(8, 0), start_date=day(5))
    r = compute_adherence([s], [], START, END)
    assert r.planned == 2


def test_start_date_mid_window_with_weekdays():
    """Pzt/Çrş/Cum planı Çarşamba başlıyor -> yalnızca Çrş + Cum = 2 doz (Pzt sayılmaz)."""
    s = ScheduleSpec(3, 20, time(9, 0), days_of_week="135", start_date=day(2))
    r = compute_adherence([s], [taken(3, day(0)), taken(3, day(2))], START, END)
    assert (r.planned, r.taken, r.missing) == (2, 1, 1)


def test_future_doses_today_excluded():
    as_of = datetime.combine(END, time(12, 0))  # son gün öğlen: akşam dozu henüz gelmedi
    r = compute_adherence([MORNING, EVENING], [], START, END, as_of=as_of)
    assert r.planned == 7 + 6


def test_dose_exactly_at_as_of_is_due():
    as_of = datetime.combine(END, time(20, 0))
    r = compute_adherence([MORNING, EVENING], [], START, END, as_of=as_of)
    assert r.planned == 14


def test_early_logged_dose_today_counts():
    """Akşam dozu (20:00) saat 18:00'de 'aldım' olarak işaretlendi -> sayılır (14 planlı, 1 alındı).

    Kayıtsız saati gelmemiş doz ise paydaya girmez (önceki test).
    """
    as_of = datetime.combine(END, time(18, 0))
    r = compute_adherence([MORNING, EVENING], [taken(2, END)], START, END, as_of=as_of)
    assert (r.planned, r.taken) == (14, 1)
    r = compute_adherence([MORNING, EVENING], [skipped(2, END)], START, END, as_of=as_of)
    assert (r.planned, r.skipped) == (14, 1)


def test_logs_after_as_of_day_are_not_counted():
    """as_of Çarşamba öğlen; Perşembe'ye ait (geleceğe) kayıt sayılmaz -> Pzt, Sal, Çrş sabah = 3."""
    as_of = datetime.combine(day(2), time(12, 0))
    r = compute_adherence([MORNING], [taken(1, day(3))], START, END, as_of=as_of)
    assert (r.planned, r.taken) == (3, 0)


def test_skipped_vs_missing_same_score_different_breakdown():
    """'almadım' (skipped) ile hiç kayıt yok (missing) skoru aynı etkiler, döküm farklıdır."""
    base = [taken(1, day(i)) for i in range(5)]
    r_skip = compute_adherence([MORNING], base + [skipped(1, day(5)), skipped(1, day(6))], START, END)
    r_miss = compute_adherence([MORNING], base, START, END)
    assert r_skip.score == r_miss.score == pytest.approx(5 / 7, abs=1e-3)
    assert (r_skip.skipped, r_skip.missing) == (2, 0)
    assert (r_miss.skipped, r_miss.missing) == (0, 2)
    assert r_skip.missed_dates == r_miss.missed_dates


def test_thirty_day_window_hand_computed():
    """30 gün: 2026-09-07 (Pzt) .. 2026-10-06 (Sal).

    Günlük sabah dozu: 30 planlı. Pzt/Çrş/Cum dozu: 4 tam hafta (12) + 5 Ekim Pzt (1) = 13 planlı.
    Sabah: 6 gün kayıt yok -> 24/30 = 0.8 -> 'iyi' (eşik dahil). 7 gün yok -> 23/30 = 0.767 -> 'orta'.
    """
    s, e = date(2026, 9, 7), date(2026, 10, 6)
    assert (e - s).days + 1 == 30
    days = [s + timedelta(days=i) for i in range(30)]

    r = compute_adherence([MORNING, MWF], [], s, e)
    assert r.planned == 30 + 13 and r.by_medication[20]["planned"] == 13

    r24 = compute_adherence([MORNING], [taken(1, d) for d in days[6:]], s, e)
    assert (r24.planned, r24.taken, r24.score, r24.percent) == (30, 24, 0.8, 80)
    assert adherence_band(r24.score) == "iyi"
    r23 = compute_adherence([MORNING], [taken(1, d) for d in days[7:]], s, e)
    assert r23.score == 0.767 and adherence_band(r23.score) == "orta"


def test_no_schedule_gives_none():
    r = compute_adherence([], [], START, END)
    assert r.score is None and r.percent is None and adherence_band(r.score) == "veri_yok"


def test_schedule_starting_after_window_gives_none():
    s = ScheduleSpec(1, 10, time(8, 0), start_date=END + timedelta(days=1))
    r = compute_adherence([s], [], START, END)
    assert (r.planned, r.score, r.percent, r.by_medication) == (0, None, None, {})


def test_as_of_before_window_gives_none():
    r = compute_adherence([MORNING], [], START, END, as_of=datetime.combine(START - timedelta(days=1), time(9)))
    assert r.planned == 0 and r.score is None


def test_invalid_range():
    with pytest.raises(ValueError):
        compute_adherence([MORNING], [], END, START)


def test_single_day_window():
    r = compute_adherence([MORNING, EVENING], [taken(1, START)], START, START)
    assert (r.planned, r.taken, r.percent) == (2, 1, 50)


# =============================== yüzde yuvarlama ===============================


@pytest.mark.parametrize(
    "n_taken,n_planned,percent",
    [(5, 8, 63), (1, 8, 13), (3, 8, 38), (7, 8, 88), (4, 7, 57), (2, 3, 67), (0, 5, 0), (5, 5, 100)],
)
def test_percent_rounds_half_up(n_taken, n_planned, percent):
    """Hata düzeltmesi: round(62.5) == 62 (banker's rounding) idi; yarım-yukarı -> %63."""
    sched = [ScheduleSpec(100 + i, 1, time(8, 0)) for i in range(n_planned)]
    logs = [taken(100 + i, START) for i in range(n_taken)]
    r = compute_adherence(sched, logs, START, START)
    assert r.percent == percent


def test_score_rounds_half_up():
    """Hata düzeltmesi: round(1/16, 3) == 0.062 idi (banker's rounding); doğrusu 0.063."""
    sched = [ScheduleSpec(100 + i, 1, time(8, 0)) for i in range(16)]
    r = compute_adherence(sched, [taken(100, START)], START, START)
    assert r.score == 0.063 and r.by_medication[1]["score"] == 0.063


# =============================== saat dilimi ===============================

LATE = ScheduleSpec(5, 40, time(23, 30))  # yerel 23:30 dozu


def test_timezone_late_dose_due_after_conversion():
    """Yerel 23:45 = UTC 20:45. tz verilirse aware as_of yerel saate çevrilir -> 23:30 dozu vadesi geldi."""
    as_of_utc = datetime(2026, 10, 11, 20, 45, tzinfo=UTC)  # Pazar, yerel 23:45
    r = compute_adherence([LATE], [], START, END, as_of=as_of_utc, tz=IST)
    assert r.planned == 7


def test_timezone_late_dose_not_yet_due():
    """Yerel 23:15 = UTC 20:15 -> son günün 23:30 dozu henüz gelmedi -> 6."""
    as_of_utc = datetime(2026, 10, 11, 20, 15, tzinfo=UTC)
    r = compute_adherence([LATE], [], START, END, as_of=as_of_utc, tz=IST)
    assert r.planned == 6


def test_timezone_local_input_equivalent_to_converted_utc():
    """coach.get_adherence yerel saate çevrilmiş as_of verir; UTC + tz ile aynı sonuç çıkmalı."""
    as_of_utc = datetime(2026, 10, 11, 20, 45, tzinfo=UTC)
    a = compute_adherence([LATE, MORNING], [], START, END, as_of=as_of_utc.astimezone(IST))
    b = compute_adherence([LATE, MORNING], [], START, END, as_of=as_of_utc, tz=IST)
    assert (a.planned, a.missing) == (b.planned, b.missing) == (14, 14)


def test_timezone_aware_without_tz_uses_wall_clock():
    """tz verilmezse aware as_of'un duvar saati kullanılır (geriye dönük uyumlu davranış)."""
    as_of_utc = datetime(2026, 10, 11, 20, 45, tzinfo=UTC)  # duvar saati 20:45 < 23:30
    r = compute_adherence([LATE], [], START, END, as_of=as_of_utc)
    assert r.planned == 6


def test_timezone_after_local_midnight():
    """UTC 21:30 (Pazar) = yerel 00:30 (Pazartesi): Pazar'a kadar olan pencerede tüm dozlar vadesi geçmiş."""
    as_of_utc = datetime(2026, 10, 11, 21, 30, tzinfo=UTC)
    r = compute_adherence([LATE], [], START, END, as_of=as_of_utc, tz=IST)
    assert r.planned == 7


def test_get_adherence_uses_local_timezone(db):
    """Uçtan uca: coach.get_adherence 'now' (UTC) değerini Europe/Istanbul'a çevirir."""
    from app.models import DoseLog, DoseStatus, Medication, MedicationSchedule, Patient, Role, User
    from app.services.coach import get_adherence

    user = User(email="tz@example.com", password_hash="x", full_name="Tz Hasta", role=Role.patient)
    db.add(user)
    db.flush()
    patient = Patient(user_id=user.id)
    db.add(patient)
    db.flush()
    med = Medication(patient_id=patient.id, name="Sentetik ilaç")
    db.add(med)
    db.flush()
    sched = MedicationSchedule(medication_id=med.id, time_of_day=time(23, 30), start_date=date(2026, 10, 1))
    db.add(sched)
    db.flush()
    db.add(DoseLog(patient_id=patient.id, schedule_id=sched.id, scheduled_date=date(2026, 10, 10),
                   status=DoseStatus.taken))
    db.commit()

    # UTC 20:45 = yerel 23:45 (11 Ekim): son 7 gün = 5-11 Ekim, 23:30 dozu dahil -> 7 planlı, 1 alındı
    r = get_adherence(db, patient.id, days=7, now=datetime(2026, 10, 11, 20, 45, tzinfo=UTC))
    assert (r.start, r.end, r.planned, r.taken) == (date(2026, 10, 5), date(2026, 10, 11), 7, 1)
    # UTC 20:15 = yerel 23:15: bugünün dozu henüz gelmedi -> 6
    r = get_adherence(db, patient.id, days=7, now=datetime(2026, 10, 11, 20, 15, tzinfo=UTC))
    assert r.planned == 6


# =============================== bantlar ===============================


@pytest.mark.parametrize(
    "score,band",
    [(1.0, "iyi"), (0.8, "iyi"), (0.799, "orta"), (0.79, "orta"), (0.6, "orta"), (0.599, "dusuk"), (0.3, "dusuk"),
     (0.0, "dusuk"), (None, "veri_yok")],
)
def test_bands(score, band):
    assert adherence_band(score) == band


# =============================== özellik tabanlı ===============================


def _reference(schedules, logs, start, end, as_of):
    """Bağımsız kaba kuvvet referansı: önce tüm (gün, plan) yuvalarını listeler, sonra sınıflandırır.

    Kurallar (docs/M4/uyum-skoru-dogrulama.md):
    - yuva planlıdır ⇔ hafta günü planda ∧ (start_date yok ∨ start_date ≤ gün)
    - as_of verilmişse: yuva zamanı > as_of ise yalnızca as_of gününe ait ve kaydı varsa sayılır
    - alındı = kayıt True ; atlandı = kayıt False ; eksik = kayıt yok
    """
    by_key = {}
    for lg in logs:
        by_key[(lg.schedule_id, lg.scheduled_date)] = "taken" if lg.taken else "skipped"

    slots = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        weekday = current.weekday() + 1  # Pazartesi=1 ... Pazar=7
        for sc in schedules:
            if weekday not in {int(c) for c in sc.days_of_week}:
                continue
            if sc.start_date is not None and current < sc.start_date:
                continue
            state = by_key.get((sc.schedule_id, current), "missing")
            if as_of is not None:
                slot_time = datetime(current.year, current.month, current.day, sc.time_of_day.hour,
                                     sc.time_of_day.minute)
                if slot_time > as_of and (state == "missing" or current != as_of.date()):
                    continue
            slots.append((sc.medication_id, current, state))

    planned = len(slots)
    n_taken = sum(1 for _, _, st in slots if st == "taken")
    meds = {}
    for med_id, _, st in slots:
        m = meds.setdefault(med_id, [0, 0])
        m[0] += 1
        m[1] += st == "taken"
    return {
        "planned": planned,
        "taken": n_taken,
        "skipped": sum(1 for _, _, st in slots if st == "skipped"),
        "missing": sum(1 for _, _, st in slots if st == "missing"),
        "ratio": (n_taken / planned) if planned else None,
        "percent": int((Decimal(n_taken * 100) / Decimal(planned)).quantize(Decimal(1), ROUND_HALF_UP))
        if planned else None,
        "meds": meds,
        "missed_dates": sorted({d.isoformat() for _, d, st in slots if st != "taken"}),
    }


def _random_case(rng: random.Random):
    start = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 300))
    end = start + timedelta(days=rng.randrange(0, 30))
    schedules = []
    for sid in range(1, rng.randint(1, 4) + 1):
        days = "".join(sorted(rng.sample("1234567", rng.randint(1, 7))))
        sd = start + timedelta(days=rng.randrange(-5, 35)) if rng.random() < 0.5 else None
        schedules.append(ScheduleSpec(sid, rng.randint(1, 3), time(rng.randrange(24), rng.choice([0, 15, 30, 45])),
                                      days, sd))
    logs, seen = [], set()
    for _ in range(rng.randint(0, 60)):
        key = (rng.randint(1, 5), start + timedelta(days=rng.randrange(-3, (end - start).days + 4)))
        if key not in seen:  # DB'de (schedule_id, scheduled_date) benzersiz
            seen.add(key)
            logs.append(LogSpec(key[0], key[1], rng.random() < 0.7))
    as_of = None
    if rng.random() < 0.6:
        as_of = datetime.combine(start + timedelta(days=rng.randrange(-1, (end - start).days + 2)),
                                 time(rng.randrange(24), rng.choice([0, 10, 29, 30, 31, 59])))
    return schedules, logs, start, end, as_of


def test_property_matches_brute_force_reference():
    rng = random.Random(20261004)
    for i in range(500):
        schedules, logs, start, end, as_of = _random_case(rng)
        got = compute_adherence(schedules, logs, start, end, as_of=as_of)
        ref = _reference(schedules, logs, start, end, as_of)
        ctx = f"vaka {i}: {schedules=} {start=} {end=} {as_of=}"
        assert (got.planned, got.taken, got.skipped, got.missing) == (
            ref["planned"], ref["taken"], ref["skipped"], ref["missing"]), ctx
        assert got.planned == got.taken + got.skipped + got.missing, ctx
        if ref["ratio"] is None:
            assert got.score is None and got.percent is None, ctx
        else:
            exact = Decimal(ref["taken"]) / Decimal(ref["planned"])
            assert got.score == float(exact.quantize(Decimal("0.001"), ROUND_HALF_UP)), ctx
            assert 0.0 <= got.score <= 1.0, ctx
        assert got.percent == ref["percent"], ctx
        assert {k: (v["planned"], v["taken"]) for k, v in got.by_medication.items()} == {
            k: tuple(v) for k, v in ref["meds"].items()}, ctx
        assert got.missed_dates == ref["missed_dates"], ctx


def test_property_monotonic_in_taken_logs():
    """Bir eksik dozu 'alındı' yapmak skoru asla düşürmez, planlı sayıyı değiştirmez."""
    rng = random.Random(7)
    for _ in range(200):
        schedules, logs, start, end, _ = _random_case(rng)
        before = compute_adherence(schedules, logs, start, end)
        logged = {(lg.schedule_id, lg.scheduled_date) for lg in logs}
        extra = [LogSpec(s.schedule_id, d, True)
                 for s in schedules for d in (start + timedelta(days=k) for k in range((end - start).days + 1))
                 if (s.schedule_id, d) not in logged][:3]
        after = compute_adherence(schedules, logs + extra, start, end)
        assert after.planned == before.planned
        if before.score is not None:
            assert after.score >= before.score
