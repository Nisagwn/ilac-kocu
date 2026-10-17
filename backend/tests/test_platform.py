"""API platform katmanı: /v1 sürümleme, spec yolları, token bucket, Idempotency-Key, ETag, cursor sayfalama,
sağlık uçları."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import Alert, CoachMessage, IdempotencyKey, Measurement, Sender, Severity
from app.redis_client import reset_redis
from app.routers import health
from app.services import idempotency, ratelimit
from tests.conftest import register

V1 = "/v1"


def _pid(patient):
    return patient[0]["patient_id"]


def _measure(client, pid, h, value=120, **extra):
    return client.post(f"{V1}/patients/{pid}/measurements", headers={**h, **extra.pop("headers", {})},
                       json={"type": "glucose_fasting", "value": value, **extra})


# ---------------- sürümleme ----------------
def test_v1_and_legacy_paths(client, patient):
    _, h = patient
    r = client.get(f"{V1}/auth/me", headers=h)
    assert r.status_code == 200 and "deprecation" not in r.headers

    legacy = client.get("/auth/me", headers=h)
    assert legacy.status_code == 200 and legacy.json() == r.json()
    assert legacy.headers["deprecation"] == "true"
    assert legacy.headers["link"] == '</v1/auth/me>; rel="successor-version"'


def test_v1_login_flow(client, patient):
    r = client.post(f"{V1}/auth/login", data={"username": "ayse@example.com", "password": "sifre1234"})
    assert r.status_code == 200 and r.json()["access_token"]


def test_openapi_lists_only_versioned_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/patients/{patient_id}/measurements" in paths and "/v1/drugs/search" in paths
    unversioned = [p for p in paths if not p.startswith("/v1/") and not p.startswith("/health")]
    assert unversioned == []
    assert "deprecation" not in client.get("/openapi.json").headers


# ---------------- spec yolları ----------------
def test_collection_paths_for_patient(client, patient):
    pid, h = _pid(patient), patient[1]
    client.post(f"{V1}/patients/{pid}/medications", headers=h,
                json={"name": "Glifor", "schedules": [{"time_of_day": "08:00"}]})
    _measure(client, pid, h)

    meds = client.get(f"{V1}/medications", headers=h).json()
    assert [m["name"] for m in meds] == ["Glifor"]
    assert client.get(f"{V1}/medications?patient_id={pid}", headers=h).json() == meds
    assert len(client.get(f"{V1}/measurements", headers=h).json()) == 1
    assert client.get(f"{V1}/doses", headers=h).json() == []


def test_collection_paths_authorization(client, patient, clinician):
    pid, h = _pid(patient), patient[1]
    _, other_h = register(client, "baska@example.com")
    for coll in ("medications", "doses", "measurements"):
        assert client.get(f"{V1}/{coll}?patient_id={pid}", headers=other_h).status_code == 404
        assert client.get(f"{V1}/{coll}?patient_id=99999", headers=h).status_code == 404
        assert client.get(f"{V1}/{coll}?patient_id={pid}", headers=clinician[1]).status_code == 404
        assert client.get(f"{V1}/{coll}", headers=clinician[1]).status_code == 422  # hekim için patient_id zorunlu

    client.put(f"{V1}/patients/me/clinician", headers=h, json={"clinician_email": "dr.mehmet@example.com"})
    assert client.get(f"{V1}/measurements?patient_id={pid}", headers=clinician[1]).status_code == 200


# ---------------- token bucket ----------------
@pytest.fixture()
def small_bucket(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "api_rate_limit_capacity", 3)
    monkeypatch.setattr(s, "api_rate_limit_refill_per_second", 0.001)
    return s


def test_rate_limit_headers_and_429(client, patient, small_bucket):
    _, h = patient
    remaining = []
    for _ in range(3):
        r = client.get(f"{V1}/auth/me", headers=h)
        assert r.status_code == 200
        assert r.headers["x-ratelimit-limit"] == "3"
        remaining.append(int(r.headers["x-ratelimit-remaining"]))
        assert int(r.headers["x-ratelimit-reset"]) >= 0
    assert remaining == [2, 1, 0]
    r = client.get(f"{V1}/auth/me", headers=h)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1 and r.headers["x-ratelimit-remaining"] == "0"
    assert "istek" in r.json()["detail"]


def test_rate_limit_bucket_per_identity(client, patient, small_bucket):
    _, h = patient
    for _ in range(3):
        client.get(f"{V1}/auth/me", headers=h)
    assert client.get(f"{V1}/auth/me", headers=h).status_code == 429
    # başka kullanıcının ve anonim isteğin kendi kovası var
    _, h2 = register(client, "ikinci@example.com")
    assert client.get(f"{V1}/auth/me", headers=h2).status_code == 200
    assert client.get(f"{V1}/auth/me").status_code == 401
    # sahte token kullanıcı kovası açamaz: IP kovasına düşer
    for _ in range(5):
        client.get(f"{V1}/auth/me", headers={"Authorization": "Bearer sahte"})
    assert client.get(f"{V1}/auth/me", headers={"Authorization": "Bearer sahte2"}).status_code == 429


def test_rate_limit_exemptions(client, patient, small_bucket):
    pid, h = _pid(patient), patient[1]
    for _ in range(3):
        client.get(f"{V1}/auth/me", headers=h)
    assert client.get("/health/live").status_code == 200
    assert "x-ratelimit-limit" not in client.get("/health/live").headers
    # kova boşken bile acil belirti mesajına güvenlik yanıtı döner
    r = client.post(f"{V1}/coach/chat", headers=h, json={"message": "göğsümde baskı var nefes alamıyorum"})
    assert r.status_code == 200 and r.json()["risk_level"] == "emergency"
    assert client.get(f"{V1}/patients/{pid}", headers=h).status_code == 429


def test_rate_limit_disabled(client, patient, small_bucket, monkeypatch):
    monkeypatch.setattr(small_bucket, "api_rate_limit_enabled", False)
    _, h = patient
    for _ in range(6):
        r = client.get(f"{V1}/auth/me", headers=h)
        assert r.status_code == 200 and "x-ratelimit-limit" not in r.headers


def test_local_bucket_refills(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    take = lambda: ratelimit.take_token("t:refill", 2, 1.0)  # noqa: E731
    assert take().allowed and take().allowed
    blocked = take()
    assert not blocked.allowed and blocked.retry_after == 1 and blocked.reset_after == 2
    now[0] += 1.0
    r = take()
    assert r.allowed and r.remaining == 0
    now[0] += 10
    assert take().remaining == 1  # kapasiteyi aşmaz


@pytest.mark.skipif(not os.environ.get("REDIS_TEST_URL"), reason="REDIS_TEST_URL yok (gerçek Redis ile Lua testi)")
def test_redis_lua_token_bucket():
    import redis

    r = redis.Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
    key = f"t:{uuid.uuid4().hex}"
    reset_redis(r)
    try:
        results = [ratelimit.take_token(key, 3, 0.001) for _ in range(4)]
        assert [x.allowed for x in results] == [True, True, True, False]
        assert [x.remaining for x in results] == [2, 1, 0, 0]
        assert results[-1].retry_after >= 1
        assert 0 < r.pttl(f"tb:{key}") <= 3_001_000
        # iki "kopya" aynı kovayı paylaşır: süreç içi durum yok
        ratelimit.reset_local_limits()
        assert not ratelimit.take_token(key, 3, 0.001).allowed
        assert ratelimit.take_token(key + "x", 3, 1000).allowed  # hızlı dolum: başka kova
    finally:
        r.delete(f"tb:{key}", f"tb:{key}x")
        reset_redis(None)


def test_redis_failure_falls_back_to_local():
    class Broken:
        def register_script(self, _):
            def run(**_):
                raise ConnectionError("redis yok")
            return run

    reset_redis(Broken())
    try:
        assert ratelimit.take_token("t:broken", 1, 0.001).allowed
        assert not ratelimit.take_token("t:broken", 1, 0.001).allowed
    finally:
        reset_redis(None)


# ---------------- Idempotency-Key ----------------
def _key():
    return f"test-{uuid.uuid4().hex}"


def test_idempotent_replay(client, patient, db):
    pid, h = _pid(patient), patient[1]
    k = _key()
    first = _measure(client, pid, h, 130, headers={"Idempotency-Key": k})
    assert first.status_code == 201 and "idempotent-replayed" not in first.headers
    second = _measure(client, pid, h, 130, headers={"Idempotency-Key": k})
    assert second.status_code == 201 and second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()
    assert second.headers["content-type"].startswith("application/json")
    assert db.scalar(select(func.count(Measurement.id))) == 1


def test_idempotency_key_reused_with_other_body(client, patient, db):
    pid, h = _pid(patient), patient[1]
    k = _key()
    assert _measure(client, pid, h, 130, headers={"Idempotency-Key": k}).status_code == 201
    r = _measure(client, pid, h, 999, headers={"Idempotency-Key": k})
    assert r.status_code == 422 and "başka bir istek" in r.json()["detail"]
    assert db.scalar(select(func.count(Measurement.id))) == 1


def test_idempotency_scoped_per_user(client, patient):
    pid, h = _pid(patient), patient[1]
    user2, h2 = register(client, "ikinci@example.com")
    k = _key()
    assert _measure(client, pid, h, headers={"Idempotency-Key": k}).status_code == 201
    r = _measure(client, user2["patient_id"], h2, headers={"Idempotency-Key": k})
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers


def test_idempotency_invalid_key_and_anonymous(client, patient, db):
    pid, h = _pid(patient), patient[1]
    assert _measure(client, pid, h, headers={"Idempotency-Key": "kisa"}).status_code == 400
    assert _measure(client, pid, h, headers={"Idempotency-Key": "bosluk var icinde"}).status_code == 400
    # kimliksiz istekte anahtar yok sayılır (401 döner, kayıt açılmaz)
    r = client.post(f"{V1}/patients/{pid}/measurements", headers={"Idempotency-Key": _key()},
                    json={"type": "glucose_fasting", "value": 100})
    assert r.status_code == 401
    assert db.scalar(select(func.count(IdempotencyKey.id))) == 0


def test_idempotency_in_progress_conflict(client, patient, db):
    pid, h = _pid(patient), patient[1]
    k = _key()
    body = b'{"type":"glucose_fasting","value":120}'
    fp = idempotency.request_fingerprint("POST", f"{V1}/patients/{pid}/measurements", "", body)
    db.add(IdempotencyKey(user_id=patient[0]["id"], key=k, method="POST", path="x", request_hash=fp,
                          created_at=datetime.now(UTC)))
    db.commit()
    r = client.post(f"{V1}/patients/{pid}/measurements", content=body,
                    headers={**h, "Idempotency-Key": k, "Content-Type": "application/json"})
    assert r.status_code == 409 and r.headers["retry-after"] == "1"


def test_idempotency_error_responses(client, patient, db):
    pid, h = _pid(patient), patient[1]
    k = _key()
    bad = client.post(f"{V1}/patients/{pid}/measurements", headers={**h, "Idempotency-Key": k},
                      json={"type": "glucose_fasting", "value": -5})
    assert bad.status_code == 422
    again = client.post(f"{V1}/patients/{pid}/measurements", headers={**h, "Idempotency-Key": k},
                        json={"type": "glucose_fasting", "value": -5})
    assert again.status_code == 422 and again.headers["idempotent-replayed"] == "true"  # 4xx de saklanır


def test_idempotency_delete_and_put(client, patient, db):
    pid, h = _pid(patient), patient[1]
    m = _measure(client, pid, h).json()
    k = _key()
    hdr = {**h, "Idempotency-Key": k}
    assert client.delete(f"{V1}/patients/{pid}/measurements/{m['id']}", headers=hdr).status_code == 204
    r = client.delete(f"{V1}/patients/{pid}/measurements/{m['id']}", headers=hdr)
    assert r.status_code == 204 and r.headers["idempotent-replayed"] == "true" and r.content == b""


def test_idempotency_purge_and_stale(db, patient):
    uid = patient[0]["id"]
    old = datetime.now(UTC) - timedelta(hours=30)
    db.add_all([
        IdempotencyKey(user_id=uid, key="eski-anahtar", method="POST", path="/x", request_hash="a",
                       status_code=201, response_body="{}", created_at=old),
        IdempotencyKey(user_id=uid, key="yeni-anahtar", method="POST", path="/x", request_hash="a",
                       status_code=201, response_body="{}", created_at=datetime.now(UTC)),
    ])
    db.commit()
    assert idempotency.purge_expired(db, timedelta(hours=24)) == 1
    assert db.scalar(select(func.count(IdempotencyKey.id))) == 1

    # çöken süreçten kalan "işleniyor" kaydı 5 dk sonra devralınır
    db.add(IdempotencyKey(user_id=uid, key="takili-anahtar", method="POST", path="/x", request_hash="b",
                          created_at=datetime.now(UTC) - timedelta(minutes=10)))
    db.commit()
    res = idempotency.begin(db, uid, "takili-anahtar", "POST", "/x", "b", timedelta(hours=24))
    assert res.outcome == "new"


# ---------------- ETag ----------------
def test_etag_and_304(client, patient):
    pid, h = _pid(patient), patient[1]
    _measure(client, pid, h)
    r = client.get(f"{V1}/patients/{pid}/measurements", headers=h)
    etag = r.headers["etag"]
    assert r.headers["cache-control"] == "private, no-cache"

    r304 = client.get(f"{V1}/patients/{pid}/measurements", headers={**h, "If-None-Match": etag})
    assert r304.status_code == 304 and r304.content == b"" and r304.headers["etag"] == etag
    assert client.get(f"{V1}/patients/{pid}/measurements",
                      headers={**h, "If-None-Match": f'"baska", W/{etag}'}).status_code == 304

    _measure(client, pid, h, 140)
    r2 = client.get(f"{V1}/patients/{pid}/measurements", headers={**h, "If-None-Match": etag})
    assert r2.status_code == 200 and r2.headers["etag"] != etag


def test_etag_not_on_errors_or_writes(client, patient):
    pid, h = _pid(patient), patient[1]
    r = client.get(f"{V1}/patients/99999/measurements", headers=h)
    assert r.status_code == 404 and "etag" not in r.headers and r.headers["cache-control"] == "private, no-cache"
    assert "etag" not in _measure(client, pid, h).headers


# ---------------- cursor sayfalama ----------------
def _walk(client, url, h, limit):
    seen, cursor, pages = [], None, 0
    while True:
        sep = "&" if "?" in url else "?"
        r = client.get(f"{url}{sep}limit={limit}" + (f"&cursor={cursor}" if cursor else ""), headers=h)
        assert r.status_code == 200, r.text
        seen += r.json()
        pages += 1
        cursor = r.headers.get("x-next-cursor")
        if not cursor:
            assert "link" not in r.headers or "rel=\"next\"" not in r.headers.get("link", "")
            return seen, pages
        assert r.headers["link"].endswith('rel="next"') and f"cursor={cursor}" in r.headers["link"]


def test_measurement_pagination(client, patient):
    pid, h = _pid(patient), patient[1]
    base = datetime.now(UTC) - timedelta(days=2)
    for i in range(5):
        _measure(client, pid, h, 100 + i, measured_at=(base + timedelta(hours=i)).isoformat())
    full = client.get(f"{V1}/patients/{pid}/measurements", headers=h).json()
    paged, pages = _walk(client, f"{V1}/patients/{pid}/measurements", h, 2)
    assert pages == 3 and paged == full and [m["value"] for m in paged] == [100, 101, 102, 103, 104]
    # spec yolu da sayfalar
    flat, _ = _walk(client, f"{V1}/measurements?patient_id={pid}", h, 4)
    assert flat == full


def test_alert_pagination_desc_and_ties(client, patient, db):
    pid, h = _pid(patient), patient[1]
    same = datetime.now(UTC) - timedelta(hours=1)
    for i in range(5):  # aynı zaman damgası: sıralama id ile tekilleşir
        db.add(Alert(patient_id=pid, severity=Severity.info, kind="test", message=f"u{i}", created_at=same))
    db.commit()
    paged, pages = _walk(client, f"{V1}/patients/{pid}/alerts", h, 2)
    assert pages == 3 and [a["message"] for a in paged] == ["u4", "u3", "u2", "u1", "u0"]


def test_coach_history_cursor(client, patient, db):
    pid, h = _pid(patient), patient[1]
    t0 = datetime.now(UTC) - timedelta(hours=1)
    for i in range(5):
        db.add(CoachMessage(patient_id=pid, sender=Sender.patient, content=f"m{i}", created_at=t0 + timedelta(minutes=i)))
    db.commit()
    r = client.get(f"{V1}/coach/history?limit=2", headers=h)
    assert [m["content"] for m in r.json()] == ["m3", "m4"]
    r = client.get(f"{V1}/coach/history?limit=2&cursor={r.headers['x-next-cursor']}", headers=h)
    assert [m["content"] for m in r.json()] == ["m1", "m2"]
    r = client.get(f"{V1}/coach/history?limit=2&cursor={r.headers['x-next-cursor']}", headers=h)
    assert [m["content"] for m in r.json()] == ["m0"] and "x-next-cursor" not in r.headers


def test_dose_pagination_and_bad_cursor(client, patient):
    pid, h = _pid(patient), patient[1]
    assert client.get(f"{V1}/patients/{pid}/doses?limit=2", headers=h).json() == []
    for bad in ("bozuk!", "W10", "eyJhIjoxfQ"):
        r = client.get(f"{V1}/patients/{pid}/doses?limit=2&cursor={bad}", headers=h)
        assert r.status_code == 400 and r.json()["detail"] == "Geçersiz cursor"
    assert client.get(f"{V1}/patients/{pid}/doses?limit=0", headers=h).status_code == 422
    assert client.get(f"{V1}/patients/{pid}/doses?limit=500", headers=h).status_code == 422


# ---------------- sağlık ----------------
def test_health_live_and_legacy(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health").json() == {"status": "ok"}


def test_health_ready_reports_missing_migrations(client):
    r = client.get("/health/ready")  # test veritabanı create_all ile kuruldu: alembic_version yok
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable",
                        "checks": {"database": "ok", "redis": "disabled", "migrations": "missing"}}


def test_health_ready_ok_at_head(client, db):
    db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    for head in health.alembic_heads():
        db.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})
    db.commit()
    try:
        r = client.get("/health/ready")
        assert r.status_code == 200 and r.json()["checks"]["migrations"] == "ok"
        db.execute(text("UPDATE alembic_version SET version_num = '0001'"))
        db.commit()
        assert client.get("/health/ready").json()["checks"]["migrations"] == "pending"
    finally:
        db.execute(text("DROP TABLE alembic_version"))
        db.commit()


def test_health_ready_migration_check_optional(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "health_check_migrations", False)
    r = client.get("/health/ready")
    assert r.status_code == 200 and "migrations" not in r.json()["checks"]


def test_health_ready_redis_down(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "health_check_migrations", False)
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")

    class Down:
        def ping(self):
            raise ConnectionError

    reset_redis(Down())
    try:
        r = client.get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"]["redis"] == "error"
    finally:
        reset_redis(None)


def test_alembic_single_head():
    assert len(health.alembic_heads()) == 1  # migration zinciri dallanmamış


def test_metrics_route_label_keeps_version(client, patient):
    _, h = patient
    client.get(f"{V1}/auth/me", headers=h)
    client.get("/auth/me", headers=h)
    text_ = client.get("/metrics").text
    assert 'route="/v1/auth/me"' in text_ and 'route="/auth/me"' in text_
