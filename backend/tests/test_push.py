"""Web push abonelikleri, doz hatırlatma penceresi ve /summaries. Ağ çağrısı yapılmaz (sahte gönderici)."""

import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.main import app
from app.models import MedicationSchedule, PushSubscription, ReminderLog
from app.routers.doses import local_today
from app.routers.push import get_push_sender
from app.services.push import PushFailed, PushGone
from app.services.reminders import scheduler_should_start, send_due_reminders
from tests.conftest import register

SUB = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "BPk3", "auth": "a1b2"}}
MED = {"name": "Glifor", "dose_text": "1000 mg", "schedules": [{"time_of_day": "08:00"}]}


class FakeSender:
    """Gönderilenleri kaydeder; `gone` / `fail` kümelerindeki endpoint'ler için hata fırlatır."""

    def __init__(self, gone=(), fail=()):
        self.calls: list[tuple[dict, dict]] = []
        self.gone, self.fail = set(gone), set(fail)

    def __call__(self, info: dict, payload: str) -> None:
        if info["endpoint"] in self.gone:
            raise PushGone("410")
        if info["endpoint"] in self.fail:
            raise PushFailed("500")
        self.calls.append((info, json.loads(payload)))


def subscribe(client, h, endpoint=SUB["endpoint"]):
    r = client.post("/push/subscribe", headers=h, json={**SUB, "endpoint": endpoint})
    assert r.status_code == 204, r.text


def local_at(day, hh, mm):
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(get_settings().timezone))


@pytest.fixture()
def med_setup(client, patient):
    """08:00 planlı ilacı olan, bir push aboneliği bulunan hasta."""
    user, h = patient
    r = client.post(f"/patients/{user['patient_id']}/medications", headers=h, json=MED)
    assert r.status_code == 201, r.text
    subscribe(client, h)
    return user, h, r.json()["schedules"][0]["id"]


# ---------------- Abonelik uç noktaları ----------------
def test_subscribe_upsert_and_unsubscribe(client, db, patient):
    _, h = patient
    subscribe(client, h)
    r = client.post("/push/subscribe", headers=h, json={**SUB, "keys": {"p256dh": "YENI", "auth": "x"}})
    assert r.status_code == 204
    subs = db.scalars(select(PushSubscription)).all()
    assert len(subs) == 1 and subs[0].p256dh == "YENI"

    assert client.request("DELETE", "/push/subscribe", headers=h, json={"endpoint": SUB["endpoint"]}).status_code == 204
    assert db.scalars(select(PushSubscription)).all() == []
    # olmayan abonelik silinirken de 204 (idempotent)
    assert client.request("DELETE", "/push/subscribe", headers=h, json={"endpoint": "yok"}).status_code == 204


def test_push_role_restriction(client, clinician):
    # Hekim haftalık özet bildirimi için abone olabilir; bakım veren (salt okunur) olamaz
    _, dh = clinician
    assert client.post("/push/subscribe", headers=dh, json=SUB).status_code == 204
    _, ch = register(client, "yakin@example.com", role="caregiver")
    assert client.post("/push/subscribe", headers=ch, json=SUB).status_code == 403
    assert client.get("/push/vapid-public-key", headers=ch).status_code == 403
    assert client.post("/push/test", headers=ch).status_code == 403
    assert client.post("/push/subscribe", json=SUB).status_code == 401


def test_vapid_public_key(client, patient, monkeypatch):
    _, h = patient
    s = get_settings()
    monkeypatch.setattr(s, "vapid_public_key", None)
    assert client.get("/push/vapid-public-key", headers=h).status_code == 503
    monkeypatch.setattr(s, "vapid_public_key", "BPUBLIC")
    assert client.get("/push/vapid-public-key", headers=h).json() == {"public_key": "BPUBLIC"}


def test_push_test_endpoint(client, db, patient, monkeypatch):
    _, h = patient
    monkeypatch.setattr(get_settings(), "vapid_private_key", None)
    assert client.post("/push/test", headers=h).status_code == 503  # VAPID yoksa

    subscribe(client, h)
    subscribe(client, h, "https://push.example.com/eski")
    fake = FakeSender(gone={"https://push.example.com/eski"})
    app.dependency_overrides[get_push_sender] = lambda: fake
    r = client.post("/push/test", headers=h)
    assert r.status_code == 200 and r.json() == {"sent": 1}
    # 410 dönen abonelik silindi
    assert [s.endpoint for s in db.scalars(select(PushSubscription))] == [SUB["endpoint"]]


# ---------------- Hatırlatma penceresi ----------------
def test_reminder_due_sends_once(db, med_setup):
    _, _, sid = med_setup
    day = local_today()
    fake = FakeSender()
    assert send_due_reminders(db, local_at(day, 8, 5), sender=fake) == 1
    info, payload = fake.calls[0]
    assert info["endpoint"] == SUB["endpoint"]
    assert payload == {"title": "İlaç zamanı", "body": "Saat 08:00 dozunu işaretlemeyi unutma.", "url": "/bugun"}
    assert db.scalar(select(ReminderLog).where(ReminderLog.schedule_id == sid)).scheduled_date == day

    # zaten hatırlatıldı → ikinci kez gönderilmez
    assert send_due_reminders(db, local_at(day, 8, 10), sender=fake) == 0
    assert len(fake.calls) == 1


def test_reminder_skipped_when_dose_logged(client, db, med_setup):
    user, h, sid = med_setup
    day = local_today()
    r = client.put(f"/patients/{user['patient_id']}/doses", headers=h,
                   json={"schedule_id": sid, "scheduled_date": day.isoformat(), "status": "taken"})
    assert r.status_code == 200
    fake = FakeSender()
    assert send_due_reminders(db, local_at(day, 8, 5), sender=fake) == 0
    assert fake.calls == []


@pytest.mark.parametrize("hh,mm", [(7, 59), (8, 16), (12, 0)])
def test_reminder_outside_window(db, med_setup, hh, mm):
    fake = FakeSender()
    assert send_due_reminders(db, local_at(local_today(), hh, mm), sender=fake) == 0
    assert fake.calls == []


def test_reminder_window_edges(db, med_setup):
    day = local_today()
    assert send_due_reminders(db, local_at(day, 8, 0), sender=FakeSender()) == 1  # tam saatinde


def test_reminder_wrong_weekday(db, med_setup):
    _, _, sid = med_setup
    day = local_today()
    sched = db.get(MedicationSchedule, sid)
    sched.days_of_week = "".join(d for d in "1234567" if d != str(day.isoweekday()))
    db.commit()
    assert send_due_reminders(db, local_at(day, 8, 5), sender=FakeSender()) == 0


def test_reminder_inactive_medication(client, db, med_setup):
    user, h, sid = med_setup
    med_id = db.get(MedicationSchedule, sid).medication_id
    assert client.delete(f"/patients/{user['patient_id']}/medications/{med_id}", headers=h).status_code == 204
    assert send_due_reminders(db, local_at(local_today(), 8, 5), sender=FakeSender()) == 0


def test_reminder_across_midnight(client, db, patient):
    user, h = patient
    r = client.post(f"/patients/{user['patient_id']}/medications", headers=h,
                    json={"name": "Beloc", "schedules": [{"time_of_day": "23:55"}]})
    sid = r.json()["schedules"][0]["id"]
    day = local_today()
    db.get(MedicationSchedule, sid).start_date = day - timedelta(days=1)
    db.commit()
    subscribe(client, h)
    fake = FakeSender()
    assert send_due_reminders(db, local_at(day, 0, 5), sender=fake) == 1
    assert fake.calls[0][1]["body"] == "Saat 23:55 dozunu işaretlemeyi unutma."
    assert db.scalar(select(ReminderLog)).scheduled_date == day - timedelta(days=1)


def test_reminder_410_cleanup_and_retry(client, db, med_setup):
    _, h, _ = med_setup
    day = local_today()
    # tek abonelik 410 → silinir, hatırlatma kaydedilmez
    fake = FakeSender(gone={SUB["endpoint"]})
    assert send_due_reminders(db, local_at(day, 8, 5), sender=fake) == 0
    assert db.scalars(select(PushSubscription)).all() == []
    assert db.scalars(select(ReminderLog)).all() == []

    # geçici hata → abonelik korunur, sonraki dakikada yeniden denenir
    subscribe(client, h)
    assert send_due_reminders(db, local_at(day, 8, 6), sender=FakeSender(fail={SUB["endpoint"]})) == 0
    assert len(db.scalars(select(PushSubscription)).all()) == 1
    assert send_due_reminders(db, local_at(day, 8, 7), sender=FakeSender()) == 1


def test_reminder_only_to_own_user(client, db, med_setup):
    _, h2 = register(client, "ali@example.com")
    subscribe(client, h2, "https://push.example.com/ali")
    fake = FakeSender()
    assert send_due_reminders(db, local_at(local_today(), 8, 5), sender=fake) == 1
    assert [c[0]["endpoint"] for c in fake.calls] == [SUB["endpoint"]]


def test_scheduler_never_starts_under_pytest():
    s = get_settings().model_copy(update={"reminders_enabled": True, "vapid_public_key": "a", "vapid_private_key": "b"})
    assert scheduler_should_start(s) is False


# ---------------- /summaries ----------------
def test_summaries_authorization(client, patient, clinician):
    user, h = patient
    pid = user["patient_id"]
    client.post(f"/patients/{pid}/measurements", headers=h, json={"type": "glucose_fasting", "value": 110})

    r = client.get(f"/summaries/{pid}", headers=h)
    assert r.status_code == 200
    assert r.json() == client.get(f"/patients/{pid}/summary", headers=h).json()  # eski rota aynı çıktı
    assert r.json()["measurements"][0]["count"] == 1

    other, oh = register(client, "ali@example.com")
    assert client.get(f"/summaries/{pid}", headers=oh).status_code == 404
    assert client.get(f"/summaries/{other['patient_id']}", headers=h).status_code == 404
    _, dh = clinician
    assert client.get(f"/summaries/{pid}", headers=dh).status_code == 404  # atanmamış hekim
    client.put("/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    assert client.get(f"/summaries/{pid}", headers=dh).status_code == 200
    assert client.get("/summaries/9999", headers=h).status_code == 404
