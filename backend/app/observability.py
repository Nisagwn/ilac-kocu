"""Gözlemlenebilirlik: istek kimliği, yapılandırılmış erişim logu ve Prometheus metrikleri.

- `RequestContextMiddleware` (saf ASGI): gelen `X-Request-ID`'yi (güvenli biçimdeyse) kullanır ya da uuid4 üretir,
  yanıta ekler; her istek için tek satır JSON log yazar (yöntem, ROTA ŞABLONU, durum, süre, istek kimliği).
  Ham yol, sorgu değerleri ve gövdeler ASLA loglanmaz / etiket olmaz (kimlik ve sağlık verisi sızmasın).
- `metrics_router`: `/metrics` (Prometheus metin biçimi). METRICS_ENABLED=false ise 404; METRICS_TOKEN tanımlıysa
  `Authorization: Bearer <token>` zorunlu. OpenAPI şemasında görünmez.
- LLM/koç metrikleri `services/llm` içinden güncellenir; guardrail sayacı CoachMessage `after_insert` olayıyla.

Etiket kardinalitesi düşük tutulur: hasta/kullanıcı kimliği hiçbir etikette yer almaz.
"""

import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import event

from app.config import get_settings
from app.models import CoachMessage, Sender

access_logger = logging.getLogger("app.access")
if not access_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    access_logger.addHandler(_handler)
access_logger.setLevel(logging.INFO)

REQUEST_ID_HEADER = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")  # log enjeksiyonunu önler
UNMATCHED_ROUTE = "unmatched"  # eşleşmeyen yollar tek etikette toplanır (404 taramaları kardinaliteyi şişirmesin)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Geçerli isteğin kimliği (istek bağlamı dışında None)."""
    return request_id_var.get()


# ---------------------------------------------------------------- metrikler
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_LLM_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP istek süresi (saniye)", ["method", "route", "status"],
    buckets=_LATENCY_BUCKETS,
)
HTTP_REQUESTS = Counter("http_requests", "HTTP istek sayısı", ["method", "route", "status"])

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "LLM isteği süresi (koç yanıtı/özet anlatısı, tüm tool-use turları dahil)",
    ["purpose", "model", "outcome"],  # outcome: ok | fallback | budget_exceeded | error
    buckets=_LLM_BUCKETS,
)
LLM_TOKENS = Counter("llm_tokens", "LLM token kullanımı", ["purpose", "model", "kind"])
LLM_COST_USD = Counter("llm_cost_usd", "LLM maliyeti (USD)", ["purpose", "model"])
LLM_RATE_LIMITED = Counter("llm_rate_limited", "Sağlayıcıdan alınan 429 yanıtları (yeniden denemeler dahil)")
COACH_GUARDRAIL_ACTIONS = Counter(
    "coach_guardrail_actions", "Koç yanıtlarındaki guardrail eylemleri", ["action"])


@dataclass
class LlmObservation:
    purpose: str
    model: str
    outcome: str = "ok"


@contextmanager
def observe_llm(purpose: str, model: str) -> Iterator[LlmObservation]:
    """LLM isteğinin süresini ölçer. Blok içinde `obs.outcome` / `obs.model` güncellenebilir;
    blok istisnayla çıkarsa outcome 'error' olur."""
    obs = LlmObservation(purpose=purpose, model=model or "unknown")
    start = time.perf_counter()
    try:
        yield obs
    except BaseException:
        obs.outcome = "error"
        raise
    finally:
        LLM_REQUEST_DURATION.labels(obs.purpose, obs.model or "unknown", obs.outcome).observe(
            time.perf_counter() - start)


def record_llm_usage(purpose: str, model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int,
                     cache_write_tokens: int, cost_usd: float) -> None:
    model = model or "unknown"
    for kind, n in (("input", input_tokens), ("output", output_tokens), ("cache_read", cache_read_tokens),
                    ("cache_write", cache_write_tokens)):
        if n:
            LLM_TOKENS.labels(purpose, model, kind).inc(n)
    if cost_usd and cost_usd > 0:
        LLM_COST_USD.labels(purpose, model).inc(cost_usd)


def record_rate_limited() -> None:
    LLM_RATE_LIMITED.inc()


@event.listens_for(CoachMessage, "after_insert")
def _count_guardrail_action(_mapper, _connection, target: CoachMessage) -> None:
    # services/coach.py'ye dokunmadan: her koç mesajı kaydında eylem sayılır (yok ise "none")
    if target.sender == Sender.coach:
        COACH_GUARDRAIL_ACTIONS.labels((target.guardrail_action or "none")[:40]).inc()


# ---------------------------------------------------------------- middleware
def _route_template(scope: dict) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else UNMATCHED_ROUTE


class RequestContextMiddleware:
    """Saf ASGI middleware (BaseHTTPMiddleware'in akış/arka plan görevi sorunlarını taşımaz)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers") or []:
            if name == b"x-request-id":
                incoming = value.decode("latin-1").strip()
                break
        request_id = incoming if incoming and _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status_holder = {"status": 500}
        start = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            method = scope.get("method", "")
            route = _route_template(scope)
            status = str(status_holder["status"])
            HTTP_REQUEST_DURATION.labels(method, route, status).observe(duration)
            HTTP_REQUESTS.labels(method, route, status).inc()
            access_logger.info(json.dumps({
                "event": "http_request", "request_id": request_id, "method": method, "route": route,
                "status": int(status), "duration_ms": round(duration * 1000, 2),
            }, ensure_ascii=False))
            request_id_var.reset(token)


# ---------------------------------------------------------------- /metrics
metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    s = get_settings()
    if not s.metrics_enabled:
        return Response(status_code=404)
    if s.metrics_token:
        auth = request.headers.get("authorization", "")
        scheme, _, given = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), s.metrics_token.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
