"""OpenTelemetry izleme: varsayılan kapalı; açıkken span'lerde kimlik/sorgu dizesi bulunmamalı."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import tracing


def test_disabled_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracing.tracing_enabled() is False
    assert tracing.setup_tracing(FastAPI()) is False


def test_redact_helpers():
    assert tracing.redact_path("/v1/patients/42/measurements?type=glucose_fasting") == "/v1/patients/{id}/measurements"
    assert tracing.redact_path("/v1/coach/jobs/1b4e28ba-2fa1-11d2-883f-0016d3cca427") == "/v1/coach/jobs/{id}"
    assert tracing.redact_path("/v1/drugs/search") == "/v1/drugs/search"
    assert tracing.redact_url("https://api.fda.gov/drug/label.json?search=x") == "https://api.fda.gov/drug/label.json"
    assert tracing.redact_url("http://api:8000/v1/patients/7?x=1") == "http://api:8000/v1/patients/{id}"


def test_server_spans_are_redacted():
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(tracing.redacting_exporter(exporter)))
    app = FastAPI()

    @app.get("/v1/patients/{pid}/measurements")
    def m(pid: int):
        return []

    @app.get("/health/live")
    def live():
        return {}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=tp, excluded_urls=tracing.EXCLUDED_URLS,
                                       exclude_spans=["receive", "send"])
    c = TestClient(app)
    c.get("/v1/patients/42/measurements?type=glucose_fasting&q=metformin")
    c.get("/health/live")
    spans = exporter.get_finished_spans()
    assert len(spans) == 1  # /health izlenmez
    dump = repr({k: v for s in spans for k, v in (s.attributes or {}).items()})
    assert "42" not in dump and "metformin" not in dump and "glucose_fasting" not in dump
    assert "testclient" not in dump  # istemci adresi / user-agent atılır
    assert spans[0].attributes["http.url"] == "http://testserver/v1/patients/{id}/measurements"
    assert spans[0].attributes.get("http.route") == "/v1/patients/{pid}/measurements"
