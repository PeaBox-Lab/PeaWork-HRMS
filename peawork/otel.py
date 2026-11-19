"""Optional OpenTelemetry instrumentation for PeaWork HRMS.

OpenTelemetry is an optional dependency. Importing this module must never fail
when the SDK is absent: the app still starts, and instrumentation is skipped
(with a warning) if ``PEAWORK_OTEL_ENABLED=true`` but the packages are missing.

Prometheus metrics are owned by :mod:`peawork.metrics` and exposed by the
``/metrics`` route in ``main.py``; this module only handles tracing.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_initialized = False


class OTELHTTPMiddleware(BaseHTTPMiddleware):
    """Create a span and record timing for each HTTP request."""

    def __init__(self, app, tracer):
        super().__init__(app)
        self.tracer = tracer

    async def dispatch(self, request, call_next):
        start_time = time.perf_counter()
        with self.tracer.start_as_current_span("http.request") as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.route", request.url.path)
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 - re-raised after recording
                span.record_exception(exc)
                raise
            elapsed = time.perf_counter() - start_time
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("http.duration_ms", round(elapsed * 1000, 2))
            span.set_attribute(
                "http.client.ip",
                request.client.host if request.client else "unknown",
            )
            return response


def init_otel(app) -> None:
    """Initialize OpenTelemetry tracing on ``app``.

    No-op unless ``PEAWORK_OTEL_ENABLED=true``. Safe to call more than once;
    the global tracer provider is only configured on the first call.
    """
    global _initialized

    if not settings.otel_enabled or _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        logger.warning(
            "PEAWORK_OTEL_ENABLED is true but the OpenTelemetry SDK is not "
            "installed (%s). Install requirements/v2-otel.txt to enable tracing.",
            exc,
        )
        return

    exporter = ConsoleSpanExporter()
    if settings.otel_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(
                endpoint=settings.otel_endpoint,
                insecure=settings.otel_insecure,
            )
        except ImportError as exc:  # pragma: no cover - optional extra
            logger.warning(
                "OTLP exporter not installed (%s); falling back to console spans.",
                exc,
            )

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    app.add_middleware(
        OTELHTTPMiddleware, tracer=provider.get_tracer("peawork.http")
    )

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:  # pragma: no cover - optional extra
        logger.info(
            "opentelemetry-instrumentation-fastapi is not installed; "
            "using the built-in request span middleware."
        )

    _initialized = True
    logger.info("OpenTelemetry tracing enabled (service=%s)", settings.otel_service_name)
