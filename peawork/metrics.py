from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter(
    "peawork_http_requests_total",
    "HTTP requests handled by PeaWork",
    labelnames=("method", "route", "status"),
)
REQUEST_LATENCY = Histogram(
    "peawork_http_request_duration_seconds",
    "HTTP request latency",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
LOGIN_FAILURES = Counter(
    "peawork_login_failures_total", "Failed login attempts", labelnames=("reason",)
)
