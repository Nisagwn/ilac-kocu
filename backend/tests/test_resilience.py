"""Dayanıklılık: devre kesici (LLM, openFDA), koç işi yeniden deneme + DLQ, lider işçi kilidi, zamanlanmış
haftalık özet ve zamanlayıcı iş listesi."""

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select

from app.config import get_settings
from app.models import CoachJob, CoachMessage, Patient, PushSubscription, WeeklySummary, utcnow
from app.redis_client import reset_redis
from app.scheduler import scheduler_jobs
from app.services import dlq, jobs, openfda
from app.services.circuit_breaker import BreakerConfig, CircuitBreaker, CircuitOpenError, get_breaker
from app.services.leader import LeaderLock
from app.services.llm.client import LlmError, create_message
from app.services.weekly_reports import generate_weekly_summaries
from tests.conftest import TestingSession, register
from tests.test_coach_llm import FakeClient, llm_settings, provider_with
from tests.test_jobs import FakeRedis, post_job


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _breaker(clock, name=None, **kw):
    cfg = BreakerConfig(**{"failure_threshold": 3, "window_seconds": 60, "open_seconds": 30, **kw})
    return CircuitBreaker(name or f"t-{uuid.uuid4().hex[:8]}", cfg, clock=clock)


def _boom():
    raise ConnectionError("servis yok")


# ---------------- devre kesici (süreç içi) ----------------
def test_breaker_opens_after_threshold_and_rejects():
    clock = Clock()
    b = _breaker(clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            b.call(_boom)
    assert b.state() == "open"
    calls = []
    with pytest.raises(CircuitOpenError) as exc:
        b.call(lambda: calls.append(1))
    assert calls == [] and 0 < exc.value.retry_after <= 30


def test_breaker_failures_outside_window_do_not_open():
    clock = Clock()
    b = _breaker(clock)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            b.call(_boom)
    clock.t += 61
    with pytest.raises(ConnectionError):
        b.call(_boom)
    assert b.state() == "closed"


def test_breaker_success_resets_counter():
    clock = Clock()
    b = _breaker(clock)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            b.call(_boom)
    assert b.call(lambda: "ok") == "ok"
    with pytest.raises(ConnectionError):
        b.call(_boom)
    assert b.state() == "closed"


def test_breaker_half_open_single_probe_then_close_or_reopen():
    clock = Clock()
    b = _breaker(clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            b.call(_boom)
    clock.t += 31
    assert b.state() == "half_open"
    b.allow()  # deneme hakkı bu çağırana
    with pytest.raises(CircuitOpenError):
        b.allow()  # ikinci çağıran beklemeli
    b.record_failure()  # deneme başarısız: yeniden açık
    assert b.state() == "open"
    clock.t += 31
    assert b.call(lambda: 42) == 42  # deneme başarılı: kapalı
    assert b.state() == "closed"


@pytest.mark.skipif(not os.environ.get("REDIS_TEST_URL"), reason="REDIS_TEST_URL yok")
def test_breaker_state_shared_through_redis():
    import redis

    r = redis.Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
    reset_redis(r)
    clock = Clock(datetime.now(UTC).timestamp())
    name = f"t-{uuid.uuid4().hex[:8]}"
    try:
        a, b = _breaker(clock, name), _breaker(clock, name)  # iki "kopya"
        for _ in range(3):
            with pytest.raises(ConnectionError):
                a.call(_boom)
        assert b.state() == "open"
        with pytest.raises(CircuitOpenError):
            b.call(lambda: None)
        clock.t += 31
        b.allow()
        with pytest.raises(CircuitOpenError):
            a.allow()  # deneme hakkı Redis'te tek
        b.record_success()
        assert a.state() == "closed"
    finally:
        r.delete(*[f"cb:{name}:{k}" for k in ("failures", "open_until", "probe")])
        reset_redis(None)


# ---------------- LLM ve openFDA entegrasyonu ----------------
def test_llm_breaker_stops_calling_provider(monkeypatch):
    s = llm_settings()
    client = FakeClient([ConnectionError("down")] * 10)
    for _ in range(get_settings().llm_breaker_failure_threshold):
        with pytest.raises(ConnectionError):
            create_message(client, s, messages=[])
    before = len(client.calls)
    with pytest.raises(LlmError, match="devresi açık"):
        create_message(client, s, messages=[])
    assert len(client.calls) == before  # açık devrede sağlayıcıya istek atılmadı
    assert get_breaker("llm").state() == "open"


def test_coach_falls_back_fast_when_llm_circuit_open(client, db, patient):
    pid = patient[0]["patient_id"]
    for _ in range(get_settings().llm_breaker_failure_threshold):
        get_breaker("llm").record_failure()
    prov, fake = provider_with([])  # boş senaryo: istemci çağrılırsa IndexError
    reply = prov.reply(db, db.get(Patient, pid), "Akşam ilacımı sık unutuyorum, ne yapabilirim?")
    assert reply.guardrail_action == "llm_fallback" and reply.message
    assert fake.calls == []


def test_openfda_breaker(monkeypatch):
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        for _ in range(get_settings().openfda_breaker_failure_threshold):
            with pytest.raises(openfda.OpenFDAUnavailable):
                openfda.fetch_label("metformin", client=c)
        n = len(hits)
        with pytest.raises(openfda.OpenFDAUnavailable, match="devresi açık"):
            openfda.fetch_label("metformin", client=c)
        assert len(hits) == n
    # 404 (etiket yok) devreyi etkilemez
    get_breaker("openfda").record_success()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        for _ in range(10):
            with pytest.raises(openfda.DrugLabelNotFound):
                openfda.fetch_label("bilinmeyen", client=c)
    assert get_breaker("openfda").state() == "closed"


# ---------------- koç işi: yeniden deneme + DLQ ----------------
class DelayRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.delayed: dict[str, float] = {}

    def zadd(self, key, mapping):
        self.delayed.update(mapping)

    def eval(self, script, numkeys, *args):
        now = float(args[-1])
        due = [k for k, t in self.delayed.items() if t <= now]
        for k in due:
            del self.delayed[k]
            self.lpush(jobs.QUEUE_KEY, k)
        return len(due)


def _job(db, pid, message="Merhaba"):
    job = CoachJob(id=str(uuid.uuid4()), patient_id=pid, message=message, status=jobs.QUEUED, attempts=0,
                   created_at=utcnow())
    db.add(job)
    db.commit()
    return job.id


@pytest.fixture()
def broken_pipeline(monkeypatch):
    def fail(*a, **k):
        raise OSError("veritabanı kesintisi")

    monkeypatch.setattr(jobs, "run_provider_stage", fail)


def test_retry_then_dead_letter(db, patient, broken_pipeline):
    pid = patient[0]["patient_id"]
    job_id = _job(db, pid)
    for attempt in (1, 2):
        job = jobs.process_job(db, job_id)
        assert job.status == jobs.QUEUED and job.attempts == attempt and job.last_error == "OSError"
    job = jobs.process_job(db, job_id)
    assert job.status == jobs.FAILED and job.dead_lettered_at is not None
    assert job.message == "Merhaba"  # DLQ'da yeniden deneme için tutulur
    assert jobs.job_out(job).error == jobs.FAILED_MESSAGE
    assert [j.id for j in dlq.list_dead(db)] == [job_id] and dlq.dlq_size(db) == 1


def test_retry_delay_backoff():
    s = get_settings()
    assert [jobs.retry_delay(n) for n in (1, 2, 3)] == [s.coach_job_retry_base_seconds * f for f in (1, 2, 4)]
    assert jobs.retry_delay(50) == s.coach_job_retry_max_seconds


def test_worker_schedules_retry_and_promotes(db, patient, broken_pipeline):
    from app import worker

    pid = patient[0]["patient_id"]
    job_id = _job(db, pid)
    r = DelayRedis()
    worker._process(job_id, r, session_factory=TestingSession)
    assert job_id in r.delayed and not r.lists.get(jobs.QUEUE_KEY)
    assert jobs.promote_due(r, now=r.delayed[job_id] - 1) == 0
    assert jobs.promote_due(r, now=r.delayed[job_id] + 1) == 1
    assert jobs.dequeue_job(r) == job_id


def test_worker_records_unexpected_crash(db, patient, monkeypatch):
    from app import worker

    pid = patient[0]["patient_id"]
    job_id = _job(db, pid)

    def crash(*a, **k):
        raise RuntimeError("beklenmeyen")

    monkeypatch.setattr(jobs, "process_job", crash)
    db.get(CoachJob, job_id).attempts = 1
    db.commit()
    r = DelayRedis()
    worker._process(job_id, r, session_factory=TestingSession)
    job = db.get(CoachJob, job_id, populate_existing=True)
    assert job.status == jobs.QUEUED and job.last_error == "RuntimeError" and job_id in r.delayed


def test_inline_path_retries_until_success(client, db, patient, monkeypatch):
    monkeypatch.setattr(get_settings(), "coach_job_retry_base_seconds", 0.0)
    real = jobs.run_provider_stage
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) <= 2:  # ilk denemede hem provider hem yedek düşer
            raise OSError("geçici")
        return real(*a, **k)

    monkeypatch.setattr(jobs, "run_provider_stage", flaky)
    r = post_job(client, patient[1], "Merhaba koç")
    body = client.get(f"/v1/coach/jobs/{r.json()['job_id']}", headers=patient[1]).json()
    assert body["status"] == "done" and body["reply"]["message"]
    job = db.get(CoachJob, r.json()["job_id"], populate_existing=True)
    assert job.attempts == 2 and job.message is None


def test_dlq_retry_and_purge(db, patient, broken_pipeline, monkeypatch):
    pid = patient[0]["patient_id"]
    job_id = _job(db, pid, "Akşam ilacımı unutuyorum")
    for _ in range(3):
        jobs.process_job(db, job_id)
    assert db.get(CoachJob, job_id).dead_lettered_at is not None

    monkeypatch.undo()  # hat düzeldi
    r = FakeRedis()
    assert dlq.retry(db, job_id, r) is True
    assert jobs.dequeue_job(r) == job_id
    job = jobs.process_job(db, job_id)
    assert job.status == jobs.DONE and job.dead_lettered_at is None and job.message is None
    assert db.scalar(select(CoachMessage).where(CoachMessage.patient_id == pid, CoachMessage.sender == "coach"))
    assert dlq.retry(db, job_id, r) is False  # artık DLQ'da değil


def test_dlq_purge_clears_old_messages(db, patient):
    pid = patient[0]["patient_id"]
    old, new = _job(db, pid, "eski"), _job(db, pid, "yeni")
    for jid, age in ((old, 10), (new, 1)):
        j = db.get(CoachJob, jid)
        j.status, j.dead_lettered_at = jobs.FAILED, utcnow() - timedelta(days=age)
    db.commit()
    assert dlq.purge(db, retention_days=7) == 1
    assert db.get(CoachJob, old).message is None and db.get(CoachJob, old).dead_lettered_at is None
    assert db.get(CoachJob, new).message == "yeni"
    assert dlq.retry(db, old) is False


def test_dlq_cli_list(db, patient, capsys, monkeypatch):
    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", TestingSession)
    pid = patient[0]["patient_id"]
    jid = _job(db, pid)
    j = db.get(CoachJob, jid)
    j.status, j.dead_lettered_at, j.last_error, j.attempts = jobs.FAILED, utcnow(), "OSError", 3
    db.commit()
    assert dlq.main(["list"]) == 0
    out = capsys.readouterr().out
    assert jid in out and "OSError" in out and "Akşam" not in out and "Merhaba" not in out  # içerik yok
    assert dlq.main(["bilinmeyen"]) == 2


# ---------------- lider kilidi ----------------
def test_sqlite_process_is_always_leader():
    lock = LeaderLock(create_engine("sqlite://"))
    assert lock.try_acquire() and lock.check()
    lock.release()
    assert not lock.is_leader


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL yok (PostgreSQL)")
def test_postgres_advisory_lock_single_leader():
    eng = create_engine(os.environ["TEST_DATABASE_URL"])
    key = 0x7E57_0000 + uuid.uuid4().int % 1000
    a, b = LeaderLock(eng, key), LeaderLock(eng, key)
    try:
        assert a.try_acquire() is True
        assert b.try_acquire() is False and not b.is_leader
        assert a.check()
        a.release()
        assert b.try_acquire() is True  # lider düşünce diğeri devralır
    finally:
        a.release()
        b.release()
        eng.dispose()


def test_leadership_starts_and_stops_scheduler():
    from app.worker import Leadership

    class FakeLock:
        def __init__(self):
            self.is_leader, self.alive, self.released = False, True, False

        def try_acquire(self):
            self.is_leader = True
            return True

        def check(self):
            if not self.alive:
                self.is_leader = False
            return self.alive

        def release(self):
            self.released = True

    class FakeScheduler:
        def __init__(self):
            self.stopped = None

        def shutdown(self, wait):
            self.stopped = wait

    started = []
    lock = FakeLock()
    lead = Leadership(threading.Event(), lock=lock, starter=lambda s: started.append(FakeScheduler()) or started[-1],
                      interval=0)
    lead.tick()
    assert len(started) == 1 and lead.scheduler is started[0]
    lead.tick()
    assert len(started) == 1  # lider kaldıkça yeniden başlatılmaz
    lock.alive = False
    lead.tick()  # liderlik düştü: zamanlayıcı durur, kilit yeniden alınır
    assert started[0].stopped is False and len(started) == 2
    lead.shutdown()
    assert started[1].stopped is True and lock.released


# ---------------- zamanlanmış haftalık özet ----------------
class FakeSender:
    def __init__(self):
        self.calls = []

    def __call__(self, info, data):
        self.calls.append((info["endpoint"], data))


def test_weekly_summaries_generated_once_and_clinician_notified(client, db, patient, clinician, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "vapid_public_key", "pub")
    monkeypatch.setattr(s, "vapid_private_key", "priv")
    ph, (doc, dh) = patient[1], clinician
    client.put("/v1/patients/me/clinician", headers=ph, json={"clinician_email": "dr.mehmet@example.com"})
    register(client, "atanmamis@example.com")  # hekimi olmayan hasta: özet üretilmez
    db.add(PushSubscription(user_id=doc["id"], endpoint="https://push.example/doc", p256dh="k", auth="a"))
    db.commit()

    sender = FakeSender()
    now = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # Pazartesi 07:00 İstanbul
    assert generate_weekly_summaries(db, now=now, sender=sender) == {"created": 1, "skipped": 0, "notified": 1}
    row = db.scalar(select(WeeklySummary))
    assert (row.period_start.isoformat(), row.period_end.isoformat()) == ("2026-10-13", "2026-10-19")
    assert row.payload["period_days"] == 7 and "adherence" in row.payload
    assert row.narrative_source == row.payload["narrative_source"]
    endpoint, data = sender.calls[0]
    assert endpoint == "https://push.example/doc" and "1 hastanızın" in data and "Ayse" not in data

    # tekrar tetiklenirse çift kayıt/bildirim yok
    assert generate_weekly_summaries(db, now=now, sender=sender) == {"created": 0, "skipped": 1, "notified": 0}
    assert len(sender.calls) == 1

    hist = client.get(f"/v1/summaries/{patient[0]['patient_id']}/history", headers=dh)
    assert hist.status_code == 200 and hist.json()[0]["period_start"] == "2026-10-13"
    _, other = register(client, "baska@example.com")
    assert client.get(f"/v1/summaries/{patient[0]['patient_id']}/history", headers=other).status_code == 404


def test_scheduler_job_list(monkeypatch):
    s = get_settings().model_copy(update={"reminders_enabled": True, "vapid_public_key": "a",
                                          "vapid_private_key": "b"})
    ids = [j[0] for j in scheduler_jobs(s)]
    assert ids == ["dose_reminders", "weekly_summaries", "idempotency_purge", "dlq_purge"]
    weekly = dict((j[0], j[2]) for j in scheduler_jobs(s))["weekly_summaries"]["trigger"]
    nxt = weekly.get_next_fire_time(None, datetime(2026, 10, 17, 12, 0, tzinfo=UTC))
    assert nxt.isoformat() == "2026-10-19T07:00:00+03:00"  # Pazartesi 07:00 İstanbul
    off = s.model_copy(update={"reminders_enabled": False, "weekly_summary_enabled": False})
    assert [j[0] for j in scheduler_jobs(off)] == ["idempotency_purge", "dlq_purge"]
