"""Oturum güvenliği (istek sınırı, refresh rotation, çıkış/iptal) ve KVKK erişim kaydı testleri."""

from datetime import timedelta

from sqlalchemy import func, select

from app.models import AccessLog, RefreshToken
from app.security import create_access_token, decode_token
from tests.conftest import register

PW = "sifre1234"


def _login(client, email, headers=None):
    return client.post("/auth/login", data={"username": email, "password": PW}, headers=headers or {})


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# --- İstek sınırı ---


def test_login_rate_limit_per_ip(client, patient):
    for _ in range(4):  # register() bir giriş yaptı; toplam 5 serbest
        assert _login(client, "ayse@example.com").status_code == 200
    r = _login(client, "ayse@example.com")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_login_rate_limit_counts_failed_attempts(client):
    for _ in range(5):
        r = client.post("/auth/login", data={"username": "yok@example.com", "password": "yanlis123"})
        assert r.status_code == 401
    assert client.post("/auth/login", data={"username": "yok@example.com", "password": "x"}).status_code == 429


def test_login_rate_limit_per_email_across_ips(client, patient, monkeypatch):
    # TestClient'ın karşı ucu "testclient"; X-Forwarded-For'a güvenmesi için güvenilen proxy sayılır
    monkeypatch.setattr("app.routers.auth._is_trusted_proxy", lambda host: True)
    for i in range(9):  # register() 1 giriş yaptı; e-posta sınırı 10
        assert _login(client, "ayse@example.com", {"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 200
    r = _login(client, "ayse@example.com", {"X-Forwarded-For": "10.0.1.1"})
    assert r.status_code == 429


def test_xff_ignored_from_untrusted_peer(client, patient):
    # Karşı uç güvenilmiyorsa sahte X-Forwarded-For ile IP sınırı atlatılamaz
    for i in range(4):
        assert _login(client, "ayse@example.com", {"X-Forwarded-For": f"10.0.0.{i}"}).status_code == 200
    assert _login(client, "ayse@example.com", {"X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_register_rate_limit_per_ip(client):
    for i in range(5):
        r = client.post("/auth/register", json={
            "email": f"k{i}@example.com", "password": PW, "full_name": "Kişi Bir", "role": "caregiver",
        })
        assert r.status_code == 201
    r = client.post("/auth/register", json={
        "email": "k9@example.com", "password": PW, "full_name": "Kişi Bir", "role": "caregiver",
    })
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_client_ip_uses_rightmost_xff_from_trusted_proxy():
    from starlette.requests import Request

    from app.routers.auth import client_ip

    def req(peer, xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "client": (peer, 1234), "headers": headers})

    assert client_ip(req("127.0.0.1", "1.1.1.1, 2.2.2.2")) == "2.2.2.2"
    assert client_ip(req("127.0.0.1", "bozuk")) == "127.0.0.1"
    assert client_ip(req("8.8.8.8", "2.2.2.2")) == "8.8.8.8"


# --- Token'lar ---


def test_login_returns_short_lived_access_with_jti_and_refresh(client, patient):
    body = _login(client, "ayse@example.com").json()
    assert body["token_type"] == "bearer" and body["role"] == "patient" and body["refresh_token"]
    payload = decode_token(body["access_token"])
    assert payload["jti"] and payload["typ"] == "access"
    assert payload["exp"] - payload["iat"] == 15 * 60 == body["expires_in"]
    assert body["refresh_expires_in"] == 14 * 86400


def test_refresh_token_stored_hashed(client, db, patient):
    raw = _login(client, "ayse@example.com").json()["refresh_token"]
    hashes = db.scalars(select(RefreshToken.token_hash)).all()
    assert raw not in hashes and all(len(h) == 64 for h in hashes)


def test_refresh_rotation(client, db, patient):
    first = _login(client, "ayse@example.com").json()
    r = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/auth/me", headers=_bearer(second["access_token"])).status_code == 200
    # Yeni token da bir kez kullanılabilir
    third = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert third.status_code == 200
    old = db.scalar(select(RefreshToken).where(RefreshToken.replaced_by.is_not(None)).order_by(RefreshToken.id))
    assert old.revoked_at is not None


def test_refresh_reuse_revokes_all_user_tokens(client, db, patient):
    first = _login(client, "ayse@example.com").json()
    other_session = _login(client, "ayse@example.com").json()  # başka cihaz
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    # Eski (döndürülmüş) token tekrar gelir -> çalınma varsayımı
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    active = db.scalar(select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None)))
    assert active == 0
    # Hem yeni zincir hem diğer cihaz artık yenileyemez
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": other_session["refresh_token"]}).status_code == 401


def test_refresh_unknown_or_expired(client, db, patient):
    assert client.post("/auth/refresh", json={"refresh_token": "uydurma"}).status_code == 401
    raw = _login(client, "ayse@example.com").json()["refresh_token"]
    rt = db.scalars(select(RefreshToken).order_by(RefreshToken.id.desc())).first()
    rt.expires_at = rt.created_at - timedelta(seconds=1)
    db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": raw}).status_code == 401


def test_logout_denylists_access_and_revokes_refresh(client, db, patient):
    pair = _login(client, "ayse@example.com").json()
    h = _bearer(pair["access_token"])
    assert client.get("/auth/me", headers=h).status_code == 200
    r = client.post("/auth/logout", headers=h, json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 204
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    # Diğer oturumun erişim token'ı etkilenmez
    _, other_h = patient
    assert client.get("/auth/me", headers=other_h).status_code == 200


def test_logout_without_body(client, patient):
    _, h = patient
    assert client.post("/auth/logout", headers=h).status_code == 204
    assert client.get("/auth/me", headers=h).status_code == 401


def test_logout_cannot_revoke_other_users_refresh(client, db, patient):
    register(client, "ali@example.com")
    ali = _login(client, "ali@example.com").json()
    _, h = patient
    client.post("/auth/logout", headers=h, json={"refresh_token": ali["refresh_token"]})
    assert client.post("/auth/refresh", json={"refresh_token": ali["refresh_token"]}).status_code == 200


def test_denylist_uses_redis_when_available(client, patient):
    from app.redis_client import reset_redis

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def set(self, key, value, ex=None):
            assert ex and ex > 0
            self.store[key] = value

        def exists(self, key):
            return int(key in self.store)

        # check_rate_limit için
        def pipeline(self):
            raise RuntimeError("yok")

    fake = FakeRedis()
    reset_redis(fake)
    tok = create_access_token(patient[0]["id"], "patient")
    assert client.post("/auth/logout", headers=_bearer(tok)).status_code == 204
    jti = decode_token(tok)["jti"]
    assert f"jwt:deny:{jti}" in fake.store


def test_expired_access_token_rejected(client, patient):
    tok = create_access_token(patient[0]["id"], "patient", expires_delta=timedelta(seconds=-5))
    assert client.get("/auth/me", headers=_bearer(tok)).status_code == 401


def test_token_without_jti_rejected(client, patient):
    import jwt as pyjwt

    from app.config import get_settings

    s = get_settings()
    tok = pyjwt.encode({"sub": str(patient[0]["id"]), "exp": 9999999999}, s.jwt_secret, algorithm=s.jwt_algorithm)
    assert client.get("/auth/me", headers=_bearer(tok)).status_code == 401


# --- KVKK erişim kaydı ---


def _logs(db):
    db.expire_all()
    return db.scalars(select(AccessLog).order_by(AccessLog.id)).all()


def _assign(client, patient_h):
    assert client.put("/patients/me/clinician", headers=patient_h,
                      json={"clinician_email": "dr.mehmet@example.com"}).status_code == 200


def test_clinician_read_creates_one_log_per_request(client, db, patient, clinician):
    (p, ph), (c, ch) = patient, clinician
    _assign(client, ph)
    pid = p["patient_id"]
    client.post(f"/patients/{pid}/measurements", headers=ph,
                json={"type": "glucose_fasting", "value": 110})
    assert _logs(db) == []

    assert client.get(f"/patients/{pid}/measurements", headers=ch).status_code == 200
    logs = _logs(db)
    assert len(logs) == 1
    log = logs[0]
    assert (log.actor_user_id, log.actor_role, log.patient_id, log.action) == (c["id"], "clinician", pid, "read")
    assert log.path == f"/patients/{pid}/measurements"

    client.get(f"/patients/{pid}/measurements", headers=ch)
    assert len(_logs(db)) == 2  # her istek ayrı satır


def test_double_read_in_one_request_logged_once(client, db, patient, clinician, monkeypatch):
    (p, ph), (_, ch) = patient, clinician
    _assign(client, ph)
    pid = p["patient_id"]
    import app.routers.patients as patients_router

    original = patients_router.get_patient_for_read

    def twice(db_, user, patient_id, **kw):
        original(db_, user, patient_id, **kw)
        return original(db_, user, patient_id, **kw)

    monkeypatch.setattr(patients_router, "get_patient_for_read", twice)
    assert client.get(f"/patients/{pid}", headers=ch).status_code == 200
    assert len(_logs(db)) == 1


def test_patient_own_reads_not_logged(client, db, patient):
    p, ph = patient
    pid = p["patient_id"]
    for path in (f"/patients/{pid}", f"/patients/{pid}/measurements", f"/patients/{pid}/adherence",
                 f"/summaries/{pid}"):
        assert client.get(path, headers=ph).status_code == 200
    assert _logs(db) == []


def test_unauthorized_read_not_logged(client, db, patient, clinician):
    p, _ = patient
    _, ch = clinician  # atanmamış hekim
    assert client.get(f"/patients/{p['patient_id']}/measurements", headers=ch).status_code == 404
    assert _logs(db) == []


def test_caregiver_read_logged(client, db, patient):
    p, ph = patient
    cg, cgh = register(client, "kizi@example.com", role="caregiver")
    client.post("/patients/me/caregivers", headers=ph, json={"caregiver_email": "kizi@example.com"})
    assert client.get(f"/patients/{p['patient_id']}/medications", headers=cgh).status_code == 200
    logs = _logs(db)
    assert len(logs) == 1 and logs[0].actor_role == "caregiver" and logs[0].actor_user_id == cg["id"]


def test_clinician_write_attempt_not_logged_as_read(client, db, patient, clinician):
    (p, ph), (_, ch) = patient, clinician
    _assign(client, ph)
    r = client.post(f"/patients/{p['patient_id']}/measurements", headers=ch,
                    json={"type": "glucose_fasting", "value": 110})
    assert r.status_code == 403
    assert _logs(db) == []


def test_patient_lists_own_access_log(client, db, patient, clinician):
    (p, ph), (_, ch) = patient, clinician
    _assign(client, ph)
    pid = p["patient_id"]
    client.get(f"/patients/{pid}/measurements", headers=ch)
    client.get(f"/patients/{pid}/doses", headers=ch)

    r = client.get("/patients/me/access-log?days=30", headers=ph)
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["path"] for i in items] == [f"/patients/{pid}/doses", f"/patients/{pid}/measurements"]
    assert items[0]["actor_name"] == "Dr.Mehmet" and items[0]["actor_role"] == "clinician"
    assert set(items[0]) == {"id", "actor_name", "actor_role", "action", "path", "created_at"}  # sağlık verisi yok
    # Listeyi okumak yeni kayıt üretmez
    assert len(_logs(db)) == 2


def test_access_log_only_for_patients(client, patient, clinician):
    _, ch = clinician
    assert client.get("/patients/me/access-log", headers=ch).status_code == 403


def test_access_log_isolated_between_patients(client, db, patient, clinician):
    (p, ph), (_, ch) = patient, clinician
    _assign(client, ph)
    client.get(f"/patients/{p['patient_id']}", headers=ch)
    _, other_h = register(client, "ali@example.com")
    assert client.get("/patients/me/access-log", headers=other_h).json() == []
