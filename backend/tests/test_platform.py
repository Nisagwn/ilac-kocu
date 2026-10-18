"""API platform katmanı: /v1 sürümleme, spec yolları + yetki, token bucket istek sınırı, Idempotency-Key,
ETag/304, cursor sayfalama ve sağlık uçları."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import CoachMessage, IdempotencyKey, Measurement, MedicationSchedule
from app.pagination import encode_cursor
from app.platform import purge_expired_idempotency_keys, request_hash
from app.redis_client import reset_redis
from app.routers.doses import local_today
from app.routers.health import alembic_heads
from app.services import ratelimit
from tests.conftest import register
from tests.fake_redis import FakeRedis

METFORMIN = {"name": "Glifor", "active_ingredient": "metformin", "dose_text": "1000 mg",
             "schedules": [{"time_of_day": "08:00"}]}
GLU = {"type": "glucose_fasting", "value": 110}


def _pid(patient) -> int:
    return patient[0]["patient_id"]


def _add_measurements(client, patient, n: int) -> None:
    h = patient[1]
    base = datetime.now(UTC) - timedelta(hours=n + 1)
    for i in range(n):
        body = {**GLU, "value": 100 + i, "measured_at": (base + timedelta(hours=i)).isoformat()}
        assert client.post(f"/v1/patients/{_pid(patient)}/measurements", headers=h, json=body).status_code == 201


@pytest.fixture()
def clock(monkeypatch):
    """İstek sınırı için elle ilerletilen saat."""
    state = {"t": 1000.0}
    monkeypatch.setattr(ratelimit, "_now", lambda: state["t"])
    return state


@pytest.fixture()
def limits(monkeypatch):
    """Genel istek sınırını küçük değerlerle açar (conftest'te kapalı)."""
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "rate_limit_read_per_minute", 3)
    monkeypatch.setattr(s, "rate_limit_write_per_minute", 2)
    monkeypatch.setattr(s, "rate_limit_coach_per_minute", 2)
    return s


# ---------------------------------------------------------------- /v1 sürümleme
def test_v1_and_legacy_paths_both_work(client, patient):
    _, h = patient
    v1 = client.get("/v1/auth/me", headers=h)
    legacy = client.get("/auth/me", headers=h)
    assert v1.status_code == legacy.status_code == 200
    assert v1.json() == legacy.json()
    assert "deprecation" not in v1.headers
    assert legacy.headers["Deprecation"] == "true"
    assert legacy.headers["Link"] == '</v1/auth/me>; rel="successor-version"'
    assert "deprecation" not in client.get("/health").headers


def test_v1_login_and_patient_paths(client, patient):
    r = client.post("/v1/auth/login", data={"username": "ayse@example.com", "password": "sifre1234"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"/v1/patients/{_pid(patient)}/medications", headers=h).status_code == 200


def test_openapi_lists_only_v1_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/patients/{patient_id}/medications" in paths
    assert "/v1/measurements" in paths and "/v1/doses" in paths and "/v1/medications" in paths
    assert "/patients/{patient_id}/medications" not in paths
    assert "/coach/chat" not in paths and "/v1/coach/chat" in paths
    assert "/health/ready" in paths


# ---------------------------------------------------------------- spec yolları + yetki
def test_spec_paths_patient_defaults_to_self(client, patient):
    _, h = patient
    client.post(f"/v1/patients/{_pid(patient)}/medications", headers=h, json=METFORMIN)
    _add_measurements(client, patient, 2)
    meds = client.get("/v1/medications", headers=h)
    assert meds.status_code == 200 and meds.json()[0]["name"] == "Glifor"
    page = client.get("/v1/measurements", headers=h).json()
    assert len(page["items"]) == 2 and page["next_cursor"] is None
    assert client.get(f"/v1/measurements?patient_id={_pid(patient)}", headers=h).status_code == 200
    assert client.get("/v1/doses", headers=h).json() == {"items": [], "next_cursor": None}


def test_spec_paths_authorization_404(client, patient, clinician):
    _, h = patient
    other, oh = register(client, "ali@example.com")
    _, dh = clinician
    for path in ("/v1/medications", "/v1/doses", "/v1/measurements"):
        # başka hastanın verisi: 404 (varlık sızdırılmaz)
        assert client.get(f"{path}?patient_id={other['patient_id']}", headers=h).status_code == 404
        # hekim/bakım veren için patient_id zorunlu
        assert client.get(path, headers=dh).status_code == 422
        # atanmamış hekim: 404
        assert client.get(f"{path}?patient_id={_pid(patient)}", headers=dh).status_code == 404
    assert client.put("/v1/patients/me/clinician", headers=h,
                      json={"clinician_email": "dr.mehmet@example.com"}).status_code == 200
    for path in ("/v1/medications", "/v1/doses", "/v1/measurements"):
        assert client.get(f"{path}?patient_id={_pid(patient)}", headers=dh).status_code == 200
        assert client.get(f"{path}?patient_id={other['patient_id']}", headers=dh).status_code == 404


def test_spec_paths_caregiver_linked_read(client, patient):
    _, h = patient
    _, ch = register(client, "kizi@example.com", role="caregiver")
    assert client.get(f"/v1/measurements?patient_id={_pid(patient)}", headers=ch).status_code == 404
    client.post("/v1/patients/me/caregivers", headers=h, json={"caregiver_email": "kizi@example.com"})
    _add_measurements(client, patient, 1)
    r = client.get(f"/v1/measurements?patient_id={_pid(patient)}", headers=ch)
    assert r.status_code == 200 and len(r.json()["items"]) == 1


def test_spec_measurements_filters(client, patient):
    _, h = patient
    _add_measurements(client, patient, 3)
    client.post(f"/v1/patients/{_pid(patient)}/measurements", headers=h,
                json={"type": "blood_pressure", "value": 120, "value2": 80})
    assert len(client.get("/v1/measurements?type=blood_pressure", headers=h).json()["items"]) == 1
    since = (datetime.now(UTC) - timedelta(hours=3, minutes=30)).isoformat()
    r = client.get("/v1/measurements", headers=h, params={"type": "glucose_fasting", "from": since})
    assert [m["value"] for m in r.json()["items"]] == [102, 101]
    future = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    assert client.get("/v1/measurements", headers=h, params={"from": future}).json()["items"] == []


def test_spec_doses_date_range(client, patient, db):
    _, h = patient
    pid = _pid(patient)
    med = client.post(f"/v1/patients/{pid}/medications", headers=h, json=METFORMIN).json()
    today = local_today()
    for s in db.query(MedicationSchedule).all():
        s.start_date = today - timedelta(days=10)
    db.commit()
    sid = med["schedules"][0]["id"]
    for i in range(1, 5):
        body = {"schedule_id": sid, "scheduled_date": str(today - timedelta(days=i)), "status": "taken"}
        assert client.put(f"/v1/patients/{pid}/doses", headers=h, json=body).status_code == 200
    r = client.get("/v1/doses", headers=h, params={"from": str(today - timedelta(days=3)),
                                                   "to": str(today - timedelta(days=2))})
    assert [d["scheduled_date"] for d in r.json()["items"]] == [str(today - timedelta(days=2)),
                                                               str(today - timedelta(days=3))]


# ---------------------------------------------------------------- token bucket
def test_token_bucket_burst_then_refill(clock):
    for i in range(5):
        res = ratelimit.hit("t:bucket", 5, 60)
        assert res.allowed and res.remaining == 4 - i
    res = ratelimit.hit("t:bucket", 5, 60)
    assert not res.allowed and res.remaining == 0 and res.retry_after == 12  # 5 jeton/60 sn → 12 sn'de bir
    assert res.headers()["Retry-After"] == "12"
    clock["t"] += 12  # tam bir jeton dolar
    assert ratelimit.hit("t:bucket", 5, 60).allowed
    assert not ratelimit.hit("t:bucket", 5, 60).allowed
    clock["t"] += 600  # kova kapasiteyi aşmaz
    assert sum(ratelimit.hit("t:bucket", 5, 60).allowed for _ in range(7)) == 5


def test_token_bucket_keys_are_independent(clock):
    assert ratelimit.hit("t:a", 1, 60).allowed
    assert not ratelimit.hit("t:a", 1, 60).allowed
    assert ratelimit.hit("t:b", 1, 60).allowed


def test_token_bucket_is_thread_safe(clock):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: ratelimit.hit("t:threads", 50, 60).allowed, range(200)))
    assert sum(results) == 50


def test_token_bucket_uses_redis_lua_script():
    calls = []

    class Script:
        def __call__(self, keys, args):
            calls.append((keys, args))
            return [1, "3.5", "0"]  # Lua betiği ondalıkları metin döndürür

    class LuaRedis:
        def register_script(self, source):
            assert "HMGET" in source and "TIME" in source
            return Script()

    reset_redis(LuaRedis())
    res = ratelimit.hit("user:7:read", 5, 60)
    assert res.allowed and res.remaining == 3
    keys, args = calls[0]
    assert keys == ["rl:user:7:read"] and args[0] == 5 and args[1] == pytest.approx(5 / 60) and args[2] == 1


def test_token_bucket_falls_back_when_redis_fails(clock):
    class Broken:
        def register_script(self, source):
            raise ConnectionError("yok")

    reset_redis(Broken())
    assert ratelimit.hit("t:fallback", 1, 60).allowed
    assert not ratelimit.hit("t:fallback", 1, 60).allowed


# ---------------------------------------------------------------- kullanıcı başına genel sınır
def test_read_limit_headers_and_429(client, patient, limits, clock):
    _, h = patient
    for remaining in (2, 1, 0):
        r = client.get("/v1/auth/me", headers=h)
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "3" and r.headers["X-RateLimit-Remaining"] == str(remaining)
    r = client.get("/v1/auth/me", headers=h)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.headers["X-RateLimit-Remaining"] == "0" and "detail" in r.json()
    clock["t"] += 20  # 3 jeton/60 sn → 20 sn'de bir jeton
    assert client.get("/v1/auth/me", headers=h).status_code == 200


def test_limits_are_per_user_and_skip_anonymous(client, patient, limits, clock):
    _, h = patient
    _, h2 = register(client, "ali@example.com")
    for _ in range(3):
        client.get("/v1/auth/me", headers=h)
    assert client.get("/v1/auth/me", headers=h).status_code == 429
    assert client.get("/v1/auth/me", headers=h2).status_code == 200
    for _ in range(5):  # kimliksiz / sistem uçları sayılmaz
        assert client.get("/health").status_code == 200


def test_write_limit_separate_from_read(client, patient, limits, clock):
    _, h = patient
    pid = _pid(patient)
    for _ in range(2):
        assert client.post(f"/v1/patients/{pid}/measurements", headers=h, json=GLU).status_code == 201
    assert client.post(f"/v1/patients/{pid}/measurements", headers=h, json=GLU).status_code == 429
    assert client.get("/v1/auth/me", headers=h).status_code == 200


def test_coach_limit_never_blocks_emergency(client, patient, limits, clock):
    _, h = patient
    for i in range(2):
        r = client.post("/v1/coach/chat", headers=h, json={"message": f"Merhaba {i}"})
        assert r.status_code == 200 and r.headers["X-RateLimit-Limit"] == "2"
    assert client.post("/v1/coach/chat", headers=h, json={"message": "Merhaba yine"}).status_code == 429
    r = client.post("/v1/coach/chat", headers=h, json={"message": "Göğsümde baskı var"})
    assert r.status_code == 200 and "112" in r.json()["message"]


def test_rate_limit_disabled_by_default_in_tests(client, patient):
    _, h = patient
    r = client.get("/v1/auth/me", headers=h)
    assert "x-ratelimit-limit" not in r.headers


# ---------------------------------------------------------------- Idempotency-Key
def test_idempotent_measurement_replay(client, patient, db):
    _, h = patient
    pid = _pid(patient)
    hk = {**h, "Idempotency-Key": "olcum-1"}
    first = client.post(f"/v1/patients/{pid}/measurements", headers=hk, json=GLU)
    second = client.post(f"/v1/patients/{pid}/measurements", headers=hk, json=GLU)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true" and "idempotent-replayed" not in first.headers
    assert db.scalar(select(func.count()).select_from(Measurement)) == 1
    # anahtarsız istek normal işlenir
    assert client.post(f"/v1/patients/{pid}/measurements", headers=h, json=GLU).status_code == 201
    assert db.scalar(select(func.count()).select_from(Measurement)) == 2


def test_idempotency_key_reused_with_different_body_is_422(client, patient):
    _, h = patient
    pid = _pid(patient)
    hk = {**h, "Idempotency-Key": "olcum-2"}
    assert client.post(f"/v1/patients/{pid}/measurements", headers=hk, json=GLU).status_code == 201
    r = client.post(f"/v1/patients/{pid}/measurements", headers=hk, json={**GLU, "value": 120})
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["detail"]


def test_idempotency_keys_are_scoped_per_user(client, patient):
    other, oh = register(client, "ali@example.com")
    r1 = client.post(f"/v1/patients/{_pid(patient)}/measurements",
                     headers={**patient[1], "Idempotency-Key": "ortak"}, json=GLU)
    r2 = client.post(f"/v1/patients/{other['patient_id']}/measurements",
                     headers={**oh, "Idempotency-Key": "ortak"}, json=GLU)
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] != r2.json()["id"]
    assert "idempotent-replayed" not in r2.headers


def test_idempotency_failed_request_not_stored(client, patient):
    _, h = patient
    pid = _pid(patient)
    hk = {**h, "Idempotency-Key": "olcum-3"}
    bad = {"type": "blood_pressure", "value": 130}  # diyastolik eksik → 422
    assert client.post(f"/v1/patients/{pid}/measurements", headers=hk, json=bad).status_code == 422
    # 2xx olmayan yanıt saklanmaz: aynı anahtar düzeltilmiş gövdeyle kullanılabilir
    assert client.post(f"/v1/patients/{pid}/measurements", headers=hk, json=GLU).status_code == 201


def test_idempotency_concurrent_in_progress_is_409(client, patient, db):
    user, h = patient
    path = f"/v1/patients/{_pid(patient)}/measurements"
    body = json.dumps(GLU).encode()
    db.add(IdempotencyKey(user_id=user["id"], key="islemde", request_hash=request_hash("POST", path, body)))
    db.commit()
    r = client.post(path, headers={**h, "Idempotency-Key": "islemde", "Content-Type": "application/json"},
                    content=body)
    assert r.status_code == 409 and r.headers["Retry-After"] == "1"


def test_idempotency_dose_put_and_post(client, patient, db):
    _, h = patient
    pid = _pid(patient)
    sid = client.post(f"/v1/patients/{pid}/medications", headers=h, json=METFORMIN).json()["schedules"][0]["id"]
    body = {"schedule_id": sid, "scheduled_date": str(local_today()), "status": "taken"}
    hk = {**h, "Idempotency-Key": "doz-1"}
    a = client.put(f"/v1/patients/{pid}/doses", headers=hk, json=body)
    b = client.put(f"/v1/patients/{pid}/doses", headers=hk, json=body)
    assert a.status_code == 200 and b.json() == a.json() and b.headers["Idempotent-Replayed"] == "true"
    # POST eşdeğeri (spec: "POST doz kaydı"); farklı yöntem = farklı istek
    assert client.post(f"/v1/patients/{pid}/doses", headers=hk, json=body).status_code == 422
    c = client.post(f"/v1/patients/{pid}/doses", headers={**h, "Idempotency-Key": "doz-2"},
                    json={**body, "status": "skipped"})
    assert c.status_code == 200 and c.json()["status"] == "skipped"


def test_idempotency_coach_message(client, patient, db):
    _, h = patient
    hk = {**h, "Idempotency-Key": "koc-1"}
    a = client.post("/v1/coach/chat", headers=hk, json={"message": "Bugün yürüyüş yaptım"})
    b = client.post("/v1/coach/chat", headers=hk, json={"message": "Bugün yürüyüş yaptım"})
    assert a.status_code == 200 and b.json() == a.json() and b.headers["Idempotent-Replayed"] == "true"
    assert db.scalar(select(func.count()).select_from(CoachMessage)) == 2  # hasta + koç, bir kez


def test_idempotency_invalid_key_is_400(client, patient):
    _, h = patient
    r = client.post(f"/v1/patients/{_pid(patient)}/measurements", headers={**h, "Idempotency-Key": "x" * 300},
                    json=GLU)
    assert r.status_code == 400


def test_purge_expired_idempotency_keys(client, patient, db):
    user, _ = patient
    old = datetime.now(UTC) - timedelta(hours=25)
    db.add_all([
        IdempotencyKey(user_id=user["id"], key="eski", request_hash="a" * 64, response_status=201, created_at=old),
        IdempotencyKey(user_id=user["id"], key="yeni", request_hash="b" * 64, response_status=201),
    ])
    db.commit()
    assert purge_expired_idempotency_keys(db) == 1
    assert db.scalars(select(IdempotencyKey.key)).all() == ["yeni"]
    assert purge_expired_idempotency_keys(db, older_than=timedelta(0)) == 1


def test_expired_key_can_be_reused(client, patient, db):
    user, h = patient
    path = f"/v1/patients/{_pid(patient)}/measurements"
    db.add(IdempotencyKey(user_id=user["id"], key="bayat", request_hash="c" * 64, response_status=201,
                          response_body={}, created_at=datetime.now(UTC) - timedelta(hours=30)))
    db.commit()
    r = client.post(path, headers={**h, "Idempotency-Key": "bayat"}, json=GLU)
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers


# ---------------------------------------------------------------- ETag / 304
def test_etag_and_304(client, patient):
    _, h = patient
    path = f"/v1/patients/{_pid(patient)}/medications"
    r = client.get(path, headers=h)
    etag = r.headers["ETag"]
    assert etag.startswith('W/"') and r.headers["Cache-Control"] == "private, no-cache"
    assert "Authorization" in r.headers["Vary"]

    cached = client.get(path, headers={**h, "If-None-Match": etag})
    assert cached.status_code == 304 and cached.content == b"" and cached.headers["ETag"] == etag
    # zayıf karşılaştırma ve liste biçimi
    strong = etag.removeprefix("W/")
    assert client.get(path, headers={**h, "If-None-Match": f'"baska", {strong}'}).status_code == 304

    client.post(path, headers=h, json=METFORMIN)
    changed = client.get(path, headers={**h, "If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["ETag"] != etag and len(changed.json()) == 1


def test_etag_not_applied_to_errors_or_writes(client, patient):
    _, h = patient
    assert "etag" not in client.get("/v1/patients/99999/medications", headers=h).headers
    assert "etag" not in client.post(f"/v1/patients/{_pid(patient)}/measurements", headers=h, json=GLU).headers


# ---------------------------------------------------------------- cursor sayfalama
def test_cursor_pagination_new_endpoint(client, patient):
    _, h = patient
    _add_measurements(client, patient, 5)
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/v1/measurements", headers=h, params=params).json()
        seen += [m["value"] for m in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [104, 103, 102, 101, 100] and pages == 3  # yeniden eskiye, tekrarsız


def test_cursor_pagination_ties_broken_by_id(client, patient):
    _, h = patient
    same = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    for v in (100, 101, 102):
        client.post(f"/v1/patients/{_pid(patient)}/measurements", headers=h, json={**GLU, "value": v, "measured_at": same})
    p1 = client.get("/v1/measurements?limit=2", headers=h).json()
    p2 = client.get("/v1/measurements", headers=h, params={"limit": 2, "cursor": p1["next_cursor"]}).json()
    assert [m["value"] for m in p1["items"] + p2["items"]] == [102, 101, 100] and p2["next_cursor"] is None


def test_cursor_pagination_legacy_headers(client, patient):
    _, h = patient
    pid = _pid(patient)
    _add_measurements(client, patient, 3)
    # parametresiz: eski davranış (eskiden yeniye, başlık yok)
    full = client.get(f"/v1/patients/{pid}/measurements", headers=h)
    assert [m["value"] for m in full.json()] == [100, 101, 102] and "x-next-cursor" not in full.headers
    r = client.get(f"/v1/patients/{pid}/measurements?limit=2", headers=h)
    assert [m["value"] for m in r.json()] == [102, 101]
    nxt = r.headers["X-Next-Cursor"]
    assert 'rel="next"' in r.headers["Link"] and f"cursor={nxt}" in r.headers["Link"]
    r2 = client.get(f"/v1/patients/{pid}/measurements", headers=h, params={"limit": 2, "cursor": nxt})
    assert [m["value"] for m in r2.json()] == [100] and "x-next-cursor" not in r2.headers


def test_cursor_pagination_alerts_and_coach_history(client, patient):
    _, h = patient
    pid = _pid(patient)
    for v in (30, 35, 40):  # her biri hipoglisemi uyarısı üretir
        client.post(f"/v1/patients/{pid}/measurements", headers=h, json={**GLU, "value": v})
    r = client.get(f"/v1/patients/{pid}/alerts?limit=2", headers=h)
    assert len(r.json()) == 2 and "X-Next-Cursor" in r.headers
    rest = client.get(f"/v1/patients/{pid}/alerts", headers=h, params={"limit": 2, "cursor": r.headers["X-Next-Cursor"]})
    assert len(rest.json()) == 1
    assert {a["id"] for a in r.json()}.isdisjoint({a["id"] for a in rest.json()})

    for i in range(2):
        client.post("/v1/coach/chat", headers=h, json={"message": f"Merhaba {i}"})
    hist = client.get("/v1/coach/history?limit=3", headers=h)
    assert len(hist.json()) == 3 and "X-Next-Cursor" in hist.headers
    older = client.get("/v1/coach/history", headers=h, params={"limit": 3, "cursor": hist.headers["X-Next-Cursor"]})
    assert len(older.json()) == 1 and older.json()[0]["sender"] == "patient"


def test_invalid_cursor_is_422(client, patient):
    _, h = patient
    assert client.get("/v1/measurements?cursor=bozuk!!", headers=h).status_code == 422
    assert client.get(f"/v1/patients/{_pid(patient)}/doses?cursor=e30", headers=h).status_code == 422
    assert client.get("/v1/measurements", headers=h, params={"cursor": encode_cursor(datetime.now(UTC), 1)}).status_code == 200


# ---------------------------------------------------------------- sağlık
def test_health_live(client):
    for path in ("/health", "/health/live"):
        r = client.get(path)
        assert r.status_code == 200 and r.json() == {"status": "ok"}


def _stamp_head(db):
    db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(text("DELETE FROM alembic_version"))
    for head in alembic_heads():
        db.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})
    db.commit()


def test_health_ready_ok_and_migration_mismatch(client, db):
    r = client.get("/health/ready")  # create_all ile kurulan test DB'sinde alembic_version yok
    assert r.status_code == 503 and r.json()["checks"]["migrations"]["ok"] is False
    assert r.json()["checks"]["database"]["ok"] is True

    _stamp_head(db)
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ok" and "redis" not in r.json()["checks"]

    db.execute(text("UPDATE alembic_version SET version_num = '0001_initial_schema'"))
    db.commit()
    body = client.get("/health/ready").json()
    assert body["checks"]["migrations"]["error"] == "not_at_head"
    db.execute(text("DROP TABLE alembic_version"))
    db.commit()


def test_health_ready_checks_redis_when_configured(client, db, monkeypatch):
    _stamp_head(db)
    monkeypatch.setattr(get_settings(), "redis_url", "redis://test")
    reset_redis(FakeRedis())
    assert client.get("/health/ready").json()["checks"]["redis"] == {"ok": True}

    class DeadRedis(FakeRedis):
        def ping(self):
            raise ConnectionError("kapalı")

    reset_redis(DeadRedis())
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["checks"]["redis"] == {"ok": False, "error": "ConnectionError"}
    db.execute(text("DROP TABLE alembic_version"))
    db.commit()


def test_health_ready_database_down(client):
    class BrokenSession:
        def execute(self, *a, **kw):
            raise ConnectionRefusedError("db yok")

        def rollback(self):
            pass

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    r = client.get("/health/ready")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["database"] == {"ok": False, "error": "ConnectionRefusedError"}
    assert checks["migrations"]["ok"] is False
