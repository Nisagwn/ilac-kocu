"""Gözlemlenebilirlik testleri: istek kimliği, JSON erişim logu, /metrics, LLM metrikleri, paylaşımlı önbellek.

Ağ çağrısı ve gerçek Redis YOK (sahte istemciler / sahte Redis nesnesi).
"""

import json
import logging

import pytest
from prometheus_client import REGISTRY

from app.config import get_settings
from app.models import Patient
from app.redis_client import reset_redis
from app.services import openfda
from app.services.coach import handle_message
from app.services.llm import narrative
from tests.test_coach_llm import FakeClient, final, provider_with, response, text
from tests.test_openrouter import chat_response, mock_client


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class FakeRedis:
    """decode_responses=True davranışı: değerler str olarak saklanır/döner."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        assert isinstance(value, str)
        self.store[key] = value
        self.ttls[key] = ttl

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def delete(self, key):
        self.store.pop(key, None)


# ---------------- HTTP: istek kimliği, log, metrikler ----------------
def test_request_id_generated_and_echoed(client):
    r = client.get("/health")
    assert r.status_code == 200 and len(r.headers["x-request-id"]) == 32

    r = client.get("/health", headers={"X-Request-ID": "abc-123.def"})
    assert r.headers["x-request-id"] == "abc-123.def"

    # güvensiz (log enjeksiyonu) kimlik yok sayılır, yenisi üretilir
    r = client.get("/health", headers={"X-Request-ID": "kotu deger\"}"})
    assert r.headers["x-request-id"] != "kotu deger\"}" and len(r.headers["x-request-id"]) == 32


def test_access_log_is_json_with_route_template_and_no_query_values(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/health?token=cok-gizli-deger&tc=12345678901", headers={"X-Request-ID": "req-1"})
        client.get("/patients/987654", headers={"X-Request-ID": "req-2"})  # yetkisiz: 401

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "app.access"]
    first = next(x for x in lines if x["request_id"] == "req-1")
    assert first == {"event": "http_request", "request_id": "req-1", "method": "GET", "route": "/health",
                     "status": 200, "duration_ms": first["duration_ms"]}
    second = next(x for x in lines if x["request_id"] == "req-2")
    assert second["route"] == "/patients/{patient_id}" and second["status"] == 401  # ham yol/kimlik yok

    raw = "\n".join(r.getMessage() for r in caplog.records)
    assert "cok-gizli-deger" not in raw and "12345678901" not in raw and "987654" not in raw


def test_metrics_endpoint_exposes_http_counters(client):
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'http_requests_total{method="GET",route="/health",status="200"}' in r.text
    assert "http_request_duration_seconds_bucket" in r.text

    client.get("/bu-yol-yok/123")
    assert 'route="unmatched"' in client.get("/metrics").text
    assert "/bu-yol-yok" not in client.get("/metrics").text  # eşleşmeyen yollar etiket olmaz


def test_metrics_not_in_openapi(client):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_metrics_token_enforced(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "gizli-token")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer yanlis"}).status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer gizli-token"})
    assert r.status_code == 200 and "http_requests_total" in r.text


def test_metrics_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_enabled", False)
    assert client.get("/metrics").status_code == 404


# ---------------- LLM / koç metrikleri ----------------
@pytest.fixture()
def pat(db, patient):
    user, _ = patient
    return db.get(Patient, user["patient_id"])


def test_coach_llm_metrics_ok_and_tokens_and_guardrail(db, pat):
    model = "claude-opus-5-5"
    before_ok = sample("llm_request_duration_seconds_count", purpose="coach", model=model, outcome="ok")
    before_in = sample("llm_tokens_total", purpose="coach", model=model, kind="input")
    before_out = sample("llm_tokens_total", purpose="coach", model=model, kind="output")
    before_cost = sample("llm_cost_usd_total", purpose="coach", model=model)
    before_none = sample("coach_guardrail_actions_total", action="none")

    provider, _ = provider_with([final("Harika gidiyorsun!", inp=1000, out=200)])
    reply = handle_message(db, pat, "Bu hafta nasılım?", provider=provider)
    db.commit()
    assert reply.guardrail_action is None

    assert sample("llm_request_duration_seconds_count", purpose="coach", model=model, outcome="ok") == before_ok + 1
    assert sample("llm_tokens_total", purpose="coach", model=model, kind="input") == before_in + 1000
    assert sample("llm_tokens_total", purpose="coach", model=model, kind="output") == before_out + 200
    assert sample("llm_cost_usd_total", purpose="coach", model=model) > before_cost
    assert sample("coach_guardrail_actions_total", action="none") == before_none + 1


def test_coach_llm_metrics_fallback_and_budget(db, pat):
    model = "claude-opus-5-5"
    before_fb = sample("llm_request_duration_seconds_count", purpose="coach", model=model, outcome="fallback")
    before_fb_action = sample("coach_guardrail_actions_total", action="llm_fallback")
    provider, _ = provider_with([RuntimeError("boom")])
    reply = handle_message(db, pat, "bu hafta nasılım", provider=provider)
    db.commit()
    assert reply.guardrail_action == "llm_fallback"
    assert sample("llm_request_duration_seconds_count", purpose="coach", model=model,
                  outcome="fallback") == before_fb + 1
    assert sample("coach_guardrail_actions_total", action="llm_fallback") == before_fb_action + 1

    before_b = sample("llm_request_duration_seconds_count", purpose="coach", model=model, outcome="budget_exceeded")
    provider, client = provider_with([], llm_monthly_budget_usd=0.0)
    reply = handle_message(db, pat, "bu hafta nasılım", provider=provider)
    assert reply.guardrail_action == "budget_exceeded" and client.calls == []
    assert sample("llm_request_duration_seconds_count", purpose="coach", model=model,
                  outcome="budget_exceeded") == before_b + 1


def test_rate_limit_429_counter_includes_retries():
    before = sample("llm_rate_limited_total")
    client = mock_client([429, 429, chat_response(content="tamam")], [], sleeps=[])
    assert client.messages.create(messages=[{"role": "user", "content": "selam"}]).content[0].text == "tamam"
    assert sample("llm_rate_limited_total") == before + 2


# ---------------- Paylaşımlı önbellek (Redis) ----------------
FACTS = {"period_days": 7, "adherence": {"score": 0.9, "planned": 10, "taken": 9, "band": "iyi"},
         "missed_by_time_of_day": {}, "measurements": [], "alerts": {"total": 0, "critical": 0, "by_kind": {}}}


@pytest.fixture()
def _clean_narrative_cache():
    narrative.clear_cache()
    yield
    reset_redis(None)
    narrative.clear_cache()


@pytest.mark.usefixtures("_clean_narrative_cache")
def test_narrative_cache_uses_shared_redis(db, pat):
    fake_redis = FakeRedis()
    reset_redis(fake_redis)
    llm = FakeClient([response([text("Hasta son 7 günde dozlarının çoğunu almış.")], inp=500, out=60)])

    before = sample("llm_request_duration_seconds_count", purpose="summary", model="claude-opus-5-5", outcome="ok")
    first = narrative.generate_narrative(db, pat.id, FACTS, client=llm)
    assert first[1] == "llm"
    assert sample("llm_request_duration_seconds_count", purpose="summary", model="claude-opus-5-5",
                  outcome="ok") == before + 1

    [key] = fake_redis.store
    assert key.startswith(f"narr:{pat.id}:") and key.split(":")[3] == "7"
    assert fake_redis.ttls[key] == 24 * 3600
    assert json.loads(fake_redis.store[key]) == list(first)
    assert narrative._cache == {}  # süreç içi önbelleğe yazılmadı

    # başka bir worker gibi: süreç içi önbellek boş, değer Redis'ten gelir; ikinci model çağrısı yok
    assert narrative.generate_narrative(db, pat.id, FACTS, client=llm) == first
    assert len(llm.calls) == 1

    narrative.clear_cache()
    assert fake_redis.store == {}


@pytest.mark.usefixtures("_clean_narrative_cache")
def test_narrative_cache_falls_back_to_process_memory_without_redis(db, pat):
    reset_redis(None)
    llm = FakeClient([response([text("Hasta son 7 günde dozlarının çoğunu almış.")], inp=500, out=60)])
    first = narrative.generate_narrative(db, pat.id, FACTS, client=llm)
    assert first[1] == "llm" and len(narrative._cache) == 1
    assert narrative.generate_narrative(db, pat.id, FACTS, client=llm) == first
    assert len(llm.calls) == 1


@pytest.mark.usefixtures("_clean_narrative_cache")
def test_narrative_cache_redis_errors_fall_back(db, pat):
    class BrokenRedis(FakeRedis):
        def get(self, key):
            raise ConnectionError("down")

        def setex(self, key, ttl, value):
            raise ConnectionError("down")

    reset_redis(BrokenRedis())
    llm = FakeClient([response([text("Hasta son 7 günde dozlarının çoğunu almış.")], inp=500, out=60)])
    first = narrative.generate_narrative(db, pat.id, FACTS, client=llm)
    assert narrative.generate_narrative(db, pat.id, FACTS, client=llm) == first and len(llm.calls) == 1


def test_openfda_label_cache_uses_shared_redis(monkeypatch):
    fake_redis = FakeRedis()
    reset_redis(fake_redis)
    openfda.label_cache.clear()
    calls = []
    monkeypatch.setattr(openfda, "fetch_label", lambda name: calls.append(name) or {"resolved_generic": "metformin",
                                                                                    "indications": "ş"})
    try:
        assert openfda.get_label_cached("Glifor")["resolved_generic"] == "metformin"
        assert openfda.get_label_cached("glucophage")["indications"] == "ş"
        assert calls == ["Glifor"]
        assert json.loads(fake_redis.store["openfda:label:metformin"])["indications"] == "ş"
        assert openfda.label_cache._memory == {}
    finally:
        openfda.label_cache.clear()
        reset_redis(None)
