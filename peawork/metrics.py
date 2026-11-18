from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, Summary

# Existing metrics
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

# R5 High Availability metrics
HA_NODES_TOTAL = Gauge(
    "peawork_ha_nodes_total", "Total HA nodes registered"
)
HA_NODES_HEALTHY = Gauge(
    "peawork_ha_nodes_healthy", "Number of healthy HA nodes"
)
HA_NODES_UNHEALTHY = Gauge(
    "peawork_ha_nodes_unhealthy", "Number of unhealthy HA nodes"
)
HA_READY = Gauge(
    "peawork_ha_ready", "1 if HA readiness is true, 0 otherwise"
)

# R5 Disaster Recovery metrics
BACKUP_COUNT = Counter(
    "peawork_backups_created_total", "Total backups created", labelnames=("label",)
)
BACKUP_SIZE_BYTES = Summary(
    "peawork_backup_size_bytes", "Size of created backups"
)
BACKUP_REPLICATED = Counter(
    "peawork_backups_replicated_total", "Backups replicated to object storage"
)
PITR_ENABLED = Gauge(
    "peawork_pitr_enabled", "1 if PITR is enabled, 0 otherwise"
)

# R5 Billing metrics
BILLING_PLANS_TOTAL = Gauge(
    "peawork_billing_plans_total", "Total number of billing plans defined"
)
BILLING_USAGE = Gauge(
    "peawork_billing_usage", "Current usage for tenant", labelnames=("tenant_id", "metric")
)
BILLING_OVERAGE = Counter(
    "peawork_billing_overage_total", "Overage events", labelnames=("tenant_id", "metric")
)
BILLING_TRIAL_USERS = Gauge(
    "peawork_billing_trial_users", "Number of users on trial plans"
)

# Application lifecycle
STARTUP_TIMESTAMP = Gauge(
    "peawork_startup_timestamp_seconds",
    "Unix timestamp of application startup",
)

# Memory/CPU (basic)
PROCESS_CPU_PERCENT = Gauge(
    "peawork_process_cpu_percent", "Process CPU usage percentage"
)
PROCESS_MEMORY_BYTES = Gauge(
    "peawork_process_memory_bytes", "Process memory usage in bytes"
)

# Database metrics
DB_CONNECTIONS_TOTAL = Counter(
    "peawork_db_connections_total", "Total database connections", labelnames=("state",)
)
DB_QUERIES_TOTAL = Counter(
    "peawork_db_queries_total", "Total database queries", labelnames=("query_type",)
)