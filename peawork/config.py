from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-change-me"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"
VERSION = "2.1.0"


class Settings(BaseSettings):
    """Typed application configuration (environment prefix ``PEAWORK_``)."""

    model_config = SettingsConfigDict(
        env_prefix="PEAWORK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "PeaWork HRMS"
    version: str = "2.0.0"
    env: str = "dev"  # dev | test | production
    debug: bool = False
    base_url: str = "http://127.0.0.1:8090"

    # Platform
    secret_key: str = DEFAULT_SECRET
    database_url: str = "sqlite:///./data/peawork_v2.db"
    allowed_hosts: str = "*"
    behind_proxy: bool = False
    auto_create_schema: bool | None = None

    # Sessions and cookies
    session_cookie_name: str = "pw_session"
    csrf_cookie_name: str = "pw_csrf"
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    cookie_secure: bool | None = None
    cookie_domain: str | None = None

    # Bootstrap
    organization_name: str = "PeaWork Demo Organisation"
    organization_country: str = "IN"
    organization_timezone: str = "Asia/Kolkata"
    organization_currency: str = "INR"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_name: str = "Platform Administrator"
    bootstrap_admin_password: str | None = None
    seed_demo_data: bool = False

    # Storage
    storage_dir: str = "./data/documents"
    max_upload_bytes: int = 10 * 1024 * 1024
    allowed_upload_types: str = (
        "application/pdf,image/png,image/jpeg,text/plain,text/csv,"
        "application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # R5 — Object storage (MinIO / S3)
    object_storage_enabled: bool = False
    object_storage_provider: str | None = None  # "minio" | "s3"
    object_storage_endpoint: str | None = None      # MinIO URL or S3 endpoint
    object_storage_access_key: str | None = None    # sensitive: rotate in CI
    object_storage_secret_key: str | None = None    # sensitive: rotate in CI
    object_storage_bucket: str | None = None        # default bucket name
    object_storage_region: str | None = None        # e.g. "us-east-1", "ap-south-1"
    object_storage_secure: bool | None = None       # HTTPS for S3/MinIO
    object_storage_cdndomain: str | None = None     # custom CDN domain
    object_storage_encrypt_at_rest: bool = True     # SSE-S3 or SSE-KMS
    object_storage_acl: str = "private"             # Canned ACL for objects

    # Observability
    metrics_token: str | None = None
    log_level: str = "INFO"

    # R5 — SSO / OIDC
    sso_enabled: bool = False
    sso_provider: str | None = None  # "oidc" | "saml"
    sso_client_id: str | None = None
    sso_client_secret: str | None = None  # sensitive: rotate in CI
    sso_authorization_endpoint: str | None = None
    sso_token_endpoint: str | None = None
    sso_userinfo_endpoint: str | None = None
    sso_jwks_url: str | None = None  # JSON Web Key Set for token validation
    sso_redirect_url: str | None = None  # must match IdP callback
    sso_scope: str = "openid profile email"
    sso_session_timeout_minutes: int = 60

    # R5 — Tenancy
    tenancy_enabled: bool = False
    tenancy_mode: str | None = None  # "database-per-tenant" | "postgres-rls"
    tenant_resolver: str | None = None  # dotted path to callable that resolves tenant from request
    tenant_id_column: str = "tenant_id"  # column name on tenant-editable tables

    # Background jobs (R2+)
    redis_url: str | None = None

    # R5 — OpenTelemetry observability
    otel_enabled: bool = False
    otel_service_name: str = "peawork-hrms"
    otel_endpoint: str | None = None  # OTLP endpoint URL
    otel_insecure: bool = False
    otel_sample_rate: float = 1.0  # 0.0 to 1.0
    otel_trace_id: str | None = None
    otel_trace_flags: int = 1  # bit flags for trace processing

    # R5 — High Availability
    ha_enabled: bool = False
    ha_mode: str | None = None  # "active-passive" or "active-active"
    ha_nodes: list[str] = []  # list of instance IDs or hostnames
    ha_heartbeat_interval: int = 30  # seconds between health checks
    ha_max_unhealthy_nodes: int = 1  # max nodes allowed to be unhealthy
    ha_backup_enabled: bool = False  # enable automated backup during HA

    # R5 — Disaster Recovery & Backups
    backup_enabled: bool = False
    backup_schedule_cron: str = "0 2 * * *"  # daily at 2am
    backup_retention_days: int = 30
    backup_compression: str = "gzip"  # or "none"
    backup_object_storage_bucket: str | None = None
    backup_cross_region: bool = False
    pitr_enabled: bool = False
    pitr_wal_archive_dir: str | None = None
    pitr_recovery_target_time: str | None = None  # ISO timestamp
    pitr_retention_days: int = 35

    # R5 — Billing & Subscription
    billing_enabled: bool = False
    billing_provider: str | None = None  # "stripe" | "paddle" | "none"
    billing_api_key: str | None = None  # sensitive: rotate in CI
    billing_webhook_secret: str | None = None  # sensitive: rotate in CI
    billing_default_plan: str = "free"
    billing_currency: str = "INR"
    billing_trial_days: int = 14
    billing_limit_users: int = 100
    billing_limit_storage_mb: int = 1024
    billing_limit_api_calls_per_day: int = 10000
    billing_overage_enabled: bool = False

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def is_test(self) -> bool:
        return self.env.lower() in {"test", "testing"}

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_production or self.base_url.startswith("https://")

    @property
    def should_auto_create_schema(self) -> bool:
        if self.auto_create_schema is not None:
            return self.auto_create_schema
        return not self.is_production

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_content_types(self) -> set[str]:
        return {t.strip() for t in self.allowed_upload_types.split(",") if t.strip()}

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).expanduser().resolve()

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        if self.is_production:
            if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
                raise ValueError("PEAWORK_SECRET_KEY must be a random value of at least 32 characters in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("PostgreSQL is required in production (set PEAWORK_DATABASE_URL)")
            if not self.bootstrap_admin_password:
                raise ValueError("PEAWORK_BOOTSTRAP_ADMIN_PASSWORD is required for first boot in production")
            if self.bootstrap_admin_password == DEFAULT_ADMIN_PASSWORD:
                raise ValueError("Refusing the default bootstrap password in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache", "DEFAULT_ADMIN_PASSWORD"]
