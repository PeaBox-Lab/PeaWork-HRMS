"""OpenTelemetry instrumentation for PeaWork HRMS.

This module initializes OpenTelemetry tracing and metrics for the application.
It should be imported early in the application startup, typically in the
main FastAPI app creation file.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from .config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Tracing setup
# ---------------------------------------------------------------------------

def _init_tracing() -> None:
    """Initialize OpenTelemetry tracing."""
    trace.set_tracer_provider(
        TracerProvider(
            resources=[Resource.create({"service.name": settings.otel_service_name})]
        )
    )

    # Choose exporter
    if settings.otel_endpoint:
        # Use OTLP gRPC exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_endpoint,
            insecure=settings.otel_insecure,
        )
    else:
        # Use console exporter for development
        otlp_exporter = ConsoleSpanExporter()

    # Add span processor
    trace.get_tracer_provider().add_span_processor(
        BatchSpanProcessor(otlp_exporter)
    )

# ---------------------------------------------------------------------------
# Metrics setup
# ---------------------------------------------------------------------------

def _init_metrics() -> Histogram:
    """Initialize OpenTelemetry metrics."""
    # Define Prometheus metrics
    request_duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path", "status_code"],
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
    )
    return request_duration

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class OTELHTTPMiddleware(BaseHTTPMiddleware):
    """Middleware to capture HTTP request timing and create spans."""
    def __init__(self, app, tracer_provider, request_duration_histogram):
        self.app = app
        self.tracer_provider = tracer_provider
        self.request_duration = request_duration_histogram

    async def dispatch(self, request: Request, call_next):
        # Start a new span for the request
        with self.tracer_provider.trace_context_manager() as span:
            # Create a span for the request
            tracer = self.tracer_provider.get_tracer("fastapi")
            with tracer.start_as_current_span("http.request"):
                start_time = time.time()
                try:
                    response: Response = await call_next(request)
                    process_time = time.time() - start_time
                    # Record metrics
                    self.request_duration.labels(
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                    ).observe(process_time)
                    # Add attributes to span
                    span.set_attribute("http.method", request.method)
                    span.set_attribute("http.route", request.url.path)
                    span.set_attribute("http.status_code", response.status_code)
                    span.set_attribute("http.client.ip", request.client.host if request.client else "unknown")
                    return response
                except Exception as exc:
                    span.record_exception(exc)
                    raise

# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------

def init_otel(app):
    """Initialize OpenTelemetry on the given FastAPI app."""
    if not settings.otel_enabled:
        return

    # Initialize tracing
    _init_tracing()

    # Initialize metrics
    request_duration = _init_metrics()

    # Add middleware
    app.add_middleware(OTELHTTPMiddleware, tracer_provider=trace.get_tracer_provider(), request_duration_histogram=request_duration)

    # Add FastAPI auto-instrumentation (requires opentelemetry-instrumentation-fastapi)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception as e:
        print(f"Warning: FastAPI instrumentation failed: {e}")

    # Add Prometheus metrics endpoint
    @app.get("/metrics")
    async def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
