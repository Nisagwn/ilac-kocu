"""Dağıtık izleme (OpenTelemetry → OTLP/HTTP → Jaeger). Yalnızca ortam değişkeniyle açılır:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318   # boşsa izleme kapalı (varsayılan; testler)
    OTEL_SERVICE_NAME=ilac-kocu-api                  # işçi: ilac-kocu-worker

Aletlenenler: FastAPI (gelen istek), SQLAlchemy (sorgular; parametre değerleri yazılmaz), httpx (LLM,
openFDA), Redis. /health* ve /metrics izlenmez.

Gizlilik (KVKK): izler sağlık verisi ya da kimlik taşımamalıdır. Dışa aktarımdan önce (`redacting_exporter`)
URL sorgu dizesi silinir, yoldaki sayısal/uuid kimlikler `{id}` olur
(`/v1/patients/42/measurements?type=...` → `/v1/patients/{id}/measurements`), istemci IP'si ve user-agent
atılır. İstek ve yanıt gövdeleri, başlıklar ve SQL parametreleri hiçbir zaman span'e yazılmaz.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

_ID_SEGMENT = re.compile(r"/(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=/|$)")
_PATH_KEYS = ("http.target", "url.path", "http.route_raw")
_URL_KEYS = ("http.url", "url.full")
_QUERY_KEYS = ("url.query",)
EXCLUDED_URLS = "health,metrics"

_configured = False


def redact_path(path: str) -> str:
    return _ID_SEGMENT.sub("/{id}", path.split("?", 1)[0])


def redact_url(url: str) -> str:
    base = url.split("?", 1)[0]
    m = re.match(r"^([a-z]+://[^/]+)(.*)$", base)
    return (m.group(1) + redact_path(m.group(2))) if m else redact_path(base)


def tracing_enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


_DROP_KEYS = ("net.peer.ip", "net.peer.port", "client.address", "client.port", "http.client_ip",
              "net.sock.peer.addr", "net.sock.peer.port", "http.user_agent", "user_agent.original")


def redact_attributes(attrs: dict) -> dict:
    out = {k: v for k, v in attrs.items() if k not in _DROP_KEYS}
    for k in _PATH_KEYS:
        if isinstance(out.get(k), str):
            out[k] = redact_path(out[k])
    for k in _URL_KEYS:
        if isinstance(out.get(k), str):
            out[k] = redact_url(out[k])
    for k in _QUERY_KEYS:
        if k in out:
            out[k] = "[redacted]"
    return out


def redacting_exporter(inner):
    """Dışa aktarmadan önce her span'in özniteliklerini temizleyen exporter sarmalayıcısı. (Aletleme
    öznitelikleri span başladıktan sonra da yazdığı için temizlik dışa aktarımda yapılır.)"""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter

    class RedactingExporter(SpanExporter):
        def export(self, spans):
            clean = [ReadableSpan(
                name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                attributes=redact_attributes(dict(s.attributes or {})), events=s.events, links=s.links,
                kind=s.kind, status=s.status, start_time=s.start_time, end_time=s.end_time,
                instrumentation_scope=s.instrumentation_scope,
            ) for s in spans]
            return inner.export(clean)

        def shutdown(self):
            return inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000):
            return inner.force_flush(timeout_millis)

    return RedactingExporter()


def setup_tracing(app=None, service_name: str | None = None, exporter=None) -> bool:
    """İzlemeyi kurar; kapalıysa (uç nokta yok ve exporter verilmemiş) hiçbir şey yapmaz, False döner.
    `exporter` testlerde bellek içi exporter vermek içindir."""
    global _configured
    if exporter is None and not tracing_enabled():
        return False
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    if not _configured:
        name = service_name or os.environ.get("OTEL_SERVICE_NAME") or "ilac-kocu-api"
        provider = TracerProvider(resource=Resource.create({"service.name": name}))
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(redacting_exporter(OTLPSpanExporter())))
        else:
            provider.add_span_processor(SimpleSpanProcessor(redacting_exporter(exporter)))
        trace.set_tracer_provider(provider)
        _instrument_libraries()
        _configured = True
        log.info("OpenTelemetry izleme açık (%s)", name)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"])
    return True


def _instrument_libraries() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.db import engine

    SQLAlchemyInstrumentor().instrument(engine=engine, enable_commenter=False)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
