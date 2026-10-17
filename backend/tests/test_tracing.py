"""OpenTelemetry kurulumu: env yoksa no-op; URL özniteliklerinden sorgu dizesi silinir (sağlık verisi sızmasın).

Gerçek exporter / ağ çağrısı YOK; global TracerProvider değiştirilmez.
"""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import observability
from app.observability import scrub_span_urls, setup_tracing


def test_setup_tracing_is_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert setup_tracing() is False
    assert observability._tracing_state["global"] is False


def test_setup_tracing_is_noop_with_blank_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "  ")
    assert setup_tracing() is False
    assert observability._tracing_state["global"] is False


def test_scrub_span_urls_removes_query_strings():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("GET /education/search") as span:
        span.set_attribute("http.target", "/education/search?q=g%C3%B6%C4%9F%C3%BCs+a%C4%9Fr%C4%B1s%C4%B1")
        span.set_attribute("http.url", "http://api:8000/education/search?q=gogus+agrisi")
        span.set_attribute("url.full", "https://api.fda.gov/drug/label.json?search=metformin")
        span.set_attribute("url.query", "q=gogus+agrisi")
        span.set_attribute("http.method", "GET")
        scrub_span_urls(span)

    (finished,) = exporter.get_finished_spans()
    attrs = dict(finished.attributes)
    assert attrs["http.target"] == "/education/search"
    assert attrs["http.url"] == "http://api:8000/education/search"
    assert attrs["url.full"] == "https://api.fda.gov/drug/label.json"
    assert attrs["url.query"] == ""
    assert attrs["http.method"] == "GET"
    assert not any("agri" in str(v) or "metformin" in str(v) for v in attrs.values())


def test_scrub_span_urls_tolerates_missing_span():
    scrub_span_urls(None)  # istisna atmamalı
