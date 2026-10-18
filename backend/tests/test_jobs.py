"""Asenkron koç işleri: POST/GET /coach/jobs, kuyruk (sahte Redis), işçi ve zamanlayıcı ayrımı.

Ağ ve gerçek Redis kullanılmaz: Redis yokken iş BackgroundTask ile süreç içinde işlenir;
kuyruk yolu bellek içi sahte Redis ile sınanır.
"""

import threading
from datetime import timedelta

from fastapi.testclient import TestClient

from app.models import Alert, CoachJob, CoachMessage, Patient, utcnow
from app.redis_client import reset_redis
from app.services import jobs
from app.services.coach import CoachReply
from tests.conftest import TestingSession, register
from tests.fake_redis import FakeRedis
from tests.test_api import add_med


class ExplodingProvider:
    def reply(self, db, patient, message):
        raise RuntimeError("sağlayıcı çöktü")


def post_job(client, h, message):
    return client.post("/coach/jobs", headers=h, json={"message": message})


# ---------------- Girdi guardrail'i: kuyruğa girmeden anında yanıt ----------------
def test_emergency_returns_done_immediately_without_job(client, db, patient):
    user, h = patient
    r = post_job(client, h, "Göğsümde baskı var")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done" and body["job_id"] is None
    assert body["reply"]["risk_level"] == "emergency" and "112" in body["reply"]["message"]
    assert db.query(CoachJob).count() == 0
    alerts = client.get(f"/patients/{user['patient_id']}/alerts", headers=h).json()
    assert alerts[0]["severity"] == "critical" and alerts[0]["kind"] == "emergency_symptom"
    hist = client.get("/coach/history", headers=h).json()
    assert [m["sender"] for m in hist] == ["patient", "coach"]


def test_dose_question_returns_done_immediately(client, db, patient):
    _, h = patient
    body = post_job(client, h, "Metformin dozumu artırabilir miyim?").json()
    assert body["status"] == "done" and body["reply"]["guardrail_action"] == "dose_request"
    assert db.query(CoachJob).count() == 0


# ---------------- Normal mesaj: 202 + süreç içi işleme (Redis yok) ----------------
def test_normal_message_is_processed_inline_and_polled(client, db, patient):
    user, h = patient
    add_med(client, user["patient_id"], h)
    r = post_job(client, h, "Bu hafta nasıl gidiyorum?")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued" and job_id

    g = client.get(f"/coach/jobs/{job_id}", headers=h)
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "done" and body["error"] is None
    assert body["reply"]["sources"] and body["reply"]["guardrail_action"] is None

    hist = client.get("/coach/history", headers=h).json()
    assert [m["sender"] for m in hist] == ["patient", "coach"]  # hasta mesajı bir kez kaydedilir
    job = db.get(CoachJob, job_id, populate_existing=True)
    assert job.message is None and job.attempts == 1 and job.finished_at is not None


def test_job_visible_only_to_owner(client, patient, clinician):
    _, h = patient
    job_id = post_job(client, h, "Merhaba").json()["job_id"]
    _, h2 = register(client, "ali@example.com")
    assert client.get(f"/coach/jobs/{job_id}", headers=h2).status_code == 404
    assert client.get("/coach/jobs/olmayan-is", headers=h).status_code == 404
    _, ch = clinician
    assert client.get(f"/coach/jobs/{job_id}", headers=ch).status_code == 403


def test_clinician_cannot_create_job(client, clinician):
    _, ch = clinician
    assert post_job(client, ch, "merhaba").status_code == 403


def test_rate_limit_eleventh_message_is_429(client, patient):
    _, h = patient
    for i in range(10):
        assert post_job(client, h, f"Merhaba {i}").status_code == 202
    r = post_job(client, h, "Merhaba yine")
    assert r.status_code == 429 and "Retry-After" in r.headers
    # Acil belirti sınıra takılmaz: güvenlik yanıtı her zaman döner
    assert post_job(client, h, "Göğsümde baskı var").json()["status"] == "done"


# ---------------- İşçi: process_job ----------------
def _queued_job(db, patient_id, message="nasılım"):
    p = db.get(Patient, patient_id)
    reply, job = jobs.submit_coach_message(db, p, message)
    assert reply is None and job.status == jobs.QUEUED
    return job.id


def test_process_job_provider_exception_falls_back(client, db, patient):
    user, _ = patient
    job_id = _queued_job(db, user["patient_id"])
    job = jobs.process_job(db, job_id, provider=ExplodingProvider())
    assert job.status == jobs.DONE
    assert CoachReply.model_validate(job.reply).guardrail_action == "llm_fallback"
    senders = [m.sender.value for m in db.query(CoachMessage).order_by(CoachMessage.id)]
    assert senders == ["patient", "coach"]


def test_process_job_unexpected_stage_error_still_answers(client, db, patient, monkeypatch):
    user, _ = patient
    job_id = _queued_job(db, user["patient_id"])
    calls = {"n": 0}
    real = jobs.run_provider_stage

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("veritabanı hıçkırığı")
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs, "run_provider_stage", flaky)
    slept = []
    job = jobs.process_job(db, job_id, sleep=slept.append)
    # Redis yok: süreç içinde üstel beklemeyle yeniden denendi, ikinci deneme asıl yanıtı üretti
    assert job.status == jobs.DONE and job.reply["guardrail_action"] is None
    assert job.attempts == 2 and calls["n"] == 2 and len(slept) == 1


def test_process_job_is_idempotent(client, db, patient):
    user, _ = patient
    job_id = _queued_job(db, user["patient_id"])
    jobs.process_job(db, job_id)
    again = jobs.process_job(db, job_id, provider=ExplodingProvider())
    assert again.status == jobs.DONE and again.reply["guardrail_action"] is None
    assert db.query(CoachMessage).filter(CoachMessage.sender == "coach").count() == 1


def test_process_job_escalation_creates_alert(client, db, patient):
    user, _ = patient

    class Escalating:
        def reply(self, db, patient, message):
            return CoachReply(message="Bunu hekiminle konuşmanı öneririm.", escalate_to_clinician=True)

    job_id = _queued_job(db, user["patient_id"])
    assert jobs.process_job(db, job_id, provider=Escalating()).status == jobs.DONE
    assert db.query(Alert).one().kind == "coach_escalation"


def test_stale_running_job_is_requeued_then_closed(client, db, patient):
    user, _ = patient
    fake = FakeRedis()
    job_id = _queued_job(db, user["patient_id"])
    job = db.get(CoachJob, job_id)
    job.status, job.started_at, job.attempts = jobs.RUNNING, utcnow() - timedelta(minutes=30), 1
    db.commit()
    assert jobs.recover_stale_jobs(db, fake) == 1
    assert db.get(CoachJob, job_id, populate_existing=True).status == jobs.QUEUED
    assert jobs.dequeue_job(fake) == job_id

    # Deneme hakkı bitmişse DLQ'ya alınır; hasta yine de kural tabanlı yedek yanıt alır (askıda kalmaz)
    job = db.get(CoachJob, job_id)
    job.status, job.started_at, job.attempts = jobs.RUNNING, utcnow() - timedelta(minutes=30), 3
    db.commit()
    jobs.recover_stale_jobs(db, fake)
    job = db.get(CoachJob, job_id, populate_existing=True)
    assert job.status == jobs.FAILED and job.reply["guardrail_action"] == "llm_fallback"
    assert [e["job_id"] for e in jobs.dlq_entries(fake)] == [job_id]


# ---------------- Kuyruk yolu (sahte Redis) ----------------
def test_enqueue_dequeue_fifo():
    fake = FakeRedis()
    assert jobs.enqueue_job(fake, "a") and jobs.enqueue_job(fake, "b")
    assert jobs.dequeue_job(fake) == "a"
    assert jobs.dequeue_job(fake) == "b"
    assert jobs.dequeue_job(fake) is None


def test_enqueue_failure_falls_back_to_inline(client, db, patient):
    class BrokenRedis(FakeRedis):
        def lpush(self, key, *values):
            raise ConnectionError("redis yok")

    _, h = patient
    reset_redis(BrokenRedis())
    r = post_job(client, h, "Merhaba")
    assert r.status_code == 202
    assert client.get(f"/coach/jobs/{r.json()['job_id']}", headers=h).json()["status"] == "done"


def test_api_enqueues_and_worker_processes(client, db, patient):
    from app import worker

    _, h = patient
    fake = FakeRedis()
    reset_redis(fake)
    r = post_job(client, h, "Merhaba")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert list(fake.lists[jobs.QUEUE_KEY]) == [job_id]
    assert client.get(f"/coach/jobs/{job_id}", headers=h).json()["status"] == "queued"

    stop = threading.Event()

    class StoppingRedis(FakeRedis):
        def brpop(self, keys, timeout=0):
            item = fake.brpop(keys, timeout)
            if item is None:
                stop.set()
            return item

    worker.run(stop, redis=StoppingRedis(), timeout=0, session_factory=TestingSession)
    body = client.get(f"/coach/jobs/{job_id}", headers=h).json()
    assert body["status"] == "done" and body["reply"]["message"]


# ---------------- Zamanlayıcı API'de başlamaz ----------------
def test_api_does_not_start_scheduler_by_default(monkeypatch):
    import app.main as main

    calls = []
    monkeypatch.setattr(main, "start_scheduler", lambda s: calls.append(s))
    assert main.settings.run_scheduler_in_api is False
    with TestClient(main.app):
        pass
    assert calls == []

    monkeypatch.setattr(main, "settings", main.settings.model_copy(update={"run_scheduler_in_api": True}))
    with TestClient(main.app):
        pass
    assert len(calls) == 1
