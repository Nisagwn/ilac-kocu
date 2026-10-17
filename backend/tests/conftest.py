import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-32b")
os.environ.setdefault("REMINDERS_ENABLED", "false")  # arka plan zamanlayıcısı testlerde çalışmaz
# Testler asla gerçek LLM'e gitmez: .env'de anahtar olsa bile kural tabanlı koç kullanılır
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["COACH_PROVIDER"] = "rule"
os.environ["REDIS_URL"] = ""  # testler Redis olmadan (süreç içi yedeklerle) koşar
# Kullanıcı başına genel istek sınırı testlerde kapalı; tests/test_platform.py kendi içinde açar
os.environ["RATE_LIMIT_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _isolate_shared_state():
    """İstek sınırı sayaçları ve Redis istemcisi testler arasında taşınmaz."""
    from app.redis_client import reset_redis
    from app.services.circuit_breaker import reset_breakers
    from app.services.ratelimit import reset_local_limits

    reset_local_limits()
    reset_redis(None)
    reset_breakers()
    yield
    reset_local_limits()
    reset_breakers()


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def register(client, email, role="patient", **extra):
    body = {"email": email, "password": "sifre1234", "full_name": email.split("@")[0].title(), "role": role}
    if role == "patient":
        body.update(consent=True, has_diabetes=True)
    body.update(extra)
    r = client.post("/auth/register", json=body)
    assert r.status_code == 201, r.text
    tok = client.post("/auth/login", data={"username": email, "password": "sifre1234"}).json()["access_token"]
    return r.json(), {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def patient(client):
    return register(client, "ayse@example.com")


@pytest.fixture()
def clinician(client):
    return register(client, "dr.mehmet@example.com", role="clinician", title="Uzm. Dr.")
