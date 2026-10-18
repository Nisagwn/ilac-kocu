"""Dayanıklılık: devre kesici, koç işi yeniden deneme + DLQ, lider kilidi, zamanlayıcı, haftalık özetler.

Ağ, gerçek Redis ve gerçek LLM kullanılmaz (tests/fake_redis.py, sahte LLM istemcisi).
"""

import json
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import get_settings
from app.models import CoachJob, CoachMessage, Patient, PushSubscription, WeeklySummary
from app.redis_client import reset_redis
from app.scripts import dlq as dlq_cli
from app.services import circuit_breaker as cb
from app.services import jobs, openfda
from app.services.leader import LeaderLock
from app.services.llm import narrative
from app.services.scheduler import build_scheduler, scheduled_jobs
from app.services.weekly_summaries import generate_weekly_summaries, latest_stored_summary
from app.worker import LeaderScheduler
from tests.conftest import TestingSession, engine, register
from tests.fake_redis import BrokenRedis, FakeRedis
from tests.test_coach_llm import FakeClient, add_plan, final, provider_with, response, text
from tests.test_summary_llm import seed_week


@pytest.fixture()
def pat(db, patient):
    user, h = patient
    return db.get(Patient, user["patient_id"]), h


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def breaker(redis=None, use_redis=False, **kw):
    clock = Clock()
    b = cb.CircuitBreaker("test", failure_threshold=3, window_seconds=60, recovery_seconds=30, redis=redis,
                          use_redis=use_redis, clock=clock, **kw)
    return b, clock


def _fail(b):
    with pytest.raises(RuntimeError), b.guard():
        raise RuntimeError("bağımlılık çöktü")


# ---------------- Devre kesici ----------------
@pytest.mark.parametrize("backend", ["local", "redis"])
def test_breaker_opens_half_opens_and_closes(backend):
    fake = FakeRedis() if backend == "redis" else None
    b, clock = breaker(redis=fake, use_redis=fake is not None)
    for _ in range(2):
        _fail(b)
    assert b.state == cb.CLOSED
    _fail(b)
    assert b.state == cb.OPEN
    with pytest.raises(cb.CircuitOpenError):  # açıkken çağrı yapılmaz
        b.call(lambda: pytest.fail("çağrılmamalıydı"))
    clock.t += 31
    assert b.state == cb.HALF_OPEN
    assert b.allow() is True and b.allow() is False  # yarı açık: tek deneme
    b.record_success()
    assert b.state == cb.CLOSED and b.call(lambda: 42) == 42
    if fake is not None:
        assert fake.hashes == {} or fake.hget("cb:test:state", "state") in (None, cb.CLOSED)


@pytest.mark.parametrize("backend", ["local", "redis"])
def test_breaker_failed_probe_reopens(backend):
    fake = FakeRedis() if backend == "redis" else None
    b, clock = breaker(redis=fake, use_redis=fake is not None)
    for _ in range(3):
        _fail(b)
    clock.t += 31
    _fail(b)  # deneme çağrısı başarısız
    assert b.state == cb.OPEN
    clock.t += 10
    assert b.allow() is False


def test_breaker_window_slides_and_ignored_errors_do_not_count():
    b, clock = breaker(ignore=(KeyError,))
    _fail(b)
    _fail(b)
    clock.t += 61  # eski hatalar pencereden düştü
    _fail(b)
    assert b.state == cb.CLOSED
    for _ in range(5):
        with pytest.raises(KeyError), b.guard():
            raise KeyError("bulunamadı")
    assert b.state == cb.CLOSED


def test_breaker_state_shared_between_replicas_via_redis():
    fake = FakeRedis()
    a, clock = breaker(redis=fake, use_redis=True)
    other = cb.CircuitBreaker("test", failure_threshold=3, window_seconds=60, recovery_seconds=30, redis=fake,
                              clock=clock)
    for _ in range(3):
        _fail(a)
    assert other.state == cb.OPEN and other.allow() is False
    clock.t += 31
    assert other.allow() is True and a.allow() is False  # tek deneme tüm kopyalarda tek


def test_breaker_falls_back_to_local_when_redis_broken():
    b, _ = breaker(redis=BrokenRedis(), use_redis=True)
    for _ in range(3):
        _fail(b)
    assert b.state == cb.OPEN


def test_breaker_metrics_exported():
    from prometheus_client import generate_latest

    b, _ = breaker()
    for _ in range(3):
        _fail(b)
    out = generate_latest().decode()
    assert 'circuit_breaker_state{name="test"} 2.0' in out
    assert 'circuit_breaker_opened_total{name="test"}' in out


def test_disabled_breaker_always_allows(monkeypatch):
    monkeypatch.setattr(get_settings(), "circuit_breaker_enabled", False)
    cb.reset_breakers()
    b = cb.get_breaker("kapali")
    for _ in range(10):
        _fail(b)
    assert b.allow() and b.state == cb.CLOSED


# ---------------- LLM yolları devre açıkken ----------------
def _open_llm_breaker():
    b = cb.llm_breaker()
    for _ in range(get_settings().circuit_breaker_failure_threshold):
        b.record_failure()
    assert b.state == cb.OPEN


def test_coach_uses_rule_based_immediately_when_llm_circuit_open(db, pat):
    p, _ = pat
    add_plan(db, p.id)
    _open_llm_breaker()
    provider, client = provider_with([final()])
    reply = provider.reply(db, p, "bu hafta nasılım")
    assert reply.guardrail_action == "llm_fallback" and client.calls == []


def test_coach_provider_errors_open_llm_circuit(db, pat):
    p, _ = pat
    n = get_settings().circuit_breaker_failure_threshold
    provider, client = provider_with([ConnectionError("ağ yok")] * n + [final()])
    for _ in range(n):
        assert provider.reply(db, p, "bu hafta nasılım").guardrail_action == "llm_fallback"
    assert cb.llm_breaker().state == cb.OPEN
    provider.reply(db, p, "bu hafta nasılım")
    assert len(client.calls) == n  # devre açıkken sağlayıcıya gidilmedi


def test_schema_errors_do_not_open_llm_circuit(db, pat):
    p, _ = pat
    n = get_settings().circuit_breaker_failure_threshold
    provider, _ = provider_with([response([text("json değil")])] * (n + 1))
    for _ in range(n + 1):
        provider.reply(db, p, "bu hafta nasılım")
    assert cb.llm_breaker().state == cb.CLOSED


def test_narrative_uses_template_when_llm_circuit_open_and_does_not_cache(db, patient):
    user, _ = patient
    seed_week(db, user["patient_id"])
    narrative.clear_cache()
    _open_llm_breaker()
    llm = FakeClient([response([text("Hasta dozlarının çoğunu almış.")])])
    from app.services.summary import weekly_summary

    out = weekly_summary(db, user["patient_id"], llm_client=llm)
    assert out["narrative_source"] == "template" and llm.calls == []
    cb.reset_breakers()  # devre kapandı: önbellekte şablon kalmadığı için LLM denenir
    assert weekly_summary(db, user["patient_id"], llm_client=llm)["narrative_source"] == "llm"
    narrative.clear_cache()


# ---------------- openFDA devre kesici ----------------
def test_openfda_circuit_returns_stale_label_then_unavailable(monkeypatch):
    openfda.label_cache.clear()
    calls = []

    def flaky(name):
        calls.append(name)
        if len(calls) == 1:
            return {"resolved_generic": "metformin", "indications": "ilk"}
        raise openfda.OpenFDAUnavailable("HTTP 503")

    monkeypatch.setattr(openfda, "fetch_label", flaky)
    try:
        assert openfda.get_label_cached("Glifor")["indications"] == "ilk"
        # taze önbellek boşaltılsa da son etiket korunur (Redis'te ayrı, daha uzun TTL'li anahtar)
        fake = FakeRedis()
        reset_redis(fake)
        openfda.label_cache._memory.clear()  # yalnız Redis'teki son etiket kalsın
        fake.setex("openfda:label:last:metformin", 1, json.dumps({"indications": "son"}))
        n = get_settings().circuit_breaker_failure_threshold
        for _ in range(n):
            assert openfda.get_label_cached("Glifor")["indications"] == "son"
        assert cb.get_breaker(openfda.OPENFDA_BREAKER).state == cb.OPEN
        before = len(calls)
        assert openfda.get_label_cached("Glifor")["indications"] == "son"
        assert len(calls) == before  # devre açık: istek atılmadı
        fake.delete("openfda:label:last:metformin")
        with pytest.raises(openfda.OpenFDAUnavailable):
            openfda.get_label_cached("Glifor")
    finally:
        reset_redis(None)
        openfda.label_cache.clear()


def test_openfda_not_found_does_not_open_circuit(monkeypatch):
    openfda.label_cache.clear()

    def not_found(name):
        raise openfda.DrugLabelNotFound(name)

    monkeypatch.setattr(openfda, "fetch_label", not_found)
    for _ in range(10):
        with pytest.raises(openfda.DrugLabelNotFound):
            openfda.get_label_cached("Glifor")
    assert cb.get_breaker(openfda.OPENFDA_BREAKER).state == cb.CLOSED


def test_fetch_label_http_error_maps_to_unavailable():
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    with httpx.Client(transport=transport) as c, pytest.raises(openfda.OpenFDAUnavailable):
        openfda.fetch_label("Glifor", client=c)


# ---------------- Koç işleri: yeniden deneme + DLQ ----------------
def _queued_job(db, patient_id, message="nasılım"):
    reply, job = jobs.submit_coach_message(db, db.get(Patient, patient_id), message)
    assert reply is None
    return job.id


def _always_failing_stage(monkeypatch):
    calls = {"n": 0}
    real = jobs.run_provider_stage

    def stage(db, patient, message, provider=None, commit=True):
        calls["n"] += 1
        if isinstance(provider, jobs._FallbackProvider | jobs._StaticSafeProvider):
            return real(db, patient, message, provider, commit=commit)
        raise RuntimeError("veritabanı hıçkırığı")

    monkeypatch.setattr(jobs, "run_provider_stage", stage)
    return calls


def test_backoff_is_exponential(monkeypatch):
    s = get_settings().model_copy(update={"coach_job_backoff_seconds": 2.0})
    assert [jobs.backoff_seconds(a, s) for a in (1, 2, 3)] == [2.0, 4.0, 8.0]


def test_job_retries_via_redis_then_dead_letters(client, db, patient, monkeypatch):
    user, h = patient
    fake = FakeRedis()
    _always_failing_stage(monkeypatch)
    job_id = _queued_job(db, user["patient_id"])
    s = get_settings().model_copy(update={"coach_job_backoff_seconds": 10.0})

    job = jobs.process_job(db, job_id, redis=fake, settings=s)
    assert job.status == jobs.QUEUED and job.attempts == 1
    assert jobs.promote_due_retries(fake) == 0  # vadesi gelmedi (10 sn)
    assert jobs.promote_due_retries(fake, now=10**10) == 1
    assert jobs.dequeue_job(fake) == job_id

    assert jobs.process_job(db, job_id, redis=fake, settings=s).attempts == 2
    jobs.promote_due_retries(fake, now=10**10)
    job = jobs.process_job(db, jobs.dequeue_job(fake), redis=fake, settings=s)

    # 3. deneme de başarısız: DLQ + failed; hasta yine de güvenli yedek yanıt aldı
    assert job.status == jobs.FAILED and job.attempts == 3
    assert job.reply["guardrail_action"] == "llm_fallback" and job.error == jobs.DLQ_ERROR
    assert job.message  # yeniden kuyruğa alınabilsin diye tutulur
    entries = jobs.dlq_entries(fake)
    assert [e["job_id"] for e in entries] == [job_id] and entries[0]["attempts"] == 3
    assert "nasılım" not in json.dumps(entries)  # DLQ'da mesaj içeriği yok
    coach_msgs = db.query(CoachMessage).filter(CoachMessage.sender == "coach").all()
    assert len(coach_msgs) == 1
    body = client.get(f"/coach/jobs/{job_id}", headers=h).json()
    assert body["status"] == "failed" and body["reply"]["message"]

    from prometheus_client import generate_latest

    assert "coach_dlq_size 1.0" in generate_latest().decode()


def test_dead_letter_uses_static_reply_when_rule_coach_fails(client, db, patient, monkeypatch):
    user, _ = patient
    real = jobs.run_provider_stage

    def stage(db, patient, message, provider=None, commit=True):
        if isinstance(provider, jobs._StaticSafeProvider):
            return real(db, patient, message, provider, commit=commit)
        raise RuntimeError("her şey çöktü")

    monkeypatch.setattr(jobs, "run_provider_stage", stage)
    job_id = _queued_job(db, user["patient_id"])
    job = jobs.process_job(db, job_id, settings=get_settings().model_copy(update={"coach_job_max_attempts": 1}))
    assert job.status == jobs.FAILED and job.reply["message"] == jobs.SAFE_FALLBACK_MESSAGE
    assert "112" in job.reply["message"]


def test_inline_retries_without_redis_end_in_failed_with_reply(client, db, patient, monkeypatch):
    user, _ = patient
    calls = _always_failing_stage(monkeypatch)
    job_id = _queued_job(db, user["patient_id"])
    slept = []
    job = jobs.process_job(db, job_id, sleep=slept.append)
    assert job.status == jobs.FAILED and job.attempts == 3 and len(slept) == 2
    assert calls["n"] == 4  # 3 deneme + 1 yedek yanıt


def test_dlq_cli_list_requeue_purge(client, db, patient, monkeypatch, capsys):
    user, _ = patient
    fake = FakeRedis()
    _always_failing_stage(monkeypatch)
    job_id = _queued_job(db, user["patient_id"])
    jobs.process_job(db, job_id, redis=fake, settings=get_settings().model_copy(update={"coach_job_max_attempts": 1}))
    monkeypatch.undo()

    assert dlq_cli.main(["size"], session_factory=TestingSession, redis=fake) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert dlq_cli.main(["list"], session_factory=TestingSession, redis=fake) == 0
    assert job_id in capsys.readouterr().out
    assert dlq_cli.main(["requeue", job_id], session_factory=TestingSession, redis=fake) == 0
    assert jobs.dlq_size(fake) == 0 and jobs.dequeue_job(fake) == job_id
    job = jobs.process_job(db, job_id, redis=fake)
    assert job.status == jobs.DONE and job.message is None

    assert dlq_cli.main(["purge"], session_factory=TestingSession, redis=fake) == 1  # onaysız
    assert dlq_cli.main(["purge", "--yes"], session_factory=TestingSession, redis=fake) == 0


def test_dlq_cli_without_redis_exits_2(capsys):
    assert dlq_cli.main(["size"]) == 2


def test_worker_promotes_due_retries(client, db, patient):
    from app import worker

    user, _ = patient
    fake = FakeRedis()
    job_id = _queued_job(db, user["patient_id"])
    fake.zadd(jobs.RETRY_KEY, {job_id: 0})
    stop = threading.Event()

    class Stopping(FakeRedis):
        def brpop(self, keys, timeout=0):
            item = fake.brpop(keys, timeout)
            if item is None:
                stop.set()
            return item

        def zrangebyscore(self, *a, **kw):
            return fake.zrangebyscore(*a, **kw)

        def zrem(self, *a):
            return fake.zrem(*a)

        def lpush(self, *a):
            return fake.lpush(*a)

    worker.run(stop, redis=Stopping(), timeout=0, session_factory=TestingSession)
    assert db.get(CoachJob, job_id, populate_existing=True).status == jobs.DONE


# ---------------- Lider kilidi + zamanlayıcı ----------------
def test_leader_lock_is_noop_on_sqlite():
    lock = LeaderLock(engine, 1)
    assert lock.try_acquire() and lock.check() and lock.held
    assert LeaderLock(engine, 1).try_acquire()  # SQLite: her kopya lider
    lock.release()
    assert not lock.held


class FakeLock:
    def __init__(self, results):
        self.results = list(results)
        self.released = False

    def try_acquire(self):
        return self.results.pop(0)

    def release(self):
        self.released = True


class FakeScheduler:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)


def test_leader_scheduler_starts_only_when_leader_and_stops_on_loss():
    started = []

    def factory(settings):
        started.append(FakeScheduler())
        return started[-1]

    ls = LeaderScheduler(FakeLock([False, True, True, False, True]), get_settings(), factory)
    assert ls.tick() is False and started == []
    assert ls.tick() is True and len(started) == 1
    assert ls.tick() is True and len(started) == 1  # liderken yeniden başlatılmaz
    assert ls.tick() is False and started[0].shutdown_calls == [False]  # liderlik kaybı
    assert ls.tick() is True and len(started) == 2  # devralma
    ls.shutdown()  # düzgün kapanış: süren işi bekle, kilidi bırak
    assert started[1].shutdown_calls == [True] and ls.lock.released and ls.scheduler is None


def test_leader_scheduler_run_loop_exits_on_stop():
    stop = threading.Event()
    stop.set()
    LeaderScheduler(FakeLock([]), get_settings(), lambda s: None).run(stop)


def test_scheduled_jobs_include_weekly_summary_and_purge():
    s = get_settings().model_copy(update={"reminders_enabled": True, "vapid_public_key": "a",
                                          "vapid_private_key": "b"})
    ids = [j.id for j in scheduled_jobs(s)]
    assert ids == ["dose_reminders", "weekly_summaries", "idempotency_purge"]
    sched = build_scheduler(s)
    weekly = sched.get_job("weekly_summaries")
    trigger = str(weekly.trigger)
    assert "day_of_week='mon'" in trigger and "hour='7'" in trigger and "minute='0'" in trigger
    assert str(sched.timezone) == "Europe/Istanbul"
    off = get_settings().model_copy(update={"weekly_summary_enabled": False, "reminders_enabled": False})
    assert [j.id for j in scheduled_jobs(off)] == ["idempotency_purge"]


def test_idempotency_purge_job_runs(monkeypatch):
    from app import platform
    from app.services import scheduler

    calls = []
    monkeypatch.setattr(platform, "purge_expired_idempotency_keys", lambda db: calls.append(db) or 3)
    monkeypatch.setattr("app.db.SessionLocal", TestingSession)
    scheduler.run_idempotency_purge()
    assert len(calls) == 1


# ---------------- Haftalık özetler ----------------
class FakeSender:
    def __init__(self):
        self.calls = []

    def __call__(self, info, payload):
        self.calls.append((info, json.loads(payload)))


def _assign(client, h, clinician_email="dr.mehmet@example.com"):
    r = client.put("/patients/me/clinician", headers=h, json={"clinician_email": clinician_email})
    assert r.status_code == 200, r.text


def test_weekly_summaries_generated_stored_and_notified(client, db, patient, clinician):
    user, h = patient
    _, ch = clinician
    _assign(client, h)
    pid = user["patient_id"]
    seed_week(db, pid)
    # Hekim aboneliği doğrudan eklenir (POST /push/subscribe şu an yalnız hasta rolüne açık)
    dr_user_id = client.get("/auth/me", headers=ch).json()["id"]
    db.add(PushSubscription(user_id=dr_user_id, endpoint="https://push.example.com/dr", p256dh="p", auth="a"))
    db.commit()
    register(client, "pasif@example.com")  # ilacı/ölçümü olmayan hasta: özet üretilmez

    sender = FakeSender()
    monday = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 07:00 Europe/Istanbul
    res = generate_weekly_summaries(db, now=monday, sender=sender)
    assert res == {"created": 1, "skipped": 0, "failed": 0, "notified": 1}
    row = db.query(WeeklySummary).one()
    assert row.patient_id == pid and str(row.period_end) == "2026-10-18" and str(row.period_start) == "2026-10-12"
    assert row.data["narrative"] and row.narrative_source == "template"
    payload = sender.calls[0][1]
    assert payload["title"] == "Haftalık özetler hazır" and "1 hastanızın" in payload["body"]

    # ikinci çalıştırma (lider değişimi) kopya üretmez
    assert generate_weekly_summaries(db, now=monday, sender=sender)["skipped"] == 1
    assert db.query(WeeklySummary).count() == 1


def test_summaries_endpoint_returns_latest_stored_summary(client, db, patient, clinician):
    user, h = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    live = client.get(f"/summaries/{pid}", headers=h).json()
    assert "stored" not in live

    generate_weekly_summaries(db, now=datetime.now(UTC), sender=FakeSender())
    stored = client.get(f"/summaries/{pid}", headers=h).json()
    assert stored["stored"] is True and stored["period_end"] and stored["narrative"]
    assert stored["adherence"] == live["adherence"]
    assert "stored" not in client.get(f"/summaries/{pid}?source=live", headers=h).json()
    # yetki aynı: başka hasta göremez
    _, oh = register(client, "ali@example.com")
    assert client.get(f"/summaries/{pid}", headers=oh).status_code == 404


def test_old_stored_summary_is_not_returned(db, patient):
    user, _ = patient
    pid = user["patient_id"]
    seed_week(db, pid)
    generate_weekly_summaries(db, now=datetime.now(UTC) - timedelta(days=9), sender=FakeSender())
    assert db.query(WeeklySummary).count() == 1
    assert latest_stored_summary(db, pid) is None


def test_weekly_summary_failure_for_one_patient_does_not_stop_others(client, db, patient, monkeypatch):
    user, _ = patient
    seed_week(db, user["patient_id"])
    other, _ = register(client, "ali@example.com")
    seed_week(db, other["patient_id"])
    from app.services import weekly_summaries as ws

    real = ws.weekly_summary

    def flaky(db, pid, **kw):
        if pid == user["patient_id"]:
            raise RuntimeError("bozuk veri")
        return real(db, pid, **kw)

    monkeypatch.setattr(ws, "weekly_summary", flaky)
    res = generate_weekly_summaries(db, sender=FakeSender())
    assert res["created"] == 1 and res["failed"] == 1
